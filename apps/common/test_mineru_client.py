import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from pypdf import PdfWriter

from common.utils.mineru_client import (
    MinerUClient, MinerUError, MinerUInputError, MinerUProtocolError, MinerUTimeoutError,
)


class MinerUClientTests(unittest.TestCase):
    def test_poll_once_does_not_sleep_or_loop(self):
        self.session.request.return_value = self.response({"request_id": "req", "status": "running"})
        with patch("common.utils.mineru_client.time.sleep", side_effect=AssertionError("Unexpected wait")):
            receipt, result = self.client.poll_once("req", 1, timeout_seconds=40)
        self.assertEqual(receipt["status"], "running")
        self.assertIsNone(result)
        self.session.request.assert_called_once()

    def setUp(self):
        self.session = Mock()
        self.client = MinerUClient({}, self.session)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.image = Path(self.directory.name) / "sample.jpg"
        self.image.write_bytes(b"image fixture")

    @staticmethod
    def response(body, status=200):
        return Mock(status_code=status, json=Mock(return_value=body))

    @staticmethod
    def result():
        return {"schema_version": 1, "request_id": "req", "content_list": [
            {"type": "table", "page_idx": 0, "table_body": "<table></table>"},
        ], "markdown": "text", "images": {"images/sample.jpg": {"data_base64": "YWJj"}},
                "page_count": 1, "is_full_document": True}

    def test_pdf_page_count_and_dynamic_timeout(self):
        path = Path(self.directory.name) / "sample.pdf"
        writer = PdfWriter()
        for _ in range(10):
            writer.add_blank_page(width=100, height=100)
        writer.write(path)
        self.assertEqual(self.client.count_pages(path), 10)
        self.assertEqual(self.client.timeout_for_pages(10), 130)
        self.assertEqual(self.client.timeout_for_pages(self.client.count_pages(self.image)), 40)
        with self.assertRaises(MinerUInputError):
            self.client.count_pages(path, page_count=1)

    def test_pptx_count_and_flash_submission(self):
        path = Path(self.directory.name) / "slides.pptx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("ppt/slides/slide1.xml", "slide")
            archive.writestr("ppt/slides/slide2.xml", "slide")
            archive.writestr("ppt/notesSlides/notesSlide1.xml", "notes")
        self.session.request.return_value = self.response({"request_id": "req", "status": "pending"}, 202)
        task = self.client.submit(path, request_id="req")
        self.assertEqual(task["page_count"], 2)
        self.assertEqual(task["timeout_seconds"], 50)
        args, kwargs = self.session.request.call_args
        self.assertEqual(args, ("POST", "http://127.0.0.1:8004/v1/content-list/tasks"))
        self.assertEqual(kwargs["data"]["tier"], "flash")

    def test_legacy_ppt_requires_explicit_page_count(self):
        path = Path(self.directory.name) / "slides.ppt"
        path.write_bytes(b"legacy fixture")
        with self.assertRaises(MinerUInputError):
            self.client.count_pages(path)
        self.assertEqual(self.client.count_pages(path, page_count=12), 12)

    def test_malformed_pdf_fails_before_network(self):
        path = Path(self.directory.name) / "bad.pdf"
        path.write_bytes(b"invalid")
        with self.assertRaises(MinerUInputError):
            self.client.submit(path)
        self.session.request.assert_not_called()

    def test_http_200_failed_task_is_an_error(self):
        self.session.request.return_value = self.response({"request_id": "req", "status": "failed",
                                                           "error": {"code": "parse_failed"}})
        with self.assertRaises(MinerUError) as caught:
            self.client.wait("req", 1)
        self.assertEqual(caught.exception.code, "parse_failed")
        self.assertEqual(caught.exception.request_id, "req")

    def test_timeout_preserves_id_and_does_not_retry_post(self):
        self.session.request.side_effect = requests.Timeout()
        with self.assertRaises(MinerUTimeoutError) as caught:
            self.client.submit(self.image, request_id="recoverable")
        self.assertEqual(caught.exception.request_id, "recoverable")
        self.assertEqual(self.session.request.call_count, 1)

    def test_wait_resumes_without_post_and_preserves_assets(self):
        self.session.request.side_effect = [self.response({"request_id": "req", "status": "completed"}),
                                            self.response(self.result())]
        result = self.client.wait("req", 1)
        self.assertEqual(result["images"], self.result()["images"])
        self.assertEqual([call.args[0] for call in self.session.request.call_args_list], ["GET", "GET"])

    def test_pending_deadline_is_shared_across_polling(self):
        clock = [0]

        def sleep(seconds):
            clock[0] += seconds

        self.session.request.return_value = self.response({"request_id": "req", "status": "running"})
        with patch("common.utils.mineru_client.time.monotonic", side_effect=lambda: clock[0]), \
                patch("common.utils.mineru_client.time.sleep", side_effect=sleep):
            with self.assertRaises(MinerUTimeoutError):
                self.client.wait("req", 1, timeout_seconds=2)
        self.assertEqual(clock[0], 2)
        self.assertEqual(self.session.request.call_count, 2)

    def test_parse_budget_includes_submission(self):
        self.client.submit = Mock(return_value={"request_id": "req", "page_count": 1, "timeout_seconds": 40})
        self.client.wait = Mock(return_value=self.result())
        with patch("common.utils.mineru_client.time.monotonic", side_effect=[0, 30]):
            self.client.parse(self.image)
        self.client.wait.assert_called_once_with("req", 1, timeout_seconds=10)

    def test_mismatched_id_and_invalid_result_are_rejected(self):
        self.session.request.return_value = self.response({"request_id": "someone-else", "status": "completed"})
        with self.assertRaises(MinerUProtocolError):
            self.client.wait("req", 1)
        result = self.result()
        result["content_list"][0]["page_idx"] = -1
        with self.assertRaises(MinerUProtocolError):
            self.client._validate_result(result, "req")

    def test_invalid_timeout_and_ids_are_rejected(self):
        for value in (0, -1, float("nan")):
            with self.assertRaises(ValueError):
                MinerUClient({"MINERU_TIMEOUT_PER_PAGE_SECONDS": value})
        with self.assertRaises(MinerUInputError):
            self.client.wait("../invalid", 1)
        with self.assertRaises(MinerUInputError):
            self.client.submit(self.image, request_id="")

    def test_incomplete_document_is_rejected_and_blank_page_is_valid(self):
        result = self.result()
        with self.assertRaises(MinerUProtocolError):
            self.client._validate_result(result, "req", expected_pages=2)
        result["content_list"] = []
        self.client._validate_result(result, "req", expected_pages=1)

    def test_resumable_submission_state_is_reported_as_error(self):
        self.session.request.return_value = self.response({"request_id": "req", "status": "submission_unknown",
                                                           "error": {"code": "submission_unknown"}})
        with self.assertRaises(MinerUError) as caught:
            self.client.wait("req", 1)
        self.assertEqual(caught.exception.request_id, "req")


if __name__ == "__main__":
    unittest.main()
