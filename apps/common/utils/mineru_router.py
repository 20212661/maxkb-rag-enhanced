# coding=utf-8
"""
    @project: MaxKB
    @file: mineru_router.py
    @desc: Knowledge upload routing and bounded PDF text-layer sampling.
"""
import logging
import math
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, BinaryIO, Literal, TypedDict

from pypdf import PdfReader

logger = logging.getLogger(__name__)
RouteReason = Literal[
    "image_ext", "ppt", "pptx", "low_text_avg", "low_text_ratio", "native_default",
    "forced", "forced_unsupported", "probe_failed", "zip_native_only",
]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
FLASH_EXTENSIONS = {".ppt", ".pptx", ".docx", ".xlsx", ".xls"}
DEFAULTS = {
    "MINERU_SCAN_AVG_CHAR_THRESHOLD": 100,
    "MINERU_SCAN_LOW_PAGE_CHAR_THRESHOLD": 50,
    "MINERU_SCAN_LOW_PAGE_RATIO_THRESHOLD": 0.30,
    "MINERU_PDF_PROBE_TIMEOUT_SECONDS": 5,
}


class ProbeMetrics(TypedDict):
    avg_chars_per_page: float | None
    low_text_page_ratio: float | None
    page_count: int | None
    probed_pages: list[int]


class RouteDecision(TypedDict):
    use_mineru: bool
    tier: Literal["basic", "flash"] | None
    reason: RouteReason
    metrics: ProbeMetrics


class PDFProbeError(ValueError):
    def __init__(self, message, metrics=None):
        super().__init__(message)
        self.metrics = metrics or _metrics(None, [], [], 50)


def _config(config):
    if config is None:
        from maxkb.const import CONFIG
        return CONFIG
    return config


def _settings(config):
    settings = {}
    for key, default in DEFAULTS.items():
        raw = config.get(key, default)
        value = float(raw)
        if isinstance(raw, bool) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid {key}")
        settings[key] = value
    if settings["MINERU_SCAN_LOW_PAGE_RATIO_THRESHOLD"] > 1:
        raise ValueError("MINERU_SCAN_LOW_PAGE_RATIO_THRESHOLD must be at most 1")
    if settings["MINERU_PDF_PROBE_TIMEOUT_SECONDS"] <= 0:
        raise ValueError("MINERU_PDF_PROBE_TIMEOUT_SECONDS must be positive")
    return settings


def _sample_pages(page_count: int) -> list[int]:
    if page_count <= 30:
        return list(range(page_count))
    return list(range(10)) + [10 + index * (page_count - 11) // 19 for index in range(20)]


def _metrics(page_count, pages, counts, low_threshold) -> ProbeMetrics:
    return {
        "avg_chars_per_page": sum(counts) / len(counts) if counts else None,
        "low_text_page_ratio": sum(count < low_threshold for count in counts) / len(counts) if counts else None,
        "page_count": page_count,
        "probed_pages": list(pages),
    }


def _probe(file, config, *, extract_text):
    page_count = None
    pages, counts = [], []
    settings = DEFAULTS
    started = time.monotonic()
    try:
        settings = _settings(_config(config))
        budget = settings["MINERU_PDF_PROBE_TIMEOUT_SECONDS"]
        position = file.tell()
        try:
            file.seek(0)
            reader = PdfReader(file)
            if reader.is_encrypted:
                raise ValueError("Encrypted PDF")
            page_count = len(reader.pages)
            if not page_count:
                raise ValueError("PDF has no pages")
            if time.monotonic() - started > budget:
                raise ValueError("PDF probe budget exhausted before the first page")
            if extract_text:
                for index in _sample_pages(page_count):
                    if time.monotonic() - started > budget:
                        break
                    text = (reader.pages[index].extract_text() or "").replace("\0", "")
                    counts.append(sum(not char.isspace() for char in text))
                    pages.append(index)
                    if time.monotonic() - started > budget:
                        break
                if not pages:
                    raise ValueError("PDF probe budget exhausted before the first page")
        finally:
            file.seek(position)
    except Exception as error:
        metrics = _metrics(page_count, pages, counts, settings["MINERU_SCAN_LOW_PAGE_CHAR_THRESHOLD"])
        raise PDFProbeError(str(error), metrics) from error
    metrics = _metrics(page_count, pages, counts, settings["MINERU_SCAN_LOW_PAGE_CHAR_THRESHOLD"])
    logger.info("MinerU PDF probe %s", metrics, extra={"mineru_probe": {
        **metrics, "chars_per_page": counts, "thresholds": dict(settings),
        "elapsed_seconds": time.monotonic() - started,
        "sampled": extract_text and len(pages) < page_count,
    }})
    return metrics


def probe_pdf(
    file: BinaryIO, *, config: Mapping[str, Any] | None = None,
) -> ProbeMetrics:
    """Probe up to 30 pages using a per-page soft time budget and restore the stream position."""
    return _probe(file, config, extract_text=True)


def decide(
    file: BinaryIO,
    knowledge_meta: Mapping[str, Any] | None,
    *,
    config: Mapping[str, Any] | None = None,
) -> RouteDecision:
    mode = (knowledge_meta or {}).get("mineru_route", "auto")
    suffix = Path(file.name).suffix.lower()
    metrics = _metrics(None, [], [], 50)
    reason: RouteReason = "native_default"
    tier = None
    if suffix == ".zip":
        reason = "zip_native_only"
    elif mode == "force_native":
        reason = "forced"
    elif mode == "force_mineru" and suffix not in IMAGE_EXTENSIONS | FLASH_EXTENSIONS | {".pdf"}:
        reason = "forced_unsupported"
    elif suffix == ".pdf":
        try:
            effective_config = _config(config)
            if mode == "force_mineru":
                metrics = _probe(file, effective_config, extract_text=False)
                reason, tier = "forced", "basic"
            else:
                metrics = probe_pdf(file, config=effective_config)
                settings = _settings(effective_config)
                low_average = metrics["avg_chars_per_page"] < settings["MINERU_SCAN_AVG_CHAR_THRESHOLD"]
                low_ratio = metrics["low_text_page_ratio"] >= settings["MINERU_SCAN_LOW_PAGE_RATIO_THRESHOLD"]
                logger.info("MinerU PDF conditions average=%s ratio=%s", low_average, low_ratio)
                if low_average:
                    reason, tier = "low_text_avg", "basic"
                elif low_ratio:
                    reason, tier = "low_text_ratio", "basic"
        except (PDFProbeError, ValueError, TypeError, OverflowError) as error:
            metrics = getattr(error, "metrics", metrics)
            reason = "probe_failed"
            logger.warning("MinerU PDF probe failed for %s: %s", file.name, error, exc_info=True)
    elif suffix in IMAGE_EXTENSIONS:
        reason, tier = "image_ext", "basic"
    elif suffix in {".ppt", ".pptx"}:
        reason, tier = suffix[1:], "flash"
    elif mode == "force_mineru" and suffix in FLASH_EXTENSIONS:
        reason, tier = "forced", "flash"
    if mode == "force_mineru" and tier:
        reason = "forced"
    decision: RouteDecision = {"use_mineru": tier is not None, "tier": tier, "reason": reason, "metrics": metrics}
    logger.info("MinerU route %s %s", file.name, decision, extra={"mineru_route": decision})
    return decision
