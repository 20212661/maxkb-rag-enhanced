# coding=utf-8
"""
    @project: MaxKB
    @Author：虎
    @file： __init__.py
    @date：2024/7/23 17:03
    @desc:
"""
from common.chunk.impl.mark_chunk_handle import MarkChunkHandle
from common.chunk.impl.table_chunk_handle import TableChunkHandle
from maxkb.const import CONFIG

handles = [MarkChunkHandle()]
if str(CONFIG.get("TABLE_CHUNK_ENABLED", True)).strip().lower() in ("true", "1", "yes", "on"):
    handles.insert(0, TableChunkHandle())


def text_to_chunk(text: str, chunk_size: int = 256):
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    for handle in handles:
        if handle.support(text):
            return handle.handle([text], chunk_size)
    return [text]
