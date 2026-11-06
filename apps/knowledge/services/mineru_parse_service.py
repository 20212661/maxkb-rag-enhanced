# coding=utf-8
"""
    @project: MaxKB
    @file: mineru_parse_service.py
    @desc: Transactional parse artifacts and optimistic state transitions.
"""
import base64
import copy
import json
from pathlib import PurePosixPath

from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OuterRef
from django.db.models.expressions import RawSQL
from django.utils import timezone
from knowledge.models import (
    BuildState, Document, File, FileSourceType, Knowledge, MinerUDocumentBuild,
    MinerUParseCache, MinerUParseJob, ParseState,
)


ACTIVE = (ParseState.SUBMITTED, ParseState.RUNNING)
TRANSITIONS = {
    "submitted": {"submitted", "running", "succeeded", "failed", "orphaned"},
    "running": {"running", "succeeded", "failed", "orphaned"},
    "failed": {"submitted"}, "succeeded": set(), "orphaned": set(),
}


class ParseServiceError(ValueError):
    pass


def _identity(sha256, tier, versions):
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise ParseServiceError("Invalid sha256")
    if tier not in {"basic", "flash"}:
        raise ParseServiceError("Invalid tier")
    if not versions.get("engine_version") or type(versions.get("protocol_version")) is not int:
        raise ParseServiceError("Invalid parse versions")
    return {"sha256": sha256, "tier": tier, "engine_version": versions["engine_version"],
            "protocol_version": versions["protocol_version"]}


def _valid_cache(cache):
    return cache and cache.file_ids and File.objects.filter(id__in=cache.file_ids).count() == len(cache.file_ids)


def find_cache(sha256, tier, versions):
    cache = MinerUParseCache.objects.filter(**_identity(sha256, tier, versions)).first()
    if not _valid_cache(cache):
        return None
    MinerUParseCache.objects.filter(pk=cache.pk).update(hit_count=F("hit_count") + 1, last_hit_at=timezone.now())
    cache.refresh_from_db()
    return cache


def load_cache(cache):
    if not _valid_cache(cache):
        raise ParseServiceError("Cache assets are missing")
    return json.loads(File.objects.get(pk=cache.file_ids[0]).get_bytes())


def cache_to_paragraphs(cache, knowledge_id, split_params):
    from common.utils.mineru_converter import blocks_to_paragraphs, content_list_to_blocks
    result = load_cache(cache)
    blocks = content_list_to_blocks(result["content_list"], page_count=result["page_count"])
    references = {}
    for path, asset in result["images"].items():
        image = _save_file(knowledge_id, path, File.objects.get(pk=asset["file_id"]).get_bytes(),
                           {"mineru_asset_path": path})
        references[path] = f"./oss/file/{image.id}"
    for block in blocks:
        if block["image_ref"]:
            block["image_ref"] = references[block["image_ref"]]
        for path, reference in references.items():
            for old, new in ((f'="{path}"', f'="{reference}"'), (f"='{path}'", f"='{reference}'"),
                             (f"]({path})", f"]({reference})"), (f"](<{path}>)", f"](<{reference}>)")):
                block["text"] = block["text"].replace(old, new)
    return blocks_to_paragraphs(blocks, limit=split_params.get("limit", 4096),
                                drop_types=split_params.get("drop_types", ("header", "footer", "page_number")))


def _save_file(knowledge_id, name, data, meta):
    file = File(file_name=name[-256:], source_type=FileSourceType.KNOWLEDGE, source_id=str(knowledge_id),
                meta={"debug": False, "knowledge_id": str(knowledge_id), **meta})
    file.save(data)
    return file


def publish_cache(*, sha256, tier, versions, knowledge_id, result):
    from common.utils.mineru_client import MinerUClient
    from common.utils.mineru_converter import content_list_to_blocks
    identity = _identity(sha256, tier, versions)
    MinerUClient._validate_result(result, result.get("request_id"))
    blocks = content_list_to_blocks(result["content_list"], page_count=result["page_count"])
    if not Knowledge.objects.filter(pk=knowledge_id).exists():
        raise ParseServiceError("Knowledge does not exist")
    existing = MinerUParseCache.objects.filter(**identity).first()
    if _valid_cache(existing):
        return existing, False
    try:
        with transaction.atomic():
            # Reserve the unique identity before creating PostgreSQL large objects.
            if existing:
                if not MinerUParseCache.objects.filter(pk=existing.pk, file_ids=existing.file_ids).delete()[0]:
                    raise ParseServiceError("Cache changed during publication, retry")
            cache = MinerUParseCache.objects.create(**identity)
            assets = {}
            for path, asset in result["images"].items():
                if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts or "\\" in path:
                    raise ParseServiceError("Invalid asset path")
                data = base64.b64decode(asset["data_base64"], validate=True)
                if not data or len(data) > 32 * 1024 * 1024 or not asset.get("mime_type", "").startswith("image/"):
                    raise ParseServiceError("Invalid image asset")
                file = _save_file(knowledge_id, path, data, {"mineru_asset_path": path})
                assets[path] = {"file_id": str(file.id), "mime_type": asset["mime_type"]}
            if any(block["image_ref"] and block["image_ref"] not in assets for block in blocks):
                raise ParseServiceError("Referenced image asset is missing")
            stored = {**result, "images": assets}
            data_file = _save_file(knowledge_id, "mineru-result.json", json.dumps(stored, ensure_ascii=False).encode(),
                                   {"mineru_cache_id": str(cache.id)})
            cache.file_ids = [data_file.id] + [asset["file_id"] for asset in assets.values()]
            cache.save(update_fields=["file_ids", "update_time"])
            cache.refresh_from_db()
            return cache, True
    except IntegrityError:
        existing = MinerUParseCache.objects.get(**identity)
        if not _valid_cache(existing):
            raise ParseServiceError("Concurrent cache publication did not produce valid assets")
        return existing, False


def validate_source(source_file_id, knowledge_id, sha256, document_id=None):
    source = File.objects.filter(pk=source_file_id).first()
    if source is None:
        raise ParseServiceError("Source file does not exist")
    owned = source.source_type == FileSourceType.KNOWLEDGE and str(source.source_id) == str(knowledge_id)
    linked = source.source_type == FileSourceType.DOCUMENT and str(source.source_id) == str(document_id)
    if not (owned or linked):
        raise ParseServiceError("Source file does not belong to this knowledge or document")
    if source.sha256_hash != sha256:
        raise ParseServiceError("Source hash does not match parse job")
    return source


def get_or_create_job(*, knowledge_id, source_file_id, sha256, tier, versions, route_decision):
    identity = _identity(sha256, tier, versions)
    validate_source(source_file_id, knowledge_id, sha256)
    if not Knowledge.objects.filter(pk=knowledge_id).exists():
        raise ParseServiceError("Knowledge does not exist")
    with transaction.atomic():
        job = MinerUParseJob.objects.filter(**identity, state__in=ACTIVE).first()
        created = False
        if not job:
            cache = MinerUParseCache.objects.filter(**identity).first()
            if _valid_cache(cache):
                job = MinerUParseJob.objects.filter(**identity, state=ParseState.SUCCEEDED).first()
            if not job:
                try:
                    with transaction.atomic():
                        job = MinerUParseJob.objects.create(**identity, knowledge_id=knowledge_id,
                                                           source_file_id=source_file_id, route_decision=route_decision)
                        created = True
                except IntegrityError:
                    job = MinerUParseJob.objects.get(**identity, state__in=ACTIVE)
        patch = json.dumps({"parse_job_id": str(job.id)})
        File.objects.filter(pk=source_file_id).update(meta=RawSQL(
            "jsonb_set(COALESCE(meta, '{}'::jsonb), '{mineru}', "
            "COALESCE(meta->'mineru', '{}'::jsonb) || %s::jsonb)", [patch]))
    return job, created


def dispatch_document(document_id):
    from knowledge.tasks import build_document
    build_document.delay(str(document_id))


def dispatch_pending(job_id):
    for document_id in MinerUDocumentBuild.objects.filter(parse_job_id=job_id, parse_status=BuildState.PENDING
                                                         ).values_list("document_id", flat=True):
        dispatch_document(document_id)


def _dispatch_if_finished(document_id):
    build = MinerUDocumentBuild.objects.filter(document_id=document_id, parse_status=BuildState.PENDING).first()
    if build and MinerUParseJob.objects.filter(pk=build.parse_job_id, state__in=["succeeded", "failed"]).exists():
        dispatch_document(document_id)


@transaction.atomic
def attach_document(document_id, source_file_id, knowledge_id):
    document = Document.objects.filter(pk=document_id, knowledge_id=knowledge_id).first()
    if document is None:
        raise ParseServiceError("Document does not belong to this knowledge")
    source = File.objects.filter(pk=source_file_id).first()
    if source is None:
        raise ParseServiceError("Source file does not exist")
    association = source.meta.get("mineru", {})
    job = MinerUParseJob.objects.filter(pk=association.get("parse_job_id")).first()
    if job is None or job.state == ParseState.ORPHANED:
        raise ParseServiceError("Source file has no usable parse job")
    validate_source(source_file_id, knowledge_id, job.sha256, document_id)
    if job.state in ACTIVE:
        updated = MinerUParseJob.objects.filter(pk=job.pk, state__in=ACTIVE).update(
            version=F("version") + 1, update_time=timezone.now())
        if not updated:
            job.refresh_from_db()
            if job.state == ParseState.ORPHANED:
                raise ParseServiceError("Parse job was orphaned during attachment")
    build, created = MinerUDocumentBuild.objects.get_or_create(document_id=document_id, defaults={
        "parse_job": job, "source_file": source, "split_params": copy.deepcopy(association.get("split_params", {})),
    })
    if not created and (build.parse_job_id != job.id or build.source_file_id != source.id):
        raise ParseServiceError("Document is already attached to another source or parse job")
    transaction.on_commit(lambda: _dispatch_if_finished(document_id))
    return build


def transition(job_id, from_states, to_state, expected_version, **fields):
    if not from_states or any(to_state not in TRANSITIONS.get(state, set()) for state in from_states):
        raise ParseServiceError("Illegal parse state transition")
    allowed = {"task_id", "attempts", "error", "started_at", "finished_at", "deadline_at"}
    if fields.keys() - allowed:
        raise ParseServiceError("Unsupported parse update field")
    if to_state == "submitted" and all(state == "failed" for state in from_states):
        fields = {"deadline_at": None, "started_at": None, "finished_at": None, "error": {}, **fields}
    return bool(MinerUParseJob.objects.filter(pk=job_id, state__in=from_states, version=expected_version).update(
        state=to_state, version=F("version") + 1, update_time=timezone.now(), **fields))


def transition_document(document_id, parse_job_id, from_statuses, to_status, expected_version, **fields):
    legal = {"pending": {"pending", "succeeded", "fallback", "failed"},
             "succeeded": {"pending"}, "fallback": {"pending"}, "failed": {"pending"}}
    if not from_statuses or any(to_status not in legal.get(state, set()) for state in from_statuses):
        raise ParseServiceError("Illegal document build transition")
    if fields.keys() - {"error", "embedding_pending", "split_params"}:
        raise ParseServiceError("Unsupported document build update field")
    return bool(MinerUDocumentBuild.objects.filter(document_id=document_id, parse_job_id=parse_job_id,
        parse_status__in=from_statuses, version=expected_version).update(
            parse_status=to_status, version=F("version") + 1, update_time=timezone.now(), **fields))


def mark_orphaned(older_than):
    references = MinerUDocumentBuild.objects.filter(parse_job_id=OuterRef("pk"))
    candidates = MinerUParseJob.objects.filter(state__in=ACTIVE, create_time__lt=older_than).annotate(
        referenced=Exists(references)).filter(referenced=False).values_list("pk", "version")
    count = 0
    for pk, version in candidates:
        count += MinerUParseJob.objects.filter(pk=pk, version=version, state__in=ACTIVE).filter(
            ~Exists(references)).update(state=ParseState.ORPHANED, version=F("version") + 1,
                                       finished_at=timezone.now(), update_time=timezone.now())
    return count
