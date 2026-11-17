#!/usr/bin/env bash
set -euo pipefail

runner_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${KANZO_RUNNER_ID:?Set a unique runner id, for example wsl-home}"
: "${KANZO_WORKSPACE:?Set an absolute WSL path for persistent workspaces}"
: "${KANZO_WORKER_STATE_DIR:?Set a persistent WSL path for worker state}"
: "${MCPPROXY_KANZO_AGENT_TOKEN:?Load the Kanzo read/write agent token from Vault}"
: "${KANZO_EXECUTOR_API_TOKEN:?Load the executor API token from Vault}"
: "${KANZO_BROKER_WORKER_TOKEN:?Load the broker worker token from Vault}"
: "${LITELLM_API_KEY:?Load the LiteLLM API key from Vault}"
: "${GITHUB_TOKEN:?Load the GitHub token from Vault}"

case "$KANZO_WORKSPACE:$KANZO_WORKER_STATE_DIR" in
  /*:/*) ;;
  *) echo "KANZO_WORKSPACE and KANZO_WORKER_STATE_DIR must be absolute WSL paths." >&2; exit 2 ;;
esac

export KANZO_ENABLED=true
export KANZO_CATALOG_PROJECT="${KANZO_CATALOG_PROJECT:-kanzo-project-catalog}"
export KANZO_RUNNER_ID
export KANZO_EVENTS_URL="${KANZO_EVENTS_URL:-https://kanzo.tail5389e9.ts.net/api/events}"
export KANZO_EXECUTOR_API_URL="${KANZO_EXECUTOR_API_URL:-https://hermes-executor.tail5389e9.ts.net}"
export KANZO_BROKER_URL="${KANZO_BROKER_URL:-https://kanzo-broker.tail5389e9.ts.net}"
export KANZO_BROKER_ALLOW_TAILNET=true
export MCPPROXY_URL="${MCPPROXY_URL:-https://mcp.tail5389e9.ts.net/mcp}"
export KANZO_WAKE_URL="${KANZO_WAKE_URL:-http://127.0.0.1:19105/wake}"
export METRICS_PORT="${METRICS_PORT:-19105}"
export METRICS_HOST=127.0.0.1

mkdir -p "$KANZO_WORKSPACE" "$KANZO_WORKER_STATE_DIR"
cd "$runner_dir"
python3 -u event_adapter.py &
adapter_pid=$!
python3 -u worker.py &
worker_pid=$!

shutdown() {
  kill "$adapter_pid" "$worker_pid" 2>/dev/null || true
  wait "$adapter_pid" 2>/dev/null || true
  wait "$worker_pid" 2>/dev/null || true
}
trap shutdown EXIT INT TERM

set +e
wait -n "$adapter_pid" "$worker_pid"
status=$?
set -e
exit "$status"
