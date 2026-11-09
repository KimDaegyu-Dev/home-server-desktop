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

payload_file=""
policy_file=""
pod_payload=""
pod_policy=""
token_file=""
pod_token=""
cleanup() {
  local remote_files=() local_files=()
  [[ -n "${pod_payload}" ]] && remote_files+=("${pod_payload}")
  [[ -n "${pod_policy}" ]] && remote_files+=("${pod_policy}")
  [[ -n "${pod_token}" ]] && remote_files+=("${pod_token}")
  [[ -n "${payload_file}" ]] && local_files+=("${payload_file}")
  [[ -n "${policy_file}" ]] && local_files+=("${policy_file}")
  [[ -n "${token_file}" ]] && local_files+=("${token_file}")
  if ((${#remote_files[@]})); then
    k3s kubectl exec -n vault vault-0 -- rm -f "${remote_files[@]}" >/dev/null 2>&1 || true
  fi
  if ((${#local_files[@]})); then
    rm -f "${local_files[@]}"
  fi
  unset VAULT_TOKEN
}
trap cleanup EXIT

vault_cmd() {
  k3s kubectl exec -n vault vault-0 -- sh -c 'token_file="$1"; shift; export VAULT_TOKEN="$(cat "$token_file")" VAULT_ADDR=http://127.0.0.1:8200; exec vault "$@"' sh "${pod_token}" "$@"
}

read -r -s -p "Vault admin token (input hidden): " VAULT_TOKEN
echo
token_file="$(mktemp /dev/shm/vault-admin-token.XXXXXX)"
chmod 0600 "${token_file}"
printf '%s' "${VAULT_TOKEN}" >"${token_file}"
pod_token="/tmp/vault-admin-token-$(date +%s)-$$"
k3s kubectl cp "${token_file}" "vault/vault-0:${pod_token}"
unset VAULT_TOKEN

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

policy_file="$(mktemp /dev/shm/tailscale-policy.XXXXXX.hcl)"
chmod 0600 "${policy_file}"
cat >"${policy_file}" <<'POLICY'
path "kv/data/tailscale/operator" {
  capabilities = ["read"]
}
POLICY
pod_policy="/tmp/tailscale-policy-$(date +%s)-$$.hcl"
k3s kubectl cp "${policy_file}" "vault/vault-0:${pod_policy}"
vault_cmd policy write tailscale-operator "${pod_policy}"

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
