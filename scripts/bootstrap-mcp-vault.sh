#!/usr/bin/env bash
set -euo pipefail
set +x

init_file=""
if [[ $# -eq 2 && "$1" == "--token-file" ]]; then
  init_file="$2"
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--token-file PATH]" >&2
  exit 2
fi

[[ $EUID -eq 0 ]] || { echo "Run inside Ubuntu with sudo." >&2; exit 1; }
command -v k3s >/dev/null 2>&1 || { echo "Run on the configured K3s host." >&2; exit 1; }
command -v openssl >/dev/null 2>&1 || { echo "OpenSSL is required." >&2; exit 1; }

tmp="$(mktemp -d /dev/shm/home-server-mcp.XXXXXX)"
chmod 0700 "$tmp"
pod_token="/tmp/home-server-mcp-token-$(date +%s)-$$"
pod_payload="/tmp/home-server-mcp-payload-$(date +%s)-$$.json"
pod_policy="/tmp/home-server-mcp-policy-$(date +%s)-$$.hcl"
cleanup() {
  k3s kubectl exec -n vault vault-0 -- rm -f "$pod_token" "$pod_payload" "$pod_policy" >/dev/null 2>&1 || true
  rm -rf "$tmp"
  unset VAULT_TOKEN
}
trap cleanup EXIT

if [[ -n "$init_file" ]]; then
  [[ -r "$init_file" ]] || { echo "Vault token file is not readable." >&2; exit 1; }
  python3 -c 'import json,sys; t=json.load(open(sys.argv[1])).get("root_token"); assert isinstance(t,str) and t; print(t,end="")' "$init_file" >"$tmp/token"
else
  read -r -s -p "Vault admin token (hidden): " VAULT_TOKEN
  echo
  printf '%s' "$VAULT_TOKEN" >"$tmp/token"
  unset VAULT_TOKEN
fi
chmod 0600 "$tmp/token"
k3s kubectl cp "$tmp/token" "vault/vault-0:$pod_token" >/dev/null

vault_cmd() {
  k3s kubectl exec -n vault vault-0 -- sh -c 'token_file="$1"; shift; export VAULT_TOKEN="$(cat "$token_file")" VAULT_ADDR=http://127.0.0.1:8200; exec vault "$@"' sh "$pod_token" "$@"
}

vault_status="$(vault_cmd status -format=json 2>/dev/null || true)"
if [[ -z "$vault_status" ]] || ! grep -q '"initialized"[[:space:]]*:[[:space:]]*true' <<<"$vault_status" || grep -q '"sealed"[[:space:]]*:[[:space:]]*true' <<<"$vault_status"; then
  echo "Vault must be initialized and unsealed." >&2
  exit 1
fi
unset vault_status

# Reuse existing values on retries; never rotate credentials implicitly.
if vault_cmd kv get -format=json -mount=kv mcp/searxng >"$tmp/searx-current.json" 2>/dev/null; then
  python3 - "$tmp/searx-current.json" "$tmp/searxng.json" <<'PY'
import json, sys
with open(sys.argv[1]) as f: values = json.load(f)["data"]["data"]
if not values.get("secret_key"):
    raise SystemExit("Existing kv/mcp/searxng is missing secret_key; refusing to replace partial data.")
with open(sys.argv[2], "w") as f: json.dump({"secret_key": values["secret_key"]}, f)
PY
else
  python3 - "$tmp/searxng.json" <<'PY'
import json, secrets, sys
with open(sys.argv[1], "w") as f: json.dump({"secret_key": secrets.token_hex(32)}, f)
PY
fi

if vault_cmd kv get -format=json -mount=kv mcp/mcpproxy >"$tmp/mcpproxy-current.json" 2>/dev/null; then
  python3 - "$tmp/mcpproxy-current.json" "$tmp/mcpproxy.json" <<'PY'
import json, sys
with open(sys.argv[1]) as f: values = json.load(f)["data"]["data"]
if not values.get("authentik_client_secret") or not values.get("authserver.pem", "").startswith("-----BEGIN PRIVATE KEY-----"):
    raise SystemExit("Existing kv/mcp/mcpproxy is incomplete; refusing to rotate or replace partial data.")
with open(sys.argv[2], "w") as f: json.dump({"authentik_client_secret": values["authentik_client_secret"], "authserver.pem": values["authserver.pem"]}, f)
PY
else
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$tmp/authserver.pem" 2>/dev/null
  python3 - "$tmp/mcpproxy.json" "$tmp/authserver.pem" <<'PY'
import json, secrets, sys
with open(sys.argv[2]) as f: signing_key = f.read()
with open(sys.argv[1], "w") as f:
    json.dump({"authentik_client_secret": secrets.token_hex(32), "authserver.pem": signing_key}, f)
PY
fi

if vault_cmd kv get -format=json -mount=kv ai/ai-memory >"$tmp/ai-memory-current.json" 2>/dev/null; then
  python3 - "$tmp/ai-memory-current.json" "$tmp/ai-memory.json" <<'PY'
import json, sys
with open(sys.argv[1]) as f: values = json.load(f)["data"]["data"]
token = values.get("AI_MEMORY_AUTH_TOKEN")
if not isinstance(token, str) or len(token) < 32:
    raise SystemExit("Existing kv/ai/ai-memory has no valid AI_MEMORY_AUTH_TOKEN; refusing to replace partial data.")
with open(sys.argv[2], "w") as f: json.dump({"AI_MEMORY_AUTH_TOKEN": token}, f)
PY
else
  python3 - "$tmp/ai-memory.json" <<'PY'
import json, secrets, sys
with open(sys.argv[1], "w") as f: json.dump({"AI_MEMORY_AUTH_TOKEN": secrets.token_urlsafe(48)}, f)
PY
fi

if ! vault_cmd kv get -format=json -mount=kv authentik/sso-clients >"$tmp/sso-current.json" 2>/dev/null; then
  echo "Vault path kv/authentik/sso-clients is required before MCP SSO can be configured." >&2
  exit 1
fi
python3 - "$tmp/mcpproxy.json" "$tmp/sso-current.json" "$tmp/sso-updated.json" <<'PY'
import json, sys
with open(sys.argv[1]) as f: mcp = json.load(f)
with open(sys.argv[2]) as f: clients = json.load(f)["data"]["data"]
existing = clients.get("mcpproxy_client_secret")
if existing and existing != mcp["authentik_client_secret"]:
    raise SystemExit("MCP proxy and Authentik client secrets differ; refusing to overwrite either value.")
clients["mcpproxy_client_secret"] = mcp["authentik_client_secret"]
with open(sys.argv[3], "w") as f: json.dump(clients, f)
PY
chmod 0600 "$tmp"/*.json "$tmp/authserver.pem" 2>/dev/null || true

vault_policy_file="$tmp/mcp-vso.hcl"
cat >"$vault_policy_file" <<'POLICY'
path "kv/data/mcp/searxng" { capabilities = ["read"] }
path "kv/data/mcp/mcpproxy" { capabilities = ["read"] }
path "kv/data/ai/ai-memory" { capabilities = ["read"] }
POLICY
chmod 0600 "$vault_policy_file"

for item in "$tmp/searxng.json:mcp/searxng" "$tmp/mcpproxy.json:mcp/mcpproxy" "$tmp/ai-memory.json:ai/ai-memory" "$tmp/sso-updated.json:authentik/sso-clients"; do
  payload_file="${item%%:*}"
  vault_path="${item#*:}"
  k3s kubectl cp "$payload_file" "vault/vault-0:$pod_payload" >/dev/null
  vault_cmd kv put -mount=kv "$vault_path" "@$pod_payload" >/dev/null
done

k3s kubectl cp "$vault_policy_file" "vault/vault-0:$pod_policy" >/dev/null
vault_cmd policy write mcp-vso "$pod_policy" >/dev/null
vault_cmd write auth/kubernetes/role/searxng-vso \
  bound_service_account_names=search-vault-auth \
  bound_service_account_namespaces=search \
  audience=vault token_policies=mcp-vso token_ttl=1h >/dev/null
vault_cmd write auth/kubernetes/role/mcpproxy-vso \
  bound_service_account_names=mcp-vault-auth \
  bound_service_account_namespaces=mcp \
  audience=vault token_policies=mcp-vso token_ttl=1h >/dev/null
vault_cmd write auth/kubernetes/role/ai-memory-vso \
  bound_service_account_names=ai-memory-vault-auth \
  bound_service_account_namespaces=ai-memory \
  audience=vault token_policies=mcp-vso token_ttl=1h >/dev/null

if k3s kubectl -n authentik get vaultstaticsecret authentik-sso-clients >/dev/null 2>&1; then
  k3s kubectl -n authentik annotate vaultstaticsecret authentik-sso-clients "force-sync=$(date +%s)" --overwrite >/dev/null
  for _ in {1..60}; do
    ready="$(k3s kubectl -n authentik get vaultstaticsecret authentik-sso-clients -o 'jsonpath={.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
    if [[ "$ready" == "True" ]] && k3s kubectl -n authentik get secret authentik-sso-clients >/dev/null 2>&1; then break; fi
    sleep 2
  done
  if [[ "${ready:-}" != "True" ]]; then
    echo "Vault values and roles were stored, but Authentik VSO did not become Ready." >&2
    exit 1
  fi
  k3s kubectl -n authentik rollout restart deployment/authentik-server deployment/authentik-worker >/dev/null
  k3s kubectl -n authentik rollout status deployment/authentik-server --timeout=180s >/dev/null
  k3s kubectl -n authentik rollout status deployment/authentik-worker --timeout=180s >/dev/null
fi

echo "Stored MCP, SearXNG, ai-memory credentials, stable MCP OAuth signing key, and VSO roles in Vault."
echo "Vault paths: kv/mcp/searxng, kv/mcp/mcpproxy, kv/ai/ai-memory, kv/authentik/sso-clients. Secret values were not printed."
