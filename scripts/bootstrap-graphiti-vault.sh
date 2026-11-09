#!/usr/bin/env bash
set -euo pipefail
set +x

[[ $EUID -eq 0 ]] || { echo "Run inside Ubuntu with sudo." >&2; exit 1; }
command -v k3s >/dev/null 2>&1 || { echo "Run on the configured K3s host." >&2; exit 1; }

tmp="$(mktemp -d /dev/shm/home-server-context.XXXXXX)"
chmod 0700 "$tmp"
pod_token="/tmp/context-vault-token-$(date +%s)-$$"
pod_payload="/tmp/context-vault-payload-$(date +%s)-$$.json"
pod_policy="/tmp/context-vault-policy-$(date +%s)-$$.hcl"
cleanup() {
  k3s kubectl exec -n vault vault-0 -- rm -f "$pod_token" "$pod_payload" "$pod_policy" /tmp/graphiti-keygen.py >/dev/null 2>&1 || true
  rm -rf "$tmp"
  unset VAULT_TOKEN
}
trap cleanup EXIT

read -r -s -p "Vault admin token (hidden): " VAULT_TOKEN
echo
printf '%s' "$VAULT_TOKEN" >"$tmp/token"
unset VAULT_TOKEN
chmod 0600 "$tmp/token"
k3s kubectl cp "$tmp/token" "vault/vault-0:$pod_token" >/dev/null

vault_cmd() {
  k3s kubectl exec -n vault vault-0 -- sh -c 'token_file="$1"; shift; export VAULT_TOKEN="$(cat "$token_file")" VAULT_ADDR=http://127.0.0.1:8200; exec vault "$@"' sh "$pod_token" "$@"
}

status="$(vault_cmd status -format=json 2>/dev/null || true)"
if [[ -z "$status" ]] || ! grep -q '"initialized"[[:space:]]*:[[:space:]]*true' <<<"$status" || grep -q '"sealed"[[:space:]]*:[[:space:]]*true' <<<"$status"; then
  echo "Vault must be initialized and unsealed." >&2
  exit 1
fi
unset status

if vault_cmd kv get -format=json -mount=kv mcp/graphiti >"$tmp/graphiti-current.json" 2>/dev/null; then
  python3 - "$tmp/graphiti-current.json" "$tmp/graphiti-existing.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)["data"]["data"]
key = data.get("OPENAI_API_KEY", "")
password = data.get("FALKORDB_PASSWORD", "")
if key and (not isinstance(key, str) or not key.startswith("sk-")):
    raise SystemExit("Existing kv/mcp/graphiti OPENAI_API_KEY is invalid; refusing to replace it.")
if password and (not isinstance(password, str) or len(password) < 32):
    raise SystemExit("Existing kv/mcp/graphiti FALKORDB_PASSWORD is invalid; refusing to replace it.")
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump({"OPENAI_API_KEY": key, "FALKORDB_PASSWORD": password}, f)
PY
else
  printf '{"OPENAI_API_KEY":"","FALKORDB_PASSWORD":""}' >"$tmp/graphiti-existing.json"
fi

if vault_cmd kv get -format=json -mount=kv mcp/agent-browser >"$tmp/agent-browser-current.json" 2>/dev/null; then
  python3 - "$tmp/agent-browser-current.json" "$tmp/agent-browser-token" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    token = json.load(f)["data"]["data"].get("AGENT_BROWSER_MCP_TOKEN", "")
if token and (not isinstance(token, str) or len(token) < 48):
    raise SystemExit("Existing agent-browser MCP token is invalid; refusing to replace it.")
with open(sys.argv[2], "w", encoding="utf-8") as f:
    f.write(token)
PY
else
  : >"$tmp/agent-browser-token"
fi

python3 - "$tmp/graphiti-existing.json" "$tmp/graphiti-state.json" "$tmp/agent-browser-token" <<'PY'
import json, secrets, sys
with open(sys.argv[1], encoding="utf-8") as f:
    graphiti = json.load(f)
if not graphiti["FALKORDB_PASSWORD"]:
    graphiti["FALKORDB_PASSWORD"] = secrets.token_urlsafe(48)
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump(graphiti, f)
token = open(sys.argv[3], encoding="utf-8").read().strip()
if not token:
    token = secrets.token_urlsafe(48)
open(sys.argv[3], "w", encoding="utf-8").write(token)
PY

python3 - "$tmp/graphiti-state.json" "$tmp/need-litellm-key" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
open(sys.argv[2], "w", encoding="utf-8").write("no" if data["OPENAI_API_KEY"] else "yes")
PY

if [[ "$(cat "$tmp/need-litellm-key")" == yes ]]; then
  k3s kubectl -n litellm rollout status deployment/litellm --timeout=180s >/dev/null
  cat >"$tmp/graphiti-keygen.py" <<'PY'
import json, os, urllib.request
master = os.environ.get("PROXY_MASTER_KEY") or os.environ.get("LITELLM_MASTER_KEY")
if not master:
    raise SystemExit("LiteLLM master key is not present in the pod environment.")
payload = {
    "models": ["iq_3s"],
    "key_alias": "graphiti",
    "duration": None,
    "allowed_routes": ["llm_api_routes"],
    "metadata": {"service": "graphiti"},
}
request = urllib.request.Request(
    "http://127.0.0.1:4000/key/generate",
    data=json.dumps(payload).encode(),
    headers={"Authorization": "Bearer " + master, "Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=30) as response:
    key = json.load(response).get("key", "")
if not isinstance(key, str) or not key.startswith("sk-"):
    raise SystemExit("LiteLLM did not return a valid virtual key.")
print(key, end="")
PY
  k3s kubectl cp "$tmp/graphiti-keygen.py" "litellm/$(k3s kubectl -n litellm get pod -l app.kubernetes.io/name=litellm -o jsonpath='{.items[0].metadata.name}'):/tmp/graphiti-keygen.py" >/dev/null
  k3s kubectl exec -n litellm deployment/litellm -- python3 /tmp/graphiti-keygen.py >"$tmp/graphiti-key"
  python3 - "$tmp/graphiti-state.json" "$tmp/graphiti-key" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
key = open(sys.argv[2], encoding="utf-8").read().strip()
if not key.startswith("sk-"):
    raise SystemExit("LiteLLM returned an invalid Graphiti key.")
data["OPENAI_API_KEY"] = key
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(data, f)
PY
else
  python3 - "$tmp/graphiti-state.json" "$tmp/graphiti-existing.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
with open(sys.argv[2], encoding="utf-8") as f:
    old = json.load(f)
data["OPENAI_API_KEY"] = old["OPENAI_API_KEY"]
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(data, f)
PY
fi

python3 - "$tmp/graphiti-state.json" "$tmp/graphiti-payload.json" "$tmp/agent-browser-token" "$tmp/agent-browser-payload.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    graphiti = json.load(f)
if not graphiti["OPENAI_API_KEY"].startswith("sk-") or len(graphiti["FALKORDB_PASSWORD"]) < 32:
    raise SystemExit("Graphiti secret generation did not produce valid values.")
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump(graphiti, f)
token = open(sys.argv[3], encoding="utf-8").read().strip()
if len(token) < 48:
    raise SystemExit("Agent Browser token generation did not produce a valid value.")
with open(sys.argv[4], "w", encoding="utf-8") as f:
    json.dump({"AGENT_BROWSER_MCP_TOKEN": token}, f)
PY
chmod 0600 "$tmp/graphiti-payload.json" "$tmp/agent-browser-payload.json"

for item in "$tmp/graphiti-payload.json:mcp/graphiti" "$tmp/agent-browser-payload.json:mcp/agent-browser"; do
  payload_file="${item%%:*}"
  vault_path="${item#*:}"
  k3s kubectl cp "$payload_file" "vault/vault-0:$pod_payload" >/dev/null
  vault_cmd kv put -mount=kv "$vault_path" "@$pod_payload" >/dev/null
done

cat >"$tmp/graphiti-policy.hcl" <<'POLICY'
path "kv/data/mcp/graphiti" { capabilities = ["read"] }
POLICY
cat >"$tmp/agent-browser-policy.hcl" <<'POLICY'
path "kv/data/mcp/agent-browser" { capabilities = ["read"] }
POLICY
chmod 0600 "$tmp/graphiti-policy.hcl" "$tmp/agent-browser-policy.hcl"
for policy in graphiti agent-browser; do
  k3s kubectl cp "$tmp/${policy}-policy.hcl" "vault/vault-0:$pod_policy" >/dev/null
  vault_cmd policy write "${policy}-vso" "$pod_policy" >/dev/null
done
vault_cmd write auth/kubernetes/role/graphiti-vso \
  bound_service_account_names=graphiti-vault-auth \
  bound_service_account_namespaces=graphiti \
  audience=vault token_policies=graphiti-vso token_ttl=1h >/dev/null
vault_cmd write auth/kubernetes/role/agent-browser-vso \
  bound_service_account_names=agent-browser-vault-auth \
  bound_service_account_namespaces=agent-browser \
  audience=vault token_policies=agent-browser-vso token_ttl=1h >/dev/null

for ref in "graphiti/graphiti-credentials" "agent-browser/agent-browser-credentials" "mcp/mcp-agent-browser-credentials"; do
  namespace="${ref%%/*}"
  name="${ref#*/}"
  if k3s kubectl -n "$namespace" get vaultstaticsecret "$name" >/dev/null 2>&1; then
    k3s kubectl -n "$namespace" annotate vaultstaticsecret "$name" "force-sync=$(date +%s)" --overwrite >/dev/null
    ready=""
    for _ in {1..60}; do
      ready="$(k3s kubectl -n "$namespace" get vaultstaticsecret "$name" -o 'jsonpath={.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
      if [[ "$ready" == True ]] && k3s kubectl -n "$namespace" get secret "$name" >/dev/null 2>&1; then break; fi
      sleep 2
    done
    if [[ "${ready:-}" != True ]]; then
      echo "Vault values were stored, but ${namespace}/${name} did not become Ready." >&2
      exit 1
    fi
  fi
done

echo "Stored/reused Graphiti LiteLLM and FalkorDB credentials and the Agent Browser backend token in Vault."
echo "Vault paths: kv/mcp/graphiti and kv/mcp/agent-browser. Values were not printed."
