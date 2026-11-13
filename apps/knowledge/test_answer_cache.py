# coding=utf-8
"""
    @project: MaxKB
    @file: test_answer_cache.py
    @desc: Exact answer admission, replay, evidence and vector-layer invalidation.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import uuid_utils.compat as uuid
from django.core.cache import cache
from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase

from application.models import Application, ApplicationFolder, ChatRecord
from application.serializers.common import ChatInfo
from application.services.answer_cache import AnswerCacheRequest
from chat.serializers.chat import ChatSerializers, get_post_handler
from common.handle.impl.response.system_to_response import SystemToResponse
from knowledge.models import Document, Embedding, Knowledge, KnowledgeCacheState, KnowledgeFolder, Paragraph
from knowledge.vector.pg_vector import PGVector
from maxkb.const import CONFIG
from models_provider.models import Model


class AnswerCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.config = patch.dict(CONFIG, {"QA_L1_CACHE_ENABLED": True})
        self.config.start()
        self.addCleanup(self.config.stop)
        folder = KnowledgeFolder.objects.create(id="answer-tests", name="cache")
        self.knowledge = Knowledge.objects.create(name="cache", desc="", folder=folder)
        self.other = Knowledge.objects.create(name="other", desc="", folder=folder)
        self.document = Document.objects.create(name="document", knowledge=self.knowledge, char_length=4)
        self.paragraph = Paragraph.objects.create(document=self.document, knowledge=self.knowledge,
                                                  title="title", content="body")
        app_folder = ApplicationFolder.objects.create(id="answer-tests", name="cache")
        model = Model.objects.create(name="answer-test-model", model_name="fake", provider="model_openai_provider",
                                     model_type="LLM", credential="unused", model_params_form=[])
        self.app = Application.objects.create(folder=app_folder, name="app", dialogue_number=0, mcp_servers={}, model=model)
        self.instance = {"message": "question 10?", "stream": False}
        self.details = {"search_step": {"paragraph_list": [{"id": str(self.paragraph.id),
            "knowledge_id": str(self.knowledge.id), "document_id": str(self.document.id),
            "document_name": self.document.name, "title": self.paragraph.title, "content": self.paragraph.content}]}}

    def info(self):
        info = ChatInfo(str(uuid.uuid7()), "cache-user", "test", "127.0.0.1", {},
                        [str(self.knowledge.id)], [], str(self.app.id))
        info.application = self.app
        return info

    def request(self, info=None, instance=None):
        return AnswerCacheRequest.prepare(info or self.info(), instance or self.instance)

    def vector(self):
        return Embedding.objects.create(id=str(uuid.uuid7()), document=self.document, paragraph=self.paragraph,
            knowledge=self.knowledge, source_id=str(self.paragraph.id), source_type="1", embedding=[0.1, 0.2])

    def version(self, knowledge=None):
        state = KnowledgeCacheState.objects.filter(knowledge=knowledge or self.knowledge).first()
        return state.cache_version if state else 0

    def test_key_isolation_for_application_scope_question_and_configuration(self):
        original = self.request().key
        for field, value in [("knowledge_setting", {"top_n": 9}), ("model_setting", {"prompt": "other"}),
                             ("workspace_id", "other"), ("model_params_setting", {"temperature": 0.2})]:
            before = deepcopy(getattr(self.app, field))
            setattr(self.app, field, value)
            self.assertNotEqual(self.request().key, original)
            setattr(self.app, field, before)
        for key, value in [("EMBEDDING_CHUNK_TITLE_PREFIX_ENABLED", False),
                           ("TABLE_CHUNK_ENABLED", False), ("EMBEDDING_CHUNK_TITLE_PREFIX_MAX_CHARS", 8)]:
            with patch.dict(CONFIG, {key: value}):
                self.assertNotEqual(self.request().key, original)
        info = self.info()
        info.chat_user_id = "another"
        self.assertNotEqual(self.request(info).key, original)
        for question in ["question 11?", "question 10!", "Question 10?", "not question 10?"]:
            self.assertNotEqual(self.request(instance={"message": question}).key, original)

    def test_admission_rejects_context_and_dynamic_inputs(self):
        for field in ["long_term_enable", "problem_optimization", "mcp_enable", "tool_enable", "application_enable"]:
            setattr(self.app, field, True)
            self.assertIsNone(self.request())
            setattr(self.app, field, False)
        for key in ["re_chat", "chat_record_id", "form_data", "image_list", "document_list"]:
            self.assertIsNone(self.request(instance={**self.instance, key: ["value"]}))
        info = self.info()
        info.chat_record_list = [Mock()]
        self.assertIsNone(self.request(info))
        self.assertIsNone(AnswerCacheRequest.prepare(self.info(), self.instance, debug=True))
        with patch.dict(CONFIG, {"QA_L1_CACHE_ENABLED": False}):
            self.assertIsNone(self.request())

    def test_document_name_switch_isolates_cached_answers_between_new_chats(self):
        with patch.dict(CONFIG, {"PROMPT_INCLUDE_DOCUMENT_NAME": False}):
            old = self.request()
            old.publish("without source", "", self.details)
            self.assertEqual(self.request().lookup()["answer"], "without source")
        with patch.dict(CONFIG, {"PROMPT_INCLUDE_DOCUMENT_NAME": True}):
            fresh = self.request()
            self.assertNotEqual(fresh.key, old.key)
            self.assertIsNone(fresh.lookup())
            fresh.publish("with source", "", self.details)
            self.assertEqual(self.request().lookup()["answer"], "with source")

    def test_publish_ttl_lookup_and_reject_late_result(self):
        request = self.request()
        with patch("application.services.answer_cache.cache.add", wraps=cache.add) as publish:
            request.publish("answer", "", self.details)
        self.assertTrue(1620 <= publish.call_args.kwargs["timeout"] <= 1980)
        self.assertEqual(request.lookup()["answer"], "answer")
        self.vector()
        PGVector().update_by_paragraph_id(self.paragraph.id, {"is_active": False})
        self.assertIsNone(request.lookup())
        late = self.request()
        PGVector().delete_by_paragraph_id(self.paragraph.id)
        late.publish("late", "", self.details)
        self.assertIsNone(cache.get(late.key))

    def test_edits_and_authorization_changes_reject_cached_evidence(self):
        request = self.request()
        request.publish("answer", "", self.details)
        Paragraph.objects.filter(pk=self.paragraph.id).update(content="changed")
        self.assertIsNone(request.lookup())
        request.publish("old answer", "", self.details)
        fresh = self.request()
        cache.delete(fresh.key)
        fresh.publish("old answer", "", self.details)
        self.assertIsNone(fresh.lookup())
        with patch("application.services.answer_cache.authorized_knowledge_ids", return_value=[]):
            self.assertFalse(request.current())

    def test_store_updates_deletes_moves_and_rollback_versions(self):
        store = PGVector()
        self.vector()
        store.update_by_source_id(str(self.paragraph.id), {"is_active": False})
        self.assertEqual(self.version(), 1)
        store.update_by_paragraph_ids([self.paragraph.id], {"knowledge_id": self.other.id})
        self.assertEqual(self.version(), 2)
        self.assertEqual(self.version(self.other), 1)
        try:
            with transaction.atomic():
                store.delete_by_document_id(self.document.id)
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
        self.assertEqual(self.version(self.other), 1)
        self.assertTrue(Embedding.objects.exists())
        store.delete_by_document_id_list([self.document.id])
        self.assertEqual(self.version(self.other), 2)

    def test_save_invalidation_and_interrupted_batch(self):
        model = Mock()
        model.embed_documents.return_value = [[0.1, 0.2]]
        row = {"document_id": self.document.id, "paragraph_id": self.paragraph.id,
            "knowledge_id": self.knowledge.id, "source_id": str(self.paragraph.id), "source_type": 1, "text": "body"}
        store = PGVector()
        store._batch_save([row], model, lambda: False)
        self.assertEqual(self.version(), 1)
        store._batch_save([row], model, lambda: True)
        self.assertEqual(self.version(), 1)
        self.assertEqual(Embedding.objects.count(), 1)

    def test_replay_creates_new_chat_records_streaming_and_block(self):
        request = self.request()
        request.publish("answer", "reasoning", self.details)
        payload = request.lookup()
        identifiers = []
        for stream in [False, True]:
            info = self.info()
            replay = self.request(info, {**self.instance, "stream": stream})
            response = replay.replay(payload, SystemToResponse(), get_post_handler(info), Mock())
            if stream:
                frames = [json.loads(part.decode().removeprefix("data: ")) for part in response.streaming_content]
                identifiers.append(frames[0]["chat_record_id"])
                self.assertTrue(frames[-1]["is_end"])
                self.assertEqual(frames[0]["usage"]["total_tokens"], 0)
                self.assertEqual(frames[0]["reasoning_content"], "")
            else:
                identifiers.append(json.loads(response.content)["data"]["id"])
                self.assertEqual(json.loads(response.content)["data"]["reasoning_content"], "")
        self.assertEqual(len(set(identifiers)), 2)
        self.assertEqual(ChatRecord.objects.count(), 2)
        self.assertTrue(all(record.message_tokens == 0 for record in ChatRecord.objects.all()))

    def test_chat_entrypoint_miss_completion_then_hit_and_failed_completion(self):
        serializer = ChatSerializers(data={"chat_id": str(uuid.uuid7()), "chat_user_id": "cache-user",
            "chat_user_type": "test", "application_id": str(self.app.id)})
        serializer.is_valid(raise_exception=True)

        def generate(info, instance, formatter, answer_cache=None):
            manage = SimpleNamespace(context={"message_tokens": 1, "answer_tokens": 2, "run_time": 0.1},
                                     get_details=lambda: self.details)
            get_post_handler(info, answer_cache).handler(info.chat_id, uuid.uuid7(), [], instance["message"],
                "answer", manage, None, cache_success=True)
            return formatter.to_block_response(info.chat_id, uuid.uuid7(), "answer", True, 1, 2)

        with patch.object(serializer, "_chat_simple", side_effect=generate) as normal, \
                patch("application.services.answer_cache.CacheLease"):
            serializer.chat_simple(self.info(), self.instance, SystemToResponse())
            result = serializer.chat_simple(self.info(), self.instance, SystemToResponse())
            self.assertEqual(normal.call_count, 1)
        self.assertEqual(json.loads(result.content)["data"]["completion_tokens"], 0)
        failed = self.request(instance={"message": "failed"})
        manage = SimpleNamespace(context={"message_tokens": 0, "answer_tokens": 0, "run_time": 0},
                                 get_details=lambda: self.details)
        info = failed.chat_info
        get_post_handler(info, failed).handler(info.chat_id, uuid.uuid7(), [], "failed", "Exception: fail", manage, None)
        self.assertIsNone(failed.lookup())


class CacheVersionConcurrencyTests(TransactionTestCase):
    def test_concurrent_increments_do_not_lose_updates(self):
        folder = KnowledgeFolder.objects.create(id="version-concurrency", name="cache")
        knowledge = Knowledge.objects.create(name="cache", desc="", folder=folder)

        def mutate(index):
            close_old_connections()
            try:
                with PGVector().vector_mutation([knowledge.id]):
                    pass
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(mutate, range(12)))
        self.assertEqual(KnowledgeCacheState.objects.get(knowledge=knowledge).cache_version, 12)
