#!/usr/bin/env python3
"""Measure a real Compose build/start, smoke check and scanned-PDF answer verification."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / "tmp/deploy-verification.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "running", "steps": []}
    started = time.monotonic()
    commands = [
        ("configuration", ["docker", "compose", "config", "--quiet"]),
        ("startup", ["docker", "compose", "up", "-d", "--build", "--wait", "--wait-timeout", "1800"]),
        ("smoke", ["docker", "compose", "exec", "-T", "web", "python", "deploy/smoke.py"]),
        ("demo", ["docker", "compose", "run", "--rm", "demo-import"]),
    ]
    with output.with_suffix(".log").open("w", encoding="utf-8") as log:
        for step, command in commands:
            step_started = time.monotonic()
            print("Running", step, flush=True)
            try:
                process = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
                exit_code = process.returncode
            except OSError as error:
                log.write(f"Unable to execute {command[0]} ({type(error).__name__})\n")
                exit_code = 127
            log.flush()
            result["steps"].append({"name": step, "seconds": time.monotonic() - step_started,
                                    "exit_code": exit_code})
            result["total_seconds"] = time.monotonic() - started
            if exit_code:
                result.update(status="failed", failed_step=step)
                output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
                print("Verification failed at", step, "see", output.with_suffix(".log"))
                return exit_code
            output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        result["status"] = "passed"
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Verification passed", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
