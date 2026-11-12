# coding=utf-8
"""
    @project: MaxKB
    @file: answer_cache.py
    @desc: Exact stateless RAG answer caching with authorization and version checks.
"""
from copy import deepcopy
import json
import logging
import random
import time
from types import SimpleNamespace

import uuid_utils.compat as uuid
from django.core.cache import cache
from django.http import StreamingHttpResponse

from common.cache.embedding_cache import text_hash
from common.cache.model_profile import digest, enabled, public_params
from common.cache.single_flight import CacheLease
from common.constants.permission_constants import RoleConstants
from common.database_model_manage.database_model_manage import DatabaseModelManage
from common.encoder.encoder import SystemEncoder
from knowledge.models import Knowledge, KnowledgeCacheState, Paragraph
from maxkb.const import CONFIG
from models_provider.models import Model
from models_provider.tools import get_model_default_params

logger = logging.getLogger("maxkb")


def authorized_knowledge_ids(chat_info):
    ids = list(chat_info.knowledge_id_list)
    authorize = DatabaseModelManage.get_model("get_knowledge_list_of_authorized")
    if authorize is not None and chat_info.chat_user_type == RoleConstants.CHAT_USER.value.name:
        ids = authorize(chat_info.chat_user_id, ids)
    return sorted(set(map(str, ids)))


def knowledge_versions(ids):
    if Knowledge.objects.filter(pk__in=ids).count() != len(ids):
        raise ValueError("An answer cache knowledge base no longer exists")
    versions = {str(key): value for key, value in KnowledgeCacheState.objects.filter(
        knowledge_id__in=ids).values_list("knowledge_id", "cache_version")}
    return [(key, versions.get(key, 0)) for key in ids]


def eligible(chat_info, instance, debug):
    app = chat_info.application
    return (enabled("QA_L1_CACHE_ENABLED") and not debug and app.type == "SIMPLE" and app.model_id is not None
            and app.dialogue_number == 0 and not chat_info.chat_record_list
            and not app.problem_optimization and not app.long_term_enable
            and not app.mcp_enable and not app.tool_enable and not app.application_enable
            and not app.mcp_tool_ids and not app.tool_ids and not app.application_ids and not app.skill_tool_ids
            and not app.mcp_servers
            and not any(instance.get(key) for key in ("re_chat", "chat_record_id", "form_data", "image_list",
                "document_list", "audio_list", "video_list", "other_list", "child_node", "runtime_node_id")))


class AnswerCacheRequest:
    def __init__(self, chat_info, instance):
        self.chat_info = chat_info
        self.instance = instance
        self.started = time.perf_counter()
        self.knowledge_ids = authorized_knowledge_ids(chat_info)
        self.versions = knowledge_versions(self.knowledge_ids)
        app = chat_info.application
        application = {field.attname: getattr(app, field.attname) for field in app._meta.concrete_fields}
        publication_hash = digest(json.loads(json.dumps(application, cls=SystemEncoder)))
        model = Model.objects.filter(pk=app.model_id).first() if app.model_id else None
        model_config = {"id": str(app.model_id), "provider": model.provider, "name": model.model_name,
                        "params": public_params(get_model_default_params(model))} if model else None
        namespace = {"application_id": str(chat_info.application_id), "publication_hash": publication_hash,
            "workspace": app.workspace_id, "knowledge_versions": self.versions,
            "knowledge_models": sorted((str(key), str(value)) for key, value in Knowledge.objects.filter(
                id__in=self.knowledge_ids).values_list("id", "embedding_model_id")),
            "retrieval": app.knowledge_setting, "excluded_documents": sorted(map(str, chat_info.exclude_document_id_list)),
            "chunking": {"prefix": enabled("EMBEDDING_CHUNK_TITLE_PREFIX_ENABLED"),
                "prefix_max_chars": int(CONFIG.get("EMBEDDING_CHUNK_TITLE_PREFIX_MAX_CHARS", 100)),
                "table": enabled("TABLE_CHUNK_ENABLED"), "chunk_size": 256, "implementation": "week3-v1"},
            "embedding_epoch": int(CONFIG.get("EMBEDDING_CACHE_EPOCH", 1)),
            "answer_model": model_config, "answer_params": public_params(app.model_params_setting),
            "prompt_hash": digest(app.model_setting),
            "scope": {"user": str(chat_info.chat_user_id), "type": chat_info.chat_user_type,
                      "knowledge_ids": self.knowledge_ids}}
        self.key = f"{app.workspace_id}:cache:v1:qa:l1:{chat_info.application_id}:{digest(namespace)}:"
        self.key += text_hash(instance["message"])
        self.lease = None

    @classmethod
    def prepare(cls, chat_info, instance, debug=False):
        if not eligible(chat_info, instance, debug):
            return None
        return cls(chat_info, instance)

    def current(self):
        return (authorized_knowledge_ids(self.chat_info) == self.knowledge_ids
                and knowledge_versions(self.knowledge_ids) == self.versions)

    def evidence_signature(self, ids):
        rows = list(Paragraph.objects.filter(id__in=ids, knowledge_id__in=self.knowledge_ids,
            is_active=True, document__is_active=True).order_by("id").values(
                "id", "knowledge_id", "document_id", "title", "content", "document__name"))
        if len(rows) != len(set(ids)):
            return None
        return digest(json.loads(json.dumps(rows, cls=SystemEncoder)))

    def lookup(self):
        try:
            payload = cache.get(self.key)
        except Exception:
            logger.warning("Answer cache read failed", exc_info=True)
            return None
        if (isinstance(payload, dict) and {"answer", "reasoning", "details", "evidence_ids", "evidence_hash"} <= payload.keys()
                and self.current() and self.evidence_signature(payload["evidence_ids"]) == payload["evidence_hash"]):
            return payload
        return None

    def acquire(self):
        self.lease = CacheLease(self.key + ":lock", wait=1, ttl=120)
        self.lease.__enter__()

    def close(self):
        if self.lease is not None:
            self.lease.__exit__()
            self.lease = None

    def publish(self, answer, reasoning, details):
        evidence = details.get("search_step", {}).get("paragraph_list", [])
        evidence_ids = [str(row["id"]) for row in evidence]
        if not answer or not evidence_ids or not self.current():
            return
        signature = self.evidence_signature(evidence_ids)
        original = [{"id": str(row["id"]), "knowledge_id": str(row["knowledge_id"]),
                     "document_id": str(row["document_id"]), "title": row["title"], "content": row["content"],
                     "document__name": row["document_name"]} for row in evidence]
        if signature is None or signature != digest(sorted(original, key=lambda row: row["id"])):
            return
        payload = {"answer": answer, "reasoning": reasoning, "details": details,
                   "evidence_ids": evidence_ids, "evidence_hash": signature}
        payload = json.loads(json.dumps(payload, cls=SystemEncoder))
        try:
            # Keep the first complete answer when bounded lock waiting allows duplicate computation.
            cache.add(self.key, payload, timeout=1800 + random.randint(-180, 180))
        except Exception:
            logger.warning("Answer cache publish failed", exc_info=True)

    def replay(self, payload, formatter, post_handler, fallback):
        from application.chat_pipeline.step.chat_step.impl.base_chat_step import add_access_num
        chat_info = self.chat_info
        record_id = uuid.uuid7()
        visible_reasoning = payload["reasoning"] if chat_info.application.model_setting.get(
            "reasoning_content_enable", False) else ""
        details = deepcopy(payload["details"])
        for step in details.values():
            step.update(message_tokens=0, answer_tokens=0, run_time=0)
        details["answer_cache"] = {"hit": True, "level": "L1"}

        def save():
            context = {"message_tokens": 0, "answer_tokens": 0,
                       "run_time": time.perf_counter() - self.started}
            manage = SimpleNamespace(context=context, get_details=lambda: details)
            post_handler.handler(chat_info.chat_id, record_id, [], self.instance["message"], payload["answer"],
                                 manage, None, reasoning_content=payload["reasoning"])
            add_access_num(chat_info.chat_user_id, chat_info.chat_user_type, chat_info.application_id)

        if not self.instance.get("stream"):
            if not self.current():
                return fallback()
            save()
            return formatter.to_block_response(str(chat_info.chat_id), str(record_id), payload["answer"], True, 0, 0,
                {"reasoning_content": visible_reasoning, "answer_list": [{"content": payload["answer"],
                  "reasoning_content": visible_reasoning}]})

        def stream():
            if not self.current():
                response = fallback()
                try:
                    yield from response.streaming_content
                finally:
                    response.close()
                return
            save()
            yield formatter.to_stream_chunk_response(str(chat_info.chat_id), str(record_id), "ai-chat-node", [],
                payload["answer"], False, 0, 0, {"node_is_end": False, "view_type": "many_view",
                "node_type": "ai-chat-node", "reasoning_content": visible_reasoning})
            yield formatter.to_stream_chunk_response(str(chat_info.chat_id), str(record_id), "ai-chat-node", [],
                "", True, 0, 0, {"node_is_end": True, "view_type": "many_view", "node_type": "ai-chat-node"})
        response = StreamingHttpResponse(stream(), content_type="text/event-stream;charset=utf-8")
        response["Cache-Control"] = "no-cache"
        return response
