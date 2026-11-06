# coding=utf-8
"""Check fractional recall, missing groups and comparison input validation."""
import json
import tempfile
import unittest
from pathlib import Path

from compare import compare_results, load_result


class CompareTests(unittest.TestCase):
    def result(self, **metrics):
        return {"dataset_sha256": "same", "complete": True, "by_type": {"跨段落型": {
            "question_count": 10, "recall_at_1": 0.25, "recall_at_5": 0.5, "mrr": 0.375, **metrics}}}

    def test_fractional_recall_and_positive_and_negative_deltas(self):
        report = compare_results(self.result(), self.result(recall_at_1=0.5, recall_at_5=0.25, mrr=0.5))
        self.assertIn("25.00% → 50.00% | +25.00", report)
        self.assertIn("50.00% → 25.00% | -25.00", report)
        self.assertIn("0.3750 → 0.5000 | +0.1250", report)

    def test_missing_types_and_different_datasets_are_explicit(self):
        candidate = {"dataset_sha256": "new", "complete": False,
                     "by_type": {"图片型": self.result()["by_type"]["跨段落型"]}}
        report = compare_results(self.result(), candidate)
        self.assertIn("10 / —", report)
        self.assertIn("— / 10", report)
        self.assertIn("dataset_sha256 不同", report)
        self.assertIn("尚未完成", report)

    def test_invalid_and_nonfinite_metrics_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            for result in ({}, {"by_type": {}}, self.result(mrr=float("nan")), self.result(recall_at_5=2),
                           self.result(recall_at_1=True), self.result(question_count=0)):
                with self.subTest(result=result):
                    path.write_text(json.dumps(result), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_result(path)

    def test_normalization_version_mismatch_is_explicit(self):
        candidate = {**self.result(), "configuration": {"normalization_version": 2}}
        self.assertIn("normalization_version 不同", compare_results(self.result(), candidate))
        self.assertNotIn("normalization_version 不同", compare_results(candidate, candidate))

    def test_baseline_round_trip_produces_zero_deltas(self):
        baseline = load_result(Path(__file__).parent / "results/baseline.json")
        report = compare_results(baseline, baseline)
        self.assertEqual(report.count("| +0.00 |"), 10)
        self.assertEqual(report.count("| +0.0000 |"), 5)
        self.assertIn("事实型 | 20 / 20", report)


if __name__ == "__main__":
    unittest.main()
