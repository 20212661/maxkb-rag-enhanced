import base64
import io
import json
import unittest
import zipfile
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from content_list_api import create_app


def result_zip(extra_name=None):
    middle = {
        "schema": "docvortex.middle", "schema_version": "2.0", "is_full_document": True,
        "metadata": {"file_suffix": "pdf", "producer": {"name": "mineru", "version": "4.1.7"}},
        "extensions": {"mineru": {"tier": "basic", "parse_mode": "ocr"}},
        "pages": [{"page_idx": 0, "blocks": [
            {"type": "text", "index": 0, "bbox": [0.1, 0.1, 0.8, 0.2],
             "content": [{"type": "text", "content": "hello"}]},
        ]}],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("middle_json.json", json.dumps(middle))
        archive.writestr("markdown.md", "hello")
        archive.writestr("images/sample.jpg", b"image bytes")
        if extra_name:
            archive.writestr(extra_name, b"unsafe")
    return buffer.getvalue()


class ContentListAPITests(unittest.TestCase):
    def setUp(self):
        self.state = "pending"
        self.result = result_zip()
        self.calls = []

        def handler(request):
            self.calls.append(request)
            if request.url.path in {"/v1/health", "/openapi.json"}:
                return httpx.Response(200, json={"status": "ok"})
            if request.url.path.endswith("/result"):
                return httpx.Response(200, content=self.result, headers={"Content-Type": "application/zip"})
            if request.method == "POST":
                return httpx.Response(202, json={"request_id": "req", "task_id": "task", "status": self.state})
            return httpx.Response(200, json={"request_id": "req", "task_id": "task", "status": self.state})

        self.transport = httpx.MockTransport(handler)

    def test_submission_uses_zero_wait_and_returns_receipt(self):
        with TestClient(create_app(self.transport)) as client:
            response = client.post("/v1/content-list/tasks", files={"file": ("sample.pdf", b"pdf")},
                                   data={"request_id": "req", "tier": "basic"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["task_id"], "task")
        self.assertEqual(len(self.calls), 1)
        self.assertIn(b'name="wait_timeout"\r\n\r\n0', self.calls[0].content)
        self.assertEqual(self.calls[0].url.port, 8003)

    def test_completed_resubmission_does_not_download_zip(self):
        class UnreadZip(httpx.AsyncByteStream):
            async def __aiter__(self):
                raise AssertionError("Completed submission must not download its ZIP")
                yield b""

        def handler(request):
            if request.method == "POST":
                return httpx.Response(200, stream=UnreadZip())
            return httpx.Response(200, json={"request_id": "req", "task_id": "same-task", "status": "completed"})

        with TestClient(create_app(httpx.MockTransport(handler))) as client:
            response = client.post("/v1/content-list/tasks", files={"file": ("sample.pdf", b"pdf")},
                                   data={"request_id": "req"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["task_id"], "same-task")

    def test_health_checks_both_separately_configured_upstreams(self):
        with patch.dict("os.environ", {"MINERU_TASK_API_URL": "http://localhost:9000",
                                      "MINERU_FILE_PARSE_URL": "http://localhost:9003"}):
            with TestClient(create_app(self.transport)) as client:
                self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual({call.url.port for call in self.calls}, {9000, 9003})

    def test_result_not_ready_does_not_download(self):
        with TestClient(create_app(self.transport)) as client:
            response = client.get("/v1/content-list/tasks/req/result")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.calls), 1)

    def test_conversion_returns_content_list_and_image_bytes(self):
        self.state = "completed"
        with TestClient(create_app(self.transport)) as client:
            response = client.get("/v1/content-list/tasks/req/result")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["content_list"][0]["text"], "hello")
        self.assertEqual(body["content_list"][0]["page_idx"], 0)
        self.assertEqual(base64.b64decode(body["images"]["images/sample.jpg"]["data_base64"]), b"image bytes")
        self.assertEqual(body["parse_info"]["tier"], "basic")

    def test_unsafe_archive_is_rejected(self):
        self.state = "completed"
        self.result = result_zip("../escape")
        with TestClient(create_app(self.transport)) as client:
            response = client.get("/v1/content-list/tasks/req/result")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "invalid_result")

    def test_archive_size_limit_is_enforced(self):
        self.state = "completed"
        with patch.dict("os.environ", {"MINERU_MAX_RESULT_BYTES": "1"}):
            with TestClient(create_app(self.transport)) as client:
                response = client.get("/v1/content-list/tasks/req/result")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "result_too_large")

    def test_failed_status_is_preserved(self):
        self.state = "failed"
        with TestClient(create_app(self.transport)) as client:
            response = client.get("/v1/content-list/tasks/req")
        self.assertEqual(response.json()["status"], "failed")

    def test_invalid_id_is_rejected_before_upstream(self):
        with TestClient(create_app(self.transport)) as client:
            response = client.get("/v1/content-list/tasks/bad.id")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
