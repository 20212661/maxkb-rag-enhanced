# coding=utf-8
"""Synthetic-only tests for the post-review summary. Real grading files are never read."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from score_answers import (
    join_metadata, main, parse_coverage, parse_score, read_review, score_stats, summarize,
)


class ScoreSummaryTests(unittest.TestCase):
    def review(self, blind_id="B001", score=0, coverage="全部（1/1）"):
        return {"编号": blind_id, "题目ID": "synthetic-a", "类型": "attribution", "score": score,
                "components": {"归属正确": score, "内容正确": score, "拒答正确": None, "出处标注正确": None},
                "coverage": parse_coverage(coverage), "期望来源是否被检索到": coverage, "备注": "测试记录"}

    def metadata(self):
        mapping = {"entries": {
            "B001": {"tag": "synthetic-off", "question_id": "synthetic-a", "round": 1,
                     "prompt_include_document_name": False},
            "B002": {"tag": "synthetic-on", "question_id": "synthetic-a", "round": 1,
                     "prompt_include_document_name": True}}}
        comparisons = [{"tag": "synthetic-" + group, "id": "synthetic-a", "identical_answer": False,
                        "review_required": flag, "text_similarity": 0.95,
                        "reasons": ["numbers_changed"] if flag else []}
                       for group, flag in (("off", False), ("on", True))]
        return mapping, {"comparisons": comparisons}

    def table(self, first_score="0", second_score="1"):
        return (
            "| 编号 | 题目ID | 类型 | 问题 | 回答 | 引用文档 | 期望来源是否被检索到 | 归属正确 | 内容正确 | 拒答正确 | 出处标注正确 | 得分 | 备注 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| B001 | synthetic-a | attribution | 测试 | 文字&#124;文字 | A.pdf | 全部（1/1） | "
            f"0 | 0 | N/A | N/A | {first_score} | 原因 |\n"
            "| B002 | synthetic-a | attribution | 测试 | 测试回答 | A.pdf | 全部（1/1） | "
            f"1 | 1 | N/A | N/A | {second_score} | 原因 |\n"
        )

    def test_explicit_scores_and_pending_are_preserved(self):
        for value in ("", "待复核", "?"):
            self.assertIsNone(parse_score(value))
        self.assertIsNone(parse_score("N/A", component=True))
        self.assertEqual(parse_score("0.50"), 0.5)
        for value in ("nan", "inf", "0.8", "正确", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_score(value)
        stats = score_stats([{"score": None}, {"score": 1}])
        self.assertEqual(stats["mean_score"], 1)
        self.assertEqual(stats["pending_count"], 1)

    def test_source_coverage_handles_partial_and_rejects_inconsistent_labels(self):
        self.assertFalse(parse_coverage("部分（1/3）")["all_retrieved"])
        self.assertIsNone(parse_coverage("不适用")["all_retrieved"])
        for value in ("全部（1/2）", "部分（2/2）", "全部（0/0）", "全部（3/2）"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_coverage(value)

    def test_markdown_parser_keeps_pending_score_without_inferring_from_components(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "review.md"
            path.write_text(self.table(second_score="待复核"), encoding="utf-8")
            rows = read_review(path)
        self.assertEqual(rows[0]["回答"], "文字|文字")
        self.assertIsNone(rows[1]["score"])
        self.assertEqual(rows[1]["components"]["内容正确"], 1)

    def test_parser_rejects_duplicate_rows_and_malformed_cells(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "review.md"
            path.write_text(self.table().replace("B002", "B001"), encoding="utf-8")
            with self.assertRaises(ValueError):
                read_review(path)
            path.write_text(self.table().replace("原因 |", "原|因 |"), encoding="utf-8")
            with self.assertRaises(ValueError):
                read_review(path)

    def test_mapping_and_stability_join_require_matching_first_round_questions(self):
        mapping, stability = self.metadata()
        reviews = [self.review(), self.review("B002", 1)]
        joined = join_metadata(reviews, mapping, stability)
        self.assertEqual([r["group"] for r in joined], ["off", "on"])
        bad_mapping = deepcopy(mapping)
        bad_mapping["entries"]["B002"]["round"] = 2
        with self.assertRaises(ValueError):
            join_metadata(reviews, bad_mapping, stability)
        with self.assertRaises(ValueError):
            join_metadata(reviews, mapping, {"comparisons": stability["comparisons"][:1]})

    def test_missing_sources_are_separate_from_retrieved_generation_errors(self):
        mapping, stability = self.metadata()
        joined = join_metadata([self.review(), self.review("B002", 0, "未检索到（0/1）")], mapping, stability)
        summary = summarize(joined)
        off, on = (summary["groups"][g] for g in ("off", "on"))
        self.assertEqual(off["by_source"]["retrieved_but_generation_error_count"], 1)
        self.assertEqual(on["by_source"]["retrieved_but_generation_error_count"], 0)
        self.assertEqual(on["by_source"]["expected_sources_missing"]["question_count"], 1)
        self.assertEqual(off["exact_text_change_count"], 1)
        self.assertEqual(off["stability_review_flag_count"], 0)

    def test_pair_delta_and_pending_denominators(self):
        mapping, stability = self.metadata()
        joined = join_metadata([self.review(), self.review("B002", 1)], mapping, stability)
        self.assertEqual(summarize(joined)["paired_comparison"]["mean_delta"], 1)
        joined[1]["score"] = None
        summary = summarize(joined)
        self.assertIsNone(summary["pairs"][0]["delta"])
        self.assertEqual(summary["paired_comparison"]["scored_pairs"], 0)

    def test_entirely_missing_sources_are_flagged_as_an_experiment_issue(self):
        mapping, stability = self.metadata()
        reviews = [self.review(coverage="未检索到（0/1）"), self.review("B002", 0, "未检索到（0/1）")]
        result = summarize(join_metadata(reviews, mapping, stability))
        self.assertTrue(result["all_in_scope_expected_sources_absent"])

    def test_cli_writes_all_reports_using_only_synthetic_files(self):
        mapping, stability = self.metadata()
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            review = folder / "review.md"
            review.write_text(self.table(), encoding="utf-8")
            for name, value in (("mapping.json", mapping), ("stability.json", stability)):
                (folder / name).write_text(json.dumps(value), encoding="utf-8")
            with patch("sys.argv", ["score_answers.py", "--review", str(review),
                                    "--mapping", str(folder / "mapping.json"),
                                    "--stability", str(folder / "stability.json"), "--output-dir", str(folder)]):
                self.assertEqual(main(), 0)
            output = json.loads((folder / "score-summary.json").read_text("utf-8"))
            self.assertEqual(output["groups"]["on"]["overall"]["mean_score"], 1)
            self.assertTrue((folder / "question-comparison.md").is_file())
            self.assertTrue((folder / "score-summary.md").is_file())


if __name__ == "__main__":
    unittest.main()
