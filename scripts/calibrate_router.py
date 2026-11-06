# coding=utf-8
"""
    @project: MaxKB
    @file: calibrate_router.py
    @desc: Report PDF text-layer metrics and automatic routing for a sample directory.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps"))

from common.utils.mineru_router import decide  # noqa: E402


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _expected_routes(directories):
    root = Path(__file__).resolve().parents[1]
    expected = {}
    manifests = {root / "eval/assets/manifest.json"}
    for directory in directories:
        manifests.update((directory / "manifest.json", directory.parent / "manifest.json"))
    dataset = root / "eval/dataset.jsonl"
    checked_text = set()
    if dataset.is_file():
        for line in dataset.read_text("utf-8").splitlines():
            for passage in json.loads(line).get("gold_passages", []):
                if passage.get("verification") == "source_text_checked":
                    checked_text.add(passage["document_name"])
    for manifest in sorted(manifests):
        if not manifest.is_file():
            continue
        data = json.loads(manifest.read_text("utf-8"))
        for item in data.get("documents", []) + data.get("cases", []):
            name = item.get("file", item.get("path"))
            if not name:
                continue
            label = item.get("expected_route", "")
            basis = "清单 expected_route"
            if item.get("text_layer") is False or item.get("kind") in {"simulated_scan", "illustrated_pages"}:
                label, basis = "mineru", "清单扫描/图片标记"
            elif item.get("text_layer") is True:
                label, basis = "native", "清单 text_layer=true"
            elif manifest == root / "eval/assets/manifest.json" and item.get("document_name") in checked_text:
                label, basis = "native", "评测 source_text_checked"
            if label not in {"mineru", "native"}:
                continue
            for base in (manifest.parent, manifest.parent.parent):
                candidate = (base / name).resolve()
                if candidate.is_file():
                    expected[candidate] = (label, basis)
                    break
    return expected


def _expected(path, expected):
    if path.resolve() in expected:
        return expected[path.resolve()]
    sidecar = path.with_suffix(".provenance.json")
    if sidecar.is_file():
        metadata = json.loads(sidecar.read_text("utf-8"))
        if metadata.get("text_layer") is False or metadata.get("kind") == "simulated_scan":
            return "mineru", "来源记录扫描标记"
        if metadata.get("text_layer") is True:
            return "native", "来源记录 text_layer=true"
    if re.search(r"(?:^|[_\W])(scan(?:ned)?|image(?:s|_panels)?)(?:[_\W]|$)|扫描|图片", path.stem, re.I):
        return "mineru", "文件名扫描/图片标记"
    if re.search(r"电子版|digital|text_pdf", path.stem, re.I):
        return "native", "文件名电子版标记"
    return "", ""


def calibrate(directories: list[Path], output: Path):
    from maxkb.const import CONFIG
    from common.utils.mineru_router import DEFAULTS

    expected = _expected_routes(directories)
    lines = ["# PDF 路由校准", "",
             "指标按实际完成的抽样页计算，页索引从 0 开始。时间预算为逐页检查的软预算。", "",
             "生效配置为 " + "，".join(f"`{key}={CONFIG.get(key, default)}`" for key, default in DEFAULTS.items()) + "。",
             "", "预期路由独立取自清单、评测文字核验记录、来源记录或明确文件名，依据不明时留空。", ""]
    false_positive, false_negative = [], []
    total = 0
    known = 0
    for directory in directories:
        files = sorted((path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"),
                       key=lambda path: path.as_posix())
        total += len(files)
        lines.extend([f"## {_cell(directory.as_posix())}", "", f"共 {len(files)} 份 PDF。", "",
                      "| 文件名 | 页数 | 平均非空白字数 | 低文字页占比 | 路由结果 | 预期路由 | 实际探测页 | 原因 | 预期依据 |",
                      "| --- | ---: | ---: | ---: | --- | --- | --- | --- | --- |"])
        for path in files:
            row, actual = _calibrate_file(path, directory, CONFIG)
            label, basis = _expected(path, expected)
            row.insert(5, label)
            row.append(basis)
            lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
            known += bool(label)
            entry = f"{_cell(path.as_posix())}（{row[7]}）"
            if label == "native" and actual == "mineru":
                false_positive.append(entry)
            if label == "mineru" and actual == "native":
                false_negative.append(entry)
        lines.append("")
    lines.extend(["## 误判清单", "", f"共 {total} 份 PDF，{known} 份有预期标签，{total - known} 份留空。", ""])
    for title, entries in (("电子版被判成 MinerU", false_positive), ("扫描件或图片 PDF 被判成原生", false_negative)):
        lines.extend([f"### {title}", ""] + ([f"- {entry}" for entry in entries] if entries else ["无。"]) + [""])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return total


def _calibrate_file(path, directory, config):
    try:
        with path.open("rb") as file:
            decision = decide(file, {}, config=config)
        metrics = decision["metrics"]
        average = metrics["avg_chars_per_page"]
        ratio = metrics["low_text_page_ratio"]
        row = [path.relative_to(directory).as_posix(),
               metrics["page_count"] if metrics["page_count"] is not None else "—",
               f"{average:.2f}" if average is not None else "—",
               f"{ratio:.2%}" if ratio is not None else "—",
               f"MinerU {decision['tier']}" if decision["use_mineru"] else "原生",
               ", ".join(map(str, metrics["probed_pages"])) or "—", decision["reason"]]
        actual = "mineru" if decision["use_mineru"] else "native"
    except OSError as error:
        row = [path.relative_to(directory).as_posix(), "—", "—", "—", "原生", "—", f"probe_failed ({error})"]
        actual = "native"
    return row, actual


def main():
    parser = argparse.ArgumentParser(description="校准目录内全部 PDF 的 MinerU 路由")
    parser.add_argument("directories", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("docs/router-calibration.md"))
    args = parser.parse_args()
    for directory in args.directories:
        if not directory.is_dir():
            parser.error(f"样本目录不存在 {directory}")
    count = calibrate(args.directories, args.output)
    print(f"已校准 {count} 份 PDF，报告保存到 {args.output}")


if __name__ == "__main__":
    main()
