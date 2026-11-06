# coding=utf-8
"""
    @project: MaxKB
    @file: test_mineru_upload.py
    @desc: Upload and save entrypoint regression checks.
"""
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase
from common.test_mineru_router import pdf_file
from common.utils.mineru_client import MinerUError
from knowledge import test_mineru_parse_service as fixtures
from knowledge.models import Document, File, MinerUDocumentBuild, MinerUParseJob, Paragraph
from knowledge.serializers.document import DocumentSerializers
from knowledge.services import mineru_parse_service as service, mineru_upload as upload


class MinerUUploadTests(TransactionTestCase):
    setUp = fixtures.ParseServiceTests.setUp
    new_job = fixtures.ParseServiceTests.new_job
    document = fixtures.ParseServiceTests.document

    def splitter(self):
        splitter = DocumentSerializers.Split(data={"knowledge_id": str(self.knowledge.id)})
        splitter.is_valid(instance={"file": []})
        return splitter

    def preview(self):
        file = SimpleUploadedFile("scan.pdf", pdf_file([0]).getvalue(), content_type="application/pdf")
        return self.splitter().file_to_paragraph(file, None, True, 4096)[0]

    def pending_preview(self):
        with patch.object(upload, "submit_job", return_value={}), patch.object(upload, "_enqueue_parse"):
            return self.preview()

    def test_miss_returns_one_placeholder_with_server_binding(self):
        with patch.object(upload, "submit_job", return_value={}) as submit, \
                patch.object(upload, "_enqueue_parse") as enqueue:
            preview = self.preview()
        self.assertEqual(len(preview["content"]), 1)
        self.assertTrue(preview["content"][0]["mineru"]["pending"])
        source = File.objects.get(pk=preview["source_file_id"])
        self.assertEqual(source.meta["mineru"]["split_params"]["limit"], 4096)
        self.assertEqual(source.meta["mineru"]["parse_job_id"], preview["content"][0]["mineru"]["parse_job_id"])
        submit.assert_called_once()
        enqueue.assert_called_once()

    def test_capacity_and_uncertain_submission_return_placeholder(self):
        for code in ("parse_capacity", "http_timeout"):
            with self.subTest(code=code), patch.object(upload, "submit_job", side_effect=MinerUError("busy", code=code)), \
                    patch.object(upload, "_enqueue_parse"):
                preview = self.preview()
                self.assertTrue(preview["content"][0]["mineru"]["pending"])
                MinerUParseJob.objects.filter(pk=preview["content"][0]["mineru"]["parse_job_id"]).update(state="failed")

    def test_repeated_preview_reuses_parse_and_submits_only_once(self):
        with patch.object(upload, "submit_job", return_value={}) as submit, patch.object(upload, "_enqueue_parse"):
            first, second = self.preview(), self.preview()
        self.assertNotEqual(first["source_file_id"], second["source_file_id"])
        self.assertEqual(first["content"][0]["mineru"]["parse_job_id"],
                         second["content"][0]["mineru"]["parse_job_id"])
        submit.assert_called_once()

    def test_multipart_options_are_normalized_before_persisting(self):
        with patch.object(upload, "submit_job", return_value={}), patch.object(upload, "_enqueue_parse"):
            file = SimpleUploadedFile("scan.pdf", pdf_file([0]).getvalue())
            preview = self.splitter().file_to_paragraph(file, None, "false", "4096")[0]
        source = File.objects.get(pk=preview["source_file_id"])
        self.assertEqual(source.meta["mineru"]["split_params"]["limit"], 4096)
        self.assertIs(source.meta["mineru"]["split_params"]["with_filter"], False)

    def test_cache_hit_materializes_images_for_target_knowledge(self):
        preview = self.pending_preview()
        source = File.objects.get(pk=preview["source_file_id"])
        result = {"schema_version": 1, "request_id": "req", "task_id": "task", "content_list": [
            {"type": "text", "text": "real body", "page_idx": 0},
            {"type": "image", "img_path": "images/chart.png", "page_idx": 0}],
            "markdown": "real body", "images": {"images/chart.png": {
                "mime_type": "image/png", "data_base64": "aW1hZ2U="}}, "page_count": 1, "is_full_document": True}
        service.publish_cache(sha256=source.sha256_hash, tier="basic", versions=upload.versions(),
                              knowledge_id=self.knowledge.id, result=result)
        with patch.object(upload, "submit_job") as submit:
            cached = self.preview()
        self.assertIn("real body", cached["content"][0]["content"])
        self.assertIn("./oss/file/", cached["content"][0]["content"])
        self.assertNotIn("mineru", cached["content"][0])
        submit.assert_not_called()
        self.assertTrue(File.objects.filter(source_id=str(self.knowledge.id),
                                           meta__mineru_asset_path="images/chart.png").exists())

    def test_single_save_excludes_edited_placeholder_and_skips_embedding(self):
        preview = self.pending_preview()
        instance = {"name": "saved", "source_file_id": preview["source_file_id"],
                    "paragraphs": [{"title": "changed", "content": "user edited placeholder"}]}
        with patch.object(service, "dispatch_document") as dispatch, \
                patch.object(DocumentSerializers.Operate, "refresh") as refresh:
            result = DocumentSerializers.Create(data={"knowledge_id": str(self.knowledge.id)}).save(instance)
        document = Document.objects.get(name="saved")
        self.assertEqual(Paragraph.objects.filter(document=document).count(), 0)
        self.assertTrue(MinerUDocumentBuild.objects.filter(document=document, parse_status="pending").exists())
        refresh.assert_not_called()
        dispatch.assert_called_once()
        self.assertEqual(result["id"], document.id)

    def test_batch_save_splits_async_and_native_documents(self):
        preview = self.pending_preview()
        instances = [{"name": "async", "source_file_id": preview["source_file_id"], "paragraphs": preview["content"]},
                     {"name": "native", "paragraphs": [{"title": "title", "content": "ordinary body"}]}]
        with patch.object(service, "dispatch_document") as dispatch, \
                patch.object(DocumentSerializers.Operate, "refresh") as refresh:
            DocumentSerializers.Batch(data={"knowledge_id": str(self.knowledge.id), "workspace_id": "default"}
                                      ).batch_save(instances)
        self.assertEqual(Paragraph.objects.filter(document__name="async").count(), 0)
        self.assertEqual(Paragraph.objects.get(document__name="native").content, "ordinary body")
        refresh.assert_called_once()
        dispatch.assert_called_once()

    def test_native_upload_calls_original_handler_with_original_options(self):
        from knowledge.serializers import document as serializers
        file = SimpleUploadedFile("notes.TXT", b"ordinary text")
        with patch.object(serializers.default_split_handle, "handle",
                          wraps=serializers.default_split_handle.handle) as handle:
            actual = self.splitter().file_to_paragraph(file, [], False, 4096)[0]
        expected = serializers.default_split_handle.handle(SimpleUploadedFile("notes.TXT", b"ordinary text"),
            [], False, 4096, lambda _: b"ordinary text", self.splitter().save_image)
        self.assertEqual(actual["content"], expected["content"])
        self.assertEqual(actual["name"], expected["name"])
        handle.assert_called_once()

    def test_normal_single_save_keeps_immediate_embedding(self):
        with patch.object(DocumentSerializers.Operate, "refresh") as refresh:
            DocumentSerializers.Create(data={"knowledge_id": str(self.knowledge.id)}).save({
                "name": "ordinary", "paragraphs": [{"title": "title", "content": "body"}]})
        refresh.assert_called_once()
        self.assertEqual(Paragraph.objects.get(document__name="ordinary").content, "body")
