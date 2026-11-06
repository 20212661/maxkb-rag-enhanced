# coding=utf-8
"""
    @project: MaxKB
    @file: mineru_upload.py
    @desc: Short preview submission and upload/save orchestration.
"""
import copy
import io
import logging

from django.db import transaction
from maxkb.const import CONFIG
from common.utils.mineru_client import MinerUClient, MinerUError
from knowledge.models import File, MinerUDocumentBuild, MinerUParseJob
from knowledge.services import mineru_parse_service as service

logger = logging.getLogger(__name__)


def versions():
    return {"engine_version": CONFIG.get("MINERU_ENGINE_VERSION", "4.1.7"),
            "protocol_version": int(CONFIG.get("MINERU_PROTOCOL_VERSION", 1))}


def submit_job(job):
    source = File.objects.get(pk=job.source_file_id)
    with MinerUClient() as client:
        receipt = client.submit_stream(source.file_name, io.BytesIO(source.get_bytes()),
            request_id=str(job.request_id), page_count=job.route_decision.get("metrics", {}).get("page_count"),
            tier=job.tier, timeout_seconds=float(CONFIG.get("MINERU_PREVIEW_SUBMIT_TIMEOUT_SECONDS", 5)))
    service.transition(job.id, [job.state], job.state, job.version,
                       task_id=receipt.get("task_id"), attempts=job.attempts + 1, error={})
    return receipt


def _enqueue_parse(job_id):
    from knowledge.tasks import run_parse_job
    run_parse_job.delay(str(job_id))


def preview(source, knowledge_id, decision, split_params):
    cache = service.find_cache(source.sha256_hash, decision["tier"], versions())
    if cache:
        with transaction.atomic():
            paragraphs = service.cache_to_paragraphs(cache, knowledge_id, split_params)
        return [{"name": source.file_name, "source_file_id": source.id, "content": paragraphs}]
    source.meta = {**source.meta, "mineru": {"preview_kind": "pending", "split_params": copy.deepcopy(split_params)}}
    File.objects.filter(pk=source.pk).update(meta=source.meta)
    job, created = service.get_or_create_job(knowledge_id=knowledge_id, source_file_id=source.id,
        sha256=source.sha256_hash, tier=decision["tier"], versions=versions(), route_decision=decision)
    if created:
        try:
            submit_job(job)
        except MinerUError as error:
            logger.warning("MinerU preview submission deferred for %s: %s", job.id, error)
            # The worker queries the stable request ID before retrying any uncertain submission.
            service.transition(job.id, [job.state], job.state, job.version,
                error={"code": error.code or "submit_failed", "message": str(error)})
    transaction.on_commit(lambda: _enqueue_parse(job.id))
    job.refresh_from_db()
    return [{"name": source.file_name, "source_file_id": source.id, "content": [{
        "title": "解析中", "content": "文档已提交解析，保存后在文档列表查看处理状态。", "problem_list": [],
        "mineru": {"pending": True, "parse_job_id": str(job.id),
                   "request_id": str(job.request_id), "task_id": job.task_id},
    }]}]


def pending_source(source_file_id, knowledge_id):
    if not source_file_id:
        return None
    source = File.objects.filter(pk=source_file_id).first()
    if source is None or source.meta.get("mineru", {}).get("preview_kind") != "pending":
        return None
    job = MinerUParseJob.objects.filter(pk=source.meta["mineru"].get("parse_job_id")).first()
    if job is None:
        raise service.ParseServiceError("Pending upload has no parse job")
    service.validate_source(source.id, knowledge_id, job.sha256)
    return source


def prepare_save(instance, knowledge_id):
    if pending_source(instance.get("source_file_id"), knowledge_id):
        return {**instance, "paragraphs": []}
    return instance


def attach_saved(document):
    source_id = document.meta.get("source_file_id")
    if pending_source(source_id, document.knowledge_id):
        service.attach_document(document.id, source_id, document.knowledge_id)

        def enqueue_if_active():
            if MinerUDocumentBuild.objects.filter(document_id=document.id,
                    parse_job__state__in=service.ACTIVE).exists():
                service.dispatch_document(document.id)

        transaction.on_commit(enqueue_if_active)


def is_async_document(document_id):
    return MinerUDocumentBuild.objects.filter(document_id=document_id).exists()
