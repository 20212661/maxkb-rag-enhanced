# coding=utf-8
"""
    @project: MaxKB
    @file: test_workflow_prompt_sources.py
    @desc: Workflow prompt source labels without changes to retrieval or ranking inputs.
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from langchain_core.documents import Document

from application.flow.step_node.reranker_node.impl import base_reranker_node as reranker
from application.flow.step_node.search_knowledge_node.impl import base_search_knowledge_node as search
from maxkb.const import CONFIG


class WorkflowPromptSourcesTests(SimpleTestCase):
    def setUp(self):
        self.rows = [{"id": "one", "title": "章节", "content": "正文足够长以测试裁剪",
                      "document_name": "单位甲.pdf", "similarity": 0.9, "is_hit_handling_method": False}]
        self.settings = {"top_n": 5, "similarity": 0.5, "search_mode": "embedding",
                         "max_paragraph_char_number": 100}

    def node(self, cls):
        node = object.__new__(cls)
        node.context = {}
        node.workflow_manage = SimpleNamespace(get_body=lambda: {"workspace_id": "default"})
        node.node_params_serializer = SimpleNamespace(data={"knowledge_setting": self.settings})
        return node

    def test_search_execute_labels_only_the_data_output(self):
        node = self.node(search.BaseSearchKnowledgeNode)
        vector = Mock()
        vector.query.return_value = [{"paragraph_id": "one"}]
        outputs = []
        query_arguments = []
        for include in (False, True):
            with patch.dict(CONFIG, {"PROMPT_INCLUDE_DOCUMENT_NAME": include}), \
                    patch.object(search, "get_embedding_id", return_value="model"), \
                    patch.object(search, "get_model_instance_by_model_workspace_id"), \
                    patch.object(search, "filter_authorized_ids", return_value=["knowledge"]), \
                    patch.object(search.DatabaseModelManage, "get_model", return_value=None), \
                    patch.object(search, "QuerySet", return_value=SimpleNamespace(filter=lambda **kw: [])), \
                    patch.object(search.VectorStore, "get_embedding_vector", return_value=vector), \
                    patch("common.cache.embedding_cache.cached_query", return_value=[0.1]), \
                    patch.object(node, "list_paragraph", return_value=self.rows), \
                    patch.object(node, "reset_paragraph", side_effect=lambda p, e: deepcopy(p)):
                outputs.append(node.execute(["knowledge"], self.settings, "问题", True,
                                            "custom", "knowledge", []).node_variable)
                query_arguments.append(vector.query.call_args)
        self.assertEqual(query_arguments[0], query_arguments[1])
        self.assertEqual(outputs[0]["paragraph_list"], outputs[1]["paragraph_list"])
        self.assertEqual(outputs[0]["directly_return"], outputs[1]["directly_return"])
        self.assertEqual(outputs[0]["data"], "#### 章节\n正文足够长以测试裁剪")
        self.assertEqual(outputs[1]["data"], "【来源：单位甲.pdf】章节：正文足够长以测试裁剪")

    def test_search_history_restore_uses_source_budget(self):
        node = self.node(search.BaseSearchKnowledgeNode)
        self.settings["max_paragraph_char_number"] = 18
        with patch.dict(CONFIG, {"PROMPT_INCLUDE_DOCUMENT_NAME": True}):
            node.save_context({"paragraph_list": self.rows}, None)
        self.assertEqual(node.context["data"], "【来源：单位甲.pdf】章节：正文足")
        self.assertEqual(node.context["paragraph_list"], self.rows)
        with patch.dict(CONFIG, {"PROMPT_INCLUDE_DOCUMENT_NAME": False}):
            node.save_context({"paragraph_list": self.rows}, None)
        self.assertEqual(node.context["data"], "章节:正文足够长以测试裁剪")

    def test_reranker_keeps_model_inputs_scores_and_ranked_records(self):
        node = self.node(reranker.BaseRerankerNode)
        factory = Mock()
        factory.return_value.compress_documents.side_effect = lambda docs, question: [
            Document(page_content=d.page_content, metadata={"relevance_score": score})
            for d, score in zip(reversed(docs), (0.9, 0.7))]
        rows = [*self.rows, {**self.rows[0], "id": "two", "document_name": "单位乙.pdf", "content": "第二段"}]
        outputs, inputs = [], []
        for include in (False, True):
            with patch.dict(CONFIG, {"PROMPT_INCLUDE_DOCUMENT_NAME": include}), \
                    patch.object(reranker, "get_model_instance_by_model_workspace_id", factory):
                outputs.append(node.execute("问题", {"top_n": 2, "similarity": 0.6,
                    "max_paragraph_char_number": 25}, [deepcopy(rows)], "model", True).node_variable)
                inputs.append(factory.return_value.compress_documents.call_args.args[0])
        self.assertEqual(inputs[0], inputs[1])
        self.assertEqual(outputs[0]["result_list"], outputs[1]["result_list"])
        self.assertEqual([r["metadata"]["id"] for r in outputs[1]["result_list"]], ["two", "one"])
        self.assertEqual(outputs[1]["result"], "【来源：单位乙.pdf】章节第二段")
        self.assertLessEqual(len(outputs[1]["result"]), 25)
        with patch.dict(CONFIG, {"PROMPT_INCLUDE_DOCUMENT_NAME": True}):
            node.save_context(outputs[1], None)
        self.assertEqual(node.context["result"], outputs[1]["result"])
