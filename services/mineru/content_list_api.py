# coding=utf-8
"""Thin async task facade. Only this service imports MinerU's result renderer."""

import argparse
import asyncio
import base64
import io
import json
import mimetypes
import os
import re
import zipfile
from contextlib import asynccontextmanager
from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool


class UpstreamError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def convert_archive(data, request_id, task_id, max_unpacked_bytes):
    from mineru.render import RenderFormat, render
    from mineru.types import MiddleJson

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if sum(item.file_size for item in entries) > max_unpacked_bytes:
            raise ValueError("Unpacked result exceeds the configured limit")
        names = [item.filename for item in entries]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive paths")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("Unsafe archive path")
        source = json.loads(archive.read("middle_json.json"))
        middle = MiddleJson.from_dict(source)
        images = {}
        for item in entries:
            if item.filename.startswith("images/") and not item.is_dir():
                images[item.filename] = {
                    "mime_type": mimetypes.guess_type(item.filename)[0] or "application/octet-stream",
                    "data_base64": base64.b64encode(archive.read(item)).decode("ascii"),
                }
        return {
            "schema_version": 1, "request_id": request_id, "task_id": task_id,
            "content_list": render(middle, RenderFormat.CONTENT_LIST),
            "markdown": archive.read("markdown.md").decode("utf-8"), "images": images,
            "page_count": len(middle.pages), "is_full_document": source.get("is_full_document"),
            "metadata": source.get("metadata", {}), "parse_info": source.get("extensions", {}).get("mineru", {}),
        }


def service_url(name, default):
    value = os.environ.get(name, default).rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError(f"{name} must be an HTTP service base URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{name} must not contain credentials")
    return value


def create_app(transport=None):
    task_url = service_url("MINERU_TASK_API_URL", "http://127.0.0.1:8000")
    file_url = service_url("MINERU_FILE_PARSE_URL", "http://127.0.0.1:8003")
    max_archive_bytes = int(os.environ.get("MINERU_MAX_RESULT_BYTES", 128 * 1024 * 1024))
    max_unpacked_bytes = int(os.environ.get("MINERU_MAX_UNPACKED_BYTES", 256 * 1024 * 1024))
    parse_concurrency = int(os.environ.get("MINERU_PARSE_CONCURRENCY", "1"))
    if parse_concurrency < 1:
        raise ValueError("MINERU_PARSE_CONCURRENCY must be positive")
    if min(max_archive_bytes, max_unpacked_bytes) <= 0:
        raise ValueError("Result size limits must be positive")

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5), transport=transport) as client:
            app.state.http = client
            app.state.conversion_slot = asyncio.Semaphore(1)
            app.state.parse_slot = asyncio.Semaphore(parse_concurrency)
            app.state.parse_monitors = {}
            try:
                yield
            finally:
                monitors = [task for task in app.state.parse_monitors.values() if task is not None]
                for task in monitors:
                    task.cancel()
                await asyncio.gather(*monitors, return_exceptions=True)

    app = FastAPI(title="MinerU Content List Tasks", version="1", lifespan=lifespan)

    @app.exception_handler(UpstreamError)
    async def upstream_error(_request, error):
        return JSONResponse(status_code=error.status,
                            content={"error": {"code": error.code, "message": error.message}})

    async def upstream(method, path, **kwargs):
        try:
            return await app.state.http.request(method, file_url + path, **kwargs)
        except httpx.HTTPError as error:
            raise UpstreamError(502, "upstream_unreachable", "Query the saved request_id before resubmitting") from error

    def payload(response):
        try:
            value = response.json()
        except ValueError as error:
            raise UpstreamError(502, "invalid_upstream_response", "Expected upstream JSON") from error
        if not isinstance(value, dict):
            raise UpstreamError(502, "invalid_upstream_response", "Expected an upstream object")
        if response.status_code not in {200, 202}:
            detail = value.get("error") or {}
            if not isinstance(detail, dict):
                detail = {}
            raise UpstreamError(response.status_code, detail.get("code", "upstream_error"),
                                detail.get("message", "Upstream request failed"))
        return value

    async def status(request_id):
        value = payload(await upstream("GET", "/v1/file_parse/" + request_id))
        if value.get("request_id") != request_id or not isinstance(value.get("status"), str):
            raise UpstreamError(502, "invalid_upstream_response", "Invalid upstream task status")
        return {key: value.get(key) for key in ("request_id", "task_id", "status", "error")}

    def release_parse(request_id):
        if request_id in app.state.parse_monitors:
            monitor = app.state.parse_monitors.pop(request_id)
            if monitor is not None and monitor is not asyncio.current_task():
                monitor.cancel()
            app.state.parse_slot.release()

    async def monitor_parse(request_id):
        while True:
            await asyncio.sleep(10)
            try:
                receipt = await status(request_id)
            except UpstreamError:
                # Unknown upstream state retains capacity until a definitive terminal receipt is available.
                continue
            if receipt["status"] in {"completed", "failed", "canceled"}:
                release_parse(request_id)
                return

    @app.get("/health")
    async def health():
        try:
            parser = await app.state.http.get(task_url + "/v1/health")
            wrapper = await app.state.http.get(file_url + "/openapi.json")
            ready = parser.status_code == wrapper.status_code == 200
        except httpx.HTTPError:
            ready = False
        return JSONResponse(status_code=200 if ready else 503,
                            content={"status": "ok" if ready else "unavailable", "schema_version": 1})

    @app.post("/v1/content-list/tasks", status_code=202)
    async def submit(
        file: Annotated[UploadFile, File()],
        request_id: Annotated[str, Form(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        tier: Annotated[str, Form(pattern=r"^(flash|basic|standard|advanced)$")] = "basic",
        ocr_mode: Annotated[str, Form(pattern=r"^(auto|ocr|txt)$")] = "auto",
    ):
        if request_id not in app.state.parse_monitors:
            if app.state.parse_slot.locked():
                raise UpstreamError(503, "parse_capacity", "Parser capacity is full; retry with the same request_id")
            await app.state.parse_slot.acquire()
            app.state.parse_monitors[request_id] = asyncio.create_task(monitor_parse(request_id))
        # wait_timeout=0 persists the upstream recovery record and returns without waiting for model inference.
        try:
            async with app.state.http.stream("POST", file_url + "/v1/file_parse", files={
                "file": (file.filename, file.file, file.content_type or "application/octet-stream"),
            }, data={"request_id": request_id, "tier": tier, "ocr_mode": ocr_mode,
                     "output_formats": "default", "wait_timeout": "0"}) as response:
                completed = response.status_code == 200
                if not completed:
                    await response.aread()
                    receipt = payload(response)
        except httpx.HTTPError as error:
            raise UpstreamError(502, "upstream_unreachable", "Query request_id before resubmitting") from error
        except UpstreamError as error:
            if 400 <= error.status < 500 and error.status != 408:
                release_parse(request_id)
            raise
        if completed:
            # A repeated completed request may return its ZIP. Query its original status instead of creating again.
            receipt = await status(request_id)
            release_parse(request_id)
            return receipt
        if receipt.get("request_id") != request_id:
            raise UpstreamError(502, "invalid_upstream_response", "Mismatched upstream request ID")
        if receipt.get("status") in {"completed", "failed", "canceled"}:
            release_parse(request_id)
        return {key: receipt.get(key) for key in ("request_id", "task_id", "status", "error")}

    @app.get("/v1/content-list/tasks/{request_id}")
    async def get_status(request_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
            raise UpstreamError(400, "invalid_request_id", "Invalid request ID")
        receipt = await status(request_id)
        if receipt["status"] in {"completed", "failed", "canceled"}:
            release_parse(request_id)
        return receipt

    @app.get("/v1/content-list/tasks/{request_id}/result")
    async def get_result(request_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
            raise UpstreamError(400, "invalid_request_id", "Invalid request ID")
        receipt = await status(request_id)
        if receipt["status"] != "completed":
            raise UpstreamError(409, "result_not_ready", "Task has not completed successfully")
        async with app.state.conversion_slot:
            try:
                async with app.state.http.stream("GET", file_url + "/v1/file_parse/" + request_id + "/result") as response:
                    if response.status_code != 200:
                        await response.aread()
                        payload(response)
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > max_archive_bytes:
                            raise UpstreamError(502, "result_too_large", "Result ZIP exceeds configured size limit")
            except httpx.HTTPError as error:
                raise UpstreamError(502, "upstream_unreachable", "Retry result download using request_id") from error
            try:
                return await run_in_threadpool(convert_archive, bytes(data), request_id,
                                               receipt["task_id"], max_unpacked_bytes)
            except (ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError) as error:
                raise UpstreamError(502, "invalid_result", "Unable to convert the upstream result ZIP") from error

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8004)
    arguments = parser.parse_args()
    uvicorn.run(create_app(), host=arguments.host, port=arguments.port)
