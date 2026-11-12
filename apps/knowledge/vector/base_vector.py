# coding=utf-8
"""
@project: maxkb
@Author：虎
@file： base_vector.py
@date：2023/10/18 19:16
@desc:
"""

import re
import threading
from abc import ABC, abstractmethod
from functools import reduce
from contextlib import contextmanager
from typing import Dict, List

from common.chunk import text_to_chunk
from common.utils.common import sub_array
from langchain_core.embeddings import Embeddings

from knowledge.models import SearchMode, SourceType
from maxkb.const import CONFIG

lock = threading.Lock()


def chunk_title_prefix(title):
    enabled = str(CONFIG.get("EMBEDDING_CHUNK_TITLE_PREFIX_ENABLED", True)).strip().lower()
    if enabled not in ("true", "1", "yes", "on"):
        return ""
    title = (title or "").strip()[:max(0, int(CONFIG.get("EMBEDDING_CHUNK_TITLE_PREFIX_MAX_CHARS", 100)))]
    return title + "\n" if title else ""


def chunk_data(data: Dict):
    if str(data.get("source_type")) == str(SourceType.PARAGRAPH.value):
        text = data.get("text")
        prefix = chunk_title_prefix(data.get("title"))
        if data.get("chunks"):
            chunk_list = data["chunks"]
        elif prefix and "content" in data:
            chunk_list = text_to_chunk(data.get("content") or "") or [""]
        else:
            chunk_list = text_to_chunk(text)
        return [{**data, "text": prefix + chunk} for chunk in chunk_list]
    return [data]


def chunk_data_list(data_list: List[Dict]):
    result = [chunk_data(data) for data in data_list]
    return reduce(lambda x, y: [*x, *y], result, [])


# 预编译正则，性能更好
RE_EMOJI = re.compile(
    r"[\U0001F300-\U0001FAFF]"  # Emoji
    r"|[\u2600-\u27BF]"  # Dingbats / Symbols（⚓ 在这）
    r"|[\uFE0E\uFE0F]",  # Variation Selectors
    flags=re.UNICODE,
)

RE_WHITESPACE = re.compile(r"\s+")


def normalize_for_embedding(text: str) -> str:
    if not text:
        return ""

    text = RE_EMOJI.sub("", text)
    text = RE_WHITESPACE.sub(" ", text)
    return text.strip()


class BaseVectorStore(ABC):
    vector_exists = False

    @contextmanager
    def vector_mutation(self, knowledge_ids):
        from django.db import transaction
        from django.db.models import F
        from django.utils import timezone
        from knowledge.models import KnowledgeCacheState
        ids = set(knowledge_ids)
        with transaction.atomic():
            yield
            if ids:
                KnowledgeCacheState.objects.bulk_create(
                    [KnowledgeCacheState(knowledge_id=value) for value in ids], ignore_conflicts=True)
                KnowledgeCacheState.objects.filter(knowledge_id__in=ids).update(
                    cache_version=F("cache_version") + 1, updated_at=timezone.now())

    def _mutate_vectors(self, filters, instance=None):
        from django.db import transaction
        from knowledge.models import Embedding
        with transaction.atomic():
            selected = list(Embedding.objects.filter(**filters).select_for_update().values_list("id", "knowledge_id"))
            rows = Embedding.objects.filter(id__in=[identifier for identifier, _ in selected])
            knowledge_ids = {knowledge_id for _, knowledge_id in selected}
            if instance and instance.get("knowledge_id"):
                knowledge_ids.add(instance["knowledge_id"])
            with self.vector_mutation(knowledge_ids):
                return self._delete_vectors(rows) if instance is None else self._update_vectors(rows, instance)

    @abstractmethod
    def _delete_vectors(self, rows):
        pass

    @abstractmethod
    def _update_vectors(self, rows, instance):
        pass

    @abstractmethod
    def vector_is_create(self) -> bool:
        """
        判断向量库是否创建
        :return: 是否创建向量库
        """
        pass

    @abstractmethod
    def vector_create(self):
        """
        创建 向量库
        :return:
        """
        pass

    def save_pre_handler(self):
        """
        插入前置处理器 主要是判断向量库是否创建
        :return: True
        """
        if not BaseVectorStore.vector_exists:
            if not self.vector_is_create():
                self.vector_create()
                BaseVectorStore.vector_exists = True
        return True

    def save(
        self,
        text,
        source_type: SourceType,
        knowledge_id: str,
        document_id: str,
        paragraph_id: str,
        source_id: str,
        is_active: bool,
        embedding: Embeddings,
    ):
        """
        插入向量数据
        :param source_id:  资源id
        :param knowledge_id: 知识库id
        :param text: 文本
        :param source_type: 资源类型
        :param document_id: 文档id
        :param is_active:   是否禁用
        :param embedding:   向量化处理器
        :param paragraph_id 段落id
        :return:  bool
        """
        self.save_pre_handler()
        data = {
            "document_id": document_id,
            "paragraph_id": paragraph_id,
            "knowledge_id": knowledge_id,
            "is_active": is_active,
            "source_id": source_id,
            "source_type": source_type,
            "text": text,
        }
        chunk_list = chunk_data(data)
        result = sub_array(chunk_list)
        for child_array in result:
            self._batch_save(child_array, embedding, lambda: False)

    def batch_save(self, data_list: List[Dict], embedding: Embeddings, is_the_task_interrupted):
        """
        批量插入
        @param data_list: 数据列表
        @param embedding: 向量化处理器
        @param is_the_task_interrupted: 判断是否中断任务
        :return: bool
        """
        self.save_pre_handler()
        chunk_list = chunk_data_list(data_list)
        result = sub_array(chunk_list)
        for child_array in result:
            if not is_the_task_interrupted():
                self._batch_save(child_array, embedding, is_the_task_interrupted)
            else:
                break

    @abstractmethod
    def _save(
        self,
        text,
        source_type: SourceType,
        knowledge_id: str,
        document_id: str,
        paragraph_id: str,
        source_id: str,
        is_active: bool,
        embedding: Embeddings,
    ):
        pass

    @abstractmethod
    def _batch_save(self, text_list: List[Dict], embedding: Embeddings, is_the_task_interrupted):
        pass

    def search(
        self,
        query_text,
        knowledge_id_list: list[str],
        exclude_document_id_list: list[str],
        exclude_paragraph_list: list[str],
        is_active: bool,
        embedding: Embeddings,
    ):
        if knowledge_id_list is None or len(knowledge_id_list) == 0:
            return []
        query_text = normalize_for_embedding(query_text)
        from common.cache.embedding_cache import cached_query
        query_embedding = cached_query(embedding, query_text)
        result = self.query(
            query_text, query_embedding,
            knowledge_id_list, None,
            exclude_document_id_list, exclude_paragraph_list,
            is_active, 3, 0.65, SearchMode.embedding
        )
        return result[0] if result else None

    @abstractmethod
    def query(
        self,
        query_text: str,
        query_embedding: List[float],
        knowledge_id_list: list[str],
        document_id_list: list[str] | None,
        exclude_document_id_list: list[str],
        exclude_paragraph_list: list[str],
        is_active: bool,
        top_n: int,
        similarity: float,
        search_mode: SearchMode,
    ):
        pass

    @abstractmethod
    def hit_test(
        self,
        query_text,
        knowledge_id: list[str],
        exclude_document_id_list: list[str],
        top_number: int,
        similarity: float,
        search_mode: SearchMode,
        embedding: Embeddings,
    ):
        pass

    def update_by_paragraph_id(self, paragraph_id: str, instance: Dict):
        self._mutate_vectors({"paragraph_id": paragraph_id}, instance)

    def update_by_paragraph_ids(self, paragraph_id: str, instance: Dict):
        self._mutate_vectors({"paragraph_id__in": paragraph_id}, instance)

    def update_by_source_id(self, source_id: str, instance: Dict):
        self._mutate_vectors({"source_id": source_id}, instance)

    def update_by_source_ids(self, source_ids: List[str], instance: Dict):
        self._mutate_vectors({"source_id__in": source_ids}, instance)

    def delete_by_knowledge_id(self, knowledge_id: str):
        self._mutate_vectors({"knowledge_id": knowledge_id})

    def delete_by_document_id(self, document_id: str):
        self._mutate_vectors({"document_id": document_id})
        return True

    def delete_by_document_id_list(self, document_id_list: List[str]):
        if not document_id_list:
            return True
        return self._mutate_vectors({"document_id__in": document_id_list})

    def delete_by_knowledge_id_list(self, knowledge_id_list: List[str]):
        self._mutate_vectors({"knowledge_id__in": knowledge_id_list})

    def delete_by_source_id(self, source_id: str, source_type: str):
        self._mutate_vectors({"source_id": source_id, "source_type": source_type})
        return True

    def delete_by_source_ids(self, source_ids: List[str], source_type: str):
        self._mutate_vectors({"source_id__in": source_ids, "source_type": source_type})

    def delete_by_paragraph_id(self, paragraph_id: str):
        self._mutate_vectors({"paragraph_id": paragraph_id})

    def delete_by_paragraph_ids(self, paragraph_ids: List[str]):
        self._mutate_vectors({"paragraph_id__in": paragraph_ids})
