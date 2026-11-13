#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker info >/dev/null
python3 deploy/configure.py
compose=(docker compose)
"${compose[@]}" config --quiet
# Keep the frontend's 4 GiB build separate from the parser dependency build.
"${compose[@]}" build maxkb-init
"${compose[@]}" build mineru-api
"${compose[@]}" up --detach --no-build --wait --wait-timeout 1800
"${compose[@]}" exec -T web python deploy/smoke.py
"${compose[@]}" exec -T worker python -m celery -A ops inspect ping --timeout 15 --json
"${compose[@]}" ps
echo 'Management URL and credentials are configured in .env'
