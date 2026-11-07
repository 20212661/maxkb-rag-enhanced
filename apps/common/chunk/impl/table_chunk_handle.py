# coding=utf-8
"""
    @project: MaxKB
    @file: table_chunk_handle.py
    @desc: Row-based table chunks with repeated headers and native prose splitting.
"""
import re

from bs4 import BeautifulSoup

from common.chunk.i_chunk_handle import IChunkHandle
from common.chunk.impl.mark_chunk_handle import MarkChunkHandle


def _cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line)]


def _fences(text):
    ranges = []
    opened = None
    offset = 0
    for line in text.splitlines(keepends=True):
        match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if match:
            token = match.group(1)
            if opened is None:
                opened = (offset, token)
            elif token[0] == opened[1][0] and len(token) >= len(opened[1]) and line.strip() == token:
                ranges.append((opened[0], offset + len(line)))
                opened = None
        offset += len(line)
    if opened is not None:
        ranges.append((opened[0], len(text)))
    return ranges


def _tables(text):
    fences = _fences(text)
    spans = [(match.start(), match.end(), "html") for match in
             re.finditer(r"<table\b[^>]*>.*?</table\s*>", text, re.I | re.S)]
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    index = 0
    while index + 1 < len(lines):
        header = _cells(lines[index])
        separator = _cells(lines[index + 1])
        if "|" in lines[index] and len(header) == len(separator) and all(
                re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
            end = index + 2
            while end < len(lines) and "|" in lines[end] and len(_cells(lines[end])) == len(header):
                end += 1
            spans.append((offsets[index], offsets[end], "markdown"))
            index = end
        else:
            index += 1
    result = []
    for start, end, kind in sorted(spans):
        if any(start < fence_end and end > fence_start for fence_start, fence_end in fences):
            continue
        if result and start < result[-1][1]:
            continue
        result.append((start, end, kind))
    return result


def _html_rows(text):
    table = BeautifulSoup(text, "html.parser").find("table")
    if table is None or table.find("table"):
        return [], [], ""
    rows, headings = [], []
    pending = {}
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        heading = row.find_parent("thead") is not None or (not rows and any(cell.name == "th" for cell in cells))
        values = {}
        for column, (value, remaining) in list(pending.items()):
            values[column] = value
            if remaining == 1:
                del pending[column]
            else:
                pending[column] = (value, remaining - 1)
        column = 0
        for cell in cells:
            while column in values:
                column += 1
            value = cell.get_text(" ", strip=True)
            colspan = max(1, int(cell.get("colspan", 1)))
            rowspan = max(1, int(cell.get("rowspan", 1)))
            for part in range(colspan):
                # Repeat spanning header labels, retain spanning data values once.
                values[column + part] = value if heading or part == 0 else ""
                if rowspan > 1:
                    pending[column + part] = (values[column + part], rowspan - 1)
            column += colspan
        if values:
            values = [values.get(column, "") for column in range(max(values) + 1)]
            (headings if heading else rows).append(values)
    width = max((len(row) for row in headings + rows), default=0)
    headers = [" / ".join(dict.fromkeys(row[column] for row in headings if column < len(row) and row[column]))
               or f"列{column + 1}" for column in range(width)]
    caption = table.find("caption", recursive=False)
    return headers, [row + [""] * (width - len(row)) for row in rows], caption.get_text(" ", strip=True) if caption else ""


def _row(cells):
    return "| " + " | ".join(cell.replace("|", "\\|").replace("\n", " ") for cell in cells) + " |"


def _long_row(cells, budget):
    fragments = []
    current = [""] * len(cells)
    for column, cell in enumerate(cells):
        while cell:
            available = max(0, budget - len(_row(current)))
            if available == 0 and any(current):
                fragments.append(_row(current))
                current = [""] * len(cells)
                continue
            # An oversized header still permits forward progress without dropping text.
            count = max(1, available // 2 if "|" in cell else available)
            current[column] += cell[:count]
            cell = cell[count:]
    if any(current):
        fragments.append(_row(current))
    return fragments or [_row(cells)]


def _group_rows(headers, rows, chunk_size, caption=""):
    header = _row(headers) + "\n" + _row(["---"] * len(headers))
    if caption:
        header = caption + "\n" + header
    if not rows:
        return [header]
    chunks = []
    current = header
    budget = max(1, chunk_size - len(header) - 1)
    if len(_row([""] * len(headers))) >= budget:
        budget = len(_row([""] * len(headers))) + 32
    for cells in rows:
        line = _row(cells)
        fragments = _long_row(cells, budget) if len(line) > budget else [line]
        for fragment in fragments:
            if current != header and len(current) + len(fragment) + 1 > chunk_size:
                chunks.append(current)
                current = header
            current += "\n" + fragment
    chunks.append(current)
    return chunks


class TableChunkHandle(IChunkHandle):
    def support(self, text: str) -> bool:
        return bool(_tables(text))

    def handle(self, chunk_list: list[str], chunk_size: int = 256) -> list[str]:
        result = []
        native = MarkChunkHandle()
        for text in chunk_list:
            offset = 0
            for start, end, kind in _tables(text):
                result.extend(native.handle([text[offset:start]], chunk_size))
                table = text[start:end]
                caption = ""
                if kind == "html":
                    try:
                        headers, rows, caption = _html_rows(table)
                    except (TypeError, ValueError):
                        headers, rows = [], []
                else:
                    lines = table.splitlines()
                    headers, rows = _cells(lines[0]), [_cells(line) for line in lines[2:]]
                result.extend(_group_rows(headers, rows, chunk_size, caption)
                              if headers else native.handle([table], chunk_size))
                offset = end
            result.extend(native.handle([text[offset:]], chunk_size))
        return result
