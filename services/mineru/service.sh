#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/env.sh"
mkdir -p "$MINERU_HOME/logs" "$MINERU_HOME/run" "$MINERU_HOME/uploads"
exec 9>"$MINERU_HOME/run/service.lock"
flock -n 9 || { echo 'Another service command is running' >&2; exit 1; }

owned_pid() {
    local name="$1" pid_file="$MINERU_HOME/run/$1.pid" pid command
    [[ -f "$pid_file" ]] || return 1
    read -r pid < "$pid_file"
    [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/cmdline" ]] || return 1
    command="$(tr '\0' ' ' < "/proc/$pid/cmdline")"
    if [[ "$name" == content-list-server ]]; then
        [[ "$command" == *"$MINERU_DEPLOY_DIR/content_list_api.py "* ]] || return 1
    else
        [[ "$command" == *"$MINERU_ROOT/venv/bin/mineru-kit $name "* ]] || return 1
    fi
    printf '%s' "$pid"
}

start_one() {
    local name="$1" port="$2" health="$3" pid
    shift 3
    if pid="$(owned_pid "$name")"; then
        echo "$name already running, PID $pid"
    else
        "$MINERU_ROOT/venv/bin/python" - "$port" <<'PY'
import socket
import sys
with socket.socket() as listener:
    listener.bind(("127.0.0.1", int(sys.argv[1])))
PY
        nohup "$@" --host 127.0.0.1 --port "$port" \
            >"$MINERU_HOME/logs/$name.log" 2>&1 < /dev/null 9>&- &
        pid=$!
        printf '%s\n' "$pid" > "$MINERU_HOME/run/$name.pid"
    fi
    for _ in {1..60}; do
        if curl --silent --fail "http://127.0.0.1:$port$health" >/dev/null; then
            echo "$name ready on 127.0.0.1:$port, PID $pid"
            return
        fi
        kill -0 "$pid" 2>/dev/null || { tail -30 "$MINERU_HOME/logs/$name.log" >&2; return 1; }
        sleep 1
    done
    echo "$name did not become ready. See $MINERU_HOME/logs/$name.log" >&2
    return 1
}

case "${1:-status}" in
    start)
        "$MINERU_ROOT/venv/bin/mineru-kit" models verify --tier basic
        start_one api-server 8000 /v1/health "$MINERU_ROOT/venv/bin/mineru-kit" api-server \
            --tier basic --concurrency 1 --upload-dir "$MINERU_HOME/uploads"
        start_one file-parse-server 8003 /openapi.json "$MINERU_ROOT/venv/bin/mineru-kit" file-parse-server \
            --api-url "$MINERU_TASK_API_URL" --state-dir "$MINERU_HOME/file-parse"
        start_one content-list-server 8004 /health "$MINERU_ROOT/venv/bin/python" "$MINERU_DEPLOY_DIR/content_list_api.py"
        ;;
    stop)
        for name in content-list-server file-parse-server api-server; do
            if pid="$(owned_pid "$name")"; then
                kill -TERM "$pid"
                echo "Sent shutdown to $name, PID $pid"
                for _ in {1..30}; do
                    if ! owned_pid "$name" >/dev/null; then
                        break
                    fi
                    sleep 1
                done
                if owned_pid "$name" >/dev/null; then
                    echo "$name is still shutting down" >&2
                    exit 1
                fi
            fi
        done
        ;;
    status)
        for name in api-server file-parse-server content-list-server; do
            if pid="$(owned_pid "$name")"; then
                echo "$name running, PID $pid"
            else
                echo "$name stopped"
            fi
        done
        ;;
    *) echo "Usage $0 {start|stop|status}" >&2; exit 2 ;;
esac
