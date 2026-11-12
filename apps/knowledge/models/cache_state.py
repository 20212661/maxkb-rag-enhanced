# coding=utf-8
"""
    @project: MaxKB
    @file: cache_state.py
    @desc: Vector-store mutation versions for exact answer cache invalidation.
"""
from django.db import models


class KnowledgeCacheState(models.Model):
    knowledge = models.OneToOneField("knowledge.Knowledge", primary_key=True,
                                    on_delete=models.CASCADE, db_constraint=False)
    cache_version = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "knowledge_cache_state"
