# coding=utf-8
"""Regression checks for answer metadata, source verification, blinding and repeat review."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from run_answer_eval import (
    EVAL, APIError, ask_question, blind, coverage_label, load_questions, source_coverage,
    stability_comparison, validate_runtime, wait_for_record,
)


class AnswerEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.question = {"id": "a01", "question": "问题", "type": "attribution", "verification": {
            "sources": [{"document_name": "A.pdf"}, {"document_name": "B.pdf"}]}}
        self.record = {"id": "record", "answer_text": "回答", "message_tokens": 12, "answer_tokens": 3,
                       "paragraph_list": [{"document_name": "A.pdf", "content": "资料"}],
                       "execution_details": [{"type": "chat"}]}

    def test_reviewed_dataset_has_expected_types_and_verified_corpus_files(self):
        questions = load_questions(EVAL / "answer_set.jsonl")
        counts = {kind: sum(q["type"] == kind for q in questions) for kind in
                  ("attribution", "disambiguation", "cross_unit_trap", "out_of_scope")}
        self.assertEqual(list(counts.values()), [11, 3, 3, 3])
        a11 = next(q for q in questions if q["id"] == "a11")
        self.assertFalse(a11["verification"]["item_number_is_unit_unique"])
        self.assertEqual(a11["verification"]["comparison"][0]["corresponding_item_numbers"], [8, 8])
        t03 = next(q for q in questions if q["id"] == "t03")
        self.assertTrue(any("第十六条" in point and "视为正确" in point for point in t03["key_points"]))

    def test_document_coverage_is_exact_and_handles_partial_and_out_of_scope(self):
        partial = source_coverage(self.question, ["A.pdf", "unrelated.pdf"])
        self.assertFalse(partial["expected_source_retrieved"])
        self.assertEqual(coverage_label(partial), "部分（1/2）")
        self.assertEqual(coverage_label(source_coverage(self.question, ["B.pdf", "A.pdf"])), "全部（2/2）")
        self.assertEqual(coverage_label(source_coverage(self.question, [])), "未检索到（0/2）")
        outside = {**self.question, "verification": {"sources": []}}
        self.assertIsNone(source_coverage(outside, ["A.pdf"])["expected_source_retrieved"])

    def test_empty_or_wrong_published_knowledge_binding_is_rejected_before_requests(self):
        runtime = {"application_id": "app", "qa_l1_cache_enabled": False,
                   "prompt_include_document_name": False, "model_params_setting": {"temperature": 0},
                   "knowledge_ids": ["expected-knowledge"]}
        validate_runtime(runtime, "app", 0, "expected-knowledge")
        for ids in ([], None, ["other-knowledge"]):
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, "knowledge binding"):
                validate_runtime({**runtime, "knowledge_ids": ids}, "app", 0, "expected-knowledge")

    def test_each_question_opens_a_new_session_and_sends_only_question(self):
        chat, admin = Mock(), Mock()
        chat.request.side_effect = ["chat-one", {"id": "record", "content": "回答", "is_end": True},
                                    "chat-two", {"id": "record", "content": "回答", "is_end": True}]
        admin.request.return_value = self.record
        for expected_id in ("chat-one", "chat-two"):
            row = ask_question(chat, admin, "/application", self.question, 1)
            self.assertEqual(row["chat_id"], expected_id)
            self.assertEqual(row["total_tokens"], 15)
            self.assertEqual(row["document_names"], ["A.pdf"])
            self.assertEqual(row["status"], "ok")
        posts = [call for call in chat.request.call_args_list if call.args[0] == "POST"]
        self.assertEqual(posts[0].args[2], {"message": "问题", "stream": False, "re_chat": False})

    def test_waits_for_final_record_and_preserves_unknown_tokens_on_failure(self):
        admin = Mock()
        admin.request.side_effect = [APIError("not yet"), {"id": "record"}, self.record]
        with patch("run_answer_eval.time.sleep"):
            self.assertEqual(wait_for_record(admin, "/record", "record", 1), self.record)
        chat = Mock()
        chat.request.side_effect = APIError("HTTP 503")
        row = ask_question(chat, admin, "/application", self.question, 1)
        self.assertEqual(row["status"], "error")
        self.assertIsNone(row["total_tokens"])

    def test_l1_replay_is_recorded_as_invalid_evaluation(self):
        chat, admin = Mock(), Mock()
        chat.request.side_effect = ["chat", {"id": "record", "content": "cached", "is_end": True}]
        admin.request.return_value = {**self.record, "execution_details": [{"hit": True, "level": "L1"}]}
        row = ask_question(chat, admin, "/application", self.question, 1)
        self.assertTrue(row["cache_hit"])
        self.assertEqual(row["status"], "error")

    def run_fixture(self, include):
        row = {"id": "a01", "type": "attribution", "question": "问题", "answer": "相同回答", "status": "ok",
               "record_id": "record", "document_names": ["A.pdf"], **source_coverage(self.question, ["A.pdf"])}
        return {"tag": "secret-on" if include else "secret-off", "complete": True,
                "dataset_sha256": "dataset", "rubric_sha256": "rubric", "manifest_sha256": "manifest",
                "evaluator_sha256": "script", "configuration": {"prompt_include_document_name": include,
                    "model_params_setting": {"temperature": 0}, "knowledge_ids": ["synthetic-knowledge"]},
                "rounds": [{"round": 1, "queries": [row]}, {"round": 2, "queries": [deepcopy(row)]}]}

    def test_blind_includes_only_first_round_and_keeps_scores_empty(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            paths = []
            for include in (False, True):
                path = folder / f"{include}.json"
                path.write_text(json.dumps(self.run_fixture(include)), encoding="utf-8")
                paths.append(path)
            blind(SimpleNamespace(runs=paths, output_dir=folder, seed=12, stability_threshold=0.75))
            table = (folder / "blind-review.md").read_text("utf-8")
            data_lines = [line for line in table.splitlines() if line.startswith("| B")]
            self.assertEqual(len(data_lines), 2)
            self.assertNotIn("secret-on", table)
            self.assertNotIn("secret-off", table)
            for line in data_lines:
                self.assertEqual([part.strip() for part in line.split("|")][-7:-1], [""] * 6)
            mapping = json.loads((folder / "mapping.json").read_text("utf-8"))
            self.assertEqual({row["round"] for row in mapping["entries"].values()}, {1})
            self.assertEqual(len(mapping["entries"]), 2)

    def test_stability_flags_text_numbers_and_citation_changes_without_scoring(self):
        result = self.run_fixture(False)
        self.assertFalse(stability_comparison(result, 0.75)[0]["review_required"])
        result["rounds"][0]["queries"][0]["answer"] = "至少1次"
        result["rounds"][1]["queries"][0].update(answer="至少2次", document_names=["B.pdf"])
        comparison = stability_comparison(result, 0.75)[0]
        self.assertIn("numbers_changed", comparison["reasons"])
        self.assertIn("citation_documents_changed", comparison["reasons"])
        self.assertNotIn("score", comparison)


if __name__ == "__main__":
    unittest.main()
