# coding=utf-8
"""
    @project: MaxKB
    @file: mineru.py
    @desc: One-shot parse polling and idempotent document materialization.
"""
import importlib
import io
import logging
import zipfile
from datetime import timedelta
from pathlib import Path

from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerError
from ops import celery_app
from pypdf import PdfReader

from common.chunk import text_to_chunk
from common.utils.mineru_client import MinerUClient, MinerUError, MinerUProtocolError, MinerUTimeoutError
from common.utils.mineru_converter import blocks_to_paragraphs, content_list_to_blocks
from knowledge.models import (
    Document, Embedding, File, FileSourceType, MinerUDocumentBuild, MinerUParseJob, Paragraph,
    ParagraphSource, ProblemParagraphMapping,
)
from knowledge.services import mineru_parse_service as service

logger = logging.getLogger(__name__)


def _versions(job):
    return {"engine_version": job.engine_version, "protocol_version": job.protocol_version}


def _enqueue_builds(job_id):
    service.dispatch_pending(job_id)


def _page_count(job):
    pages = job.route_decision.get("metrics", {}).get("page_count")
    if type(pages) is int and pages > 0:
        return pages
    source = File.objects.get(pk=job.source_file_id)
    suffix = Path(source.file_name).suffix.lower()
    if suffix in MinerUClient.IMAGE_SUFFIXES:
        return 1
    if suffix == ".pdf":
        return len(PdfReader(io.BytesIO(source.get_bytes())).pages)
    if suffix == ".pptx":
        import re
        with zipfile.ZipFile(io.BytesIO(source.get_bytes())) as archive:
            return sum(bool(re.fullmatch(r"ppt/slides/slide\d+\.xml", name)) for name in archive.namelist())
    raise service.ParseServiceError("Parse job requires an accurate positive page_count")


@celery_app.task(bind=True, queue="mineru", max_retries=None, acks_late=True, reject_on_worker_lost=True,
                 autoretry_for=(OperationalError, BrokerError), retry_backoff=True)
def run_parse_job(self, job_id):
    job = MinerUParseJob.objects.filter(pk=job_id).first()
    if job is None or job.state == "orphaned":
        return
    if job.state in {"succeeded", "failed"}:
        _enqueue_builds(job.id)
        return
    try:
        cache = service.find_cache(job.sha256, job.tier, _versions(job))
        if cache:
            if not service.transition(job.id, [job.state], "succeeded", job.version,
                                      finished_at=timezone.now(), error={}):
                raise self.retry(countdown=10)
            _enqueue_builds(job.id)
            return
        pages = _page_count(job)
        if type(pages) is not int or pages < 1:
            raise service.ParseServiceError("Parse job requires an accurate positive page_count")
        with MinerUClient() as client:
            deadline = job.deadline_at or job.create_time + timedelta(seconds=client.timeout_for_pages(pages))
            if job.deadline_at is None:
                if not service.transition(job.id, [job.state], "running", job.version, deadline_at=deadline,
                                          started_at=job.create_time, attempts=F("attempts") + 1):
                    raise self.retry(countdown=10)
                job.refresh_from_db()
            remaining = (deadline - timezone.now()).total_seconds()
            if remaining <= 0:
                raise MinerUTimeoutError("Total parse budget exceeded", code="parse_timeout")
            receipt, result = client.poll_once(str(job.request_id), pages, timeout_seconds=remaining)
        if result is None:
            service.transition(job.id, ["running"], "running", job.version, task_id=receipt.get("task_id"))
            raise self.retry(countdown=10)
        # A stale parser may publish reusable immutable artifacts but cannot overwrite a newer job state.
        service.publish_cache(sha256=job.sha256, tier=job.tier, versions=_versions(job),
                              knowledge_id=job.knowledge_id, result=result)
        if not service.transition(job.id, [job.state], "succeeded", job.version,
                                  task_id=receipt.get("task_id"), finished_at=timezone.now(), error={}):
            raise self.retry(countdown=10)
    except MinerUError as error:
        logger.warning("MinerU parse job %s failed: %s", job_id, error)
        if (not isinstance(error, MinerUProtocolError)
                and error.code in {"http_timeout", "connection_error", "upstream_unreachable"}
                and job.deadline_at and timezone.now() < job.deadline_at):
            raise self.retry(countdown=10, exc=error)
        if not service.transition(job.id, [job.state], "failed", job.version, finished_at=timezone.now(),
                                  error={"code": error.code or "protocol_error", "message": str(error)}):
            raise self.retry(countdown=10)
    except (service.ParseServiceError, ValueError, KeyError, TypeError) as error:
        logger.warning("MinerU parse job %s failed: %s", job_id, error, exc_info=True)
        if not service.transition(job.id, [job.state], "failed", job.version, finished_at=timezone.now(),
                                  error={"code": "parse_failed", "message": str(error)}):
            raise self.retry(countdown=10)
    _enqueue_builds(job.id)


def _native_parse(source, params, save_images):
    handlers = {".pdf": ("pdf", "Pdf"), ".docx": ("doc", "Doc"), ".xlsx": ("xlsx", "Xlsx"),
                ".xls": ("xls", "Xls"), ".csv": ("csv", "Csv")}
    suffix = Path(source.file_name).suffix.lower()
    if suffix not in handlers:
        raise service.ParseServiceError("no_native_fallback")
    module, name = handlers[suffix]
    handler = getattr(importlib.import_module(f"common.handle.impl.text.{module}_split_handle"), f"{name}SplitHandle")()
    data = source.get_bytes()
    file = io.BytesIO(data)
    file.name = source.file_name
    result = handler.handle(file, params.get("patterns", []), params.get("with_filter", True),
                            params.get("limit", 4096), lambda _: data, save_images)
    return result["content"]


@transaction.atomic
def _commit_build(build, *, fallback=False, reason=None):
    if not service.transition_document(build.document_id, build.parse_job_id, ["pending"], "pending", build.version):
        return False
    document = build.document
    # This conditional UPDATE also serializes against a concurrent knowledge migration.
    if not Document.objects.filter(pk=document.id, knowledge_id=document.knowledge_id).update(char_length=0):
        raise service.ParseServiceError("Document knowledge changed during build")
    source = service.validate_source(build.source_file_id, document.knowledge_id, build.parse_job.sha256, document.id)
    images = []
    if fallback:
        paragraphs = _native_parse(source, build.split_params, images.extend)
        for image in images:
            data = image.meta.pop("content")
            image.source_type = FileSourceType.KNOWLEDGE
            image.source_id = str(document.knowledge_id)
            image.meta["knowledge_id"] = str(document.knowledge_id)
            image.save(data)
    else:
        cache = service.find_cache(build.parse_job.sha256, build.parse_job.tier, _versions(build.parse_job))
        if cache is None:
            raise service.ParseServiceError("Completed parse cache is missing")
        result = service.load_cache(cache)
        blocks = content_list_to_blocks(result["content_list"], page_count=result["page_count"])
        references = {}
        for block in blocks:
            path = block["image_ref"]
            if path and path not in references:
                asset = result["images"][path]
                image = service._save_file(document.knowledge_id, path, File.objects.get(pk=asset["file_id"]).get_bytes(),
                                           {"mineru_asset_path": path})
                references[path] = f"./oss/file/{image.id}"
            if path:
                block["image_ref"] = references[path]
        paragraphs = blocks_to_paragraphs(
            blocks, limit=build.split_params.get("limit", 4096),
            drop_types=build.split_params.get("drop_types", ("header", "footer", "page_number")))
    if not paragraphs or not any(paragraph["content"].strip() for paragraph in paragraphs):
        raise service.ParseServiceError("Parser returned no usable paragraphs")
    Embedding.objects.filter(document_id=document.id).delete()
    ProblemParagraphMapping.objects.filter(document_id=document.id).delete()
    Paragraph.objects.filter(document_id=document.id).delete()
    for position, row in enumerate(paragraphs, start=1):
        paragraph = Paragraph.objects.create(
            document_id=document.id, knowledge_id=document.knowledge_id, title=row.get("title", "")[:256],
            content=row["content"], position=position, chunks=text_to_chunk(row["content"]))
        ParagraphSource.objects.create(paragraph=paragraph, document_id=document.id,
            page_indices=sorted(set(row.get("page_indices", []))),
            source_block_indices=sorted(set(row.get("source_block_indices", []))))
    Document.objects.filter(pk=document.id).update(
        char_length=sum(len(row["content"]) for row in paragraphs), update_time=timezone.now())
    service.transition_document(document.id, build.parse_job_id, ["pending"], "fallback" if fallback else "succeeded",
                                build.version + 1, error=reason or {}, embedding_pending=True)
    return True


def _dispatch_embedding(document_id):
    from celery_once import AlreadyQueued
    from knowledge.task.embedding import embedding_by_document
    build = MinerUDocumentBuild.objects.select_related("document__knowledge").filter(
        document_id=document_id, embedding_pending=True,
        parse_status__in=["succeeded", "fallback"]).first()
    if build:
        try:
            embedding_by_document.delay(str(document_id), str(build.document.knowledge.embedding_model_id))
        except AlreadyQueued:
            pass
        MinerUDocumentBuild.objects.filter(pk=build.pk, version=build.version).update(embedding_pending=False)


@celery_app.task(queue="mineru", acks_late=True, reject_on_worker_lost=True,
                 autoretry_for=(OperationalError, BrokerError), retry_backoff=True, max_retries=None)
def build_document(document_id):
    build = MinerUDocumentBuild.objects.select_related("document", "parse_job").filter(document_id=document_id).first()
    if build is None:
        return
    if build.parse_status != "pending":
        _dispatch_embedding(document_id)
        return
    if build.parse_job.state not in {"succeeded", "failed"}:
        return
    try:
        try:
            _commit_build(build, fallback=build.parse_job.state == "failed", reason=build.parse_job.error)
        except (OperationalError, BrokerError):
            raise
        except Exception as error:
            if build.parse_job.state == "failed":
                raise
            _commit_build(build, fallback=True, reason={"code": "conversion_error", "message": str(error)})
    except (OperationalError, BrokerError):
        raise
    except Exception as error:
        logger.warning("MinerU document build %s failed: %s", document_id, error, exc_info=True)
        service.transition_document(document_id, build.parse_job_id, ["pending"], "failed", build.version,
                                    error={"code": "build_failed", "message": str(error)})
    _dispatch_embedding(document_id)
