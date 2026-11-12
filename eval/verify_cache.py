#!/usr/bin/env python3
"""Rebuild frozen Week 3 D inputs and check cache-off retrieval through Django views."""
import argparse
import hashlib
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
    parser.add_argument("--phase", required=True, choices=["vector", "l1", "l2"])
    parser.add_argument("--config", type=Path, default=ROOT / "eval/.local-week3-D.json")
    parser.add_argument("--reference", type=Path, default=ROOT / "eval/results/week3/D.json")
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()
    import django
    django.setup()
    from django.test import Client
    from common.cache.model_profile import get_profile
    from knowledge.models import Document, Embedding, EmbeddingVectorCache, Knowledge, Paragraph
    from knowledge.serializers.document import DocumentSerializers
    from knowledge.task.embedding import embedding_by_knowledge
    from maxkb.const import CONFIG
    from models_provider.tools import get_model_instance_by_model_workspace_id
    from ops import celery_app
    import run_eval

    CONFIG.update(EMBEDDING_CHUNK_TITLE_PREFIX_ENABLED=True, TABLE_CHUNK_ENABLED=True,
                  EMBEDDING_CHUNK_TITLE_PREFIX_MAX_CHARS=100, EMBEDDING_CACHE_ENABLED=False,
                  QUERY_EMBEDDING_CACHE_ENABLED=False, QA_L1_CACHE_ENABLED=False)
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    local = json.loads(args.config.read_text("utf-8"))
    reference = json.loads(args.reference.read_text("utf-8"))
    source = Knowledge.objects.get(pk=local["knowledge_id"])
    frozen = [{"name": doc["name"], "paragraphs": [dict(row, problem_list=[]) for row in
        Paragraph.objects.filter(document_id=doc["id"]).order_by("position").values("title", "content")]}
        for doc in Document.objects.filter(knowledge=source).order_by("name").values("id", "name")]
    clone = Knowledge.objects.create(name=f"Week4-{args.phase}-cache-regression", desc="Frozen Week3 D rebuild",
        folder_id=source.folder_id, user_id=source.user_id, workspace_id=source.workspace_id,
        embedding_model_id=source.embedding_model_id, meta={"mineru_route": "auto"})
    output = ROOT / "eval/results/week4"
    output.mkdir(parents=True, exist_ok=True)
    benchmark = {}
    model = get_model_instance_by_model_workspace_id(source.embedding_model_id, source.workspace_id)
    profile = get_profile(model)
    counts = {"requests": 0, "texts": 0, "model_seconds": 0, "input_texts": 0,
              "batch_unique_texts": 0, "vectorization_seconds": 0}
    unique_texts = set()
    original = type(model).embed_documents
    from knowledge.vector import pg_vector
    cached_documents = pg_vector.cached_documents

    def rebuild():
        result = embedding_by_knowledge.apply(args=(str(clone.id), source.embedding_model_id), throw=True)
        assert result.successful()
        assert Embedding.objects.filter(knowledge=clone, source_type="1").count() == reference["embedding_count"]

    def measured_cache(instance, texts, mode="embed_documents"):
        counts["input_texts"] += len(texts)
        counts["batch_unique_texts"] += len(set(texts))
        unique_texts.update(texts)
        start = time.perf_counter()
        try:
            return cached_documents(instance, texts, mode)
        finally:
            counts["vectorization_seconds"] += time.perf_counter() - start

    def measured(instance, texts):
        counts["requests"] += 1
        counts["texts"] += len(texts)
        start = time.perf_counter()
        try:
            return original(instance, texts)
        finally:
            counts["model_seconds"] += time.perf_counter() - start

    DocumentSerializers.Batch(data={"knowledge_id": str(clone.id), "workspace_id": clone.workspace_id,
        "user_id": str(source.user_id)}).batch_save(frozen)
    if args.benchmark:
        CONFIG["EMBEDDING_CACHE_ENABLED"] = True
        identity = {"workspace_id": profile.workspace_id, "embedding_model_id": profile.model_id,
                    "model_profile_hash": profile.documents_hash}
        EmbeddingVectorCache.objects.filter(**identity).delete()
        snapshots = []
        with patch.object(type(model), "embed_documents", measured), \
                patch.object(pg_vector, "cached_documents", measured_cache):
            for stage in ["cold", "warm"]:
                for key in counts:
                    counts[key] = 0
                unique_texts.clear()
                start = time.perf_counter()
                rebuild()
                total_seconds = time.perf_counter() - start
                benchmark[stage] = {**counts, "total_seconds": total_seconds,
                    "global_unique_texts": len(unique_texts),
                    "unique_cache_hit_rate": 1 - counts["texts"] / counts["batch_unique_texts"],
                    "cache_rows": EmbeddingVectorCache.objects.filter(**identity).count()}
                snapshots.append(sorted((row.meta["embedding_text"], str(row.embedding))
                    for row in Embedding.objects.filter(knowledge=clone, source_type="1")))
        assert snapshots[0] == snapshots[1], "Cold/warm stored vectors differ"
        assert counts["requests"] == 0 and len(snapshots[1]) > 0, counts
        benchmark.update(pgvector_equal=True, model_runtime=type(model).__module__, model_name=model.model_name,
            vectorization_scope="cache lookup, model computation, serialization and cache publication",
            model_time_scope="wrapped embed_documents calls, including local_model HTTP transport",
            rebuild_entrypoint="knowledge.task.embedding.embedding_by_knowledge.apply, eager Celery for both runs")
    # Regression itself uses freshly written direct-compute vectors, with all caches disabled.
    CONFIG.update(EMBEDDING_CACHE_ENABLED=False, QUERY_EMBEDDING_CACHE_ENABLED=False, QA_L1_CACHE_ENABLED=False)
    if args.benchmark:
        rebuild()
    clone_config = {**local, "knowledge_id": str(clone.id), "documents": {
        doc.name: {**local["documents"][doc.name], "id": str(doc.id)}
        for doc in Document.objects.filter(knowledge=clone)}}
    config_path = ROOT / f"eval/.local-week4-{args.phase}.json"
    run_eval.write_result(config_path, clone_config)

    class DjangoAPI(run_eval.MaxKBAPI):
        client = Client(HTTP_HOST="localhost")

        def request(self, method, path, payload=None, *, body=None, content_type=None):
            response = self.client.generic(method, "/admin/api" + path,
                data=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else body or b"",
                content_type=content_type or "application/json",
                HTTP_AUTHORIZATION="Bearer " + self.token if self.token else "")
            value = response.json()
            if response.status_code != 200 or value.get("code") != 200:
                raise run_eval.APIError(f"{method} {path} failed HTTP={response.status_code} code={value.get('code')}")
            return value.get("data")

    password = os.environ.get("MAXKB_DEFAULT_PASSWORD", "MaxKB@123..")
    expected = {row["id"]: row for row in reference["queries"]}
    keys = ["recall_at_1", "recall_at_5", "reciprocal_rank", "all_gold_in_top5", "matched_gold_indices_by_rank", "status"]
    stages = ["off", "on-first", "on-second"] if args.benchmark else ["off"]
    query_calls = {"requests": 0, "model_seconds": 0, "cache_path_seconds": 0}
    original_query = type(model).embed_query
    original_cached_query = pg_vector.cached_query

    def measured_query(instance, text):
        query_calls["requests"] += 1
        start = time.perf_counter()
        try:
            return original_query(instance, text)
        finally:
            query_calls["model_seconds"] += time.perf_counter() - start

    def measured_query_cache(instance, text):
        start = time.perf_counter()
        try:
            return original_cached_query(instance, text)
        finally:
            query_calls["cache_path_seconds"] += time.perf_counter() - start

    # Evict only this experiment's query texts, preserving unrelated Redis data.
    from django.core.cache import cache
    from common.cache.embedding_cache import text_hash
    from knowledge.vector.base_vector import normalize_for_embedding
    query_texts = [row["question"] for row in reference["queries"]] + ["信息系统安全管理评测预热"]
    cache.delete_many([f"{profile.workspace_id}:cache:v1:qemb:{profile.model_id}:"
                       f"{profile.query_hash}:{text_hash(normalize_for_embedding(text))}" for text in query_texts])
    for stage in stages:
        for key in query_calls:
            query_calls[key] = 0
        CONFIG.update(EMBEDDING_CACHE_ENABLED=stage != "off", QUERY_EMBEDDING_CACHE_ENABLED=stage != "off")
        result_path = output / f"{args.phase}-cache-{stage}.json"
        with patch.object(run_eval, "MaxKBAPI", DjangoAPI), patch.dict(os.environ, {"MAXKB_PASSWORD": password}), \
                patch.object(sys, "argv", ["run_eval.py", "--config", str(config_path), "--output", str(result_path)]), \
                patch.object(type(model), "embed_query", measured_query), \
                patch.object(pg_vector, "cached_query", measured_query_cache):
            assert run_eval.main() == 0
        result = json.loads(result_path.read_text("utf-8"))
        differences = [{"id": row["id"], "field": key, "expected": expected[row["id"]].get(key),
                        "actual": row.get(key)} for row in result["queries"] for key in keys
                       if row.get(key) != expected[row["id"]].get(key)]
        result["configuration"]["latency_scope"] = "Django test client through authenticated HTTP views, no network transport"
        result["cache_acceptance"] = {"reference_sha256": hashlib.sha256(args.reference.read_bytes()).hexdigest(),
            "all_caches_disabled": stage == "off", "fresh_rebuild": True, "differences": differences,
            "benchmark": benchmark, "query_embedding": dict(query_calls), "query_count_includes_one_warmup": True}
        run_eval.write_result(result_path, result)
        print(json.dumps(result["cache_acceptance"], ensure_ascii=False, indent=2), flush=True)
        assert not differences, differences
    if args.benchmark:
        assert query_calls["requests"] == 0, query_calls


if __name__ == "__main__":
    main()
