# coding=utf-8
"""Exercise the real curl/file-parse path and sample Linux service-process memory."""

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import statistics
import subprocess
import threading
import time
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


DEPLOY_DIR = Path(__file__).resolve().parent
MIB = 1024 * 1024


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_kib_fields(path):
    fields = {}
    for line in path.read_text().splitlines():
        key, _, value = line.partition(":")
        if value.strip().endswith("kB"):
            fields[key] = int(value.split()[0]) / 1024
    return fields


def sample_memory(root_pids):
    processes = {}
    for directory in Path("/proc").glob("[0-9]*"):
        try:
            status = (directory / "status").read_text()
            ppid = int(next(line.split()[1] for line in status.splitlines() if line.startswith("PPid:")))
            processes[int(directory.name)] = (ppid, directory)
        except (OSError, StopIteration):
            continue
    selected = set(root_pids)
    while True:
        descendants = {pid for pid, (ppid, _) in processes.items() if ppid in selected}
        if descendants.issubset(selected):
            break
        selected.update(descendants)
    rss, pss, swap, count = 0.0, 0.0, 0.0, 0
    for pid in selected:
        if pid not in processes:
            continue
        directory = processes[pid][1]
        try:
            status = read_kib_fields(directory / "status")
            rollup = read_kib_fields(directory / "smaps_rollup")
        except FileNotFoundError:
            continue
        rss += status.get("VmRSS", 0)
        pss += rollup.get("Pss", 0)
        swap += status.get("VmSwap", 0)
        count += 1
    system = read_kib_fields(Path("/proc/meminfo"))
    return {"rss_mib": rss, "pss_mib": pss, "swap_mib": swap,
            "system_available_mib": system["MemAvailable"], "process_count": count}


def monitor_memory(root_pids, stop, samples, errors, started, trace_path):
    try:
        with trace_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["elapsed_s", "rss_mib", "pss_mib", "swap_mib",
                                                       "system_available_mib", "process_count"])
            writer.writeheader()
            while True:
                sample = {"elapsed_s": time.perf_counter() - started, **sample_memory(root_pids)}
                writer.writerow(sample)
                stream.flush()
                samples.append(sample)
                if stop.wait(0.2):
                    break
    except Exception as error:
        errors.append(f"{type(error).__name__}: {error}")


def inspect_archive(directory):
    from mineru.render import RenderFormat, render
    from mineru.types import MiddleJson

    with zipfile.ZipFile(directory / "response.zip") as archive:
        for member in archive.infolist():
            destination = (directory / member.filename).resolve()
            if not destination.is_relative_to(directory.resolve()):
                raise ValueError("Unsafe archive member")
        archive.extractall(directory)
    middle = MiddleJson.from_dict(json.loads((directory / "middle_json.json").read_text(encoding="utf-8")))
    content_list = render(middle, RenderFormat.CONTENT_LIST)
    save_json(directory / "content_list.json", content_list)
    markdown = (directory / "markdown.md").read_text(encoding="utf-8")
    if not markdown.strip() or not content_list:
        raise ValueError("Empty parsed text or content list")
    if any("type" not in block or "page_idx" not in block for block in content_list):
        raise ValueError("Content List is missing block types or source page indices")
    structured = json.loads((directory / "structured_content.json").read_text(encoding="utf-8"))
    images = sorted(str(path.relative_to(directory)) for path in (directory / "images").rglob("*") if path.is_file())
    return {"markdown_characters": len(markdown), "content_list_blocks": len(content_list),
            "block_types": dict(Counter(block["type"] for block in content_list)),
            "page_indices": sorted({block["page_idx"] for block in content_list}),
            "exported_images": images, "structured_content_type": type(structured).__name__,
            "actual_parser": middle.extensions.get("mineru")}


def run_case(case, repeat, args, root_pids, run_root):
    source = (DEPLOY_DIR / case["path"]).resolve()
    directory = run_root / f'{case["id"]}-{repeat}'
    directory.mkdir(parents=True)
    request_id = "day1-" + uuid.uuid4().hex
    command = ["curl", "--silent", "--show-error", "--connect-timeout", "10", "--max-time", "3700",
               "--dump-header", str(directory / "response.headers"), "--output", str(directory / "response.zip"),
               "--write-out", "%{http_code} %{time_total} %{size_download}", args.base_url + "/v1/file_parse",
               "--form", f"file=@{source}", "--form", "tier=basic", "--form", "ocr_mode=ocr",
               "--form", "output_formats=default", "--form", "wait_timeout=3600",
               "--form", "request_id=" + request_id]
    record = {"case": case["id"], "repeat": repeat, "request_id": request_id,
              "source": case["path"], "input_bytes": source.stat().st_size,
              "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "expected_pages": case["pages"], "artifact_directory": str(directory.relative_to(DEPLOY_DIR)),
              "status": "error", "started_at": datetime.now(timezone.utc).isoformat()}
    save_json(directory / "request.json", record)
    baseline = sample_memory(root_pids)
    samples, errors, stop = [], [], threading.Event()
    started = time.perf_counter()
    monitor = threading.Thread(target=monitor_memory,
                               args=(root_pids, stop, samples, errors, started, directory / "memory.csv"))
    monitor.start()
    print(f'Starting {case["id"]} repeat {repeat}, request {request_id}', flush=True)
    try:
        response = subprocess.run(command, capture_output=True, text=True, timeout=3720, check=False)
        record["wall_seconds"] = time.perf_counter() - started
        record["curl_exit_code"] = response.returncode
        if response.returncode:
            raise RuntimeError("curl failed " + response.stderr.strip())
        http_status, total, download = response.stdout.strip().split()
        record.update(http_status=int(http_status), http_seconds=float(total), response_bytes=int(download))
        if http_status != "200":
            raise RuntimeError(f"HTTP {http_status}. Inspect response.zip and recover using the saved request_id.")
        record["status"] = "parsed"
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        record["error"] = str(error)
    finally:
        stop.set()
        monitor.join()
    record["memory"] = {"baseline": baseline, "sample_count": len(samples), "sampling_interval_s": 0.2,
                        "sampling_errors": errors}
    if samples:
        record["memory"].update({"peak_rss_mib": max(sample["rss_mib"] for sample in samples),
                                 "peak_pss_mib": max(sample["pss_mib"] for sample in samples),
                                 "peak_swap_mib": max(sample["swap_mib"] for sample in samples),
                                 "min_system_available_mib": min(sample["system_available_mib"] for sample in samples)})
    if record["status"] == "parsed":
        try:
            record["output"] = inspect_archive(directory)
            if record["output"]["page_indices"] != list(range(case["pages"])):
                raise ValueError("Output page indices differ from the expected complete document")
            record["status"] = "ok" if not errors else "memory_error"
        except Exception as error:
            record.update(status="output_error", error=f"{type(error).__name__}: {error}")
    save_json(directory / "measurement.json", record)
    print(f'Finished {case["id"]} repeat {repeat}, {record["status"]}, '
          f'{record.get("http_seconds")} s, peak RSS {record["memory"].get("peak_rss_mib")} MiB', flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8003")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--case", choices=["scan_pdf", "image"])
    parser.add_argument("--output", type=Path, default=DEPLOY_DIR / "results/benchmark.json")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if not Path("/proc/meminfo").exists():
        parser.error("Run in Linux or WSL to measure the parser service process trees")
    runtime = Path(os.environ.get("MINERU_HOME", "/opt/mineru4/runtime"))
    root_pids = [int((runtime / "run" / (name + ".pid")).read_text())
                 for name in ("api-server", "file-parse-server")]
    for pid, name in zip(root_pids, ("api-server", "file-parse-server")):
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        if "mineru-kit " + name + " " not in command:
            raise ValueError("PID file does not belong to the expected MinerU service")
    manifest = json.loads((DEPLOY_DIR / "samples/manifest.json").read_text(encoding="utf-8"))
    cases = [case for case in manifest["cases"] if not args.case or case["id"] == args.case]
    for case in cases:
        source = DEPLOY_DIR / case["path"]
        if hashlib.sha256(source.read_bytes()).hexdigest() != case["sha256"]:
            raise ValueError("Input checksum mismatch for " + case["id"])
    run_root = DEPLOY_DIR / "results" / ("run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    result = {"created_at": datetime.now(timezone.utc).isoformat(),
              "mineru_version": importlib.metadata.version("mineru"),
              "python_version": platform.python_version(), "platform": platform.platform(),
              "logical_cpus": os.cpu_count(), "base_url": args.base_url, "root_pids": root_pids,
              "tier": "basic", "small_backend": "onnx", "onnx_device": "cpu", "concurrency": 1,
              "ocr_mode": "ocr", "sample_manifest_sha256": hashlib.sha256(
                  (DEPLOY_DIR / "samples/manifest.json").read_bytes()).hexdigest(),
              "timing_scope": "curl upload through complete ZIP download, excluding local export inspection",
              "memory_scope": "API and file-parse process trees, sampled every 200 ms during HTTP only",
              "warmup": "No warmup. First request includes lazy model loading if the service is fresh.",
              "complete": False, "runs": []}
    for case in cases:
        for repeat in range(1, args.repeats + 1):
            result["runs"].append(run_case(case, repeat, args, root_pids, run_root))
            save_json(args.output, result)
    result["summary"] = {}
    for case in cases:
        successful = [record for record in result["runs"] if record["case"] == case["id"] and record["status"] == "ok"]
        result["summary"][case["id"]] = {
            "success_count": len(successful), "attempt_count": args.repeats,
            "mean_http_seconds": statistics.mean(row["http_seconds"] for row in successful) if successful else None,
            "max_rss_mib": max((row["memory"]["peak_rss_mib"] for row in successful), default=None),
            "max_pss_mib": max((row["memory"]["peak_pss_mib"] for row in successful), default=None)}
    result["complete"] = True
    save_json(args.output, result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2), flush=True)
    return int(any(record["status"] != "ok" for record in result["runs"]))


if __name__ == "__main__":
    raise SystemExit(main())
