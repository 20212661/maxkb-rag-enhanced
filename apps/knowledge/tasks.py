# coding=utf-8
"""Celery autodiscovery entrypoint for MinerU tasks."""
from knowledge.task.mineru import build_document, run_parse_job  # noqa: F401
