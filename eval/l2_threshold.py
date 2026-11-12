#!/usr/bin/env python3
"""Evaluate offline semantic answer reuse on frozen anchors, rewrites and hard negatives."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_rows(path):
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    ids = [row["id"] for row in rows]
    if not rows or len(set(ids)) != len(ids):
        raise ValueError("Dataset must be nonempty with unique IDs")
    anchors = {row["id"]: row for row in rows if row["kind"] == "anchor"}
    if not anchors:
        raise ValueError("Dataset needs frozen answer anchors")
    groups = {}
    for row in rows:
        if row["kind"] not in ("anchor", "rewrite", "hard_negative") or row["split"] not in ("dev", "test"):
            raise ValueError("Invalid kind or split")
        if not isinstance(row["question"], str) or not row["question"].strip():
            raise ValueError("Questions must be nonempty strings")
        if row["group_id"] in groups and groups[row["group_id"]] != row["split"]:
            raise ValueError("Intent groups must not cross dev/test splits")
        groups[row["group_id"]] = row["split"]
        if row["kind"] != "anchor":
            targets = row["reusable_answer_ids"]
            if not isinstance(targets, list) or not set(targets) <= anchors.keys():
                raise ValueError("Reuse labels must reference existing anchors")
            if row["kind"] == "hard_negative" and targets:
                raise ValueError("Hard negatives must reject all seeded answers")
    for split in ("dev", "test"):
        if not any(row["split"] == split and row["kind"] != "anchor" for row in rows):
            raise ValueError("Both dev and test need evaluation queries")
    anchor_questions = {row["question"] for row in anchors.values()}
    if any(row["kind"] != "anchor" and row["question"] in anchor_questions for row in rows):
        raise ValueError("Exact anchor repeats belong to L1 and must be excluded from L2 queries")
    return rows


def cosine(left, right):
    if len(left) != len(right) or not left or not all(math.isfinite(value) for value in [*left, *right]):
        raise ValueError("Vectors must have equal positive dimensions and finite elements")
    denominator = math.sqrt(math.fsum(value * value for value in left) * math.fsum(value * value for value in right))
    if denominator == 0:
        raise ValueError("Zero-norm embedding")
    return max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(left, right)) / denominator))


def score_rows(rows, vectors):
    anchors = sorted([row for row in rows if row["kind"] == "anchor"], key=lambda row: row["id"])
    scores = []
    for row in rows:
        if row["kind"] == "anchor":
            continue
        query = vectors[hashlib.sha256(row["question"].encode()).hexdigest()]
        candidates = [(anchor["id"], cosine(query, vectors[hashlib.sha256(anchor["question"].encode()).hexdigest()]))
                      for anchor in anchors]
        best, similarity = max(candidates, key=lambda candidate: candidate[1])
        scores.append({"id": row["id"], "group_id": row["group_id"], "split": row["split"], "kind": row["kind"],
                       "candidate_id": best, "similarity": similarity, "correct_reuse": best in row["reusable_answer_ids"]})
    return scores


def metrics(scores, threshold):
    accepted = [row for row in scores if row["similarity"] >= threshold]
    wrong = sum(not row["correct_reuse"] for row in accepted)
    total = len(scores)
    if not total:
        raise ValueError("Cannot evaluate an empty split")
    return {"threshold": threshold, "query_count": total, "accepted": len(accepted), "wrong_accepted": wrong,
            "acceptance_rate": len(accepted) / total,
            "false_hit_rate": wrong / len(accepted) if accepted else None,
            "wrong_per_request": wrong / total, "correct_acceptance_rate": (len(accepted) - wrong) / total}


def select_threshold(curve, max_false_hit_rate):
    eligible = [point for point in curve if point["accepted"] and point["false_hit_rate"] <= max_false_hit_rate]
    return max(eligible, key=lambda point: (point["accepted"], point["threshold"])) if eligible else None


def get_vectors(rows, args):
    if args.vectors:
        return json.loads(args.vectors.read_text("utf-8"))
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "maxkb.settings")
    os.environ.setdefault("SERVER_NAME", "web")
    sys.path.insert(0, str(ROOT / "apps"))
    import django
    django.setup()
    from common.cache.model_profile import get_profile
    from models_provider.tools import get_model_instance_by_model_workspace_id
    model = get_model_instance_by_model_workspace_id(args.model_id, args.workspace)
    vectors = {}
    for row in rows:
        key = hashlib.sha256(row["question"].encode()).hexdigest()
        if key not in vectors:
            vectors[key] = [float(value) for value in model.embed_query(row["question"])]
    profile = get_profile(model)
    return {"schema_version": 1, "model_id": str(args.model_id), "workspace": args.workspace,
            "profile_hash": profile.query_hash, "call_mode": "embed_query", "vectors": vectors}


def plot_curve(curve, path, reviewed):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    figure, axis = plt.subplots(figsize=(8, 4.5))
    x = [point["threshold"] for point in curve]
    axis.plot(x, [point["acceptance_rate"] for point in curve], label="Acceptance rate")
    axis.plot(x, [point["false_hit_rate"] if point["false_hit_rate"] is not None else math.nan for point in curve],
              label="False hits / accepted")
    axis.plot(x, [point["wrong_per_request"] for point in curve], label="False hits / all requests", linestyle="--")
    title = "L2 dev threshold sweep" if reviewed else "L2 dev threshold sweep (unreviewed pilot)"
    axis.set(xlabel="Cosine similarity threshold", ylabel="Rate", ylim=(-0.02, 1.02), title=title)
    axis.grid(alpha=0.25)
    axis.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "eval/l2_dataset.jsonl")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--vectors", type=Path, help="Frozen embeddings JSON for repeatable offline runs")
    source.add_argument("--model-id", help="Use the configured MaxKB embedding model")
    parser.add_argument("--workspace", default="default")
    parser.add_argument("--output", type=Path, default=ROOT / "eval/results/week4/l2")
    parser.add_argument("--min-threshold", type=float, default=0.5)
    parser.add_argument("--step", type=float, default=0.005)
    parser.add_argument("--max-false-hit-rate", type=float, default=0.01)
    parser.add_argument("--require-human-review", action="store_true")
    parser.add_argument("--plot", action="store_true", help="Export a PNG using optional matplotlib")
    args = parser.parse_args()
    if not (math.isfinite(args.min_threshold) and -1 <= args.min_threshold <= 1
            and math.isfinite(args.step) and 0 < args.step <= 1 and 0 <= args.max_false_hit_rate <= 1):
        parser.error("Invalid threshold range, step or false-hit target")
    rows = load_rows(args.dataset)
    reviewed = all(row.get("human_reviewed") is True for row in rows)
    if args.require_human_review and not reviewed:
        parser.error("Dataset still contains annotations awaiting human review")
    vectors = get_vectors(rows, args)
    scores = score_rows(rows, vectors["vectors"])
    thresholds = [round(args.min_threshold + index * args.step, 12)
                  for index in range(math.floor((1 - args.min_threshold) / args.step) + 1)]
    if thresholds[-1] < 1:
        thresholds.append(1.0)
    dev = [row for row in scores if row["split"] == "dev"]
    test = [row for row in scores if row["split"] == "test"]
    curve = [metrics(dev, threshold) for threshold in thresholds]
    selected = select_threshold(curve, args.max_false_hit_rate)
    test_result = metrics(test, selected["threshold"]) if selected else None
    result = {"schema_version": 1, "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "annotation_status": "human_reviewed" if reviewed else "assistant_draft_pending_human_review",
        "anchor_count": sum(row["kind"] == "anchor" for row in rows),
        "selection": "maximum dev acceptance under false-hit target, highest threshold breaks ties",
        "target_false_hit_rate": args.max_false_hit_rate, "dev_curve": curve, "selected_dev": selected,
        "test_at_locked_threshold": test_result, "scores": scores,
        "embedding_profile": {key: value for key, value in vectors.items() if key != "vectors"}}
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in [("thresholds.json", result), ("vectors.json", vectors)]:
        (args.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf-8")
    lines = ["# L2 离线阈值实验", "", f"标注状态为 `{result['annotation_status']}`。候选库冻结为 {result['anchor_count']} 个原问题答案锚点。",
        "", "开发集扫描阈值并选择接受率最高的合格点，测试集仅计算锁定阈值。误命中率为错误接受数除以接受数，零接受时记为空。",
        "", "| 阈值 | 接受数 / 题数 | 接受率 | 错误接受数 | 误命中率 | 错误接受 / 全请求 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for point in curve:
        error = f"{point['false_hit_rate']:.2%}" if point["false_hit_rate"] is not None else "—"
        lines.append(f"| {point['threshold']:.3f} | {point['accepted']}/{point['query_count']} | "
                     f"{point['acceptance_rate']:.2%} | {point['wrong_accepted']} | {error} | {point['wrong_per_request']:.2%} |")
    lines += ["", "## 锁定阈值", "", "```json", json.dumps({"dev": selected, "test": test_result}, indent=2), "```", "",
        "样本数有限。助手草案的标签需经人工确认，阈值结果用于离线探索。"]
    if args.plot:
        plot_curve(curve, args.output / "threshold-curve.png", reviewed)
        lines += ["", "![开发集阈值曲线](threshold-curve.png)"]
    (args.output / "README.md").write_text("\n".join(lines) + "\n", "utf-8")
    print(json.dumps({"annotation_status": result["annotation_status"], "dev": selected, "test": test_result}, indent=2))


if __name__ == "__main__":
    main()
