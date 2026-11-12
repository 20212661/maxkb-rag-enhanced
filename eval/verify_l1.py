#!/usr/bin/env python3
"""Measure published stateless RAG caching with a fixture or a configured LLM API."""
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


def provider_usage(result):
    usage = (getattr(result, "llm_output", None) or {}).get("token_usage")
    for group in getattr(result, "generations", []):
        for generation in group:
            message = getattr(generation, "message", None)
            usage = usage or getattr(message, "usage_metadata", None)
            usage = usage or (getattr(message, "response_metadata", None) or {}).get("token_usage")
    if not usage:
        return None
    prompt = usage.get("input_tokens", usage.get("prompt_tokens"))
    answer = usage.get("output_tokens", usage.get("completion_tokens"))
    if prompt is None or answer is None:
        return None
    return {"input_tokens": int(prompt), "output_tokens": int(answer),
            "total_tokens": int(usage.get("total_tokens", prompt + answer))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "eval/.local-week3-D.json")
    parser.add_argument("--model-id", help="Use an existing configured real LLM instead of the offline fixture")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--questions", nargs="+")
    parser.add_argument("--max-output-tokens", type=int, default=256)
    args = parser.parse_args()
    if args.max_output_tokens <= 0:
        parser.error("--max-output-tokens must be positive")
    import django
    django.setup()
    import numpy as np
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, AIMessageChunk
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
    from langchain_core.callbacks import BaseCallbackHandler
    from application.models import Application, ApplicationVersion, Chat, ChatRecord, ChatUserType
    from chat.serializers.chat import ChatSerializers, OpenChatSerializers
    from knowledge.models import Knowledge
    from maxkb.const import CONFIG
    from models_provider.models import Model
    from models_provider.tools import get_model_default_params, get_model_instance_by_model_workspace_id
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

    class UsageProbe(BaseCallbackHandler):
        def __init__(self):
            self.calls = 0
            self.usage = []

        def on_chat_model_start(self, *args, **kwargs):
            self.calls += 1

        def on_llm_end(self, response, **kwargs):
            self.usage.append(provider_usage(response))

    local = json.loads(args.config.read_text("utf-8"))
    knowledge = Knowledge.objects.get(pk=local["knowledge_id"])
    selected = set(args.questions or (["q003", "q004", "q006", "q007", "q008", "q009", "q012", "q015",
        "q019", "q039", "q049", "q059"] if args.model_id else
        ["q003", "q004", "q012", "q019", "q039", "q041", "q049", "q059"]))
    questions = [row for row in load_dataset(ROOT / "eval/dataset.jsonl") if row["id"] in selected]
    if len(questions) != len(selected) or (args.model_id and not 10 <= len(questions) <= 20):
        raise ValueError("Choose 10–20 existing question IDs for the real LLM comparison")
    model = Model.objects.get(pk=args.model_id, model_type="LLM", status="SUCCESS") if args.model_id else Model.objects.create(
        name="L1-offline-fixture-" + str(time.time_ns()), model_type="LLM",
        model_name="gpt-4o-mini", provider="model_openai_provider", credential="unused",
        workspace_id=knowledge.workspace_id, user_id=knowledge.user_id)
    parameters = get_model_default_params(model)
    if "max_tokens" in parameters:
        parameters["max_tokens"] = args.max_output_tokens
    app = Application.objects.create(name="L1 cache acceptance", folder_id="default", model_params_setting=parameters,
        model=model, user_id=knowledge.user_id, dialogue_number=0, mcp_servers={},
        knowledge_setting={"top_n": 5, "similarity": 0.55, "search_mode": "embedding", "max_paragraph_char_number": 5000,
            "no_references_setting": {"status": "ai_questioning", "value": "{question}"}})
    version = {field.attname: getattr(app, field.attname) for field in ApplicationVersion._meta.concrete_fields
               if hasattr(app, field.attname) and field.attname not in ("id", "application_id", "create_time", "update_time")}
    ApplicationVersion.objects.create(**{**version, "application": app, "application_name": app.name,
                                        "knowledge_ids": [str(knowledge.id)]})
    fixture = ResponseFixture()
    probe = UsageProbe()

    def measured_model(*args_, **kwargs):
        if not args.model_id:
            return fixture
        instance = get_model_instance_by_model_workspace_id(*args_, **kwargs)
        handlers = instance.callbacks if isinstance(instance.callbacks, list) else []
        instance.callbacks = [handler for handler in handlers if handler is not probe] + [probe]
        if hasattr(instance, "stream_usage"):
            instance.stream_usage = True
        return instance

    def call_count():
        return probe.calls if args.model_id else fixture.calls

    CONFIG.update(EMBEDDING_CACHE_ENABLED=False, QUERY_EMBEDDING_CACHE_ENABLED=False)
    outputs = []
    try:
        with patch("application.chat_pipeline.step.chat_step.impl.base_chat_step.get_model_instance_by_model_workspace_id",
                   side_effect=measured_model):
            for stage in ["off", "on-first", "on-second"]:
                CONFIG["QA_L1_CACHE_ENABLED"] = stage != "off"
                calls_before = call_count()
                rows = []
                for index, question in enumerate(questions):
                    fields = {"workspace_id": app.workspace_id, "application_id": str(app.id), "debug": False,
                              "chat_user_id": "l1-offline-fixture", "chat_user_type": ChatUserType.APPLICATION_API_KEY.value,
                              "ip_address": "127.0.0.1", "source": {}}
                    chat_id = OpenChatSerializers(data=fields).open()
                    stream = bool(args.model_id) or bool(index % 2)
                    usage_before = len(probe.usage)
                    start = time.perf_counter()
                    response = ChatSerializers(data={**fields, "chat_id": chat_id}).chat(
                        {"message": question["question"], "stream": stream, "re_chat": False})
                    first_content_ms = None
                    if response.streaming:
                        frames = []
                        for part in response.streaming_content:
                            frame = json.loads(part.decode().removeprefix("data: "))
                            if first_content_ms is None and frame.get("content"):
                                first_content_ms = (time.perf_counter() - start) * 1000
                            frames.append(frame)
                        content = "".join(frame.get("content", "") for frame in frames)
                        assert frames[-1]["is_end"]
                    else:
                        data = json.loads(response.content)
                        assert data["code"] == 200, data
                        content = data["data"]["content"]
                        first_content_ms = (time.perf_counter() - start) * 1000
                    elapsed = (time.perf_counter() - start) * 1000
                    response.close()
                    record = ChatRecord.objects.get(chat_id=chat_id)
                    hit = record.details.get("answer_cache", {}).get("hit", False)
                    usage = probe.usage[usage_before:]
                    rows.append({"id": question["id"], "stream": stream, "response_ms": elapsed,
                        "first_content_ms": first_content_ms, "answer": content,
                        "provider_usage": usage[-1] if usage else None,
                        "framework_input_tokens": record.message_tokens, "framework_output_tokens": record.answer_tokens,
                        "cache_hit": hit, "record_id": str(record.id), "evidence_ids": [str(row["id"]) for row in
                            record.details.get("search_step", {}).get("paragraph_list", [])]})
                    assert content and not content.startswith("Exception:"), content
                    if not args.model_id:
                        assert content == "离线缓存链路验收回答", content
                    assert rows[-1]["evidence_ids"]
                    print(stage, question["id"], f"response_ms={elapsed:.1f}", f"cache_hit={hit}", flush=True)
                timings = [row["response_ms"] for row in rows]
                first_times = [row["first_content_ms"] for row in rows if row["first_content_ms"] is not None]
                usage_complete = all(row["cache_hit"] or row["provider_usage"] is not None for row in rows)
                outputs.append({"stage": stage, "requests": len(rows), "model_calls": call_count() - calls_before,
                    "cache_hit_rate": sum(row["cache_hit"] for row in rows) / len(rows),
                    "p50_ms": float(np.percentile(timings, 50)), "p95_ms": float(np.percentile(timings, 95)),
                    "first_content_p50_ms": float(np.percentile(first_times, 50)),
                    "first_content_p95_ms": float(np.percentile(first_times, 95)),
                    "provider_usage_count": sum(row["provider_usage"] is not None for row in rows),
                    "provider_total_tokens": sum(row["provider_usage"]["total_tokens"] for row in rows
                                                 if row["provider_usage"]) if usage_complete else None,
                    "framework_total_tokens": sum(row["framework_input_tokens"] + row["framework_output_tokens"] for row in rows),
                    "queries": rows})
        assert [row["model_calls"] for row in outputs] == [len(questions), len(questions), 0], outputs
        assert outputs[-1]["cache_hit_rate"] == 1
        assert len({row["record_id"] for output in outputs for row in output["queries"]}) == 3 * len(questions)
        output_path = args.output or ROOT / ("eval/results/week5/l1-api.json" if args.model_id else "eval/results/week4/l1-protocol.json")
        write_result(output_path, {
            "schema_version": 1, "scope": "published ChatSerializers entrypoint with real local retrieval",
            "answer_quality_evaluated": False, "external_llm_latency_evaluated": bool(args.model_id),
            "model": {"id": str(model.id), "name": model.model_name, "provider": model.provider},
            "first_content_scope": "first nonempty visible response content",
            "results": outputs})
        print(json.dumps([{key: value for key, value in output.items() if key != "queries"}
                          for output in outputs], ensure_ascii=False, indent=2))
    finally:
        ChatRecord.objects.filter(chat__application=app).delete()
        Chat.objects.filter(application=app).delete()
        ApplicationVersion.objects.filter(application=app).delete()
        app.delete()
        if not args.model_id:
            model.delete()


if __name__ == "__main__":
    main()
