#!/usr/bin/env bash
set -euo pipefail
set +x

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="v0.1.7"
archive="mcpproxy_0.1.7_linux_amd64.tar.gz"
sha256="3a6e794321ded9cd71c467e426407fa24b1dad26b0d9040f81a12fce3f166b7d"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

command -v python3 >/dev/null || { echo "python3 is required." >&2; exit 1; }
command -v k3s >/dev/null || { echo "Run inside the K3s host or install k3s CLI." >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required." >&2; exit 1; }

k3s kubectl kustomize "$repo_root/platform/mcp-stack" >/dev/null
config_hash="$(sha256sum "$repo_root/platform/mcp-stack/mcpproxy-config.yaml" | awk '{print $1}')"
declared_hash="$(sed -n 's/^[[:space:]]*checksum-mcpproxy-config: "\([0-9a-f]*\)"/\1/p' "$repo_root/platform/mcp-stack/mcpproxy.yaml")"
[[ "$config_hash" == "$declared_hash" ]] || {
    echo "MCPProxy config checksum annotation is stale; update mcpproxy.yaml." >&2
    exit 1
}
python3 - "$repo_root/platform/mcp-stack/searxng-mcp.py" <<'PY'
import ast, json, subprocess, sys
path = sys.argv[1]
ast.parse(open(path, encoding="utf-8").read())
messages = "\n".join([
    json.dumps({"jsonrpc":"2.0", "id":1, "method":"initialize", "params":{"protocolVersion":"2025-11-25", "capabilities":{}, "clientInfo":{"name":"preflight", "version":"1"}}}),
    json.dumps({"jsonrpc":"2.0", "id":2, "method":"tools/list", "params":{}}),
]) + "\n"
result = subprocess.run([sys.executable, path], input=messages, text=True, capture_output=True, check=True)
replies = [json.loads(line) for line in result.stdout.splitlines()]
assert replies[0]["result"]["serverInfo"]["name"] == "searxng-search"
assert [tool["name"] for tool in replies[1]["result"]["tools"]] == ["search"]
print("SearXNG MCP initialize and tools/list passed.")
PY

python3 - "$repo_root/platform/mcp-stack/mcpproxy-config.yaml" <<'PY'
import ast, sys
from pathlib import Path
lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
start = lines.index("  oauth-proxy.py: |") + 1
source = []
for line in lines[start:]:
    if line and not line.startswith("    "):
        break
    source.append(line[4:] if line.startswith("    ") else "")
ast.parse("\n".join(source), filename="oauth-proxy.py")
print("MCP OAuth resource metadata proxy syntax passed.")
PY

mkdir -p "$tmp/bin"
curl -fsSL "https://github.com/hoophq/mcpproxy/releases/download/${version}/${archive}" -o "$tmp/$archive"
echo "$sha256  $tmp/$archive" | sha256sum -c - >/dev/null
tar -xzf "$tmp/$archive" -C "$tmp/bin" ./configcheck
chmod 0555 "$tmp/bin/configcheck"

python3 - "$repo_root/platform/mcp-stack/mcpproxy-config.yaml" "$tmp/config.yaml" <<'PY'
from pathlib import Path
import sys
lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
start = lines.index("  config.yaml: |") + 1
block = []
for line in lines[start:]:
    if line and not line.startswith("    "):
        break
    block.append(line[4:] if line.startswith("    ") else "")
Path(sys.argv[2]).write_text("\n".join(block) + "\n", encoding="utf-8")
PY

AUTHENTIK_OIDC_CLIENT_SECRET=preflight-only-value "$tmp/bin/configcheck" "$tmp/config.yaml"
echo "MCPProxy ${version} configuration passed configcheck."
