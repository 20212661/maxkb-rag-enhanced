# coding=utf-8
"""Ask a reviewed answer set through public HTTP chat, then prepare blinded human review."""
import argparse
from difflib import SequenceMatcher
import getpass
import hashlib
import html
import json
import os
from pathlib import Path
import random
import re
import secrets
import sys
import time
from datetime import datetime, timezone

from run_eval import APIError, MaxKBAPI, write_result

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval"
OUTPUT = EVAL / "results/week5/answer"
TYPES = {"attribution", "disambiguation", "cross_unit_trap", "out_of_scope"}
RUBRIC = EVAL / "answer_rubric.md"


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_questions(path):
    questions = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    ids = [q.get("id") for q in questions]
    if not questions or len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i for i in ids):
        raise ValueError("Questions need unique nonempty string IDs")
    corpus = (EVAL / "assets/corpus").resolve()
    for question in questions:
        if question.get("type") not in TYPES or not question.get("question", "").strip():
            raise ValueError(f"Invalid question {question['id']}")
        if not isinstance(question.get("key_points"), list) or not question["key_points"]:
            raise ValueError(f"Missing key points for {question['id']}")
        unit = question.get("expected_unit")
        if question["type"] == "out_of_scope":
            if unit is not None:
                raise ValueError("Out-of-scope expected_unit must be null")
        elif not unit or not isinstance(unit, (str, list)):
            raise ValueError(f"Invalid expected_unit for {question['id']}")
        sources = question.get("verification", {}).get("sources", [])
        if question["type"] != "out_of_scope" and not sources:
            raise ValueError(f"Missing source verification for {question['id']}")
        checks = [*sources, *(row["source"] for row in question["verification"].get("comparison", []))]
        for source in checks:
            file = (ROOT / source["file"]).resolve()
            if not file.is_relative_to(corpus) or file_hash(file) != source["sha256"]:
                raise ValueError(f"Invalid corpus path or changed source for {question['id']}")
    return questions


class PublicChatAPI(MaxKBAPI):
    def __init__(self, base_url, access_token, timeout):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = None
        self.token = self.request("POST", "/auth/anonymous", {"access_token": access_token})


def source_coverage(question, document_names):
    expected = sorted({s["document_name"] for s in question["verification"]["sources"] if s.get("expected", True)})
    found = sorted(set(expected) & set(document_names))
    return {"expected_documents": expected, "retrieved_expected_documents": found,
            "expected_source_retrieved": set(found) == set(expected) if expected else None,
            "expected_source_count": len(expected), "retrieved_expected_source_count": len(found)}


def wait_for_record(admin, path, record_id, timeout):
    deadline = time.monotonic() + timeout
    while True:
        try:
            record = admin.request("GET", path)
            ready = (isinstance(record, dict) and record.get("id") == record_id and record.get("answer_text")
                     and "paragraph_list" in record and "execution_details" in record
                     and record.get("message_tokens") is not None and record.get("answer_tokens") is not None)
            if ready:
                return record
        except APIError:
            pass
        if time.monotonic() >= deadline:
            raise APIError("Chat record did not finish within the metadata timeout")
        time.sleep(0.2)


def ask_question(chat, admin, app_path, question, record_timeout):
    row = {"id": question["id"], "type": question["type"], "question": question["question"],
           "status": "error", "answer": None, "document_names": [], "evidence": [],
           "input_tokens": None, "output_tokens": None, "total_tokens": None, "cache_hit": None}
    try:
        started = time.perf_counter()
        chat_id = chat.request("GET", "/open")
        row.update(chat_id=chat_id, session_creation_ms=(time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        answer = chat.request("POST", f"/chat_message/{chat_id}", {
            "message": question["question"], "stream": False, "re_chat": False})
        row["response_ms"] = (time.perf_counter() - started) * 1000
        row["http_status"] = 200
        if not isinstance(answer, dict) or not answer.get("is_end") or not answer.get("content"):
            raise APIError("Chat returned an incomplete or empty answer")
        row.update(answer=answer["content"], record_id=answer["id"])
        started = time.perf_counter()
        record = wait_for_record(admin, f"{app_path}/chat/{chat_id}/chat_record/{answer['id']}",
                                 answer["id"], record_timeout)
        evidence = record["paragraph_list"]
        cache_hit = any(step.get("hit") is True and step.get("level") == "L1"
                        for step in record["execution_details"])
        row.update(evidence=evidence, document_names=sorted({p["document_name"] for p in evidence}),
                   input_tokens=record["message_tokens"], output_tokens=record["answer_tokens"],
                   cache_hit=cache_hit, metadata_wait_ms=(time.perf_counter() - started) * 1000,
                   execution_details=record["execution_details"])
        row["total_tokens"] = row["input_tokens"] + row["output_tokens"]
        if cache_hit:
            raise APIError("L1 hit observed during answer evaluation; disable L1 in the web service")
        row["status"] = "ok"
    except APIError as error:
        row["error"] = str(error)
    row.update(source_coverage(question, row["document_names"]))
    return row


def latest_version(admin, app_path):
    versions = admin.request("GET", app_path + "/application_version")
    if not versions:
        raise ValueError("Application has no published version")
    return versions[0]["id"]


def validate_runtime(runtime, application_id, temperature, expected_knowledge_id):
    if runtime.get("application_id") != application_id or runtime.get("qa_l1_cache_enabled") is not False:
        raise ValueError("Runtime manifest must identify this application and disabled L1")
    if not isinstance(runtime.get("prompt_include_document_name"), bool):
        raise ValueError("Runtime manifest needs a boolean prompt_include_document_name")
    if runtime.get("model_params_setting", {}).get("temperature") != temperature:
        raise ValueError("Published temperature differs from --temperature")
    knowledge_ids = runtime.get("knowledge_ids")
    if not isinstance(knowledge_ids, list) or not knowledge_ids or expected_knowledge_id not in knowledge_ids:
        raise ValueError("Published knowledge binding is empty or omits the answer corpus knowledge base")


def run(args):
    questions = load_questions(args.dataset)
    runtime = json.loads(args.runtime_config.read_text("utf-8"))
    manifest = json.loads((EVAL / "assets/answer_manifest.json").read_text("utf-8"))
    validate_runtime(runtime, args.application_id, args.temperature, manifest["knowledge_id"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.tag):
        raise ValueError("Tag must contain letters, digits, underscores or hyphens")
    path = args.output_dir / (args.tag + ".json")
    if path.exists():
        raise ValueError("Result tag already exists; use a fresh --tag")
    token, password = os.environ.get("MAXKB_TOKEN"), os.environ.get("MAXKB_PASSWORD")
    if not token and not password and sys.stdin.isatty():
        password = getpass.getpass("MaxKB password: ")
    admin = MaxKBAPI(args.base_url, args.workspace_id, args.timeout, token, args.username, password)
    app_path = f"/workspace/{args.workspace_id}/application/{args.application_id}"
    if latest_version(admin, app_path) != runtime.get("published_version_id"):
        raise ValueError("Published application version differs from the runtime manifest")
    access = admin.request("GET", app_path + "/access_token")
    chat = PublicChatAPI(args.chat_base_url, access["access_token"], args.timeout)
    result = {"schema_version": 1, "tag": args.tag, "created_at": datetime.now(timezone.utc).isoformat(),
              "dataset_sha256": file_hash(args.dataset), "rubric_sha256": file_hash(RUBRIC),
              "manifest_sha256": file_hash(EVAL / "assets/answer_manifest.json"),
              "evaluator_sha256": file_hash(Path(__file__)), "configuration": runtime,
              "runtime_configuration_source": "operator-supplied web launch manifest, latest version checked via HTTP",
              "latency_scope": "POST public chat_message to complete nonstreaming answer, "
                               "excluding session and metadata",
              "token_scope": "framework ChatRecord message_tokens and answer_tokens", "rounds": [], "complete": False}
    for round_number in range(1, args.rounds + 1):
        if latest_version(admin, app_path) != runtime["published_version_id"]:
            raise ValueError("Published version changed during the experiment")
        rows = []
        result["rounds"].append({"round": round_number, "queries": rows})
        for question in questions:
            row = ask_question(chat, admin, app_path, question, args.record_timeout)
            rows.append(row)
            write_result(path, result)
            print(f"{args.tag} round={round_number} {row['id']} {row['status']}", flush=True)
    if latest_version(admin, app_path) != runtime["published_version_id"]:
        raise ValueError("Published version changed during the experiment")
    result["complete"] = True
    write_result(path, result)
    return int(any(q["status"] != "ok" for r in result["rounds"] for q in r["queries"]))


def coverage_label(row):
    count, found = row["expected_source_count"], row["retrieved_expected_source_count"]
    if not count:
        return "不适用"
    label = "全部" if found == count else ("部分" if found else "未检索到")
    return f"{label}（{found}/{count}）"


def cell(value):
    return html.escape(str(value)).replace("|", "&#124;").replace("\r", "").replace("\n", "<br>")


def stability_comparison(result, threshold):
    first, second = ({q["id"]: q for q in r["queries"]} for r in result["rounds"][:2])
    comparisons = []
    for item_id, before in first.items():
        after = second[item_id]
        a, b = before.get("answer") or "", after.get("answer") or ""
        normalized_a, normalized_b = re.sub(r"\s+", "", a), re.sub(r"\s+", "", b)
        similarity = SequenceMatcher(None, normalized_a, normalized_b, autojunk=False).ratio()
        reasons = []
        if before["status"] != "ok" or after["status"] != "ok":
            reasons.append("request_failed")
        if similarity < threshold:
            reasons.append("text_similarity_below_threshold")
        if re.findall(r"\d+(?:\.\d+)?", a) != re.findall(r"\d+(?:\.\d+)?", b):
            reasons.append("numbers_changed")
        if before["document_names"] != after["document_names"]:
            reasons.append("citation_documents_changed")
        comparisons.append({"tag": result["tag"], "id": item_id, "text_similarity": similarity,
                            "identical_answer": a == b, "review_required": bool(reasons), "reasons": reasons,
                            "first_answer": a, "second_answer": b})
    return comparisons


def blind(args):
    runs = [json.loads(path.read_text("utf-8")) for path in args.runs]
    if len(runs) != 2 or {r["configuration"]["prompt_include_document_name"] for r in runs} != {False, True}:
        raise ValueError("Blinding requires one document-name-off run and one on run")
    comparable = ("dataset_sha256", "rubric_sha256", "manifest_sha256", "evaluator_sha256")
    for key in comparable:
        if runs[0][key] != runs[1][key]:
            raise ValueError(f"Runs have different {key}")
    settings = [{k: v for k, v in r["configuration"].items()
                 if k not in ("prompt_include_document_name", "observed_at", "web_pid")} for r in runs]
    if settings[0] != settings[1]:
        raise ValueError("Runs differ in configuration beyond the document-name switch")
    for result in runs:
        if not result["configuration"].get("knowledge_ids"):
            raise ValueError("Run has an empty published knowledge binding and cannot support source comparison")
        if not result["complete"] or len(result["rounds"]) != 2:
            raise ValueError("Each run must contain two complete rounds")
        ids = [{q["id"] for q in r["queries"]} for r in result["rounds"]]
        if ids[0] != ids[1] or len(ids[0]) != len(result["rounds"][0]["queries"]):
            raise ValueError("Question sets differ between rounds")
    if {q["id"] for q in runs[0]["rounds"][0]["queries"]} != {
            q["id"] for q in runs[1]["rounds"][0]["queries"]}:
        raise ValueError("Question sets differ between groups")
    entries = [(result, q) for result in runs for q in result["rounds"][0]["queries"]]
    seed = args.seed if args.seed is not None else secrets.randbits(64)
    random.Random(seed).shuffle(entries)
    lines = ["# 回答盲评", "", "依据 answer_rubric.md 人工评分。空白评分列由人工填写。", "",
             "| 编号 | 题目ID | 类型 | 问题 | 回答 | 引用文档 | 期望来源是否被检索到 | 归属正确 | 内容正确 | 拒答正确 | 出处标注正确 | 得分 | 备注 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    mapping = {"seed": seed, "dataset_sha256": runs[0]["dataset_sha256"], "entries": {}}
    for index, (result, row) in enumerate(entries, 1):
        blind_id = f"B{index:03d}"
        values = [blind_id, row["id"], row["type"], row["question"], row["answer"] or row.get("error"),
                  "、".join(row["document_names"]), coverage_label(row), "", "", "", "", "", ""]
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
        mapping["entries"][blind_id] = {"tag": result["tag"], "round": 1, "question_id": row["id"],
            "record_id": row.get("record_id"), "status": row["status"],
            "prompt_include_document_name": result["configuration"]["prompt_include_document_name"]}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "blind-review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_result(args.output_dir / "mapping.json", mapping)
    comparisons = [row for result in runs for row in stability_comparison(result, args.stability_threshold)]
    write_result(args.output_dir / "stability.json", {
        "threshold": args.stability_threshold, "comparisons": comparisons})
    review = ["# 两遍回答稳定性复核", "", "标记依据为文本相似度、数字及引用文档变化，需人工复核。", "",
              "| 组别 | 题目ID | 文本相似度 | 需复核原因 |", "| --- | --- | --- | --- |"]
    for row in comparisons:
        review.append(f"| {cell(row['tag'])} | {row['id']} | {row['text_similarity']:.4f} | "
                      f"{cell('、'.join(row['reasons']))} |")
    for row in comparisons:
        if row["review_required"]:
            review.extend(["", f"## {row['tag']} / {row['id']}", "", "### 第1遍", "", row["first_answer"],
                           "", "### 第2遍", "", row["second_answer"]])
    (args.output_dir / "stability.md").write_text("\n".join(review) + "\n", encoding="utf-8")
    print(f"Saved {len(entries)} blinded first-round answers and {len(comparisons)} stability comparisons")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    runner = modes.add_parser("run")
    runner.add_argument("--tag", required=True)
    runner.add_argument("--application-id", required=True)
    runner.add_argument("--runtime-config", type=Path, required=True)
    runner.add_argument("--dataset", type=Path, default=EVAL / "answer_set.jsonl")
    runner.add_argument("--base-url", default="http://127.0.0.1:8080/admin/api")
    runner.add_argument("--chat-base-url", default="http://127.0.0.1:8080/chat/api")
    runner.add_argument("--workspace-id", default="default")
    runner.add_argument("--username", default="admin")
    runner.add_argument("--timeout", type=float, default=120)
    runner.add_argument("--record-timeout", type=float, default=30)
    runner.add_argument("--temperature", type=float, default=0)
    runner.add_argument("--rounds", type=int, choices=[2], default=2)
    runner.add_argument("--output-dir", type=Path, default=OUTPUT)
    reviewer = modes.add_parser("blind")
    reviewer.add_argument("--runs", type=Path, nargs=2, required=True)
    reviewer.add_argument("--seed", type=int)
    reviewer.add_argument("--stability-threshold", type=float, default=0.75)
    reviewer.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.mode == "blind" and not 0 <= args.stability_threshold <= 1:
        parser.error("--stability-threshold must be between 0 and 1")
    return run(args) if args.mode == "run" else blind(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (APIError, ValueError, OSError, KeyError) as error:
        print(f"Answer evaluation failed: {error}", file=sys.stderr)
        raise SystemExit(2)
