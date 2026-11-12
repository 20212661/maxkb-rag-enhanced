import tempfile
import unittest
from pathlib import Path

from l2_threshold import cosine, load_rows, metrics, score_rows, select_threshold


class L2ThresholdTests(unittest.TestCase):
    def test_cosine_rejects_invalid_vectors(self):
        self.assertEqual(cosine([1, 0], [1, 0]), 1)
        self.assertEqual(cosine([1, 0], [0, 1]), 0)
        for left, right in [([], []), ([0], [0]), ([1], [1, 2]), ([float("nan")], [1])]:
            with self.assertRaises(ValueError):
                cosine(left, right)

    def test_false_hit_denominator_and_empty_acceptance(self):
        rows = [{"similarity": 0.95, "correct_reuse": True}, {"similarity": 0.9, "correct_reuse": False},
                {"similarity": 0.8, "correct_reuse": True}]
        point = metrics(rows, 0.9)
        self.assertEqual(point["acceptance_rate"], 2 / 3)
        self.assertEqual(point["false_hit_rate"], 1 / 2)
        self.assertEqual(point["wrong_per_request"], 1 / 3)
        self.assertIsNone(metrics(rows, 1)["false_hit_rate"])
        self.assertIsNone(select_threshold([metrics(rows, 1)], 0.01))

    def test_selection_uses_dev_only_and_maximizes_safe_acceptance(self):
        curve = [{"threshold": 0.8, "accepted": 10, "false_hit_rate": 0.2},
                 {"threshold": 0.9, "accepted": 8, "false_hit_rate": 0},
                 {"threshold": 0.95, "accepted": 8, "false_hit_rate": 0},
                 {"threshold": 1, "accepted": 0, "false_hit_rate": None}]
        self.assertEqual(select_threshold(curve, 0.01)["threshold"], 0.95)

    def test_frozen_candidates_and_deterministic_ties(self):
        import hashlib
        rows = [{"id": "a", "kind": "anchor", "question": "one"},
                {"id": "b", "kind": "anchor", "question": "two"},
                {"id": "q", "kind": "rewrite", "question": "new", "group_id": "g", "split": "dev",
                 "reusable_answer_ids": ["a"]}]
        vectors = {hashlib.sha256(text.encode()).hexdigest(): [1, 0] for text in ["one", "two", "new"]}
        result = score_rows(rows, vectors)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["candidate_id"], "a")
        self.assertTrue(result[0]["correct_reuse"])

    def test_dataset_and_cross_split_group_validation(self):
        import json
        dataset = Path(__file__).with_name("l2_dataset.jsonl")
        rows = load_rows(dataset)
        rows[-1]["group_id"] = rows[0]["group_id"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows), "utf-8")
            with self.assertRaisesRegex(ValueError, "Intent groups"):
                load_rows(path)


if __name__ == "__main__":
    unittest.main()
