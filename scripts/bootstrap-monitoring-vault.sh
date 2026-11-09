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

if ! command -v k3s >/dev/null 2>&1; then
  echo "Run this script inside the configured Ubuntu host with K3s installed." >&2
  exit 1
fi
if ! k3s kubectl -n vault get pod vault-0 >/dev/null 2>&1 || ! k3s kubectl -n monitoring get serviceaccount monitoring-vault-auth >/dev/null 2>&1; then
  echo "Wait for Vault and the monitoring-credentials Application before bootstrapping." >&2
  exit 1
fi

vault_status="$(k3s kubectl exec -n vault vault-0 -- vault status -format=json 2>/dev/null || true)"
if [[ -z "${vault_status}" ]] || ! grep -q '"initialized"[[:space:]]*:[[:space:]]*true' <<<"${vault_status}" || grep -q '"sealed"[[:space:]]*:[[:space:]]*true' <<<"${vault_status}"; then
  echo "Vault must be initialized and unsealed before storing Grafana credentials." >&2
  exit 1
fi
unset vault_status

token_file=""
policy_file=""
payload_file=""
pod_token=""
pod_policy=""
pod_payload=""
cleanup() {
  local remote_files=() local_files=()
  [[ -n "${pod_payload}" ]] && remote_files+=("${pod_payload}")
  [[ -n "${pod_policy}" ]] && remote_files+=("${pod_policy}")
  [[ -n "${pod_token}" ]] && remote_files+=("${pod_token}")
  if ((${#remote_files[@]})); then
    k3s kubectl exec -n vault vault-0 -- rm -f "${remote_files[@]}" >/dev/null 2>&1 || true
  fi
  [[ -n "${payload_file}" ]] && rm -f "${payload_file}"
  [[ -n "${policy_file}" ]] && rm -f "${policy_file}"
  [[ -n "${token_file}" ]] && rm -f "${token_file}"
  unset VAULT_TOKEN admin_password
}
trap cleanup EXIT

token_file="$(mktemp /dev/shm/vault-admin-token.XXXXXX)"
chmod 0600 "${token_file}"
if [[ -n "${init_file}" ]]; then
  if [[ ! -r "${init_file}" ]]; then
    echo "Vault init file is not readable: ${init_file}" >&2
    exit 1
  fi
  python3 -c 'import json,sys; value=json.load(open(sys.argv[1])).get("root_token"); assert isinstance(value,str) and value; print(value,end="")' "${init_file}" >"${token_file}"
else
  read -r -s -p "Vault admin token (input hidden): " VAULT_TOKEN
  echo
  printf '%s' "${VAULT_TOKEN}" >"${token_file}"
  unset VAULT_TOKEN
fi
pod_token="/tmp/vault-admin-token-monitoring-$(date +%s)-$$"
k3s kubectl cp "${token_file}" "vault/vault-0:${pod_token}"
vault_cmd() {
  k3s kubectl exec -n vault vault-0 -- sh -c 'token_file="$1"; shift; export VAULT_TOKEN="$(cat "$token_file")" VAULT_ADDR=http://127.0.0.1:8200; exec vault "$@"' sh "${pod_token}" "$@"
}

if vault_cmd kv metadata get -mount=kv monitoring/grafana >/dev/null 2>&1; then
  echo "Grafana credentials already exist at kv/monitoring/grafana; refusing to replace them." >&2
  exit 1
fi

policy_file="$(mktemp /dev/shm/monitoring-policy.XXXXXX.hcl)"
chmod 0600 "${policy_file}"
cat >"${policy_file}" <<'POLICY'
path "kv/data/monitoring/grafana" {
  capabilities = ["read"]
}
POLICY
pod_policy="/tmp/monitoring-policy-$(date +%s)-$$.hcl"
k3s kubectl cp "${policy_file}" "vault/vault-0:${pod_policy}"
vault_cmd policy write monitoring-vso "${pod_policy}"
vault_cmd write auth/kubernetes/role/monitoring-vso \
  bound_service_account_names=monitoring-vault-auth \
  bound_service_account_namespaces=monitoring \
  audience=vault \
  token_policies=monitoring-vso \
  token_ttl=1h

admin_password="$(openssl rand -hex 32)"
payload_file="$(mktemp /dev/shm/grafana-admin.XXXXXX.json)"
chmod 0600 "${payload_file}"
printf '%s' "${admin_password}" | python3 -c 'import json,sys; json.dump({"admin-user":"admin","admin-password":sys.stdin.read()},open(sys.argv[1],"w"))' "${payload_file}"
unset admin_password
pod_payload="/tmp/grafana-admin-$(date +%s)-$$.json"
k3s kubectl cp "${payload_file}" "vault/vault-0:${pod_payload}"
vault_cmd kv put -mount=kv monitoring/grafana "@${pod_payload}" >/dev/null
echo "Stored the Grafana admin credentials in Vault at kv/monitoring/grafana."
