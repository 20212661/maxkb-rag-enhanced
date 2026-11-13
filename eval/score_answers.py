# coding=utf-8
"""Summarize explicitly entered blind-review scores after unblinding."""
import argparse
from collections import Counter
import html
import json
from pathlib import Path
import re
import statistics
import sys
import unicodedata

from run_answer_eval import OUTPUT, TYPES, cell, file_hash
from run_eval import write_result

SCORE_COLUMNS = ["归属正确", "内容正确", "拒答正确", "出处标注正确"]
PENDING = {"", "待复核", "未决", "?", "？"}
GROUP_NAMES = {False: "off", True: "on"}


def parse_score(value, *, component=False):
    value = value.strip()
    if value in PENDING or (component and value.upper() == "N/A"):
        return None
    if not re.fullmatch(r"(?:0(?:\.0+)?|0\.5(?:0+)?|1(?:\.0+)?)", value):
        raise ValueError(f"Unsupported score {value!r}")
    score = float(value)
    if component and score == 0.5:
        raise ValueError("Component columns use 0, 1, N/A or pending")
    return score


def parse_coverage(value):
    value = unicodedata.normalize("NFKC", value.strip())
    if value == "不适用":
        return {"all_retrieved": None, "found": 0, "expected": 0}
    match = re.fullmatch(r"(全部|部分|未检索到)\((\d+)/(\d+)\)", value)
    if not match:
        raise ValueError(f"Invalid source coverage {value!r}")
    label, found, expected = match.group(1), int(match.group(2)), int(match.group(3))
    if expected <= 0 or not 0 <= found <= expected:
        raise ValueError("Invalid source coverage counts")
    expected_label = "全部" if found == expected else ("部分" if found else "未检索到")
    if label != expected_label:
        raise ValueError("Source coverage label disagrees with counts")
    return {"all_retrieved": found == expected, "found": found, "expected": expected}


def read_review(path):
    header, reviews = None, []
    required = {"编号", "题目ID", "类型", "期望来源是否被检索到", "得分", "备注", *SCORE_COLUMNS}
    for line_number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
        if not line.startswith("|"):
            continue
        values = [html.unescape(value.strip()) for value in line.strip().strip("|").split("|")]
        if header is None:
            if required <= set(values):
                header = values
            continue
        if all(re.fullmatch(r":?-+:?", value) for value in values):
            continue
        if len(values) != len(header):
            raise ValueError(f"Review line {line_number} has {len(values)} cells, expected {len(header)}")
        review = dict(zip(header, values))
        if review["类型"] not in TYPES:
            raise ValueError(f"Unknown question type on line {line_number}")
        review["score"] = parse_score(review["得分"])
        review["components"] = {column: parse_score(review[column], component=True) for column in SCORE_COLUMNS}
        review["coverage"] = parse_coverage(review["期望来源是否被检索到"])
        if review["score"] == 0.5 and review["类型"] != "disambiguation":
            raise ValueError("Only disambiguation accepts half credit under this rubric")
        reviews.append(review)
    ids = [review["编号"] for review in reviews]
    if not header or not reviews or len(ids) != len(set(ids)):
        raise ValueError("Review must contain a nonempty table with unique blind IDs")
    return reviews


def join_metadata(reviews, mapping, stability):
    entries = mapping["entries"]
    if set(entries) != {review["编号"] for review in reviews}:
        raise ValueError("Review blind IDs and mapping entries differ")
    comparisons = {}
    for row in stability["comparisons"]:
        key = (row["tag"], row["id"])
        if key in comparisons:
            raise ValueError("Duplicate stability entry")
        comparisons[key] = row
    joined, seen = [], set()
    for review in reviews:
        entry = entries[review["编号"]]
        if entry["question_id"] != review["题目ID"] or entry["round"] != 1:
            raise ValueError("Mapping question or round disagrees with the blind review")
        include = entry["prompt_include_document_name"]
        if not isinstance(include, bool):
            raise ValueError("Mapping document-name switch must be boolean")
        group = GROUP_NAMES[include]
        key = (entry["tag"], entry["question_id"])
        if key not in comparisons:
            raise ValueError(f"Missing stability entry {key}")
        identity = (group, entry["question_id"])
        if identity in seen:
            raise ValueError("Duplicate question in a group")
        seen.add(identity)
        comparison = comparisons[key]
        if not isinstance(comparison["identical_answer"], bool) or not isinstance(comparison["review_required"], bool):
            raise ValueError("Stability flags must be boolean")
        joined.append({"blind_id": review["编号"], "question_id": review["题目ID"], "type": review["类型"],
                       "group": group, "tag": entry["tag"], "score": review["score"],
                       "components": review["components"], "note": review["备注"],
                       "coverage": review["coverage"], "coverage_label": review["期望来源是否被检索到"],
                       "text_identical_between_rounds": comparison["identical_answer"],
                       "stability_review_required": comparison["review_required"],
                       "text_similarity": comparison["text_similarity"],
                       "stability_reasons": comparison["reasons"]})
    ids = [{row["question_id"] for row in joined if row["group"] == group} for group in ("off", "on")]
    if ids[0] != ids[1] or not ids[0]:
        raise ValueError("Both groups must contain the same question IDs")
    for question_id in ids[0]:
        if len({r["type"] for r in joined if r["question_id"] == question_id}) != 1:
            raise ValueError("Question type differs between groups")
    return joined


def score_stats(rows):
    scores = [row["score"] for row in rows if row["score"] is not None]
    return {"question_count": len(rows), "scored_count": len(scores), "pending_count": len(rows) - len(scores),
            "mean_score": statistics.mean(scores) if scores else None,
            "full_credit_count": scores.count(1), "half_credit_count": scores.count(0.5),
            "zero_credit_count": scores.count(0)}


def source_stats(rows):
    all_retrieved = [r for r in rows if r["coverage"]["all_retrieved"] is True]
    missing = [r for r in rows if r["coverage"]["all_retrieved"] is False]
    outside = [r for r in rows if r["coverage"]["all_retrieved"] is None]
    generation_errors = [r["question_id"] for r in all_retrieved if r["score"] is not None and r["score"] < 1]
    return {"all_expected_sources_retrieved": score_stats(all_retrieved),
            "retrieved_but_generation_error_count": len(generation_errors),
            "retrieved_but_generation_error_question_ids": generation_errors,
            "expected_sources_missing": score_stats(missing),
            "missing_source_question_ids": [r["question_id"] for r in missing],
            "source_expectation_not_applicable": score_stats(outside)}


def summarize(rows):
    groups = {}
    for group in ("off", "on"):
        selected = [row for row in rows if row["group"] == group]
        groups[group] = {"tags": sorted({row["tag"] for row in selected}), "overall": score_stats(selected),
                         "by_type": {kind: score_stats([row for row in selected if row["type"] == kind])
                                     for kind in sorted(TYPES)},
                         "by_source": source_stats(selected),
                         "exact_text_change_count": sum(not r["text_identical_between_rounds"] for r in selected),
                         "stability_review_flag_count": sum(r["stability_review_required"] for r in selected)}
    pairs = []
    for question_id in sorted({row["question_id"] for row in rows}):
        pair = {row["group"]: row for row in rows if row["question_id"] == question_id}
        off, on = pair["off"], pair["on"]
        delta = on["score"] - off["score"] if off["score"] is not None and on["score"] is not None else None
        pairs.append({"question_id": question_id, "type": off["type"], "off": off, "on": on, "delta": delta})
    deltas = [p["delta"] for p in pairs if p["delta"] is not None]
    in_scope = [row for row in rows if row["coverage"]["expected"] > 0]
    no_expected_sources = bool(in_scope) and all(row["coverage"]["found"] == 0 for row in in_scope)
    return {"groups": groups, "pairs": pairs, "all_in_scope_expected_sources_absent": no_expected_sources,
            "paired_comparison": {"scored_pairs": len(deltas), "pending_pairs": len(pairs) - len(deltas),
                                  "mean_delta": statistics.mean(deltas) if deltas else None,
                                  "improved": sum(d > 0 for d in deltas), "declined": sum(d < 0 for d in deltas),
                                  "tied": sum(d == 0 for d in deltas)}}


def display_score(value):
    return "待复核" if value is None else f"{value:g}"


def write_reports(summary, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    write_result(output_dir / "score-summary.json", summary)
    lines = ["# 回答评分汇总", "", "得分来自已填写的盲评表，待复核项单列并从已决平均分分母排除。", ""]
    if summary["all_in_scope_expected_sources_absent"]:
        lines.extend(["**全部库内题的期望来源均未被检索到，应核查发布版本和知识库绑定。当前分数无法验证来源注入效果。**", ""])
    lines.extend([
             "| 题型 | 关闭组均分 | 已决/总题数 | 开启组均分 | 已决/总题数 |",
             "| --- | --- | --- | --- | --- |"])
    for kind in [*sorted(TYPES), "overall"]:
        stats = [summary["groups"][g]["overall"] if kind == "overall" else
                 summary["groups"][g]["by_type"][kind] for g in ("off", "on")]
        values = [kind]
        for stat in stats:
            mean = "待复核" if stat["mean_score"] is None else f"{stat['mean_score']:.4f}"
            values.extend([mean, f"{stat['scored_count']}/{stat['question_count']}"])
        lines.append("| " + " | ".join(values) + " |")
    paired = summary["paired_comparison"]
    lines.extend(["", "## 逐题配对", "",
                  f"已决配对{paired['scored_pairs']}题，待复核配对{paired['pending_pairs']}题。"
                  f"开启组提高{paired['improved']}题，下降{paired['declined']}题，相同{paired['tied']}题。", "",
                  "## 来源覆盖与错误", "",
                  "文档名覆盖作为检索诊断代理。全部期望文档均在引用列表、且得分低于1的项归入检索到但生成出错。"
                  "部分或全部期望文档缺失的项归入来源缺失组。具体条款召回及资料预算仍需结合证据检查。", "",
                  "| 组别 | 全部来源检索到 | 其中得分低于1 | 来源有缺失 | 来源缺失组均分 | 来源不适用 |",
                  "| --- | --- | --- | --- | --- | --- |"])
    for group in ("off", "on"):
        source = summary["groups"][group]["by_source"]
        missing_mean = source["expected_sources_missing"]["mean_score"]
        values = [group, source["all_expected_sources_retrieved"]["question_count"],
                  source["retrieved_but_generation_error_count"], source["expected_sources_missing"]["question_count"],
                  "待复核" if missing_mean is None else f"{missing_mean:.4f}",
                  source["source_expectation_not_applicable"]["question_count"]]
        lines.append("| " + " | ".join(map(str, values)) + " |")
    lines.extend(["", "## 两遍文本稳定性", "",
                  "| 组别 | 逐字不同对数 | 触发复核标记对数 |", "| --- | --- | --- |"])
    for group in ("off", "on"):
        stats = summary["groups"][group]
        lines.append(f"| {group} | {stats['exact_text_change_count']} | {stats['stability_review_flag_count']} |")
    lines.extend(["", "文本差异、复核标记和回答正确性为不同观测。第二遍的正确性波动需要同规则评分。", ""])
    (output_dir / "score-summary.md").write_text("\n".join(lines), encoding="utf-8")
    comparison = ["# 逐题对比", "",
                  "| 题目 | 题型 | 关闭得分 | 开启得分 | 变化 | 关闭来源覆盖 | 开启来源覆盖 | 关闭两遍文本 | 开启两遍文本 | 关闭复核标记 | 开启复核标记 | 备注 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for pair in summary["pairs"]:
        off, on = pair["off"], pair["on"]
        notes = "关闭组 " + off["note"] + " 开启组 " + on["note"]
        values = [pair["question_id"], pair["type"], display_score(off["score"]), display_score(on["score"]),
                  display_score(pair["delta"]), off["coverage_label"], on["coverage_label"],
                  "完全一致" if off["text_identical_between_rounds"] else "文字有差异",
                  "完全一致" if on["text_identical_between_rounds"] else "文字有差异",
                  "、".join(off["stability_reasons"]), "、".join(on["stability_reasons"]), notes]
        comparison.append("| " + " | ".join(cell(value) for value in values) + " |")
    (output_dir / "question-comparison.md").write_text("\n".join(comparison) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, default=OUTPUT / "blind-review.md")
    parser.add_argument("--mapping", type=Path, default=OUTPUT / "mapping.json")
    parser.add_argument("--stability", type=Path, default=OUTPUT / "stability.json")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    reviews = read_review(args.review)
    mapping = json.loads(args.mapping.read_text("utf-8"))
    stability = json.loads(args.stability.read_text("utf-8"))
    rows = join_metadata(reviews, mapping, stability)
    summary = summarize(rows)
    summary["inputs"] = {"review_sha256": file_hash(args.review), "mapping_sha256": file_hash(args.mapping),
                         "stability_sha256": file_hash(args.stability), "script_sha256": file_hash(Path(__file__))}
    summary["review_type_counts"] = dict(Counter(row["type"] for row in rows))
    summary["question_type_counts"] = dict(Counter(pair["type"] for pair in summary["pairs"]))
    write_reports(summary, args.output_dir)
    print(f"Summarized {len(reviews)} review rows and {len(summary['pairs'])} question pairs")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError) as error:
        print(f"Score summary failed: {error}", file=sys.stderr)
        raise SystemExit(2)
