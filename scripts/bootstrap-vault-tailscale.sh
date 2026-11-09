#!/usr/bin/env bash
set -euo pipefail
set +x

auth_only=false
if [[ "${1:-}" == "--auth-only" ]]; then
  auth_only=true
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--auth-only]" >&2
  exit 2
fi

if ! command -v k3s >/dev/null 2>&1; then
  echo "Run this script inside the configured Ubuntu host with K3s installed." >&2
  exit 1
fi

if ! k3s kubectl -n vault get pod vault-0 >/dev/null 2>&1; then
  echo "The Vault Helm release is not ready. Wait for the vault-0 Pod." >&2
  exit 1
fi

if ! k3s kubectl -n tailscale get serviceaccount tailscale-vault-auth >/dev/null 2>&1; then
  echo "The Argo CD tailscale-credentials Application has not created its ServiceAccount yet." >&2
  exit 1
fi

vault_status="$(k3s kubectl exec -n vault vault-0 -- vault status -format=json 2>/dev/null || true)"
if [[ -z "${vault_status}" ]] || ! grep -q '"initialized"[[:space:]]*:[[:space:]]*true' <<<"${vault_status}" || grep -q '"sealed"[[:space:]]*:[[:space:]]*true' <<<"${vault_status}"; then
  echo "Vault must be initialized and unsealed before bootstrapping Kubernetes auth." >&2
  exit 1
fi
unset vault_status

read -r -s -p "Vault admin token (input hidden): " VAULT_TOKEN
echo
payload_file=""
pod_payload=""
cleanup() {
  unset VAULT_TOKEN
  if [[ -n "${pod_payload}" ]]; then
    k3s kubectl exec -n vault vault-0 -- rm -f "${pod_payload}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${payload_file}" ]]; then
    rm -f "${payload_file}"
  fi
}
trap cleanup EXIT

# Send the admin token over kubectl exec stdin so it is not placed in process arguments.
vault_cmd() {
  if [[ -t 0 ]]; then
    printf '%s\n' "${VAULT_TOKEN}" | k3s kubectl exec -i -n vault vault-0 -- sh -c 'IFS= read -r VAULT_TOKEN; export VAULT_TOKEN VAULT_ADDR=http://127.0.0.1:8200; exec vault "$@"' sh "$@"
  else
    { printf '%s\n' "${VAULT_TOKEN}"; cat; } | k3s kubectl exec -i -n vault vault-0 -- sh -c 'IFS= read -r VAULT_TOKEN; export VAULT_TOKEN VAULT_ADDR=http://127.0.0.1:8200; exec vault "$@"' sh "$@"
  fi
}

if ! vault_cmd auth list -format=json | grep -q '"kubernetes/"'; then
  vault_cmd auth enable -path=kubernetes kubernetes
fi

vault_cmd write auth/kubernetes/config \
  kubernetes_host=https://kubernetes.default.svc \
  kubernetes_ca_cert=@/var/run/secrets/kubernetes.io/serviceaccount/ca.crt \
  disable_iss_validation=true

if ! vault_cmd secrets list -format=json | grep -q '"kv/"'; then
  vault_cmd secrets enable -path=kv kv-v2
fi

vault_cmd policy write tailscale-operator - <<'POLICY'
path "kv/data/tailscale/operator" {
  capabilities = ["read"]
}
POLICY

vault_cmd write auth/kubernetes/role/tailscale-operator \
  bound_service_account_names=tailscale-vault-auth \
  bound_service_account_namespaces=tailscale \
  audience=vault \
  token_policies=tailscale-operator \
  token_ttl=1h

if [[ "${auth_only}" == true ]]; then
  echo "Updated Vault Kubernetes auth for in-cluster TokenReview. Existing KV credentials were left untouched."
  exit 0
fi

read -r -p "Tailscale OAuth client ID: " client_id
read -r -s -p "Tailscale OAuth client secret (input hidden): " client_secret
echo

payload_file="$(mktemp /dev/shm/tailscale-oauth.XXXXXX.json)"
chmod 0600 "${payload_file}"
printf '%s\n%s' "${client_id}" "${client_secret}" | python3 -c 'import json,sys; v=sys.stdin.read().splitlines(); json.dump({"client_id":v[0],"client_secret":v[1]},sys.stdout)' >"${payload_file}"
unset client_id client_secret

pod_payload="/tmp/tailscale-oauth-$(date +%s)-$$.json"
k3s kubectl cp "${payload_file}" "vault/vault-0:${pod_payload}"
vault_cmd kv put -mount=kv tailscale/operator "@${pod_payload}"
echo "Stored OAuth credentials in Vault at kv/tailscale/operator. VSO will synchronize them to the tailscale namespace."
