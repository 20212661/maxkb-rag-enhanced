#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/env.sh"
UV_BIN="${UV_BIN:-uv}"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
MODEL_SOURCE="${MODEL_SOURCE:-modelscope}"
mkdir -p "$MINERU_ROOT" "$MINERU_HOME"
if [[ ! -x "$MINERU_ROOT/venv/bin/python" ]]; then
    "$UV_BIN" --no-config venv --python "$PYTHON_BIN" "$MINERU_ROOT/venv"
fi
"$UV_BIN" --no-config pip install --python "$MINERU_ROOT/venv/bin/python" \
    -r "$MINERU_DEPLOY_DIR/requirements.txt" -c "$MINERU_DEPLOY_DIR/requirements.lock"
"$MINERU_ROOT/venv/bin/mineru" version --json
"$MINERU_ROOT/venv/bin/mineru-kit" models download --tier basic --small-backend onnx --source "$MODEL_SOURCE"
"$MINERU_ROOT/venv/bin/mineru-kit" models verify --tier basic --small-backend onnx
