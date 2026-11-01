#!/usr/bin/env bash
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
PID_FILE="$REPO_ROOT/logs/pids"

if [ ! -f "$PID_FILE" ]; then
    echo "No recorded HYQUB processes found ($PID_FILE does not exist)."
    echo "Nothing to stop."
    exit 0
fi

FRONTEND_PID=""
BACKEND_PID=""
ANVIL_PID=""

while IFS='=' read -r key val; do
    case "$key" in
        FRONTEND_PID) FRONTEND_PID="$val" ;;
        BACKEND_PID)  BACKEND_PID="$val" ;;
        ANVIL_PID)    ANVIL_PID="$val" ;;
    esac
done < "$PID_FILE"

echo "=== Stopping HYQUB Services Gracefully ==="

# 1. Stop Frontend
if [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    echo "Stopping frontend (PID $FRONTEND_PID)..."
    kill -TERM "$FRONTEND_PID" 2>/dev/null || true
    for i in $(seq 1 10); do
        if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then break; fi
        sleep 0.5
    done
    if kill -0 "$FRONTEND_PID" 2>/dev/null; then
        kill -9 "$FRONTEND_PID" 2>/dev/null || true
    fi
    echo "Frontend stopped."
else
    echo "Frontend was not running."
fi

# 2. Stop Backend
if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "Stopping backend (PID $BACKEND_PID)..."
    kill -TERM "$BACKEND_PID" 2>/dev/null || true
    for i in $(seq 1 10); do
        if ! kill -0 "$BACKEND_PID" 2>/dev/null; then break; fi
        sleep 0.5
    done
    if kill -0 "$BACKEND_PID" 2>/dev/null; then
        kill -9 "$BACKEND_PID" 2>/dev/null || true
    fi
    echo "Backend stopped."
else
    echo "Backend was not running."
fi

# 3. Stop Anvil (Allow time to dump state to anvil-state.json)
if [ -n "$ANVIL_PID" ] && kill -0 "$ANVIL_PID" 2>/dev/null; then
    echo "Stopping Anvil (PID $ANVIL_PID)..."
    kill -TERM "$ANVIL_PID" 2>/dev/null || true
    for i in $(seq 1 20); do
        if ! kill -0 "$ANVIL_PID" 2>/dev/null; then break; fi
        sleep 0.5
    done
    if kill -0 "$ANVIL_PID" 2>/dev/null; then
        kill -9 "$ANVIL_PID" 2>/dev/null || true
    fi
    echo "Anvil stopped and state saved."
else
    echo "Anvil was not running."
fi

rm -f "$PID_FILE"
echo "All recorded HYQUB services stopped."
