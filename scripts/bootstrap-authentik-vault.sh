#!/usr/bin/env bash
set -euo pipefail
set +x

if [[ $# -ne 0 ]]; then
  echo "Usage: $0" >&2
  exit 2
fi

if ! command -v k3s >/dev/null 2>&1; then
  echo "Run this script inside the configured Ubuntu host with K3s installed." >&2
  exit 1
fi

if ! k3s kubectl -n vault get pod vault-0 >/dev/null 2>&1; then
  echo "The in-cluster Vault Pod is not available." >&2
  exit 1
fi

if ! k3s kubectl -n authentik get serviceaccount authentik-vault-auth >/dev/null 2>&1; then
  echo "Wait for Argo CD to apply the authentik-credentials Application first." >&2
  exit 1
fi

vault_status="$(k3s kubectl exec -n vault vault-0 -- vault status -format=json 2>/dev/null || true)"
if [[ -z "${vault_status}" ]] || ! grep -q '"initialized"[[:space:]]*:[[:space:]]*true' <<<"${vault_status}" || grep -q '"sealed"[[:space:]]*:[[:space:]]*true' <<<"${vault_status}"; then
  echo "Vault must be initialized and unsealed before storing Authentik credentials." >&2
  exit 1
fi
unset vault_status

postgres_payload_file=""
config_payload_file=""
policy_file=""
pod_payload=""
pod_policy=""
token_file=""
pod_token=""
cleanup() {
  local remote_files=() local_files=()
  [[ -n "${pod_payload}" ]] && remote_files+=("${pod_payload}" "${pod_payload}-config")
  [[ -n "${pod_policy}" ]] && remote_files+=("${pod_policy}")
  [[ -n "${pod_token}" ]] && remote_files+=("${pod_token}")
  if ((${#remote_files[@]})); then
    k3s kubectl exec -n vault vault-0 -- rm -f "${remote_files[@]}" >/dev/null 2>&1 || true
  fi
  [[ -n "${postgres_payload_file}" ]] && rm -f "${postgres_payload_file}"
  [[ -n "${config_payload_file}" ]] && rm -f "${config_payload_file}"
  [[ -n "${policy_file}" ]] && rm -f "${policy_file}"
  [[ -n "${token_file}" ]] && rm -f "${token_file}"
  unset VAULT_TOKEN db_password postgres_password secret_key
}
trap cleanup EXIT

read -r -s -p "Vault admin token (input hidden): " VAULT_TOKEN
echo
token_file="$(mktemp /dev/shm/vault-admin-token.XXXXXX)"
chmod 0600 "${token_file}"
printf '%s' "${VAULT_TOKEN}" >"${token_file}"
pod_token="/tmp/vault-admin-token-authentik-$(date +%s)-$$"
k3s kubectl cp "${token_file}" "vault/vault-0:${pod_token}"
unset VAULT_TOKEN

vault_cmd() {
  k3s kubectl exec -n vault vault-0 -- sh -c 'token_file="$1"; shift; export VAULT_TOKEN="$(cat "$token_file")" VAULT_ADDR=http://127.0.0.1:8200; exec vault "$@"' sh "${pod_token}" "$@"
}

if vault_cmd kv metadata get -mount=kv authentik/postgresql >/dev/null 2>&1 || vault_cmd kv metadata get -mount=kv authentik/config >/dev/null 2>&1; then
  echo "Authentik Vault data already exists (or is partially initialized); refusing to replace it." >&2
  echo "Inspect kv/authentik/postgresql and kv/authentik/config in Vault before retrying." >&2
  exit 1
fi

policy_file="$(mktemp /dev/shm/authentik-policy.XXXXXX.hcl)"
chmod 0600 "${policy_file}"
cat >"${policy_file}" <<'POLICY'
path "kv/data/authentik/*" {
  capabilities = ["read"]
}
POLICY
pod_policy="/tmp/authentik-policy-$(date +%s)-$$.hcl"
k3s kubectl cp "${policy_file}" "vault/vault-0:${pod_policy}"
vault_cmd policy write authentik-vso "${pod_policy}"
vault_cmd write auth/kubernetes/role/authentik-vso \
  bound_service_account_names=authentik-vault-auth \
  bound_service_account_namespaces=authentik \
  audience=vault \
  token_policies=authentik-vso \
  token_ttl=1h

db_password="$(openssl rand -hex 32)"
postgres_password="$(openssl rand -hex 32)"
secret_key="$(openssl rand -hex 48)"
postgres_payload_file="$(mktemp /dev/shm/authentik-postgresql.XXXXXX.json)"
config_payload_file="$(mktemp /dev/shm/authentik-config.XXXXXX.json)"
chmod 0600 "${postgres_payload_file}" "${config_payload_file}"
printf '%s\n%s\n%s\n' "${db_password}" "${postgres_password}" "${secret_key}" | python3 -c 'import json,sys; db,admin,key=sys.stdin.read().splitlines(); json.dump({"password":db,"postgres-password":admin},open(sys.argv[1],"w")); json.dump({"secret_key":key,"postgresql__host":"authentik-postgresql","postgresql__name":"authentik","postgresql__user":"authentik","postgresql__password":db},open(sys.argv[2],"w"))' "${postgres_payload_file}" "${config_payload_file}"
unset db_password postgres_password secret_key

pod_payload="/tmp/authentik-secrets-$(date +%s)-$$.json"
k3s kubectl cp "${postgres_payload_file}" "vault/vault-0:${pod_payload}"
k3s kubectl cp "${config_payload_file}" "vault/vault-0:${pod_payload}-config"
vault_cmd kv put -mount=kv authentik/postgresql "@${pod_payload}" >/dev/null
vault_cmd kv put -mount=kv authentik/config "@${pod_payload}-config" >/dev/null

echo "Stored Authentik credentials in Vault at kv/authentik/{postgresql,config}."
echo "VSO will create the Kubernetes Secrets after the GitOps resources sync."
