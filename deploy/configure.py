#!/usr/bin/env python3
"""Create the local Compose configuration once, preserving existing secrets."""
import os
from pathlib import Path
import secrets


def main():
    target = Path(__file__).with_name(".env")
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("Using existing deploy/.env")
        return
    values = {"SECRET_KEY": secrets.token_hex(32), "DB_PASSWORD": secrets.token_hex(24),
              "REDIS_PASSWORD": secrets.token_hex(24), "WEB_PORT": "8088", "EMBEDDING_CACHE_EPOCH": "1",
              "MINERU_MODEL_SOURCE": "modelscope"}
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in values.items()))
    print("Created deploy/.env with persistent local credentials")


if __name__ == "__main__":
    main()
