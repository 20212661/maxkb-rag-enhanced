# coding=utf-8
"""
    @project: MaxKB
    @file: mineru_converter.py
    @desc: Pure Content List normalization and default section-based paragraph splitting.
"""
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict


MAX_TITLE_LENGTH = 256
MAX_CONTENT_LENGTH = 102400
DEFAULT_DROP_TYPES = ("header", "footer", "page_number")


class MinerUBlock(TypedDict):
    type: str
    text: str
    level: int | None
    page: int
    bbox: list[float] | None
    image_ref: str | None
    table_header: str | None
    source_index: int


class Paragraph(TypedDict):
    title: str
    content: str
    problem_list: list[str]
    page_indices: list[int]
    source_block_indices: list[int]


class MinerUConversionError(ValueError):
    """Invalid input or an indivisible block exceeding the paragraph storage limit."""


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise MinerUConversionError("Content List text fields must be strings")
    return value.replace("\0", "")


def _lines(value: Any) -> str:
    if isinstance(value, str):
        return _text(value)
    if not isinstance(value, list):
        raise MinerUConversionError("Content List captions and list items must be strings or lists of strings")
    return "\n\n".join(_text(item) for item in value)


def _table_header(body: str) -> str | None:
    head = re.search(r"<thead\b[^>]*>.*?</thead\s*>", body, re.IGNORECASE | re.DOTALL)
    if head:
        return head.group()
    rows = re.findall(r"<tr\b[^>]*>.*?</tr\s*>", body, re.IGNORECASE | re.DOTALL)
    headers = [row for row in rows if re.search(r"<th\b", row, re.IGNORECASE)]
    if headers:
        return "\n".join(headers)
    lines = body.splitlines(keepends=True)
    for index in range(1, len(lines)):
        separator = lines[index].strip().strip("|")
        cells = separator.split("|")
        if "|" in lines[index - 1] and all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in cells):
            return lines[index - 1] + lines[index]
    return None


def _block_text(item: Mapping[str, Any], kind: str) -> tuple[str, str | None]:
    if kind == "table":
        body = _text(item.get("table_body"))
        text = "\n\n".join(part for part in (
            _lines(item.get("table_caption", [])), body, _lines(item.get("table_footnote", [])),
        ) if part)
        return text, _table_header(body)
    if kind == "image":
        return "\n\n".join(part for part in (
            _lines(item.get("image_caption", [])), _lines(item.get("image_footnote", [])),
        ) if part), None
    if kind in {"list", "index"} or "list_items" in item:
        return _lines(item.get("list_items")), None
    if kind == "text":
        return _text(item.get("text")), None
    return _text(item.get("text", "")), None


def content_list_to_blocks(
    content_list: Sequence[Mapping[str, Any]],
    *,
    page_count: int | None = None,
) -> list[MinerUBlock]:
    """Normalize every source item without filtering types, fetching assets or changing the input."""
    if page_count is not None and (type(page_count) is not int or page_count < 0):
        raise MinerUConversionError("page_count must be a non-negative integer or None")
    if not isinstance(content_list, Sequence) or isinstance(content_list, (str, bytes, bytearray)):
        raise MinerUConversionError("Expected a Content List array, not a result envelope or receipt")
    blocks = []
    for source_index, item in enumerate(content_list):
        if not isinstance(item, Mapping):
            raise MinerUConversionError(f"Invalid block at index {source_index}")
        kind = item.get("type")
        page = item.get("page_idx")
        if not isinstance(kind, str) or not kind.strip():
            raise MinerUConversionError(f"Missing block type at index {source_index}")
        if type(page) is not int or page < 0 or (page_count is not None and page >= page_count):
            raise MinerUConversionError(f"Invalid page_idx at index {source_index}")
        bbox = item.get("bbox")
        if bbox is not None:
            if not isinstance(bbox, list) or len(bbox) != 4:
                raise MinerUConversionError(f"Invalid bbox at index {source_index}")
            if any(type(value) not in (int, float) or not 0 <= value <= 1000 or not math.isfinite(value)
                   for value in bbox):
                raise MinerUConversionError(f"Invalid bbox coordinates at index {source_index}")
            if bbox[0] > bbox[2] or bbox[1] > bbox[3]:
                raise MinerUConversionError(f"Reversed bbox coordinates at index {source_index}")
            bbox = [float(value) for value in bbox]
        level = item.get("text_level")
        if type(level) is not int or not 1 <= level <= 6:
            level = None
        text, table_header = _block_text(item, kind)
        image_ref = item.get("img_path")
        if image_ref is not None and (not isinstance(image_ref, str) or not image_ref.strip()):
            raise MinerUConversionError(f"Invalid image reference at index {source_index}")
        if (kind == "image" or (kind == "equation" and not text.strip())) and image_ref is None:
            raise MinerUConversionError(f"Missing image reference at index {source_index}")
        blocks.append({
            "type": kind, "text": text, "level": level, "page": page, "bbox": bbox,
            "image_ref": image_ref, "table_header": table_header, "source_index": source_index,
        })
    return blocks


def _render(block: MinerUBlock) -> str:
    text = block["text"]
    # Table snapshots and equation snapshots duplicate their available textual representation.
    if block["image_ref"] and (block["type"] == "image" or not text.strip()):
        image = f"![](<{block['image_ref']}>)"
        return "\n\n".join(part for part in (image, text) if part)
    return text


def _text_parts(text: str, limit: int) -> list[str]:
    parts = []
    while len(text) > limit:
        chunk_count = (len(text) + limit - 1) // limit
        target = (len(text) + chunk_count - 1) // chunk_count
        # Prefer a nearby paragraph or line boundary without making small sentence-sized chunks.
        boundary = text.rfind("\n", target // 2, target)
        end = boundary + 1 if boundary >= 0 else target
        parts.append(text[:end])
        text = text[end:]
    if text:
        parts.append(text)
    return parts


def _section_to_paragraphs(
    headings: list[MinerUBlock], body: list[MinerUBlock], limit: int,
) -> list[Paragraph]:
    full_title = " / ".join(block["text"] for block in headings)
    title = full_title[:MAX_TITLE_LENGTH]
    # The overflow remains searchable, including when a title alone exceeds the title column.
    overflow = full_title[MAX_TITLE_LENGTH:]
    rendered = [(block, _render(block)) for block in body]
    pieces = [(block, text) for block, text in rendered if text.strip()]
    if not pieces and full_title:
        pieces = [(headings[-1], full_title)]
        overflow = ""
    if overflow:
        pieces.insert(0, (headings[-1], overflow))
    paragraphs = []
    content_parts = []
    sources = []
    length = 0

    def flush():
        nonlocal length
        if content_parts:
            paragraph_sources = headings + sources
            paragraphs.append({
                "title": title, "content": "\n\n".join(content_parts), "problem_list": [],
                "page_indices": sorted({block["page"] for block in paragraph_sources}),
                "source_block_indices": sorted({block["source_index"] for block in paragraph_sources}),
            })
            content_parts.clear()
            sources.clear()
            length = 0

    for block, text in pieces:
        atomic = block["type"] in {"table", "image", "equation"} or bool(block["image_ref"])
        if atomic and len(text) > MAX_CONTENT_LENGTH:
            raise MinerUConversionError(f"Atomic block {block['source_index']} exceeds the paragraph storage limit")
        parts = [text] if atomic else _text_parts(text, limit)
        for part in parts:
            separator_length = 2 if content_parts else 0
            if length + separator_length + len(part) > limit:
                flush()
            content_parts.append(part)
            sources.append(block)
            length += len(part) + (2 if len(content_parts) > 1 else 0)
    flush()
    return paragraphs


def blocks_to_paragraphs(
    blocks: Sequence[MinerUBlock],
    *,
    limit: int = 4096,
    drop_types: Sequence[str] = DEFAULT_DROP_TYPES,
) -> list[Paragraph]:
    """Keep sections together up to limit. Atomic tables/images may exceed this soft limit.

    Every paragraph respects the 102400-character storage limit. Provenance is returned for the caller to persist.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CONTENT_LENGTH:
        raise MinerUConversionError(f"limit must be an integer from 1 to {MAX_CONTENT_LENGTH}")
    if (not isinstance(drop_types, Sequence) or isinstance(drop_types, str)
            or any(not isinstance(kind, str) for kind in drop_types)):
        raise MinerUConversionError("drop_types must be a sequence of block type names")
    headings = []
    body = []
    paragraphs = []
    for block in blocks:
        if block["type"] in drop_types:
            continue
        if block["type"] in {"text", "heading"} and block["level"] is not None:
            if body or (headings and block["level"] <= headings[-1]["level"]):
                paragraphs.extend(_section_to_paragraphs(headings, body, limit))
                body = []
            headings = [heading for heading in headings if heading["level"] < block["level"]]
            headings.append(block)
        else:
            body.append(block)
    paragraphs.extend(_section_to_paragraphs(headings, body, limit))
    return paragraphs
