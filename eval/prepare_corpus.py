# coding=utf-8
"""Import the versioned evaluation corpus through MaxKB's normal document APIs."""

import argparse
import getpass
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from run_eval import APIError, EVAL_DIR, MaxKBAPI, write_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("MAXKB_API_BASE", "http://127.0.0.1:8080/admin/api"))
    parser.add_argument("--workspace-id", default="default")
    parser.add_argument("--username", default=os.environ.get("MAXKB_USERNAME", "admin"))
    parser.add_argument("--embedding-model-id", default=os.environ.get("MAXKB_EMBEDDING_MODEL_ID"))
    parser.add_argument("--config", type=Path, default=EVAL_DIR / ".local.json")
    parser.add_argument("--wait-seconds", type=int, default=600)
    args = parser.parse_args()
    if args.wait_seconds <= 0:
        parser.error("--wait-seconds must be positive")
    manifest_path = EVAL_DIR / "assets/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    for item in manifest["documents"]:
        path = EVAL_DIR / "assets" / item["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"Corpus checksum mismatch for {path.name}")
    local = json.loads(args.config.read_text(encoding="utf-8")) if args.config.exists() else {}
    if local and (local["corpus_sha256"] != digest or local["base_url"] != args.base_url
                  or local["workspace_id"] != args.workspace_id):
        raise ValueError("Existing local state belongs to another corpus or server. Use a different --config path.")
    token, password = os.environ.get("MAXKB_TOKEN"), os.environ.get("MAXKB_PASSWORD")
    if not token and not password and sys.stdin.isatty():
        password = getpass.getpass("MaxKB password: ")
    api = MaxKBAPI(args.base_url, args.workspace_id, 180, token, args.username, password)
    models = api.request("GET", f"/workspace/{args.workspace_id}/model")
    candidates = [model for model in models if model["model_type"] == "EMBEDDING"]
    model_id = args.embedding_model_id or local.get("embedding_model", {}).get("id")
    if model_id:
        candidates = [model for model in candidates if model["id"] == model_id]
    if len(candidates) != 1:
        raise ValueError("Select one available embedding model with --embedding-model-id")
    model = {key: candidates[0].get(key) for key in ("id", "name", "model_name", "provider")}
    if local and local["embedding_model"] != model:
        raise ValueError("Embedding model changed. Use a new corpus configuration for a new index.")
    prefix = f"/workspace/{args.workspace_id}/knowledge"
    if not local:
        knowledge = api.request("POST", prefix + "/base", {
            "name": "Day45-检索评测-60题", "folder_id": "default",
            "desc": "公开文本、模拟扫描和漫画图文评测。标准答案仅在离线评测文件中。",
            "embedding_model_id": model["id"],
        })
        local = {"knowledge_id": knowledge["id"], "base_url": args.base_url,
                 "workspace_id": args.workspace_id, "corpus_sha256": digest,
                 "embedding_model": model, "documents": {}}
        write_result(args.config, local)
    knowledge_path = prefix + "/" + local["knowledge_id"]
    knowledge = api.request("GET", knowledge_path)
    if knowledge.get("embedding_model_id") != model["id"]:
        raise ValueError("Knowledge base embedding model does not match the local configuration")
    for item in manifest["documents"]:
        # Reconcile names before creation so a retry after a lost response does not duplicate a document.
        existing = api.request("GET", knowledge_path + "/document")
        same_name = [doc for doc in existing if doc["name"] == item["document_name"]]
        if len(same_name) > 1:
            raise ValueError(f'Duplicate document {item["document_name"]}')
        if same_name:
            document = same_name[0]
        else:
            parsed = api.split_file(local["knowledge_id"], EVAL_DIR / "assets" / item["file"], manifest["split_limit"])
            if len(parsed) != 1 or parsed[0]["name"] != item["document_name"]:
                raise APIError("Unexpected document split response")
            parsed = parsed[0]
            paragraphs = [{**paragraph, "title": paragraph.get("title") or Path(item["document_name"]).stem}
                          for paragraph in parsed["content"]]
            document = api.request("PUT", knowledge_path + "/document/batch_create", [{
                "name": item["document_name"], "source_file_id": parsed["source_file_id"], "paragraphs": paragraphs,
            }])[0]
        local["documents"][item["document_name"]] = {"id": document["id"], "sha256": item["sha256"]}
        write_result(args.config, local)
        print("Imported", item["document_name"], flush=True)
    deadline = time.monotonic() + args.wait_seconds
    while True:
        documents = api.request("GET", knowledge_path + "/document")
        expected = {item["document_name"] for item in manifest["documents"]}
        if {doc["name"] for doc in documents} != expected or len(documents) != len(expected):
            raise ValueError("Knowledge base document set differs from the versioned corpus")
        # TaskType.EMBEDDING is the rightmost status digit. Empty documents have no embedding tasks.
        failed = [doc["name"] for doc in documents if doc["paragraph_count"] and doc["status"][-1] in "345"]
        if failed:
            raise APIError("Document embedding failed for " + ", ".join(failed))
        pending = [doc for doc in documents if doc["paragraph_count"] and doc["status"][-1] != "2"]
        if not pending:
            break
        if time.monotonic() >= deadline:
            raise APIError("Timed out waiting for embedding. Run the same command to resume checking.")
        print("Waiting for embedding", len(pending), flush=True)
        time.sleep(5)
    local["index_summary"] = [{key: doc[key] for key in ("id", "name", "paragraph_count", "char_length", "status")}
                              for doc in documents]
    local["ready"] = True
    write_result(args.config, local)
    print("Ready", local["knowledge_id"], "paragraphs", sum(doc["paragraph_count"] for doc in documents))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (APIError, ValueError, OSError) as error:
        print(f"Corpus preparation failed: {error}", file=sys.stderr)
        raise SystemExit(2)
