#!/usr/bin/env python3
"""Fill Compose settings and replace blank or change-me secrets, preserving configured credentials."""
import argparse
import os
from pathlib import Path
import secrets
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SECRET_LENGTHS = {"SECRET_KEY": 32, "DB_PASSWORD": 24, "REDIS_PASSWORD": 24, "ADMIN_PASSWORD": 24}


def configure(target, template):
    content = target.read_text(encoding="utf-8") if target.exists() else template.read_text(encoding="utf-8")
    keys = set(re.findall(r"^([A-Za-z_][A-Za-z_0-9]*)=", content, re.MULTILINE))
    for line in template.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=", line)
        if match and match[1] not in keys:
            content = content.rstrip("\n") + "\n" + line + "\n"
            keys.add(match[1])
    for key, length in SECRET_LENGTHS.items():
        content = re.sub(rf"^{key}=[ \t]*(?:''|\"\"|change-me|'change-me'|\"change-me\")?[ \t]*$",
                         lambda _: f"{key}={secrets.token_hex(length)}",
                         content, flags=re.MULTILINE)
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix=".compose-env-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content.rstrip("\n") + "\n")
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(f"Compose configuration ready at {target}. Existing values preserved. Set the LLM fields before demo import.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    configure(args.env_file.resolve(), ROOT / ".env.example")


if __name__ == "__main__":
    main()
