# coding=utf-8
"""
    @project: MaxKB
    @file: mineru_client.py
    @desc: HTTP client for the isolated MinerU Content List service.
"""
import math
import re
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import requests
from pypdf import PdfReader
from pypdf.errors import PyPdfError


class MinerUError(RuntimeError):
    def __init__(self, message, *, request_id=None, code=None, status_code=None):
        super().__init__(message)
        self.request_id = request_id
        self.code = code
        self.status_code = status_code


class MinerUTimeoutError(MinerUError):
    pass


class MinerUInputError(MinerUError):
    pass


class MinerUProtocolError(MinerUError):
    pass


class MinerUClient:
    """Submit quickly, then wait in a background worker or resume by request ID."""

    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    PENDING_STATES = {"prepared", "submitting", "waiting-file", "pending", "running"}

    def __init__(self, config=None, session=None):
        if config is None:
            from maxkb.const import CONFIG
            config = CONFIG
        self.base_url = config.get("MINERU_CONTENT_LIST_URL", "http://127.0.0.1:8004").rstrip("/")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("MINERU_CONTENT_LIST_URL must be an HTTP service base URL")
        if parsed.username or parsed.password:
            raise ValueError("MINERU_CONTENT_LIST_URL must not contain credentials")
        self.base_seconds = self._number(config, "MINERU_TIMEOUT_BASE_SECONDS", 30)
        self.per_page_seconds = self._number(config, "MINERU_TIMEOUT_PER_PAGE_SECONDS", 10)
        self.connect_seconds = self._number(config, "MINERU_CONNECT_TIMEOUT_SECONDS", 5)
        self.poll_seconds = self._number(config, "MINERU_POLL_INTERVAL_SECONDS", 1)
        self.session = session or requests.Session()
        self._owns_session = session is None

    @staticmethod
    def _number(config, key, default):
        value = float(config.get(key, default))
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be a positive finite number")
        return value

    @staticmethod
    def _valid_page_count(page_count):
        if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1:
            raise MinerUInputError("page_count must be a positive integer")
        return page_count

    @classmethod
    def count_pages(cls, filename, *, page_count=None):
        path = Path(filename)
        if not path.is_file():
            raise MinerUInputError("Input file does not exist")
        suffix = path.suffix.lower()
        try:
            if suffix == ".pdf":
                reader = PdfReader(path)
                if reader.is_encrypted and not reader.decrypt(""):
                    raise MinerUInputError("Password-protected PDFs require preprocessing")
                actual = len(reader.pages)
            elif suffix == ".pptx":
                with zipfile.ZipFile(path) as archive:
                    actual = sum(bool(re.fullmatch(r"ppt/slides/slide\d+\.xml", name)) for name in archive.namelist())
            elif suffix in cls.IMAGE_SUFFIXES:
                actual = 1
            elif suffix == ".ppt":
                return cls._valid_page_count(page_count)
            else:
                raise MinerUInputError("Supported inputs are PDF, PNG/JPG/WEBP/BMP, PPTX and PPT with page_count")
        except (OSError, ValueError, PyPdfError, zipfile.BadZipFile) as error:
            raise MinerUInputError("Unable to read input page count") from error
        cls._valid_page_count(actual)
        if page_count is not None and cls._valid_page_count(page_count) != actual:
            raise MinerUInputError("page_count differs from the actual input page count")
        return actual

    def timeout_for_pages(self, page_count):
        try:
            value = self.base_seconds + self.per_page_seconds * self._valid_page_count(page_count)
        except OverflowError as error:
            raise MinerUInputError("Computed page timeout is too large") from error
        if not math.isfinite(value):
            raise MinerUInputError("Computed page timeout is too large")
        return value

    @staticmethod
    def _request_id(request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
            raise MinerUInputError("request_id must contain 1-64 letters, digits, underscores or hyphens")
        return request_id

    @staticmethod
    def _remaining(deadline, request_id):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MinerUTimeoutError("MinerU page-based deadline exceeded. Resume using request_id.",
                                     request_id=request_id, code="deadline_exceeded")
        return remaining

    def _request(self, method, path, deadline, request_id, expected, **kwargs):
        remaining = self._remaining(deadline, request_id)
        try:
            response = self.session.request(method, self.base_url + path, allow_redirects=False,
                                            timeout=(min(self.connect_seconds, remaining), remaining), **kwargs)
        except requests.Timeout as error:
            raise MinerUTimeoutError("MinerU HTTP timeout. Resume using request_id.",
                                     request_id=request_id, code="http_timeout") from error
        except requests.RequestException as error:
            raise MinerUError("MinerU HTTP request failed. Query request_id before submitting again.",
                              request_id=request_id, code="connection_error") from error
        self._remaining(deadline, request_id)
        try:
            body = response.json()
        except ValueError as error:
            raise MinerUProtocolError("MinerU returned invalid JSON", request_id=request_id) from error
        if not isinstance(body, dict):
            raise MinerUProtocolError("MinerU returned a non-object JSON response", request_id=request_id)
        if response.status_code not in expected:
            detail = body.get("error") or {}
            code = detail.get("code", f"http_{response.status_code}") if isinstance(detail, dict) else "http_error"
            raise MinerUError(f"MinerU returned HTTP {response.status_code} ({code})",
                              request_id=request_id, code=code, status_code=response.status_code)
        if body.get("request_id") != request_id:
            raise MinerUProtocolError("MinerU response request_id does not match", request_id=request_id)
        return body

    def submit(self, filename, *, request_id=None, page_count=None, tier="basic"):
        path = Path(filename)
        pages = self.count_pages(path, page_count=page_count)
        with path.open("rb") as source:
            return self.submit_stream(path.name, source, request_id=request_id, page_count=pages, tier=tier,
                                      timeout_seconds=self.timeout_for_pages(pages))

    def submit_stream(self, filename, source, *, request_id=None, page_count=None, tier="basic", timeout_seconds=5):
        """Submit an existing upload stream with a short HTTP budget and a stable request identity."""
        request_id = self._request_id(uuid.uuid4().hex if request_id is None else request_id)
        if tier not in {"flash", "basic", "standard", "advanced"}:
            raise MinerUInputError("Unsupported MinerU tier")
        if Path(filename).suffix.lower() in {".ppt", ".pptx"}:
            tier = "flash"
        deadline = time.monotonic() + timeout_seconds
        receipt = self._request("POST", "/v1/content-list/tasks", deadline, request_id, {202},
                                files={"file": (filename, source, "application/octet-stream")},
                                data={"request_id": request_id, "tier": tier, "ocr_mode": "auto"})
        if receipt.get("status") not in self.PENDING_STATES | {"completed", "failed", "canceled", "submission_unknown"}:
            raise MinerUProtocolError("MinerU returned an unknown task state", request_id=request_id)
        return {**receipt, "page_count": page_count,
                "timeout_seconds": self.timeout_for_pages(page_count) if page_count else None}

    def poll_once(self, request_id, page_count, *, timeout_seconds):
        """Read one upstream status, downloading its result only when it has completed."""
        request_id = self._request_id(request_id)
        deadline = time.monotonic() + timeout_seconds
        path = "/v1/content-list/tasks/" + request_id
        receipt = self._request("GET", path, deadline, request_id, {200})
        state = receipt.get("status")
        if state == "completed":
            result = self._request("GET", path + "/result", deadline, request_id, {200})
            self._validate_result(result, request_id, expected_pages=page_count)
            return receipt, result
        if state in {"failed", "canceled", "submission_unknown"}:
            raise MinerUError(f"MinerU task {state}", request_id=request_id, code=state)
        if state not in self.PENDING_STATES:
            raise MinerUProtocolError("MinerU returned an unknown task state", request_id=request_id)
        return receipt, None

    def wait(self, request_id, page_count, *, timeout_seconds=None):
        request_id = self._request_id(request_id)
        budget = self.timeout_for_pages(page_count)
        if timeout_seconds is not None:
            if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
                raise MinerUTimeoutError("MinerU page-based deadline exceeded", request_id=request_id,
                                         code="deadline_exceeded")
            budget = min(budget, timeout_seconds)
        deadline = time.monotonic() + budget
        path = "/v1/content-list/tasks/" + request_id
        while True:
            status = self._request("GET", path, deadline, request_id, {200})
            state = status.get("status")
            if state == "completed":
                result = self._request("GET", path + "/result", deadline, request_id, {200})
                self._validate_result(result, request_id, expected_pages=page_count)
                return result
            if state in {"failed", "canceled", "submission_unknown"}:
                detail = status.get("error") or {}
                code = detail.get("code", state) if isinstance(detail, dict) else state
                raise MinerUError(f"MinerU task {state}", request_id=request_id, code=code)
            if state not in self.PENDING_STATES:
                raise MinerUProtocolError("MinerU returned an unknown task state", request_id=request_id)
            time.sleep(min(self.poll_seconds, self._remaining(deadline, request_id)))

    @staticmethod
    def _validate_result(result, request_id, expected_pages=None):
        blocks = result.get("content_list")
        if result.get("schema_version") != 1 or not isinstance(blocks, list):
            raise MinerUProtocolError("MinerU returned an invalid Content List result", request_id=request_id)
        pages = result.get("page_count")
        if isinstance(pages, bool) or not isinstance(pages, int) or pages < 1:
            raise MinerUProtocolError("MinerU returned an invalid page count", request_id=request_id)
        if result.get("is_full_document") is not True or (expected_pages is not None and pages != expected_pages):
            raise MinerUProtocolError("MinerU returned an incomplete document", request_id=request_id)
        for block in blocks:
            if not isinstance(block, dict) or not isinstance(block.get("type"), str) or not block["type"]:
                raise MinerUProtocolError("MinerU returned an invalid block type", request_id=request_id)
            page = block.get("page_idx")
            if isinstance(page, bool) or not isinstance(page, int) or not 0 <= page < pages:
                raise MinerUProtocolError("MinerU returned an invalid block page index", request_id=request_id)
        if not isinstance(result.get("markdown"), str) or not isinstance(result.get("images"), dict):
            raise MinerUProtocolError("MinerU result is missing Markdown or image assets", request_id=request_id)

    def parse(self, filename, *, request_id=None, page_count=None, tier="basic"):
        """Blocking convenience method for background workers. submit() returns without waiting for parsing."""
        started = time.monotonic()
        receipt = self.submit(filename, request_id=request_id, page_count=page_count, tier=tier)
        remaining = receipt["timeout_seconds"] - (time.monotonic() - started)
        return self.wait(receipt["request_id"], receipt["page_count"], timeout_seconds=remaining)

    def close(self):
        if self._owns_session:
            self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
