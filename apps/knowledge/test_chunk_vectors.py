# coding=utf-8
"""
    @project: MaxKB
    @file: test_chunk_vectors.py
    @desc: Chunk vector inputs and keyword indexing alignment regression checks.
"""
from unittest.mock import Mock, patch

from django.test import TestCase

from common.event.listener_manage import ListenerManagement
from knowledge.models import Document, Embedding, Knowledge, KnowledgeFolder, Paragraph, SourceType
from knowledge.vector.pg_vector import PGVector


class ChunkVectorTests(TestCase):
    def setUp(self):
        folder = KnowledgeFolder.objects.create(id="chunk-tests", name="chunks")
        self.knowledge = Knowledge.objects.create(name="chunks", desc="", folder=folder)
        self.document = Document.objects.create(name="document", knowledge=self.knowledge, char_length=0)
        self.paragraph = Paragraph.objects.create(document=self.document, knowledge=self.knowledge,
            title="Heading", content="first second", chunks=["first", "second"])

    def vector(self, identifier, *, meta=None, source_type=SourceType.PARAGRAPH):
        return Embedding.objects.create(id=identifier, knowledge=self.knowledge, document=self.document,
            paragraph=self.paragraph, source_id=str(self.paragraph.id), source_type=source_type,
            embedding=[0.1, 0.2], meta=meta or {}, search_vector="original")

    def test_tokenize_uses_record_text_and_excludes_problem_vectors(self):
        self.vector("b", meta={"embedding_text": "actual second"})
        self.vector("a", meta={"embedding_text": "actual first"})
        problem = self.vector("0", source_type=SourceType.PROBLEM)
        with patch("common.event.listener_manage.to_ts_vector", return_value="token") as tokenize:
            ListenerManagement.tokenize_by_paragraph(self.paragraph.id)
        self.assertEqual([call.args[0] for call in tokenize.call_args_list], ["actual first", "actual second"])
        problem.refresh_from_db()
        self.assertEqual(problem.search_vector, "'original'")

    def test_legacy_fallback_is_ordered_warns_and_preserves_vectors(self):
        self.vector("b")
        self.vector("a")
        before = list(Embedding.objects.order_by("id").values_list("id", "embedding"))
        with patch("common.event.listener_manage.to_ts_vector", return_value="token") as tokenize, \
                patch("common.event.listener_manage.maxkb_logger.warning") as warning:
            ListenerManagement.tokenize_by_paragraph(self.paragraph.id)
        self.assertEqual([call.args[0] for call in tokenize.call_args_list], ["Heading\nfirst", "Heading\nsecond"])
        warning.assert_called_once()
        self.assertEqual(list(Embedding.objects.order_by("id").values_list("id", "embedding")), before)

    def test_legacy_count_mismatch_does_not_partially_update(self):
        row = self.vector("a")
        with patch("common.event.listener_manage.to_ts_vector") as tokenize:
            ListenerManagement.tokenize_by_paragraph(self.paragraph.id)
        tokenize.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(row.search_vector, "'original'")

    def test_indexing_records_actual_normalized_input_and_preserves_meta(self):
        model = Mock()
        model.embed_documents.return_value = [[0.1, 0.2]]
        row = {"knowledge_id": self.knowledge.id, "document_id": self.document.id,
               "paragraph_id": self.paragraph.id, "source_id": str(self.paragraph.id),
               "source_type": SourceType.PARAGRAPH, "text": "Heading\nfirst", "meta": {"origin": "test"}}
        PGVector()._batch_save([row], model, lambda: False)
        stored = Embedding.objects.get()
        self.assertEqual(stored.meta, {"origin": "test", "embedding_text": "Heading first"})
        model.embed_documents.assert_called_once_with(["Heading first"])
