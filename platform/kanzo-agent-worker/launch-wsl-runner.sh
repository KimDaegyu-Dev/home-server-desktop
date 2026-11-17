#!/usr/bin/env bash
set -euo pipefail

runner_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$runner_dir/../.." && pwd)"
namespace="${KANZO_NAMESPACE:-mcp}"
kubectl_bin="${K3S_BIN:-/usr/local/bin/k3s}"

if [[ "$(id -u)" -eq 0 ]]; then
  kubectl=("$kubectl_bin" kubectl)
else
  command -v sudo >/dev/null 2>&1 || { echo "sudo is required to read the cluster credentials." >&2; exit 2; }
  sudo -v
  kubectl=(sudo -n "$kubectl_bin" kubectl)
fi

"${kubectl[@]}" -n "$namespace" get secrets \
  kanzo-agent-credentials \
  kanzo-sandbox-broker-credentials \
  hermes-langfuse-credentials \
  -o json |
  KANZO_RUNNER_REPO="$repo_root" python3 -c '
import base64
import json
import os
import sys

items = json.load(sys.stdin).get("items", [])
env = os.environ.copy()
for item in items:
    for key, value in item.get("data", {}).items():
        env[key] = base64.b64decode(value, validate=True).decode("utf-8")

env.update({
    "KANZO_ENABLED": "true",
    "KANZO_RUNNER_ID": env.get("KANZO_RUNNER_ID", "wsl-home"),
    "KANZO_RUNNER_OS": "wsl",
    "KANZO_WORKSPACE": env.get("KANZO_WORKSPACE", "/home/eorbr/kanzo-workspaces"),
    "KANZO_WORKER_STATE_DIR": env.get("KANZO_WORKER_STATE_DIR", "/home/eorbr/.local/state/kanzo-worker"),
    "KANZO_LANGFUSE_USER_ID": env.get("KANZO_LANGFUSE_USER_ID", "kanzo-owner"),
    "HERMES_LANGFUSE_BASE_URL": env.get("HERMES_LANGFUSE_BASE_URL", "https://langfuse.tail5389e9.ts.net"),
})

required = (
    "MCPPROXY_KANZO_AGENT_TOKEN",
    "KANZO_EXECUTOR_API_TOKEN",
    "KANZO_BROKER_WORKER_TOKEN",
    "LITELLM_API_KEY",
    "GITHUB_TOKEN",
    "HERMES_LANGFUSE_PUBLIC_KEY",
    "HERMES_LANGFUSE_SECRET_KEY",
)
missing = [key for key in required if not env.get(key)]
if missing:
    print("Required credentials are missing from Kubernetes Secrets: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(2)

script = os.path.join(env.pop("KANZO_RUNNER_REPO"), "platform/kanzo-agent-worker/run-host-runner.sh")
os.execvpe("bash", ["bash", script], env)
'
