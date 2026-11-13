# coding=utf-8
"""
    @project: MaxKB
    @file: prompt_sources.py
    @desc: Source-aware knowledge context formatting and character budgets.
"""
from common.cache.model_profile import enabled

DOCUMENT_NAME_MAX_CHARS = 64


def source_format_settings():
    return {"include_document_name": enabled("PROMPT_INCLUDE_DOCUMENT_NAME"),
            "document_name_max_chars": DOCUMENT_NAME_MAX_CHARS, "version": 1}


def source_prefix(document_name):
    name = (document_name or "").strip()[:DOCUMENT_NAME_MAX_CHARS]
    return f"【来源：{name}】" if name and enabled("PROMPT_INCLUDE_DOCUMENT_NAME") else ""


def join_sources(sources, max_chars, separator="\n", wrap_data=False):
    """Budget source labels and text. SIMPLE's data tags and framing stay outside its budget."""
    remaining = max(0, max_chars)
    result = []
    for prefix, body in sources:
        separator_chars = len(separator) if result and not wrap_data else 0
        available = remaining - separator_chars
        if available <= len(prefix):
            break
        text = (prefix + body)[:available]
        if not text:
            continue
        result.append(f"<data>{text}</data>" if wrap_data else text)
        remaining -= separator_chars + len(text)
        if remaining <= 0:
            break
    return separator.join(result)


def format_paragraphs(paragraphs, max_chars, legacy_format=None, wrap_data=False):
    sources = []
    for paragraph in paragraphs:
        title = paragraph.get("title") or ""
        content = paragraph.get("content") or ""
        prefix = source_prefix(paragraph.get("document_name"))
        body = f"{title}：{content}" if prefix else (
            legacy_format(paragraph) if legacy_format else f"{title}:{content}")
        sources.append((prefix, body))
    return join_sources(sources, max_chars, wrap_data=wrap_data)
