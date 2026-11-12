#!/usr/bin/env python3
"""Exercise published stateless RAG cache hits with local retrieval and an offline response fixture."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps"), str(ROOT / "eval")]
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "maxkb.settings")
os.environ.setdefault("SERVER_NAME", "web")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "eval/.local-week3-D.json")
    args = parser.parse_args()
    import django
    django.setup()
    import numpy as np
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, AIMessageChunk
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
    from application.models import Application, ApplicationVersion, Chat, ChatRecord, ChatUserType
    from chat.serializers.chat import ChatSerializers, OpenChatSerializers
    from knowledge.models import Knowledge
    from maxkb.const import CONFIG
    from models_provider.models import Model
    from run_eval import load_dataset, write_result

    class ResponseFixture(BaseChatModel):
        calls: int = 0

        @property
        def _llm_type(self):
            return "cache-protocol-fixture"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="离线缓存链路验收回答"))])

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            yield ChatGenerationChunk(message=AIMessageChunk(content="离线缓存链路验收回答"))

        def get_num_tokens(self, text):
            return len(text)

        def get_num_tokens_from_messages(self, messages, **kwargs):
            return sum(len(message.content) for message in messages)

    local = json.loads(args.config.read_text("utf-8"))
    knowledge = Knowledge.objects.get(pk=local["knowledge_id"])
    model = Model.objects.create(name="L1-offline-fixture-" + str(time.time_ns()), model_type="LLM",
        model_name="gpt-4o-mini", provider="model_openai_provider", credential="unused",
        workspace_id=knowledge.workspace_id, user_id=knowledge.user_id)
    app = Application.objects.create(name="L1 offline acceptance fixture", folder_id="default",
        model=model, user_id=knowledge.user_id, dialogue_number=0, mcp_servers={},
        knowledge_setting={"top_n": 5, "similarity": 0.55, "search_mode": "embedding", "max_paragraph_char_number": 5000,
            "no_references_setting": {"status": "ai_questioning", "value": "{question}"}})
    version = {field.attname: getattr(app, field.attname) for field in ApplicationVersion._meta.concrete_fields
               if hasattr(app, field.attname) and field.attname not in ("id", "application_id", "create_time", "update_time")}
    ApplicationVersion.objects.create(**{**version, "application": app, "application_name": app.name,
                                        "knowledge_ids": [str(knowledge.id)]})
    fixture = ResponseFixture()
    CONFIG.update(EMBEDDING_CACHE_ENABLED=False, QUERY_EMBEDDING_CACHE_ENABLED=False)
    selected = {"q003", "q004", "q012", "q019", "q039", "q041", "q049", "q059"}
    questions = [row for row in load_dataset(ROOT / "eval/dataset.jsonl") if row["id"] in selected]
    outputs = []
    try:
        with patch("application.chat_pipeline.step.chat_step.impl.base_chat_step.get_model_instance_by_model_workspace_id",
                   return_value=fixture):
            for stage in ["off", "on-first", "on-second"]:
                CONFIG["QA_L1_CACHE_ENABLED"] = stage != "off"
                calls_before = fixture.calls
                rows = []
                for index, question in enumerate(questions):
                    fields = {"workspace_id": app.workspace_id, "application_id": str(app.id), "debug": False,
                              "chat_user_id": "l1-offline-fixture", "chat_user_type": ChatUserType.APPLICATION_API_KEY.value,
                              "ip_address": "127.0.0.1", "source": {}}
                    chat_id = OpenChatSerializers(data=fields).open()
                    start = time.perf_counter()
                    response = ChatSerializers(data={**fields, "chat_id": chat_id}).chat(
                        {"message": question["question"], "stream": bool(index % 2), "re_chat": False})
                    if response.streaming:
                        frames = [json.loads(part.decode().removeprefix("data: ")) for part in response.streaming_content]
                        content = "".join(frame.get("content", "") for frame in frames)
                        assert frames[-1]["is_end"]
                    else:
                        data = json.loads(response.content)
                        assert data["code"] == 200, data
                        content = data["data"]["content"]
                    elapsed = (time.perf_counter() - start) * 1000
                    response.close()
                    record = ChatRecord.objects.get(chat_id=chat_id)
                    hit = record.details.get("answer_cache", {}).get("hit", False)
                    rows.append({"id": question["id"], "stream": bool(index % 2), "response_ms": elapsed,
                        "cache_hit": hit, "record_id": str(record.id), "evidence_ids": [str(row["id"]) for row in
                            record.details.get("search_step", {}).get("paragraph_list", [])]})
                    assert content == "离线缓存链路验收回答", content
                    assert rows[-1]["evidence_ids"]
                timings = [row["response_ms"] for row in rows]
                outputs.append({"stage": stage, "requests": len(rows), "model_calls": fixture.calls - calls_before,
                    "cache_hit_rate": sum(row["cache_hit"] for row in rows) / len(rows),
                    "p50_ms": float(np.percentile(timings, 50)), "p95_ms": float(np.percentile(timings, 95)),
                    "queries": rows})
        assert [row["model_calls"] for row in outputs] == [8, 8, 0], outputs
        assert outputs[-1]["cache_hit_rate"] == 1
        assert len({row["record_id"] for output in outputs for row in output["queries"]}) == 24
        write_result(ROOT / "eval/results/week4/l1-protocol.json", {
            "schema_version": 1, "scope": "published ChatSerializers entrypoint, real local retrieval, offline response fixture",
            "answer_quality_evaluated": False, "external_llm_latency_evaluated": False, "results": outputs})
        print(json.dumps([{key: value for key, value in output.items() if key != "queries"}
                          for output in outputs], ensure_ascii=False, indent=2))
    finally:
        ChatRecord.objects.filter(chat__application=app).delete()
        Chat.objects.filter(application=app).delete()
        ApplicationVersion.objects.filter(application=app).delete()
        app.delete()
        model.delete()


if __name__ == "__main__":
    main()
