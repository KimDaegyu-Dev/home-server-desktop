#!/usr/bin/env bash
set -euo pipefail
set +x

command -v k3s >/dev/null || { echo "Run on the configured K3s host." >&2; exit 1; }
tmp="$(mktemp)"
chmod 0600 "$tmp"
cleanup() {
  k3s kubectl -n grafana-mcp delete pod grafana-mcp-smoke --wait=true --ignore-not-found >/dev/null 2>&1 || true
  rm -f "$tmp"
}
trap cleanup EXIT

cat >"$tmp" <<'YAML'
apiVersion: v1
kind: Pod
metadata:
  name: grafana-mcp-smoke
  namespace: grafana-mcp
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
    runAsGroup: 10001
  containers:
    - name: smoke
      image: python:3.13-slim@sha256:48d884813cf44e927cf64c5529dd630dd3b18338c6dc727991eef9b71e934a20
      env:
        - name: MCP_GRAFANA_SERVER_TOKEN
          valueFrom:
            secretKeyRef:
              name: grafana-mcp-credentials
              key: mcp_server_token
      command: [python3, -c]
      args:
        - |
          import json, os, urllib.request
          u="http://grafana-mcp.grafana-mcp.svc.cluster.local:8000/mcp"
          h={"Content-Type":"application/json","Accept":"application/json, text/event-stream","MCP-Protocol-Version":"2025-03-26","Authorization":"Bearer "+os.environ["MCP_GRAFANA_SERVER_TOKEN"]}
          def call(i,method,params):
              body=json.dumps({"jsonrpc":"2.0","id":i,"method":method,"params":params}).encode()
              r=urllib.request.urlopen(urllib.request.Request(u,data=body,headers=h),timeout=25)
              text=r.read().decode()
              payload=next((x[6:] for x in text.splitlines() if x.startswith("data: ")),text)
              return r.status,json.loads(payload)
          status,init=call(1,"initialize",{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}})
          assert status==200 and "result" in init, "MCP initialize failed"
          status,listed=call(2,"tools/list",{})
          names=[x["name"] for x in listed.get("result",{}).get("tools",[])]
          assert status==200 and names, "MCP tools/list failed or returned no tools"
          print("initialize_status",status,"protocol",init["result"].get("protocolVersion"))
          print("tools_status",status,"tools",names)
YAML

k3s kubectl apply -f "$tmp" >/dev/null
if ! k3s kubectl -n grafana-mcp wait --for=jsonpath='{.status.phase}'=Succeeded pod/grafana-mcp-smoke --timeout=90s; then
  k3s kubectl -n grafana-mcp describe pod grafana-mcp-smoke
  k3s kubectl -n grafana-mcp logs grafana-mcp-smoke || true
  exit 1
fi
k3s kubectl -n grafana-mcp logs grafana-mcp-smoke
