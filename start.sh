#!/usr/bin/env bash
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"

export PATH="$HOME/.foundry/bin:$PATH"

# -----------------------------------------------------------------------------
# 1. PREFLIGHT
# -----------------------------------------------------------------------------

echo "=== [1/6] Running Preflight Checks ==="

if ! command -v anvil >/dev/null 2>&1; then
    echo "ERROR: anvil is not installed or not in PATH." >&2
    echo "Install Foundry via: https://getfoundry.sh" >&2
    exit 1
fi

if ! command -v cast >/dev/null 2>&1; then
    echo "ERROR: cast is not installed or not in PATH." >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 is not installed or not in PATH." >&2
    exit 1
fi

# Locate backend virtualenv
VENV_DIR=""
if [ -d "$REPO_ROOT/backend/venv" ] && [ -x "$REPO_ROOT/backend/venv/bin/python" ]; then
    VENV_DIR="$REPO_ROOT/backend/venv"
elif [ -d "$REPO_ROOT/backend/venv313" ] && [ -x "$REPO_ROOT/backend/venv313/bin/python" ]; then
    VENV_DIR="$REPO_ROOT/backend/venv313"
elif [ -d "$REPO_ROOT/venv" ] && [ -x "$REPO_ROOT/venv/bin/python" ]; then
    VENV_DIR="$REPO_ROOT/venv"
elif [ -d "$HOME/HYQUB/backend/venv" ] && [ -x "$HOME/HYQUB/backend/venv/bin/python" ]; then
    VENV_DIR="$HOME/HYQUB/backend/venv"
fi

if [ -z "$VENV_DIR" ]; then
    echo "ERROR: Backend virtual environment was not found." >&2
    echo "Please set up the virtualenv in backend/venv first." >&2
    exit 1
fi

if [ ! -f "$REPO_ROOT/backend/.env" ]; then
    echo "ERROR: backend/.env configuration file was not found." >&2
    echo "Copy backend/.env.example to backend/.env and configure it." >&2
    exit 1
fi

echo "Preflight checks passed."

# -----------------------------------------------------------------------------
# 2. CHECK PORTS (8545, 8000, 3000)
# -----------------------------------------------------------------------------

echo "=== [2/6] Checking Port Availability ==="

is_port_in_use() {
    local p="$1"
    if nc -z 127.0.0.1 "$p" 2>/dev/null; then
        return 0
    fi
    if ! python3 -c "import socket; s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.bind(('127.0.0.1', $p)); s.close()" 2>/dev/null; then
        return 0
    fi
    return 1
}

for port in 8545 8000 3000; do
    if is_port_in_use "$port"; then
        echo "ERROR: Port $port is already in use." >&2
        echo "If HYQUB is already running, run ./stop.sh first." >&2
        exit 1
    fi
done

echo "Ports 8545, 8000, and 3000 are available."

# -----------------------------------------------------------------------------
# 3. START ANVIL IN BACKGROUND
# -----------------------------------------------------------------------------

echo "=== [3/6] Starting Anvil ==="

mkdir -p "$REPO_ROOT/logs"

# Ensure initial persistent state exists if available in repository
if [ ! -f "$REPO_ROOT/anvil-state.json" ]; then
    if [ -f "$REPO_ROOT/blockchain/anvil-state/anvil-state.json" ]; then
        cp "$REPO_ROOT/blockchain/anvil-state/anvil-state.json" "$REPO_ROOT/anvil-state.json"
    elif [ -f "$HOME/HYQUB/blockchain/anvil-state/anvil-state.json" ]; then
        cp "$HOME/HYQUB/blockchain/anvil-state/anvil-state.json" "$REPO_ROOT/anvil-state.json"
    fi
fi

anvil --state "$REPO_ROOT/anvil-state.json" > "$REPO_ROOT/logs/anvil.log" 2>&1 &
ANVIL_PID=$!

ANVIL_RPC="http://127.0.0.1:8545"
ANVIL_READY=false
for i in $(seq 1 30); do
    if curl -s -X POST "$ANVIL_RPC" \
        -H "Content-Type: application/json" \
        --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' 2>/dev/null | grep -q '"result"'; then
        ANVIL_READY=true
        break
    fi
    sleep 1
done

if [ "$ANVIL_READY" != "true" ]; then
    echo "ERROR: Anvil failed to answer RPC on $ANVIL_RPC within 30 seconds." >&2
    kill -TERM "$ANVIL_PID" 2>/dev/null || true
    exit 1
fi

echo "Anvil is running (PID $ANVIL_PID)."

# -----------------------------------------------------------------------------
# 4. CHECK CONTRACTS ON CHAIN
# -----------------------------------------------------------------------------

echo "=== [4/6] Verifying Smart Contracts On Chain ==="

DEPLOYMENT_FILE="$REPO_ROOT/blockchain/.last_deployment.json"

check_contracts() {
    if [ ! -f "$DEPLOYMENT_FILE" ]; then
        return 1
    fi
    local entries
    entries=$(python3 -c "
import json
try:
    with open('$DEPLOYMENT_FILE') as f:
        data = json.load(f)
    for k, v in data.items():
        if v:
            print(f'{k}:{v}')
except Exception:
    pass
")
    if [ -z "$entries" ]; then
        return 1
    fi
    for item in $entries; do
        local name="${item%%:*}"
        local addr="${item#*:}"
        local code
        code=$(cast code "$addr" --rpc-url "$ANVIL_RPC" 2>/dev/null || true)
        if [ -z "$code" ] || [ "$code" = "0x" ]; then
            return 1
        fi
    done
    return 0
}

confirm_env_match() {
    python3 -c "
import json, sys

try:
    with open('$DEPLOYMENT_FILE') as f:
        dep = json.load(f)

    env_map = {}
    with open('$REPO_ROOT/backend/.env') as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env_map[k.strip()] = v.strip().lower()

    mismatches = []
    checks = [
        ('HYQUBRegistry', 'HYQUB_REGISTRY_ADDRESS'),
        ('EntryPoint', 'HYQUB_ENTRYPOINT_ADDRESS'),
        ('HYQUBWallet', 'HYQUB_WALLET_ADDRESS')
    ]
    for contract_key, env_key in checks:
        expected = dep.get(contract_key, '').lower()
        actual = env_map.get(env_key, '').lower()
        if not expected or expected != actual:
            mismatches.append(f'{contract_key} ({expected}) != {env_key} ({actual})')

    if mismatches:
        sys.exit(1)
    sys.exit(0)
except Exception:
    sys.exit(1)
"
}

if ! check_contracts; then
    echo "Contract code missing on chain. Executing deploy step..."
    if [ -x "$REPO_ROOT/setup_hyqub_env.sh" ] || [ -f "$REPO_ROOT/setup_hyqub_env.sh" ]; then
        bash "$REPO_ROOT/setup_hyqub_env.sh" || {
            echo "ERROR: Contract deployment script failed." >&2
            echo "To deploy manually, start Anvil and run: ./setup_hyqub_env.sh" >&2
            kill -TERM "$ANVIL_PID" 2>/dev/null || true
            exit 1
        }
    else
        echo "ERROR: setup_hyqub_env.sh not found." >&2
        echo "Cannot safely deploy automatically. Please run ./setup_hyqub_env.sh." >&2
        kill -TERM "$ANVIL_PID" 2>/dev/null || true
        exit 1
    fi

    if ! check_contracts; then
        echo "ERROR: Contracts still do not have bytecode on chain after deployment." >&2
        kill -TERM "$ANVIL_PID" 2>/dev/null || true
        exit 1
    fi
fi

if ! confirm_env_match; then
    echo "ERROR: Deployed contract addresses do not match backend/.env." >&2
    echo "Run ./setup_hyqub_env.sh to synchronize deployment addresses." >&2
    kill -TERM "$ANVIL_PID" 2>/dev/null || true
    exit 1
fi

echo "Contracts verified on chain and match backend/.env."

# -----------------------------------------------------------------------------
# 5. START BACKEND
# -----------------------------------------------------------------------------

echo "=== [5/6] Starting Backend ==="

(
    cd "$REPO_ROOT/backend"
    source "$VENV_DIR/bin/activate"
    exec uvicorn app.main:app --host 127.0.0.1 --port 8000
) > "$REPO_ROOT/logs/backend.log" 2>&1 &
BACKEND_PID=$!

BACKEND_READY=false
for i in $(seq 1 30); do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health 2>/dev/null || true)
    if [ "$STATUS" = "200" ]; then
        BACKEND_READY=true
        break
    fi
    sleep 1
done

if [ "$BACKEND_READY" != "true" ]; then
    echo "ERROR: Backend failed to respond with HTTP 200 on /health within 30 seconds." >&2
    echo "Last 20 lines of logs/backend.log:" >&2
    tail -n 20 "$REPO_ROOT/logs/backend.log" >&2
    kill -TERM "$BACKEND_PID" 2>/dev/null || true
    kill -TERM "$ANVIL_PID" 2>/dev/null || true
    exit 1
fi

echo "Backend is healthy (PID $BACKEND_PID)."

# -----------------------------------------------------------------------------
# 6. START FRONTEND
# -----------------------------------------------------------------------------

echo "=== [6/6] Starting Frontend ==="

(
    cd "$REPO_ROOT"
    exec python3 -m http.server 3000 --directory frontend
) > "$REPO_ROOT/logs/frontend.log" 2>&1 &
FRONTEND_PID=$!

# Record PIDs for stop.sh
cat <<EOF > "$REPO_ROOT/logs/pids"
ANVIL_PID=$ANVIL_PID
BACKEND_PID=$BACKEND_PID
FRONTEND_PID=$FRONTEND_PID
EOF

# -----------------------------------------------------------------------------
# 7. SUMMARY
# -----------------------------------------------------------------------------

echo ""
echo "=========================================="
echo "          HYQUB IS RUNNING 🚀"
echo "=========================================="
echo ""
echo "App:      http://localhost:3000/index.html"
echo "Landing:  http://localhost:3000/landing.html"
echo "API docs: http://127.0.0.1:8000/docs"
echo ""
echo "Press Ctrl+C to stop all services gracefully."

# -----------------------------------------------------------------------------
# 8. SHUTDOWN TRAP
# -----------------------------------------------------------------------------

cleanup() {
    echo ""
    echo "Stopping HYQUB services gracefully..."

    if [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
        echo "Stopping frontend (PID $FRONTEND_PID)..."
        kill -TERM "$FRONTEND_PID" 2>/dev/null || true
        wait "$FRONTEND_PID" 2>/dev/null || true
    fi

    if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "Stopping backend (PID $BACKEND_PID)..."
        kill -TERM "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    fi

    if [ -n "$ANVIL_PID" ] && kill -0 "$ANVIL_PID" 2>/dev/null; then
        echo "Stopping Anvil (PID $ANVIL_PID)..."
        kill -TERM "$ANVIL_PID" 2>/dev/null || true
        wait "$ANVIL_PID" 2>/dev/null || true
    fi

    rm -f "$REPO_ROOT/logs/pids"
    echo "All HYQUB services stopped."
    exit 0
}

trap cleanup SIGINT SIGTERM

wait
