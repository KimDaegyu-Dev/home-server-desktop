#!/usr/bin/env bash
set -euo pipefail
set +x

command -v k3s >/dev/null 2>&1 || { echo "Run inside the Ubuntu K3s host." >&2; exit 1; }

tmp="$(mktemp -d /dev/shm/home-server-vault-repair.XXXXXX)"
chmod 0700 "$tmp"
pod_token="/tmp/home-server-vault-repair-token-$(date +%s)-$$"
pod_policy="/tmp/home-server-vault-repair-policy-$(date +%s)-$$.hcl"
cleanup() {
  k3s kubectl exec -n vault vault-0 -- rm -f "$pod_token" "$pod_policy" >/dev/null 2>&1 || true
  rm -rf "$tmp"
  unset VAULT_TOKEN
}
trap cleanup EXIT

read -r -s -p "Vault admin token (hidden): " VAULT_TOKEN
echo
printf '%s' "$VAULT_TOKEN" >"$tmp/token"
unset VAULT_TOKEN
chmod 0600 "$tmp/token"
k3s kubectl cp "$tmp/token" "vault/vault-0:$pod_token"

vault_cmd() {
  k3s kubectl exec -n vault vault-0 -- sh -c 'token_file="$1"; shift; export VAULT_TOKEN="$(cat "$token_file")" VAULT_ADDR=http://127.0.0.1:8200; exec vault "$@"' sh "$pod_token" "$@"
}

vault_status="$(vault_cmd status -format=json 2>/dev/null || true)"
if [[ -z "$vault_status" ]] || ! grep -q '"initialized"[[:space:]]*:[[:space:]]*true' <<<"$vault_status" || grep -q '"sealed"[[:space:]]*:[[:space:]]*true' <<<"$vault_status"; then
  echo "Vault must be initialized and unsealed." >&2
  exit 1
fi
unset vault_status

cat >"$tmp/langfuse-policy.hcl" <<'POLICY'
path "kv/data/ai/langfuse" { capabilities = ["read"] }
path "kv/data/ai/langfuse-auth" { capabilities = ["read"] }
POLICY
cat >"$tmp/litellm-policy.hcl" <<'POLICY'
path "kv/data/ai/litellm" { capabilities = ["read"] }
POLICY
cat >"$tmp/monitoring-policy.hcl" <<'POLICY'
path "kv/data/monitoring/grafana" { capabilities = ["read"] }
path "kv/data/monitoring/grafana-oidc" { capabilities = ["read"] }
path "kv/data/ai/langfuse-exporter" { capabilities = ["read"] }
POLICY
chmod 0600 "$tmp"/*.hcl

for namespace in langfuse litellm; do
  k3s kubectl -n "$namespace" get serviceaccount ai-vault-auth >/dev/null
done
k3s kubectl -n monitoring get serviceaccount monitoring-vault-auth >/dev/null

k3s kubectl cp "$tmp/langfuse-policy.hcl" "vault/vault-0:$pod_policy"
vault_cmd policy write langfuse-vso "$pod_policy" >/dev/null
k3s kubectl cp "$tmp/litellm-policy.hcl" "vault/vault-0:$pod_policy"
vault_cmd policy write litellm-vso "$pod_policy" >/dev/null
k3s kubectl cp "$tmp/monitoring-policy.hcl" "vault/vault-0:$pod_policy"
vault_cmd policy write monitoring-vso "$pod_policy" >/dev/null

vault_cmd write auth/kubernetes/role/langfuse-vso \
  bound_service_account_names=ai-vault-auth bound_service_account_namespaces=langfuse \
  audience=vault token_policies=langfuse-vso token_ttl=1h >/dev/null
vault_cmd write auth/kubernetes/role/litellm-vso \
  bound_service_account_names=ai-vault-auth bound_service_account_namespaces=litellm \
  audience=vault token_policies=litellm-vso token_ttl=1h >/dev/null
vault_cmd write auth/kubernetes/role/monitoring-vso \
  bound_service_account_names=monitoring-vault-auth bound_service_account_namespaces=monitoring \
  audience=vault token_policies=monitoring-vso token_ttl=1h >/dev/null

echo "Configured the Langfuse, LiteLLM, and monitoring VSO policies and Kubernetes auth roles."
echo "No Vault KV secret values were read or changed."
