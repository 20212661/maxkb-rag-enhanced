# coding=utf-8
"""
    @project: MaxKB
    @file: test_mineru_tasks.py
    @desc: Task idempotence, shared builds and nonblocking poll integration checks.
"""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import MagicMock, patch

from celery.exceptions import Retry
from django.test import TransactionTestCase
from django.db import connections
from django.utils import timezone
from knowledge.models import Document, File, MinerUDocumentBuild, MinerUParseJob, Paragraph, ParagraphSource
from knowledge.services import mineru_parse_service as service
from knowledge.task import mineru as tasks
from knowledge import test_mineru_parse_service as fixtures


class MinerUTaskTests(TransactionTestCase):
    setUp = fixtures.ParseServiceTests.setUp
    new_job = fixtures.ParseServiceTests.new_job
    document = fixtures.ParseServiceTests.document

    def finish_parse(self):
        result = {"schema_version": 1, "request_id": str(self.job.request_id), "task_id": "task",
                  "content_list": [{"type": "text", "text": "section", "text_level": 1, "page_idx": 0},
                                   {"type": "text", "text": "A" * 500, "page_idx": 0}],
                  "markdown": "body", "images": {}, "page_count": 1, "is_full_document": True}
        service.publish_cache(sha256=self.job.sha256, tier="basic", versions=fixtures.VERSIONS,
                              knowledge_id=self.knowledge.id, result=result)
        self.job.refresh_from_db()
        service.transition(self.job.id, [self.job.state], "succeeded", self.job.version)

    def test_two_documents_finish_with_independent_limits_and_repeat_safely(self):
        first, second = self.document(), self.document()
        service.attach_document(first.id, self.source.id, self.knowledge.id)
        self.source.refresh_from_db()
        self.source.meta["mineru"]["split_params"] = {"limit": 100}
        File.objects.filter(pk=self.source.pk).update(meta=self.source.meta)
        service.attach_document(second.id, self.source.id, self.knowledge.id)
        self.finish_parse()
        with patch.object(tasks, "_dispatch_embedding"):
            tasks.build_document.run(str(first.id))
            tasks.build_document.run(str(second.id))
            ids = list(Paragraph.objects.filter(document=first).values_list("id", flat=True))
            tasks.build_document.run(str(first.id))
        self.assertEqual(MinerUDocumentBuild.objects.filter(parse_status="succeeded").count(), 2)
        self.assertEqual(Paragraph.objects.filter(document=first).count(), 1)
        self.assertEqual(Paragraph.objects.filter(document=second).count(), 5)
        self.assertEqual(list(Paragraph.objects.filter(document=first).values_list("id", flat=True)), ids)
        self.assertEqual(ParagraphSource.objects.count(), 6)
        self.assertTrue(all(paragraph.chunks for paragraph in Paragraph.objects.all()))

    def test_finished_parse_before_attach_can_build_immediately(self):
        self.finish_parse()
        with patch.object(service, "dispatch_document") as dispatch:
            document = self.document()
            service.attach_document(document.id, self.source.id, self.knowledge.id)
            dispatch.assert_called_once_with(document.id)
        with patch.object(tasks, "_dispatch_embedding"):
            tasks.build_document.run(str(document.id))
        self.assertEqual(MinerUDocumentBuild.objects.get(document=document).parse_status, "succeeded")

    def test_migrated_build_remains_and_marks_ownership_failure(self):
        document = self.document()
        service.attach_document(document.id, self.source.id, self.knowledge.id)
        self.finish_parse()
        Document.objects.filter(pk=document.id).update(knowledge=self.other, meta={})
        with patch.object(tasks, "_dispatch_embedding"):
            tasks.build_document.run(str(document.id))
        build = MinerUDocumentBuild.objects.get(document=document)
        self.assertEqual(build.parse_status, "failed")
        self.assertIn("belong", build.error["message"])
        self.assertEqual(Paragraph.objects.count(), 0)

    def test_failed_parse_falls_back_per_document(self):
        document = self.document()
        service.attach_document(document.id, self.source.id, self.knowledge.id)
        self.job.refresh_from_db()
        service.transition(self.job.id, [self.job.state], "failed", self.job.version, error={"code": "upstream_failed"})
        with patch.object(tasks, "_native_parse", return_value=[{"content": "native body", "title": "native"}]), \
                patch.object(tasks, "_dispatch_embedding"):
            tasks.build_document.run(str(document.id))
        self.assertEqual(MinerUDocumentBuild.objects.get(document=document).parse_status, "fallback")
        self.assertEqual(Paragraph.objects.get(document=document).content, "native body")

    def test_image_and_ppt_failures_keep_original(self):
        self.job.refresh_from_db()
        service.transition(self.job.id, [self.job.state], "failed", self.job.version)
        for name in ("scan.PNG", "slides.PPTX"):
            File.objects.filter(pk=self.source.pk).update(file_name=name)
            with patch.object(service, "dispatch_document"):
                document = self.document()
                service.attach_document(document.id, self.source.id, self.knowledge.id)
            with patch.object(tasks, "_dispatch_embedding"):
                tasks.build_document.run(str(document.id))
            build = MinerUDocumentBuild.objects.get(document=document)
            self.assertEqual(build.parse_status, "failed")
            self.assertIn("no_native_fallback", build.error["message"])
            self.assertTrue(File.objects.filter(pk=self.source.pk).exists())

    def test_poll_queries_once_then_retries_ten_seconds(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.timeout_for_pages.return_value = 40
        client.poll_once.return_value = ({"status": "running", "task_id": "task"}, None)
        with patch.object(tasks, "MinerUClient", return_value=client), \
                patch.object(tasks.run_parse_job, "retry", side_effect=Retry()) as retry:
            with self.assertRaises(Retry):
                tasks.run_parse_job.run(str(self.job.id))
        client.poll_once.assert_called_once()
        retry.assert_called_once_with(countdown=10)
        self.job.refresh_from_db()
        self.assertIsNotNone(self.job.deadline_at)
        self.assertEqual(self.job.state, "running")

    def test_concurrent_builds_commit_only_one_paragraph_set(self):
        document = self.document()
        service.attach_document(document.id, self.source.id, self.knowledge.id)
        self.finish_parse()
        barrier = Barrier(2)

        def build():
            connections.close_all()
            try:
                barrier.wait(timeout=5)
                tasks.build_document.run(str(document.id))
            finally:
                connections.close_all()

        with patch.object(tasks, "_dispatch_embedding"), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(build) for _ in range(2)]
            for future in futures:
                future.result(timeout=20)
        self.assertEqual(Paragraph.objects.filter(document=document).count(), 1)
        self.assertEqual(ParagraphSource.objects.filter(document=document).count(), 1)
        self.assertEqual(MinerUDocumentBuild.objects.get(document=document).version, 2)

    def test_native_pdf_fallback_uses_existing_parser(self):
        from common.test_mineru_router import pdf_file
        with patch.object(self.source, "get_bytes", return_value=pdf_file([200]).getvalue()):
            paragraphs = tasks._native_parse(self.source, {"limit": 4096}, lambda images: None)
        self.assertTrue(paragraphs)
        self.assertIn("x" * 100, paragraphs[0]["content"])

    def test_expired_budget_fails_and_dispatches_pending_builds(self):
        service.attach_document(self.document().id, self.source.id, self.knowledge.id)
        MinerUParseJob.objects.filter(pk=self.job.pk).update(deadline_at=timezone.now() - timedelta(seconds=1))
        with patch.object(tasks, "_enqueue_builds") as dispatch:
            tasks.run_parse_job.run(str(self.job.id))
        self.job.refresh_from_db()
        self.assertEqual(self.job.state, "failed")
        self.assertEqual(self.job.error["code"], "parse_timeout")
        dispatch.assert_called_once()

    def test_cache_hit_fans_out_and_finished_replay_is_safe(self):
        self.finish_parse()
        with patch.object(tasks, "_enqueue_builds") as dispatch:
            tasks.run_parse_job.run(str(self.job.id))
            tasks.run_parse_job.run(str(self.job.id))
        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(tasks.run_parse_job._get_exec_options()["queue"], "mineru")
        self.assertEqual(tasks.build_document._get_exec_options()["queue"], "mineru")

    def test_completed_poll_publishes_cache_and_builds_every_reference(self):
        documents = [self.document(), self.document()]
        for document in documents:
            service.attach_document(document.id, self.source.id, self.knowledge.id)
        result = {"schema_version": 1, "request_id": str(self.job.request_id), "task_id": "task",
                  "content_list": [{"type": "text", "text": "body", "page_idx": 0}],
                  "markdown": "body", "images": {}, "page_count": 1, "is_full_document": True}
        client = MagicMock()
        client.__enter__.return_value = client
        client.timeout_for_pages.return_value = 40
        client.poll_once.return_value = ({"status": "completed", "task_id": "task"}, result)
        with patch.object(tasks, "MinerUClient", return_value=client), \
                patch.object(service, "dispatch_document", side_effect=tasks.build_document.run), \
                patch.object(tasks, "_dispatch_embedding"):
            tasks.run_parse_job.run(str(self.job.id))
        self.job.refresh_from_db()
        self.assertEqual(self.job.state, "succeeded")
        self.assertEqual(MinerUDocumentBuild.objects.filter(parse_status="succeeded").count(), 2)
        client.poll_once.assert_called_once()
