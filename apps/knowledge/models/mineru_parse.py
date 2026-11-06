# coding=utf-8
"""
    @project: MaxKB
    @file: mineru_parse.py
    @desc: Shared parse jobs, artifacts and independent document builds.
"""
import uuid_utils.compat as uuid
from django.contrib.postgres.fields import ArrayField
from django.db import models
from common.mixins.app_model_mixin import AppModelMixin


class ParseState(models.TextChoices):
    SUBMITTED = "submitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ORPHANED = "orphaned"


class BuildState(models.TextChoices):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FALLBACK = "fallback"
    FAILED = "failed"


class MinerUParseCache(AppModelMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    sha256 = models.CharField(max_length=64)
    tier = models.CharField(max_length=16)
    engine_version = models.CharField(max_length=128)
    protocol_version = models.PositiveIntegerField()
    file_ids = ArrayField(models.UUIDField(), default=list)
    hit_count = models.PositiveBigIntegerField(default=0)
    last_hit_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "mineru_parse_cache"
        constraints = [models.UniqueConstraint(fields=["sha256", "tier", "engine_version", "protocol_version"],
                                                name="mineru_cache_identity")]


class MinerUParseJob(AppModelMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    knowledge = models.ForeignKey("knowledge.Knowledge", on_delete=models.DO_NOTHING, db_constraint=False)
    source_file = models.ForeignKey("knowledge.File", on_delete=models.DO_NOTHING, db_constraint=False)
    sha256 = models.CharField(max_length=64)
    tier = models.CharField(max_length=16)
    engine_version = models.CharField(max_length=128)
    protocol_version = models.PositiveIntegerField()
    state = models.CharField(max_length=16, choices=ParseState.choices, default=ParseState.SUBMITTED)
    route_decision = models.JSONField(default=dict)
    request_id = models.CharField(max_length=64, unique=True, default=uuid.uuid4)
    task_id = models.CharField(max_length=128, null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    error = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "mineru_parse_job"
        constraints = [models.UniqueConstraint(fields=["sha256", "tier", "engine_version", "protocol_version"],
                       condition=models.Q(state__in=["submitted", "running"]), name="mineru_active_identity")]


class MinerUDocumentBuild(AppModelMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    document = models.OneToOneField("knowledge.Document", on_delete=models.CASCADE, db_constraint=False)
    parse_job = models.ForeignKey(MinerUParseJob, on_delete=models.DO_NOTHING, db_constraint=False)
    source_file = models.ForeignKey("knowledge.File", on_delete=models.DO_NOTHING, db_constraint=False)
    split_params = models.JSONField(default=dict)
    parse_status = models.CharField(max_length=16, choices=BuildState.choices, default=BuildState.PENDING)
    version = models.PositiveIntegerField(default=0)
    error = models.JSONField(default=dict)
    embedding_pending = models.BooleanField(default=False)

    class Meta:
        db_table = "mineru_document_build"
        indexes = [models.Index(fields=["parse_job", "parse_status"], name="mineru_build_dispatch")]


class ParagraphSource(AppModelMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    paragraph = models.OneToOneField("knowledge.Paragraph", on_delete=models.CASCADE, db_constraint=False)
    document = models.ForeignKey("knowledge.Document", on_delete=models.CASCADE, db_constraint=False)
    page_indices = ArrayField(models.PositiveIntegerField(), default=list)
    source_block_indices = ArrayField(models.PositiveIntegerField(), default=list)

    class Meta:
        db_table = "paragraph_source"
