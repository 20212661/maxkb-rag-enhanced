# coding=utf-8
"""
    @project: MaxKB
    @Author：虎
    @file： i_chunk_handle.py
    @date：2024/7/23 16:51
    @desc:
"""
from abc import ABC, abstractmethod
from typing import List


class IChunkHandle(ABC):
    def support(self, text: str) -> bool:
        return True

    @abstractmethod
    def handle(self, chunk_list: List[str], chunk_size: int = 256):
        pass
