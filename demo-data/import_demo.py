#!/usr/bin/env python3
"""Import the public scan through normal HTTP APIs and verify a cited answer."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
from run_eval import APIError, MaxKBAPI, write_result  # noqa: E402

DATA = Path(__file__).resolve().parent
KNOWLEDGE_NAME = "Demo-公开制度扫描件"
APPLICATION_NAME = "Demo-带引用制度问答"
MODEL_NAME = "Demo-OpenAI-compatible"


def named(rows, name):
    matches = [row for row in rows if row.get("name") == name]
    if len(matches) > 1:
        raise ValueError(f"Duplicate managed demo resource {name}")
    return matches[0] if matches else None


def wait_documents(api, path, expected, timeout):
    deadline = time.monotonic() + timeout
    while True:
        documents = api.request("GET", path + "/document")
        if {row["name"] for row in documents} != set(expected):
            raise ValueError("Demo knowledge contains an unexpected document set")
        pending = []
        for row in documents:
            parse_status = row.get("mineru_parse_status", row.get("parse_status"))
            if parse_status in {"failed", "fallback"}:
                raise APIError(f"Scan did not complete through MinerU: {row['name']} ({parse_status})")
            if parse_status in {"pending", "running"} or not row["paragraph_count"]:
                pending.append(row["name"])
                continue
            status = str(row["status"])[-1]
            if status in "345":
                raise APIError(f"Embedding failed for {row['name']}")
            if status != "2":
                pending.append(row["name"])
        if not pending:
            return documents
        if time.monotonic() >= deadline:
            raise APIError("Timed out waiting for MinerU/embedding. Run the import again to resume.")
        print("Waiting for parsing and embedding", ", ".join(pending), flush=True)
        time.sleep(5)


def prepare_model(api, prefix):
    payload = {
        "name": MODEL_NAME, "provider": os.environ.get("LLM_PROVIDER", "model_openai_provider"),
        "model_type": "LLM", "model_name": os.environ["LLM_MODEL_NAME"],
        "credential": {"api_base": os.environ["LLM_API_BASE"], "api_key": os.environ["LLM_API_KEY"]},
        "model_params_form": [
            {"field": "temperature", "label": "Temperature", "required": True,
             "default_value": float(os.environ.get("LLM_TEMPERATURE", "0.2"))},
            {"field": "max_tokens", "label": "Max tokens", "required": True,
             "default_value": int(os.environ.get("LLM_MAX_TOKENS", "512"))},
        ],
    }
    existing = named(api.request("GET", prefix + "/model"), MODEL_NAME)
    path = prefix + "/model" + ("/" + existing["id"] if existing else "")
    return api.request("PUT" if existing else "POST", path, payload)


def prepare_application(api, prefix, knowledge_id, model_id):
    prompt = ("根据以下资料回答问题，并用资料中的文档名说明来源。资料不足时明确说明，区分不同单位。\n"
              "<data>\n{data}\n</data>\n问题：{question}")
    payload = {
        "name": APPLICATION_NAME, "desc": "公开扫描件解析与带引用问答演示", "folder_id": "default",
        "type": "SIMPLE", "model_id": model_id, "dialogue_number": 0, "problem_optimization": False,
        "prologue": "可以询问中卫市公开制度中的信息安全责任原则。", "knowledge_id_list": [knowledge_id],
        "knowledge_setting": {
            "top_n": 5, "similarity": 0.3, "search_mode": "embedding", "max_paragraph_char_number": 10000,
            "no_references_setting": {"status": "designated_answer", "value": "资料不足，无法回答。"},
        },
        "model_setting": {"prompt": prompt, "no_references_prompt": "{question}", "reasoning_content_enable": False},
        "model_params_setting": {"temperature": float(os.environ.get("LLM_TEMPERATURE", "0.2")),
                                 "max_tokens": int(os.environ.get("LLM_MAX_TOKENS", "512"))},
    }
    existing = named(api.request("GET", prefix + "/application"), APPLICATION_NAME)
    path = prefix + "/application" + ("/" + existing["id"] if existing else "")
    application = api.request("PUT" if existing else "POST", path, payload)
    app_path = prefix + "/application/" + application["id"]
    published = api.request("PUT", app_path + "/publish", {})
    versions = api.request("GET", app_path + "/application_version")
    if not versions or set(published.get("knowledge_id_list") or []) != {knowledge_id}:
        raise APIError("Published version is missing the demo knowledge binding")
    access = api.request("PUT", app_path + "/access_token", {"show_source": True, "is_active": True})
    return application, access, app_path


def verify_answer(api, access, app_path, question, timeout):
    chat = MaxKBAPI.__new__(MaxKBAPI)
    chat.base_url = os.environ.get("CHAT_API_URL", "http://web:8080/chat/api").rstrip("/")
    chat.timeout = 180
    chat.token = None
    chat.token = chat.request("POST", "/auth/anonymous", {"access_token": access["access_token"]})
    chat_id = chat.request("GET", "/open")
    started = time.monotonic()
    response = chat.request("POST", f"/chat_message/{chat_id}", {
        "message": question["question"], "stream": False, "re_chat": False,
    })
    response_seconds = time.monotonic() - started
    answer = response.get("content", "")
    if not response.get("is_end") or not answer or answer.startswith("Exception:"):
        raise APIError("Demo chat returned an incomplete or failed answer")
    deadline = time.monotonic() + timeout
    record_path = f"{app_path}/chat/{chat_id}/chat_record/{response['id']}"
    while True:
        record = api.request("GET", record_path)
        if record and record.get("answer_text") and record.get("paragraph_list"):
            break
        if time.monotonic() >= deadline:
            raise APIError("No saved chat record with citations arrived")
        time.sleep(0.5)
    sources = [{key: row.get(key) for key in ("id", "document_id", "document_name", "content")}
               for row in record["paragraph_list"]]
    if not any(row["document_name"] == question["document_name"] for row in sources):
        raise APIError("The answer did not cite the scanned demo document")
    if not all(part in answer for part in question["expected_answer_parts"]):
        raise APIError("The answer did not contain the expected responsibility principle")
    return {"question": question["question"], "answer": answer, "sources": sources,
            "chat_id": chat_id, "record_id": response["id"], "response_seconds": response_seconds,
            "input_tokens": record.get("message_tokens"), "output_tokens": record.get("answer_tokens")}


def main():
    import fcntl

    required = ("ADMIN_PASSWORD", "LLM_API_BASE", "LLM_MODEL_NAME", "LLM_API_KEY")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        raise ValueError("Set the following .env fields before importing: " + ", ".join(missing))
    manifest = json.loads((DATA / "manifest.json").read_text("utf-8"))
    for item in manifest["files"]:
        if hashlib.sha256((DATA / item["file"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Demo checksum mismatch for " + item["file"])
    state_path = Path(os.environ.get("DEMO_STATE_PATH", "/opt/maxkb/demo/state.json"))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        started = time.monotonic()
        workspace = os.environ.get("DEMO_WORKSPACE_ID", "default")
        api = MaxKBAPI(os.environ.get("WEB_API_URL", "http://web:8080/admin/api"), workspace, 180,
                       username=os.environ.get("ADMIN_USERNAME", "admin"), password=os.environ["ADMIN_PASSWORD"])
        prefix = f"/workspace/{workspace}"
        models = api.request("GET", prefix + "/model")
        embedding_id = os.environ.get("DEMO_EMBEDDING_MODEL_ID", "42f63a3d-427e-11ef-b3ec-a8a1595801ab")
        embeddings = [row for row in models if row["id"] == embedding_id and row["model_type"] == "EMBEDDING"
                      and row["status"] == "SUCCESS"]
        if len(embeddings) != 1:
            raise ValueError("DEMO_EMBEDDING_MODEL_ID does not identify a ready model in the demo workspace")
        knowledge = named(api.request("GET", prefix + "/knowledge"), KNOWLEDGE_NAME)
        if not knowledge:
            knowledge = api.request("POST", prefix + "/knowledge/base", {
                "name": KNOWLEDGE_NAME, "desc": "Versioned public demo scan", "folder_id": "default",
                "embedding_model_id": embeddings[0]["id"],
            })
        knowledge_path = prefix + "/knowledge/" + knowledge["id"]
        if knowledge.get("embedding_model_id") != embeddings[0]["id"]:
            raise ValueError("Demo knowledge is configured with a different embedding model")
        documents = api.request("GET", knowledge_path + "/document")
        imported = [row for row in manifest["files"] if row.get("import")]
        for item in imported:
            document = named(documents, item["file"])
            if not document:
                parsed = api.split_file(knowledge["id"], DATA / item["file"], 1200)
                if len(parsed) != 1 or parsed[0]["name"] != item["file"]:
                    raise APIError("Unexpected scan upload response")
                document = api.request("PUT", knowledge_path + "/document/batch_create", [{
                    "name": item["file"], "source_file_id": parsed[0]["source_file_id"],
                    "paragraphs": [{**row, "title": row.get("title") or item["title"]}
                                   for row in parsed[0]["content"]],
                }])[0]
                print("Uploaded", item["file"], "document", document["id"], flush=True)
        timeout = int(os.environ.get("DEMO_WAIT_SECONDS", "1800"))
        ready = wait_documents(api, knowledge_path, [row["file"] for row in imported], timeout)
        build_seconds = time.monotonic() - started
        print("Scan ready; upload/build wait seconds", round(build_seconds, 3), flush=True)
        model = prepare_model(api, prefix)
        application, access, app_path = prepare_application(api, prefix, knowledge["id"], model["id"])
        question = json.loads((DATA / "questions.json").read_text("utf-8"))[0]
        verification = verify_answer(api, access, app_path, question, 30)
        result = {"knowledge_id": knowledge["id"], "application_id": application["id"], "documents": ready,
                  "build_seconds": build_seconds, "total_seconds": time.monotonic() - started,
                  "verification": verification}
        write_result(state_path, result)
        public_url = os.environ.get("WEB_PUBLIC_URL", "http://localhost:8088").rstrip("/")
        print("Demo ready. Knowledge", knowledge["id"], "application", application["id"])
        print("Chat", public_url + "/chat/" + access["access_token"])
        print("Answer", verification["answer"])
        print("Citation", question["document_name"])
        print("Verification record", state_path)


if __name__ == "__main__":
    try:
        main()
    except (APIError, ValueError, OSError) as error:
        print(f"Demo import failed: {error}", file=sys.stderr)
        raise SystemExit(2)
