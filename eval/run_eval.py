# coding=utf-8
"""Evaluate MaxKB retrieval against source-anchored JSONL annotations."""

import argparse
import getpass
import hashlib
import json
import math
import os
import platform
import re
import statistics
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


EVAL_DIR = Path(__file__).resolve().parent
QUESTION_TYPES = {"事实型", "跨段落型", "表格型", "扫描件型", "图片型"}
NORMALIZATION_VERSION = 2


class APIError(RuntimeError):
    pass


class MaxKBAPI:
    def __init__(self, base_url, workspace_id="default", timeout=60, token=None, username="admin", password=None):
        self.base_url = base_url.rstrip("/")
        self.workspace_id = workspace_id
        self.timeout = timeout
        self.token = token
        if not self.token:
            if not password:
                raise APIError("Set MAXKB_TOKEN or MAXKB_PASSWORD, or enter a password in an interactive terminal.")
            self.token = self.request("POST", "/user/login", {"username": username, "password": password})["token"]

    def request(self, method, path, payload=None, *, body=None, content_type=None):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = body
        if content_type:
            headers["Content-Type"] = content_type
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            raise APIError(f"{method} {path} returned HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise APIError(f"{method} {path} failed ({type(error).__name__})") from error
        if not isinstance(result, dict) or result.get("code") != 200:
            code = result.get("code") if isinstance(result, dict) else "invalid JSON response"
            raise APIError(f"{method} {path} returned API error {code}")
        return result.get("data")

    def split_file(self, knowledge_id, path, limit):
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in {"limit": str(limit), "with_filter": "true"}.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                      f'filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode("utf-8"))
        parts.extend([path.read_bytes(), f"\r\n--{boundary}--\r\n".encode()])
        return self.request("POST", f"/workspace/{self.workspace_id}/knowledge/{knowledge_id}/document/split",
                            body=b"".join(parts), content_type=f"multipart/form-data; boundary={boundary}")

    def hit_test(self, knowledge_id, question, similarity, search_mode):
        path = f"/workspace/{self.workspace_id}/knowledge/{knowledge_id}/hit_test"
        hits = self.request("POST", path, {
            "query_text": question, "top_number": 5, "similarity": similarity, "search_mode": search_mode,
        })
        if not isinstance(hits, list) or any(not isinstance(hit, dict) for hit in hits):
            raise APIError("hit_test returned an invalid paragraph list")
        return sorted(hits, key=ranking_score, reverse=True)[:5]


def ranking_score(hit):
    value = hit.get("comprehensive_score", hit.get("similarity", 0))
    try:
        score = float(value)
    except (TypeError, ValueError) as error:
        raise APIError("hit_test returned an invalid ranking score") from error
    if not math.isfinite(score):
        raise APIError("hit_test returned a non-finite ranking score")
    return score


def _normalize_math(match):
    value = re.sub(r"\\(?:mathrm|text|operatorname)\s*\{\s*([a-z0-9]+)\s*\}", r"\1", match.group(1))
    value = re.sub(r"\^\s*(?:\{\s*\\circ\s*\}|\\circ(?![a-z]))", "°", value)
    symbols = {"sim": "~", "times": "×", "leq": "≤", "geq": "≥", "pm": "±", "div": "÷", "cdot": "·"}
    value = re.sub(r"\\(sim|times|leq|geq|pm|div|cdot)(?![a-z])", lambda item: symbols[item.group(1)], value)
    # Preserve unsupported expressions, including their delimiters.
    return match.group(0) if re.search(r"[\\{}^_]", value) else value


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    for pattern in (r"\$\$([^$]+)\$\$", r"(?<!\$)\$([^$]+)\$(?!\$)", r"\\\((.*?)\\\)", r"\\\[(.*?)\\\]"):
        text = re.sub(pattern, _normalize_math, text, flags=re.DOTALL)
    text = re.sub(r"[\s\u200b\ufeff]+", "", text)
    return text.translate(str.maketrans({"∕": "/", "∼": "~"}))


def load_dataset(path):
    questions = []
    seen_ids = set()
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError(f"Line {line_number} must be a JSON object")
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id or item_id in seen_ids:
                raise ValueError(f"Line {line_number} has an invalid or duplicate id")
            if not isinstance(item.get("question"), str) or not item["question"].strip():
                raise ValueError(f"{item_id} has no question")
            if item.get("type") not in QUESTION_TYPES:
                raise ValueError(f"{item_id} has an unsupported question type")
            passages = item.get("gold_passages")
            if not isinstance(passages, list) or not passages:
                raise ValueError(f"{item_id} must have at least one gold passage")
            for passage in passages:
                if not isinstance(passage, dict):
                    raise ValueError(f"{item_id} has an invalid gold passage")
                if not isinstance(passage.get("document_name"), str) or not passage["document_name"].strip():
                    raise ValueError(f"{item_id} has no gold document name")
                sentence = passage.get("key_sentence")
                if not isinstance(sentence, str) or not normalize(sentence):
                    raise ValueError(f"{item_id} has an empty gold sentence")
            seen_ids.add(item_id)
            questions.append(item)
    if not questions:
        raise ValueError("The evaluation dataset is empty")
    return questions


def score_question(question, hits):
    matched = []
    for hit in hits:
        document = normalize(hit.get("document_name", ""))
        content = normalize(hit.get("content", ""))
        matched.append([
            index for index, passage in enumerate(question["gold_passages"])
            if document == normalize(passage["document_name"])
            and normalize(passage["key_sentence"]) in content
        ])
    gold_count = len(question["gold_passages"])
    top1 = set(matched[0]) if matched else set()
    top5 = {index for indexes in matched for index in indexes}
    first_rank = next((rank for rank, indexes in enumerate(matched, 1) if indexes), None)
    return {
        "recall_at_1": len(top1) / gold_count,
        "recall_at_5": len(top5) / gold_count,
        "reciprocal_rank": 1 / first_rank if first_rank else 0.0,
        "first_relevant_rank": first_rank,
        "all_gold_in_top5": len(top5) == gold_count,
        "matched_gold_indices_by_rank": matched,
    }


def summarize(records):
    return {
        "question_count": len(records),
        "error_count": sum(record["status"] == "error" for record in records),
        "recall_at_1": statistics.mean(record["recall_at_1"] for record in records),
        "recall_at_5": statistics.mean(record["recall_at_5"] for record in records),
        "mrr": statistics.mean(record["reciprocal_rank"] for record in records),
        "mean_retrieval_ms": statistics.mean(record["retrieval_ms"] for record in records),
        "all_gold_top5_rate": statistics.mean(record["all_gold_in_top5"] for record in records),
    }


def write_result(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_chat_application(api, application_id, knowledge_id):
    application = api.request("GET", f"/workspace/{api.workspace_id}/application/{application_id}")
    if application.get("type") != "SIMPLE" or not application.get("model_id"):
        raise APIError("End-to-end timing requires a SIMPLE application with a configured conversation model")
    knowledge_ids = application.get("knowledge_id_list") or [
        item["id"] for item in application.get("knowledge_list", [])
    ]
    if knowledge_id not in knowledge_ids:
        raise APIError("The conversation application must be linked to the evaluated knowledge base")


def time_chat(api, application_id, question):
    record = {"id": question["id"], "status": "error", "response_ms": None}
    try:
        chat_id = api.request("GET", f"/workspace/{api.workspace_id}/application/{application_id}/open")
        start = time.perf_counter()
        answer = api.request("POST", f"/chat_message/{chat_id}", {
            "message": question["question"], "stream": False, "re_chat": False,
        })
        record["response_ms"] = (time.perf_counter() - start) * 1000
        if not isinstance(answer, dict) or not answer.get("is_end") or not answer.get("content"):
            raise APIError("Chat did not return a complete non-empty answer")
        record["status"] = "ok"
        record["answer"] = answer
    except APIError as error:
        record["error"] = str(error)
    return record


def summarize_chat(records):
    successful = [record["response_ms"] for record in records if record["status"] == "ok"]
    return {
        "status": "measured" if records else "not_run",
        "reason": None if records else "No conversation application selected; LLM configuration remains deferred.",
        "question_count": len(records),
        "error_count": sum(record["status"] == "error" for record in records),
        "mean_response_ms": statistics.mean(successful) if successful else None,
        "timing_scope": "POST chat_message through complete non-streaming response, excluding session creation",
        "queries": records,
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=EVAL_DIR / "dataset.jsonl")
    parser.add_argument("--output", type=Path, default=EVAL_DIR / "results/baseline.json")
    parser.add_argument("--config", type=Path, default=EVAL_DIR / ".local.json")
    parser.add_argument("--base-url", default=os.environ.get("MAXKB_API_BASE", "http://127.0.0.1:8080/admin/api"))
    parser.add_argument("--workspace-id", default="default")
    parser.add_argument("--knowledge-id", default=os.environ.get("MAXKB_EVAL_KNOWLEDGE_ID"))
    parser.add_argument("--username", default=os.environ.get("MAXKB_USERNAME", "admin"))
    parser.add_argument("--similarity", type=float, default=0.55)
    parser.add_argument("--search-mode", choices=["embedding", "keywords", "blend"], default="embedding")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--application-id", help="Also time complete non-streaming debug chat responses")
    args = parser.parse_args()
    maximum = 2 if args.search_mode == "blend" else 1
    if not math.isfinite(args.similarity) or not 0 <= args.similarity <= maximum:
        parser.error(f"--similarity must be between 0 and {maximum}")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def main():
    args = parse_args()
    questions = load_dataset(args.dataset)
    local = json.loads(args.config.read_text(encoding="utf-8")) if args.config.exists() else {}
    knowledge_id = args.knowledge_id or local.get("knowledge_id")
    if not knowledge_id:
        raise ValueError("Pass --knowledge-id or run eval/prepare_corpus.py first")
    token = os.environ.get("MAXKB_TOKEN")
    password = os.environ.get("MAXKB_PASSWORD")
    if not token and not password and sys.stdin.isatty():
        password = getpass.getpass("MaxKB password: ")
    api = MaxKBAPI(args.base_url, args.workspace_id, args.timeout, token, args.username, password)
    knowledge_path = f"/workspace/{args.workspace_id}/knowledge/{knowledge_id}"
    knowledge = api.request("GET", knowledge_path)
    index_summary = None
    corpus_digest = None
    embedding_model = None
    if local and knowledge_id == local.get("knowledge_id"):
        if local["base_url"] != args.base_url or local["workspace_id"] != args.workspace_id:
            raise ValueError("Local configuration belongs to another server or workspace")
        if not local.get("ready"):
            raise ValueError("Run prepare_corpus.py until the index is ready")
        manifest_path = EVAL_DIR / "assets/manifest.json"
        corpus_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        if corpus_digest != local["corpus_sha256"]:
            raise ValueError("Corpus manifest changed. Prepare a new evaluation knowledge base.")
        embedding_model = local["embedding_model"]
        if knowledge.get("embedding_model_id") != embedding_model["id"]:
            raise ValueError("Knowledge base embedding model changed")
        index_summary = api.request("GET", knowledge_path + "/document")
        actual = {doc["name"]: doc["id"] for doc in index_summary}
        expected = {name: doc["id"] for name, doc in local["documents"].items()}
        if actual != expected or len(index_summary) != len(expected):
            raise ValueError("Knowledge base document set changed")
        if any(doc["paragraph_count"] and doc["status"][-1] != "2" for doc in index_summary):
            raise ValueError("Document embedding is incomplete")
    if args.application_id:
        validate_chat_application(api, args.application_id, knowledge_id)
    if not args.no_warmup:
        api.hit_test(knowledge_id, "信息系统安全管理评测预热", args.similarity, args.search_mode)
    records = []
    chat_records = []
    for question in questions:
        start = time.perf_counter()
        hits = []
        status, error_message = "ok", None
        try:
            hits = api.hit_test(knowledge_id, question["question"], args.similarity, args.search_mode)
        except APIError as error:
            status, error_message = "error", str(error)
        elapsed = (time.perf_counter() - start) * 1000
        record = {"id": question["id"], "type": question["type"], "question": question["question"],
                  "status": status, "error": error_message, "retrieval_ms": elapsed,
                  **score_question(question, hits), "top5": hits}
        records.append(record)
        if args.application_id:
            chat_records.append(time_chat(api, args.application_id, question))
        print(f'{question["id"]} {question["type"]} R@5={record["recall_at_5"]:.2f} '
              f'MRR={record["reciprocal_rank"]:.3f} {elapsed:.1f}ms', flush=True)
    result = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "complete": len(records) == len(questions),
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "evaluator_runtime": {"python": platform.python_version(), "platform": platform.platform()},
        "configuration": {"base_url": args.base_url, "workspace_id": args.workspace_id,
                          "knowledge_id": knowledge_id, "search_mode": args.search_mode, "top_k": 5,
                          "similarity": args.similarity, "warmup_excluded": not args.no_warmup,
                          "ranking": "comprehensive_score descending, matching the UI",
                          "matching": "exact document plus NFKC/case/whitespace, slash and supported LaTeX "
                                      "symbol-normalized sentence containment",
                          "normalization_version": NORMALIZATION_VERSION,
                          "corpus_sha256": corpus_digest, "embedding_model": embedding_model,
                          "latency_scope": "sequential HTTP hit_test including query embedding, search and JSON parsing",
                          "index_summary": [{key: doc[key] for key in
                                             ("id", "name", "paragraph_count", "char_length", "status")}
                                            for doc in index_summary] if index_summary is not None else None},
        "metrics": summarize(records),
        "by_type": {kind: summarize([record for record in records if record["type"] == kind])
                    for kind in sorted({record["type"] for record in records})},
        "type_counts": dict(Counter(question["type"] for question in questions)),
        "end_to_end": summarize_chat(chat_records),
        "queries": records,
    }
    write_result(args.output, result)
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    print(f"Saved {args.output}")
    return int(bool(result["metrics"]["error_count"] or result["end_to_end"].get("error_count")))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (APIError, ValueError, OSError) as error:
        print(f"Evaluation failed: {error}", file=sys.stderr)
        raise SystemExit(2)
