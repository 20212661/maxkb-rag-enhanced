# coding=utf-8
"""Compare per-type retrieval metrics from two run_eval.py result files."""
import argparse
import json
import math
import sys
from pathlib import Path


METRICS = ("recall_at_1", "recall_at_5", "mrr")


def load_result(path):
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict) or not isinstance(result.get("by_type"), dict) or not result["by_type"]:
        raise ValueError(f"{path} must contain non-empty by_type evaluation metrics")
    for kind, metrics in result["by_type"].items():
        if not kind or not isinstance(metrics, dict):
            raise ValueError(f"{path} has an invalid question type")
        count = metrics.get("question_count")
        if type(count) is not int or count < 1:
            raise ValueError(f"{path} has an invalid question_count for {kind}")
        for key in METRICS:
            value = metrics.get(key)
            if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
                raise ValueError(f"{path} has an invalid {key} for {kind}")
    return result


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def compare_results(baseline, candidate, baseline_name="baseline", candidate_name="candidate"):
    lines = ["# 检索评测对比", "",
             f"基线为 {_cell(baseline_name)}，当前为 {_cell(candidate_name)}。变化量为当前减基线。", "",
             "| 题型 | 题数（基线 / 当前） | Recall@1（基线 → 当前） | Δ Recall@1（百分点） | "
             "Recall@5（基线 → 当前） | Δ Recall@5（百分点） | MRR（基线 → 当前） | Δ MRR |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for kind in sorted(baseline["by_type"].keys() | candidate["by_type"].keys()):
        before, after = baseline["by_type"].get(kind), candidate["by_type"].get(kind)
        counts = f"{before['question_count'] if before else '—'} / {after['question_count'] if after else '—'}"
        row = [_cell(kind), counts]
        for key in METRICS:
            scale = 1 if key == "mrr" else 100
            precision = 4 if key == "mrr" else 2
            suffix = "" if key == "mrr" else "%"
            left = f"{before[key] * scale:.{precision}f}{suffix}" if before else "—"
            right = f"{after[key] * scale:.{precision}f}{suffix}" if after else "—"
            delta = f"{(after[key] - before[key]) * scale:+.{precision}f}" if before and after else "—"
            row.extend([f"{left} → {right}", delta])
        lines.append("| " + " | ".join(row) + " |")
    if baseline.get("dataset_sha256") != candidate.get("dataset_sha256"):
        lines.extend(["", "两份结果的 dataset_sha256 不同，指标分别反映各自题集。"])
    if baseline.get("complete") is False or candidate.get("complete") is False:
        lines.extend(["", "输入包含尚未完成的评测结果，表格使用当前已记录的题目指标。"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path, help="Write Markdown to this file, otherwise print to stdout")
    args = parser.parse_args()
    try:
        report = compare_results(load_result(args.baseline), load_result(args.candidate),
                                 args.baseline.as_posix(), args.candidate.as_posix())
        if args.output:
            if args.output.resolve() in {args.baseline.resolve(), args.candidate.resolve()}:
                raise ValueError("Output must differ from the input result files")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report, encoding="utf-8")
        else:
            print(report, end="")
    except (OSError, ValueError) as error:
        parser.exit(2, f"{error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
