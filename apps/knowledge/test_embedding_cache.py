# coding=utf-8
"""
    @project: MaxKB
    @file: test_embedding_cache.py
    @desc: Exact-input caches, instance snapshots and pgvector precision.
"""
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import numpy as np
from django.core.cache import cache
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings

from common.cache.embedding_cache import cached_documents, cached_query, text_hash
from common.cache.model_profile import bind_embedding_profile, digest, public_params
from knowledge.models import Document, Embedding, EmbeddingVectorCache, Knowledge, KnowledgeFolder, Paragraph
from knowledge.vector.pg_vector import PGVector
from maxkb.const import CONFIG
from models_provider.tools import get_model


class EmbeddingCacheTests(TestCase):
    def setUp(self):
        self.config = patch.dict(CONFIG, {"EMBEDDING_CACHE_ENABLED": True, "QUERY_EMBEDDING_CACHE_ENABLED": True,
                                        "EMBEDDING_CACHE_EPOCH": 1})
        self.config.start()
        self.addCleanup(self.config.stop)
        lease = patch("common.cache.embedding_cache.CacheLease", side_effect=lambda *a, **kw: nullcontext())
        lease.start()
        self.addCleanup(lease.stop)
        cache.clear()

    def model(self, params=None):
        model = Mock()
        model.embed_documents.side_effect = lambda texts: [[0.123456789, -0.987654321] for text in texts]
        model.embed_query.return_value = [0.123456789, -0.987654321]
        return bind_embedding_profile(model, "model", "default", "provider", "name", params or {})

    def test_batch_deduplicates_restores_order_and_partial_hits(self):
        model = self.model()
        model.embed_documents.side_effect = lambda texts: [[float(len(text)), 0.25] for text in texts]
        first = cached_documents(model, ["title body", "other", "title body"])
        self.assertEqual(first, [[10.0, 0.25], [5.0, 0.25], [10.0, 0.25]])
        self.assertEqual(len(model.embed_documents.call_args.args[0]), 2)
        cached_documents(model, ["other", "title body"])
        self.assertEqual(model.embed_documents.call_count, 1)
        cached_documents(model, ["third", "other"])
        model.embed_documents.assert_called_with(["third"])
        self.assertEqual(EmbeddingVectorCache.objects.count(), 3)

    def test_actual_input_changes_key_and_modes_are_separate(self):
        model = self.model()
        for text in ["body", "Heading body", "Head body"]:
            cached_documents(model, [text])
        cached_documents(model, ["body"], mode="embed_query")
        self.assertEqual(EmbeddingVectorCache.objects.count(), 4)
        self.assertTrue(EmbeddingVectorCache.objects.filter(text_sha256=text_hash("Heading body")).exists())

    def test_old_instance_keeps_creation_profile_after_config_update(self):
        record = SimpleNamespace(id="model", workspace_id="default", provider="provider", model_name="name",
                                 credential="unused", model_type="EMBEDDING",
                                 model_params_form=[{"field": "dimensions", "default_value": 2}])
        with patch("models_provider.tools.get_model_", side_effect=lambda *a, **kw: Mock()) as factory:
            old = get_model(record)
            old.embed_documents.return_value = [[0.1, 0.2]]
            old_hash = old._maxkb_embedding_profile.documents_hash
            record.model_params_form[0]["default_value"] = 3
            new = get_model(record)
            new.embed_documents.return_value = [[0.1, 0.2, 0.3]]
        self.assertEqual(factory.call_args_list[0].kwargs["dimensions"], 2)
        self.assertNotEqual(old_hash, new._maxkb_embedding_profile.documents_hash)
        cached_documents(old, ["old writer"])
        self.assertEqual(EmbeddingVectorCache.objects.get().model_profile_hash, old_hash)
        cached_documents(new, ["old writer"])
        self.assertEqual(EmbeddingVectorCache.objects.count(), 2)

    def test_epoch_credentials_canonical_params(self):
        first = self.model({"b": 2, "a": 1, "api_key": "one", "max_tokens": 10})
        same = self.model({"a": 1, "max_tokens": 10, "api_key": "two", "b": 2})
        self.assertEqual(first._maxkb_embedding_profile, same._maxkb_embedding_profile)
        self.assertIn("max_tokens", public_params({"max_tokens": 10}))
        with patch.dict(CONFIG, {"EMBEDDING_CACHE_EPOCH": 2}):
            newer = self.model({"b": 2, "a": 1, "max_tokens": 10})
        self.assertNotEqual(first._maxkb_embedding_profile.documents_hash,
                            newer._maxkb_embedding_profile.documents_hash)
        self.assertEqual(digest({"a": 1, "b": 2}), digest({"b": 2, "a": 1}))

    def test_query_ttl_and_instance_isolation(self):
        model = self.model()
        with patch("common.cache.embedding_cache.cache.set", wraps=cache.set) as setter:
            value = cached_query(model, "question")
            self.assertEqual(setter.call_args.kwargs["timeout"], 600)
        self.assertEqual(cached_query(model, "question"), value)
        model.embed_query.assert_called_once()
        different = self.model({"dimensions": 2})
        cached_query(different, "question")
        different.embed_query.assert_called_once()

    def test_failures_do_not_publish_and_redis_failure_computes(self):
        model = self.model()
        model.embed_documents.side_effect = None
        for invalid in [[], [[float("nan")]], [[1], [1, 2]]]:
            model.embed_documents.return_value = invalid
            with self.assertRaises(ValueError):
                cached_documents(model, ["a", "b"])
        self.assertFalse(EmbeddingVectorCache.objects.exists())
        with patch("common.cache.embedding_cache.cache.get", side_effect=ConnectionError), \
                patch("common.cache.embedding_cache.cache.set", side_effect=ConnectionError):
            self.assertEqual(len(cached_query(model, "fallback")), 2)

    def test_cached_and_direct_pgvector_elements_are_identical(self):
        folder = KnowledgeFolder.objects.create(id="cache-tests", name="cache")
        knowledge = Knowledge.objects.create(name="cache", desc="", folder=folder)
        document = Document.objects.create(name="document", knowledge=knowledge, char_length=0)
        paragraph = Paragraph.objects.create(document=document, knowledge=knowledge, content="body")
        row = {"document_id": document.id, "paragraph_id": paragraph.id, "knowledge_id": knowledge.id,
               "source_id": str(paragraph.id), "source_type": 1, "text": "Heading\nbody"}
        model = self.model()
        store = PGVector()
        with patch.dict(CONFIG, {"EMBEDDING_CACHE_ENABLED": False}):
            store._batch_save([row], model, lambda: False)
        direct = Embedding.objects.get().embedding
        Embedding.objects.all().delete()
        store._batch_save([row], model, lambda: False)
        Embedding.objects.all().delete()
        store._batch_save([row], model, lambda: False)
        hit = Embedding.objects.get()
        def as_array(value):
            return np.asarray(json.loads(value) if isinstance(value, str) else value, dtype=np.float32)
        np.testing.assert_array_equal(as_array(direct), as_array(hit.embedding))
        self.assertEqual(hit.meta["embedding_text"], "Heading body")
        self.assertEqual(EmbeddingVectorCache.objects.get().dimension, 2)
        self.assertEqual(len(EmbeddingVectorCache.objects.get().vector_data), 8)
        self.assertEqual(model.embed_documents.call_count, 2)

    def test_cache_disabled_never_reads_pg(self):
        model = self.model()
        with patch.dict(CONFIG, {"EMBEDDING_CACHE_ENABLED": False, "QUERY_EMBEDDING_CACHE_ENABLED": False}), \
                patch("common.cache.embedding_cache._read_documents") as read:
            cached_documents(model, ["same", "same"])
            cached_query(model, "same")
        read.assert_not_called()
        model.embed_documents.assert_called_once_with(["same", "same"])


@override_settings(CACHES=CONFIG.get_cache_setting())
class RedisEmbeddingCacheTests(TransactionTestCase):
    def test_concurrent_document_and_query_misses_compute_once(self):
        model = Mock()
        bind_embedding_profile(model, str(uuid4()), "cache-tests", "test", "test", {})

        def documents(texts):
            time.sleep(0.1)
            return [[0.1, 0.2] for text in texts]

        def query(text):
            time.sleep(0.1)
            return [0.1, 0.2]

        model.embed_documents.side_effect = documents
        model.embed_query.side_effect = query

        def worker(index):
            close_old_connections()
            try:
                return cached_documents(model, ["concurrent"]), cached_query(model, "concurrent")
            finally:
                close_old_connections()

        with patch.dict(CONFIG, {"EMBEDDING_CACHE_ENABLED": True, "QUERY_EMBEDDING_CACHE_ENABLED": True}), \
                ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(worker, range(4)))
        self.assertTrue(all(value == results[0] for value in results))
        self.assertEqual(model.embed_documents.call_count, 1)
        self.assertEqual(model.embed_query.call_count, 1)
        self.assertEqual(EmbeddingVectorCache.objects.count(), 1)

    def test_old_lock_owner_cannot_delete_successor_and_lease_renews(self):
        from common.cache.single_flight import CacheLease
        from django_redis import get_redis_connection
        key = "maxkb:test:lease:" + uuid4().hex
        with CacheLease(key, wait=0, ttl=0.3) as first:
            self.assertTrue(first.acquired)
            time.sleep(0.5)
            self.assertTrue(get_redis_connection().exists(key))
            first.stop.set()
            get_redis_connection().delete(key)
            with CacheLease(key, wait=0, ttl=1) as successor:
                self.assertTrue(successor.acquired)
                first.__exit__()
                self.assertTrue(get_redis_connection().exists(key))
