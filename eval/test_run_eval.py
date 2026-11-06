# coding=utf-8
"""Regression checks for rank order, source matching and multi-passage metrics."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from run_eval import APIError, MaxKBAPI, load_dataset, normalize, score_question, summarize, summarize_chat


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.question = {"id": "test", "question": "问题", "type": "跨段落型", "gold_passages": [
            {"document_name": "A.pdf", "key_sentence": "第一句。"},
            {"document_name": "B.docx", "key_sentence": "第二句。"},
        ]}

    def test_api_sorts_before_cutting_top_five(self):
        api = MaxKBAPI("http://localhost", token="test-token")
        api.request = Mock(return_value=[{"comprehensive_score": value} for value in (0.1, 0.5, 0.2, 0.7, 0.8, 0.9)])
        hits = api.hit_test("knowledge", "question", 0.55, "embedding")
        self.assertEqual([hit["comprehensive_score"] for hit in hits], [0.9, 0.8, 0.7, 0.5, 0.2])

    def test_wrong_document_and_title_only_do_not_match(self):
        hits = [{"document_name": "wrong.pdf", "content": "第一句。"},
                {"document_name": "A.pdf", "title": "第一句。", "content": "其他"}]
        result = score_question(self.question, hits)
        self.assertEqual(result["recall_at_5"], 0)
        self.assertEqual(result["reciprocal_rank"], 0)

    def test_multi_passage_recall_counts_unique_gold(self):
        hits = [{"document_name": "A.pdf", "content": "第一句。"},
                {"document_name": "A.pdf", "content": "第一句。"},
                {"document_name": "B.docx", "content": "第二句。"}]
        result = score_question(self.question, hits)
        self.assertEqual(result["recall_at_1"], 0.5)
        self.assertEqual(result["recall_at_5"], 1)
        self.assertEqual(result["reciprocal_rank"], 1)
        self.assertTrue(result["all_gold_in_top5"])

    def test_mrr_uses_first_relevant_rank(self):
        hits = [{"document_name": "wrong", "content": "第一句。"},
                {"document_name": "B.docx", "content": "第二句。"}]
        self.assertEqual(score_question(self.question, hits)["reciprocal_rank"], 0.5)

    def test_normalization_preserves_exact_sentence_semantics(self):
        question = {"gold_passages": [{"document_name": "A.pdf", "key_sentence": "Win+L锁屏。"}]}
        hits = [{"document_name": "Ａ.PDF", "content": "ＷＩＮ＋Ｌ\n锁 屏。"}]
        self.assertEqual(score_question(question, hits)["recall_at_1"], 1)
        hits[0]["content"] = "Win+L不锁屏。"
        self.assertEqual(score_question(question, hits)["recall_at_1"], 0)

    def test_supported_math_and_slash_representations_match(self):
        temperature = r"机房环境温度应控制在 $1 8 ^ { \circ } \mathrm { C } \sim 2 4 ^ { \circ } \mathrm { C }$"
        self.assertEqual(normalize(temperature), normalize("机房环境温度应控制在18℃～24℃"))
        self.assertEqual(normalize("不同网站∕应用"), normalize("不同网站/应用"))
        for expression in (r"$18^{\circ}\mathrm{C}$", r"$$18^{\circ}\mathrm{C}$$",
                           r"\(18^{\circ}\mathrm{C}\)", r"\[18^{\circ}\mathrm{C}\]"):
            with self.subTest(expression=expression):
                self.assertEqual(normalize(expression), normalize("18℃"))
        self.assertEqual(normalize(r"$2\times3\leq6\pm0$"), normalize("2×3≤6±0"))
        self.assertEqual(normalize(r"$18^\circ C \sim 24^\circ C$"), normalize("18℃～24℃"))
        self.assertEqual(normalize("\\(18^{\\circ}\n\\mathrm{C}\\)"), normalize("18℃"))

    def test_normalization_preserves_ocr_errors_numbers_and_unknown_math(self):
        for left, right in [("U盘", "∪盘"), ("18℃～24℃", "18℃～25℃"),
                            ("直接打电话", "直接电话"), ("可以备份", "不可以备份")]:
            with self.subTest(left=left, right=right):
                self.assertNotEqual(normalize(left), normalize(right))
        for expression in (r"$\frac{1}{2}$", r"$$\sqrt{2}$$", "$100", "100$"):
            with self.subTest(expression=expression):
                self.assertEqual(normalize(expression), expression)

    def test_frozen_week2_hits_with_corrected_dataset(self):
        directory = Path(__file__).parent
        questions = {question["id"]: question for question in load_dataset(directory / "dataset.jsonl")}
        after = json.loads((directory / "results/after.json").read_text(encoding="utf-8"))
        rows = {row["id"]: row for row in after["queries"]}
        for question_id in ("q048", "q054", "q059"):
            with self.subTest(question_id=question_id):
                self.assertEqual(score_question(questions[question_id], rows[question_id]["top5"])["recall_at_1"], 1)
        self.assertEqual(score_question(questions["q058"], rows["q058"]["top5"])["recall_at_5"], 0)

    def test_invalid_gold_and_duplicate_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "questions.jsonl"
            for values in ([[]], [{**self.question, "gold_passages": [None]}],
                           [{**self.question, "gold_passages": []}], [self.question, self.question]):
                path.write_text("\n".join(json.dumps(value) for value in values), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_dataset(path)

    def test_invalid_api_rank_is_an_error(self):
        api = MaxKBAPI("http://localhost", token="test-token")
        api.request = Mock(return_value=[{"comprehensive_score": float("nan")}])
        with self.assertRaises(APIError):
            api.hit_test("knowledge", "question", 0.55, "embedding")

    def test_errors_stay_in_denominator_and_no_llm_is_null(self):
        records = [{"status": "error", "retrieval_ms": 100, **score_question(self.question, [])},
                   {"status": "ok", "retrieval_ms": 20, **score_question(self.question, [
                       {"document_name": "B.docx", "content": "第二句。"}])}]
        metrics = summarize(records)
        self.assertEqual(metrics["recall_at_5"], 0.25)
        self.assertEqual(metrics["mean_retrieval_ms"], 60)
        self.assertEqual(metrics["error_count"], 1)
        self.assertIsNone(summarize_chat([])["mean_response_ms"])


if __name__ == "__main__":
    unittest.main()
