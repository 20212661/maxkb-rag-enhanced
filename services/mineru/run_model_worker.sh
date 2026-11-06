#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export SERVER_NAME=web
cd apps
exec "${MAXKB_PYTHON:-python}" -m celery -A ops worker --loglevel=info \
    --queues=mineru --concurrency="${MINERU_WORKER_CONCURRENCY:-2}" --hostname='mineru@%h'
