#!/usr/bin/env bash
set -euo pipefail
set +x

[[ $EUID -eq 0 ]] || { echo "Run inside Ubuntu with sudo." >&2; exit 1; }
command -v k3s >/dev/null 2>&1 || { echo "Run on the configured K3s host." >&2; exit 1; }

tmp="$(mktemp -d /dev/shm/home-server-watch-skill.XXXXXX)"
chmod 0700 "$tmp"
pod_token="/tmp/watch-skill-token-$(date +%s)-$$"
pod_policy="/tmp/watch-skill-policy-$(date +%s)-$$.hcl"
pod_payload="/tmp/watch-skill-payload-$(date +%s)-$$.json"
cleanup() {
  k3s kubectl exec -n vault vault-0 -- rm -f "$pod_token" "$pod_policy" "$pod_payload" >/dev/null 2>&1 || true
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

vault_status="$(vault_cmd status -format=json 2>/dev/null || true)"
if [[ -z "$vault_status" ]] || ! grep -q '"initialized"[[:space:]]*:[[:space:]]*true' <<<"$vault_status" || grep -q '"sealed"[[:space:]]*:[[:space:]]*true' <<<"$vault_status"; then
  echo "Vault must be initialized and unsealed." >&2
  exit 1
fi
unset vault_status

need_new_key=1
if vault_cmd kv get -format=json -mount=kv ai/watch-skill >"$tmp/current-key.json" 2>/dev/null; then
  python3 - "$tmp/current-key.json" "$tmp/existing-key" <<'PY'
import json, sys
with open(sys.argv[1]) as f: values = json.load(f)["data"]["data"]
key = values.get("WATCHSKILL_CUSTOM_API_KEY")
if key not in (None, "pending-liteLLM-virtual-key") and (not isinstance(key, str) or not key.startswith("sk-")):
    raise SystemExit("Existing kv/ai/watch-skill has an unexpected API key; refusing to replace it.")
open(sys.argv[2], "w").write(key or "")
PY
  if [[ -s "$tmp/existing-key" ]] && grep -q '^sk-' "$tmp/existing-key"; then need_new_key=0; fi
fi

if vault_cmd kv get -format=json -mount=kv mcp/watch-skill >"$tmp/current-token.json" 2>/dev/null; then
  python3 - "$tmp/current-token.json" "$tmp/mcp-token" <<'PY'
import json, sys
with open(sys.argv[1]) as f: token = json.load(f)["data"]["data"].get("WATCHSKILL_MCP_TOKEN")
if not isinstance(token, str) or len(token) < 32:
    raise SystemExit("Existing kv/mcp/watch-skill has no valid MCP token; refusing to replace partial data.")
open(sys.argv[2], "w").write(token)
PY
else
  python3 - "$tmp/mcp-token" <<'PY'
import secrets, sys
open(sys.argv[1], "w").write(secrets.token_urlsafe(48))
PY
fi

if [[ "$need_new_key" == 1 ]]; then
  k3s kubectl -n litellm rollout status deployment/litellm --timeout=180s >/dev/null
  key_generator='import json,os,urllib.request; master=os.environ.get("LITELLM_MASTER_KEY") or os.environ.get("PROXY_MASTER_KEY"); assert master, "LiteLLM master key is not present in the pod environment"; data={"models":["iq_3s"],"key_alias":"watch-skill","duration":None,"allowed_routes":["llm_api_routes"],"metadata":{"service":"watch-skill"}}; req=urllib.request.Request("http://127.0.0.1:4000/key/generate",data=json.dumps(data).encode(),headers={"Authorization":"Bearer "+master,"Content-Type":"application/json"},method="POST"); result=json.load(urllib.request.urlopen(req,timeout=30)); key=result.get("key"); assert isinstance(key,str) and key.startswith("sk-"), "LiteLLM did not return a virtual key"; print(key,end="")'
  k3s kubectl exec -n litellm deployment/litellm -- python3 -c "$key_generator" >"$tmp/litellm-key"
  python3 - "$tmp/litellm-key" "$tmp/watch-skill-key.json" <<'PY'
import json, sys
key = open(sys.argv[1]).read().strip()
if not key.startswith("sk-"):
    raise SystemExit("LiteLLM returned an invalid virtual key; refusing to store it.")
with open(sys.argv[2], "w") as f: json.dump({"WATCHSKILL_CUSTOM_API_KEY": key}, f)
PY
else
  python3 - "$tmp/existing-key" "$tmp/watch-skill-key.json" <<'PY'
import json, sys
key = open(sys.argv[1]).read().strip()
with open(sys.argv[2], "w") as f: json.dump({"WATCHSKILL_CUSTOM_API_KEY": key}, f)
PY
fi
python3 - "$tmp/mcp-token" "$tmp/watch-skill-mcp.json" <<'PY'
import json, sys
token = open(sys.argv[1]).read().strip()
if len(token) < 32:
    raise SystemExit("Invalid MCP bearer token; refusing to store it.")
with open(sys.argv[2], "w") as f: json.dump({"WATCHSKILL_MCP_TOKEN": token}, f)
PY
chmod 0600 "$tmp/watch-skill-key.json" "$tmp/watch-skill-mcp.json"

for item in "$tmp/watch-skill-key.json:ai/watch-skill" "$tmp/watch-skill-mcp.json:mcp/watch-skill"; do
  payload_file="${item%%:*}"
  vault_path="${item#*:}"
  k3s kubectl cp "$payload_file" "vault/vault-0:$pod_payload" >/dev/null
  vault_cmd kv put -mount=kv "$vault_path" "@$pod_payload" >/dev/null
done

cat >"$tmp/watch-skill-policy.hcl" <<'POLICY'
path "kv/data/ai/watch-skill" { capabilities = ["read"] }
path "kv/data/mcp/watch-skill" { capabilities = ["read"] }
POLICY
chmod 0600 "$tmp/watch-skill-policy.hcl"
k3s kubectl cp "$tmp/watch-skill-policy.hcl" "vault/vault-0:$pod_policy" >/dev/null
vault_cmd policy write watch-skill-vso "$pod_policy" >/dev/null
vault_cmd write auth/kubernetes/role/watch-skill-vso \
  bound_service_account_names=watch-skill-vault-auth \
  bound_service_account_namespaces=watch-skill \
  audience=vault token_policies=watch-skill-vso token_ttl=1h >/dev/null

for ref in "watch-skill/watch-skill-credentials" "watch-skill/watch-skill-mcp-credentials" "mcp/watch-skill-backend-credentials"; do
  namespace="${ref%%/*}"
  name="${ref#*/}"
  if k3s kubectl -n "$namespace" get vaultstaticsecret "$name" >/dev/null 2>&1; then
    k3s kubectl -n "$namespace" annotate vaultstaticsecret "$name" "force-sync=$(date +%s)" --overwrite >/dev/null
    ready=""
    for _ in {1..60}; do
      ready="$(k3s kubectl -n "$namespace" get vaultstaticsecret "$name" -o 'jsonpath={.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
      if [[ "$ready" == "True" ]] && k3s kubectl -n "$namespace" get secret "$name" >/dev/null 2>&1; then break; fi
      sleep 2
    done
    if [[ "${ready:-}" != "True" ]]; then
      echo "Vault values were stored, but $namespace/$name did not become Ready." >&2
      exit 1
    fi
  fi
done

echo "Stored/reused the Watch Skill LiteLLM virtual key in kv/ai/watch-skill and MCP bearer in kv/mcp/watch-skill. Values were not printed."
echo "The LiteLLM virtual key is limited to the iq_3s model group and llm_api_routes."
