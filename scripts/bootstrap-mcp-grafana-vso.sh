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

tmp="$(mktemp -d /dev/shm/home-server-mcp-grafana.XXXXXX)"
chmod 0700 "$tmp"
pod_token="/tmp/home-server-mcp-grafana-token-$(date +%s)-$$"
pod_policy="/tmp/home-server-mcp-grafana-policy-$(date +%s)-$$.hcl"
cleanup() {
  k3s kubectl exec -n vault vault-0 -- rm -f "$pod_token" "$pod_policy" >/dev/null 2>&1 || true
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

vault_cmd kv get -format=json -mount=kv monitoring/grafana-mcp >/dev/null 2>&1 || {
  echo "Vault path kv/monitoring/grafana-mcp is required." >&2
  exit 1
}

policy_file="$tmp/mcp-grafana-vso.hcl"
cat >"$policy_file" <<'POLICY'
path "kv/data/monitoring/grafana-mcp" {
  capabilities = ["read"]
}
POLICY
chmod 0600 "$policy_file"
k3s kubectl cp "$policy_file" "vault/vault-0:$pod_policy" >/dev/null
vault_cmd policy write mcp-grafana-vso "$pod_policy" >/dev/null
vault_cmd write auth/kubernetes/role/mcp-grafana-vso \
  bound_service_account_names=mcp-grafana-vault-auth \
  bound_service_account_namespaces=mcp \
  audience=vault \
  token_policies=mcp-grafana-vso \
  token_ttl=1h >/dev/null

if k3s kubectl -n mcp get vaultstaticsecret grafana-mcp-credentials >/dev/null 2>&1; then
  k3s kubectl -n mcp annotate vaultstaticsecret grafana-mcp-credentials "force-sync=$(date +%s)" --overwrite >/dev/null
  ready=""
  for _ in {1..60}; do
    ready="$(k3s kubectl -n mcp get vaultstaticsecret grafana-mcp-credentials -o 'jsonpath={.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
    if [[ "$ready" == "True" ]]; then break; fi
    sleep 2
  done
  if [[ "$ready" != "True" ]]; then
    echo "Vault role is configured, but VSO did not sync grafana-mcp-credentials." >&2
    exit 1
  fi
  echo "Created the least-privilege Grafana MCP Vault role and synced its gateway credential. Secret values were not printed."
else
  echo "Created the least-privilege Grafana MCP Vault role. VSO will sync the credential after the GitOps resource is added."
fi
