#!/usr/bin/env bash
set -euo pipefail
set +x

export VAULT_ADDR="${VAULT_ADDR:-https://vault.home.arpa:8200}"
export VAULT_CACERT="${VAULT_CACERT:-/etc/vault.d/tls/ca.crt}"

if ! command -v vault >/dev/null 2>&1 || ! command -v k3s >/dev/null 2>&1; then
  echo "Run this script inside the configured Ubuntu host after Vault and K3s are installed." >&2
  exit 1
fi

if ! k3s kubectl -n tailscale get serviceaccount tailscale-vault-auth >/dev/null 2>&1; then
  echo "The Argo CD tailscale-credentials application has not created its ServiceAccount yet." >&2
  exit 1
fi

if ! k3s kubectl get clusterrolebinding tailscale-vault-auth-delegator >/dev/null 2>&1; then
  echo "The Tailscale Vault auth-delegator binding is missing; wait for Argo CD sync." >&2
  exit 1
fi

vault_status="$(vault status -format=json 2>/dev/null || true)"
if [[ -z "${vault_status}" ]] || ! grep -q '"initialized"[[:space:]]*:[[:space:]]*true' <<<"${vault_status}" || grep -q '"sealed"[[:space:]]*:[[:space:]]*true' <<<"${vault_status}"; then
  echo "Vault must be initialized and unsealed before bootstrapping Kubernetes auth." >&2
  exit 1
fi

read -r -s -p "Vault admin token (input hidden): " VAULT_TOKEN
echo
export VAULT_TOKEN
unset vault_status
trap 'unset VAULT_TOKEN; rm -f "${payload_file:-}"' EXIT

if ! vault auth list -format=json | grep -q '"kubernetes/"'; then
  vault auth enable -path=kubernetes kubernetes
fi

vault write auth/kubernetes/config \
  kubernetes_host=https://127.0.0.1:6443 \
  kubernetes_ca_cert=@/var/lib/rancher/k3s/server/tls/server-ca.crt \
  disable_iss_validation=true

if ! vault secrets list -format=json | grep -q '"kv/"'; then
  vault secrets enable -path=kv kv-v2
fi

vault policy write tailscale-operator - <<'POLICY'
path "kv/data/tailscale/operator" {
  capabilities = ["read"]
}
POLICY

vault write auth/kubernetes/role/tailscale-operator \
  bound_service_account_names=tailscale-vault-auth \
  bound_service_account_namespaces=tailscale \
  audience=vault \
  token_policies=tailscale-operator \
  token_ttl=1h

read -r -p "Tailscale OAuth client ID: " client_id
read -r -s -p "Tailscale OAuth client secret (input hidden): " client_secret
echo

payload_file="$(mktemp /dev/shm/tailscale-oauth.XXXXXX.json)"
chmod 0600 "${payload_file}"
printf '%s\n%s' "${client_id}" "${client_secret}" | python3 -c 'import json,sys; v=sys.stdin.read().splitlines(); json.dump({"client_id":v[0],"client_secret":v[1]},sys.stdout)' >"${payload_file}"
unset client_id client_secret

vault kv put -mount=kv tailscale/operator "@${payload_file}"
echo "Stored OAuth credentials in Vault at kv/tailscale/operator. VSO will sync them to the tailscale namespace."
