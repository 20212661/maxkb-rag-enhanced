# coding=utf-8
"""
    @project: MaxKB
    @file: embedding_cache.py
    @desc: Exact-input numeric embedding cache.
"""
import uuid_utils.compat as uuid
from django.db import models


class EmbeddingVectorCache(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    workspace_id = models.CharField(max_length=64)
    embedding_model_id = models.CharField(max_length=64)
    model_profile_hash = models.CharField(max_length=64)
    text_sha256 = models.CharField(max_length=64)
    vector_data = models.BinaryField()
    dimension = models.PositiveIntegerField()
    codec_version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "embedding_vector_cache"
        constraints = [models.UniqueConstraint(
            fields=["workspace_id", "embedding_model_id", "model_profile_hash", "text_sha256"],
            name="embedding_cache_identity")]
