# coding=utf-8
"""
    @project: MaxKB
    @file: test_mineru_parse_service.py
    @desc: PostgreSQL integration checks for parse identity and optimistic concurrency.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.db import connections
from django.test import TransactionTestCase
from django.utils import timezone
from knowledge.models import Document, File, FileSourceType, Knowledge, KnowledgeFolder, MinerUDocumentBuild
from knowledge.services import mineru_parse_service as service


VERSIONS = {"engine_version": "test-engine", "protocol_version": 1}


class ParseServiceTests(TransactionTestCase):
    def setUp(self):
        folder = KnowledgeFolder.objects.create(id="mineru-tests", name="tests")
        self.knowledge = Knowledge.objects.create(name="first", desc="", folder=folder)
        self.other = Knowledge.objects.create(name="second", desc="", folder=folder)
        self.source = File(file_name="source.pdf", source_type=FileSourceType.KNOWLEDGE,
                           source_id=str(self.knowledge.id), meta={"mineru": {"split_params": {"limit": 4096}}})
        self.source.save(b"test source")
        self.job, _ = self.new_job()

    def new_job(self, source=None):
        source = source or self.source
        return service.get_or_create_job(knowledge_id=self.knowledge.id, source_file_id=source.id,
            sha256=source.sha256_hash, tier="basic", versions=VERSIONS, route_decision={"metrics": {"page_count": 1}})

    def document(self):
        return Document.objects.create(name="document", knowledge=self.knowledge, char_length=0,
                                       meta={"source_file_id": str(self.source.id)})

    def test_active_reuse_and_different_document_params(self):
        repeated, created = self.new_job()
        self.assertFalse(created)
        self.assertEqual(repeated.id, self.job.id)
        first, second = self.document(), self.document()
        a = service.attach_document(first.id, self.source.id, self.knowledge.id)
        self.source.refresh_from_db()
        self.source.meta["mineru"]["split_params"] = {"limit": 100}
        File.objects.filter(pk=self.source.pk).update(meta=self.source.meta)
        b = service.attach_document(second.id, self.source.id, self.knowledge.id)
        self.assertEqual(a.parse_job_id, b.parse_job_id)
        self.assertEqual((a.split_params["limit"], b.split_params["limit"]), (4096, 100))

    def test_states_and_late_result(self):
        self.assertTrue(service.transition(self.job.id, ["submitted"], "running", 0))
        self.assertFalse(service.transition(self.job.id, ["submitted"], "failed", 0))
        self.assertTrue(service.transition(self.job.id, ["running"], "failed", 1, deadline_at=timezone.now()))
        self.assertTrue(service.transition(self.job.id, ["failed"], "submitted", 2))
        self.job.refresh_from_db()
        self.assertIsNone(self.job.deadline_at)
        self.assertFalse(service.transition(self.job.id, ["running"], "succeeded", 1))
        with self.assertRaises(service.ParseServiceError):
            service.transition(self.job.id, ["failed"], "succeeded", 3)

    def test_concurrent_transition_only_one_wins(self):
        barrier = Barrier(2)

        def update():
            connections.close_all()
            try:
                barrier.wait(timeout=5)
                return service.transition(self.job.id, ["submitted"], "running", 0)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: update(), range(2))), [False, True])

    def test_ownership_and_missing_original(self):
        document = self.document()
        with self.assertRaises(service.ParseServiceError):
            service.attach_document(document.id, self.source.id, self.other.id)
        Document.objects.filter(pk=document.pk).update(knowledge=self.other)
        with self.assertRaises(service.ParseServiceError):
            service.attach_document(document.id, self.source.id, self.other.id)
        self.source.delete()
        with self.assertRaises(service.ParseServiceError):
            service.attach_document(document.id, self.source.id, self.other.id)

    def test_finished_before_save_dispatches_after_commit(self):
        service.transition(self.job.id, ["submitted"], "failed", 0)
        with patch.object(service, "dispatch_document") as dispatch:
            document = self.document()
            service.attach_document(document.id, self.source.id, self.knowledge.id)
            dispatch.assert_called_once_with(document.id)

    def test_migration_retains_build_and_conditional_updates(self):
        document = self.document()
        build = service.attach_document(document.id, self.source.id, self.knowledge.id)
        Document.objects.filter(pk=document.pk).update(knowledge=self.other, meta={})
        self.assertTrue(MinerUDocumentBuild.objects.filter(pk=build.id).exists())
        with self.assertRaises(service.ParseServiceError):
            service.validate_source(build.source_file_id, self.other.id, self.job.sha256, document.id)
        self.assertTrue(service.transition_document(document.id, self.job.id, ["pending"], "failed", 0,
                                                   error={"code": "source_ownership_changed"}))
        self.assertFalse(service.transition_document(document.id, self.job.id, ["pending"], "succeeded", 0))

    def test_orphans_exclude_any_document_reference(self):
        service.attach_document(self.document().id, self.source.id, self.knowledge.id)
        self.assertEqual(service.mark_orphaned(timezone.now() + timedelta(seconds=1)), 0)
        MinerUDocumentBuild.objects.all().delete()
        self.assertEqual(service.mark_orphaned(timezone.now() + timedelta(seconds=1)), 1)

    def test_cache_publication_idempotent_and_hits(self):
        result = {"schema_version": 1, "request_id": "test", "task_id": "task", "content_list": [
            {"type": "text", "text": "body", "page_idx": 0}], "images": {}, "markdown": "body",
            "page_count": 1, "is_full_document": True}
        args = dict(sha256=self.source.sha256_hash, tier="basic", versions=VERSIONS,
                    knowledge_id=self.knowledge.id, result=result)
        first, created = service.publish_cache(**args)
        second, duplicate = service.publish_cache(**args)
        self.assertTrue(created)
        self.assertFalse(duplicate)
        self.assertEqual(first.file_ids, second.file_ids)
        hit = service.find_cache(self.source.sha256_hash, "basic", VERSIONS)
        self.assertEqual(hit.hit_count, 1)
        self.assertEqual(service.load_cache(hit)["content_list"], result["content_list"])
