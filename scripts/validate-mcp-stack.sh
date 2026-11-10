#!/usr/bin/env bash
set -euo pipefail
set +x

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v python3 >/dev/null || { echo "python3 is required." >&2; exit 1; }
command -v k3s >/dev/null || { echo "Run inside the K3s host or install k3s CLI." >&2; exit 1; }

k3s kubectl kustomize "$repo_root/platform/mcp-stack" >/dev/null
python3 - "$repo_root/platform/mcp-stack/mcpproxy-config.yaml" "$repo_root/platform/mcp-stack/searxng-mcp.py" <<'PY'
import ast, json, subprocess, sys, time, urllib.request
from pathlib import Path
config_path, adapter_path = map(Path, sys.argv[1:])
lines = config_path.read_text(encoding="utf-8").splitlines()
start = lines.index("  mcp_config.json: |") + 1
block = []
for line in lines[start:]:
    if line and not line.startswith("    "):
        break
    block.append(line[4:] if line.startswith("    ") else "")
config = json.loads("\n".join(block))
assert config["server_edition"]["enabled"] is True
assert config["server_edition"]["oauth"]["provider"] == "oidc"
assert config["server_edition"]["public_url"] == "https://mcp.tail5389e9.ts.net"
assert len(config["mcpServers"]) == 9
assert all(server.get("enabled") is True for server in config["mcpServers"])
assert "${env:OIDC_CLIENT_SECRET}" in json.dumps(config)
assert "${env:MCPPROXY_CRED_KEY}" in json.dumps(config)
assert "${env:GRAFANA_MCP_SERVER_TOKEN}" in json.dumps(config)
assert not any(secret in json.dumps(config) for secret in ("hvs.", "sk-", "-----BEGIN PRIVATE KEY-----"))
ast.parse(adapter_path.read_text(encoding="utf-8"), filename=str(adapter_path))

messages = "\n".join([
    json.dumps({"jsonrpc":"2.0", "id":1, "method":"initialize", "params":{"protocolVersion":"2025-11-25", "capabilities":{}, "clientInfo":{"name":"preflight", "version":"1"}}}),
    json.dumps({"jsonrpc":"2.0", "id":2, "method":"tools/list", "params":{}}),
]) + "\n"
result = subprocess.run([sys.executable, str(adapter_path)], input=messages, text=True, capture_output=True, check=True)
replies = [json.loads(line) for line in result.stdout.splitlines()]
assert replies[0]["result"]["serverInfo"]["name"] == "searxng-search"
assert [tool["name"] for tool in replies[1]["result"]["tools"]] == ["search"]

proc = subprocess.Popen([sys.executable, str(adapter_path), "--http"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(50):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8090/healthz", timeout=1) as response:
                assert response.status == 200
            break
        except Exception:
            if proc.poll() is not None:
                raise RuntimeError("SearXNG HTTP adapter exited before health became ready")
            time.sleep(0.1)
    else:
        raise RuntimeError("SearXNG HTTP adapter health check timed out")
    payload = json.dumps({"jsonrpc":"2.0", "id":3, "method":"tools/list", "params":{}}).encode()
    request = urllib.request.Request("http://127.0.0.1:8090/mcp", data=payload, headers={"Content-Type":"application/json", "Accept":"application/json, text/event-stream"})
    with urllib.request.urlopen(request, timeout=2) as response:
        assert json.load(response)["result"]["tools"][0]["name"] == "search"
finally:
    proc.terminate()
    proc.wait(timeout=5)
print("MCPProxy Server Edition JSON config and SearXNG stdio/HTTP MCP adapter checks passed.")
PY

echo "Kustomize render and MCP stack validation passed."
