#!/usr/bin/env bash
# Source from the deployment scripts to keep both services on the same model store.
MINERU_DEPLOY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export MINERU_ROOT="${MINERU_ROOT:-/opt/mineru4}"
export MINERU_HOME="${MINERU_HOME:-$MINERU_ROOT/runtime}"
export MINERU_CONFIG="$MINERU_DEPLOY_DIR/config.yaml"
export MINERU_TASK_API_URL="${MINERU_TASK_API_URL:-http://127.0.0.1:8000}"
export MINERU_FILE_PARSE_URL="${MINERU_FILE_PARSE_URL:-http://127.0.0.1:8003}"
export MINERU_TELEMETRY_DISABLED=1
export MINERU_MODEL_SMALL_BACKEND=onnx
export MINERU_MODEL_ONNX_DEVICE=cpu
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export TOKENIZERS_PARALLELISM=false
