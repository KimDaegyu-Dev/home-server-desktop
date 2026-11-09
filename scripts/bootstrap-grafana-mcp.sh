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

if ! command -v k3s >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1 || ! command -v openssl >/dev/null 2>&1; then
  echo "Run this script inside the configured Ubuntu host with K3s, curl, and OpenSSL installed." >&2
  exit 1
fi
if ! k3s kubectl -n monitoring get pod -l app.kubernetes.io/name=grafana >/dev/null 2>&1 || ! k3s kubectl -n grafana-mcp get serviceaccount grafana-mcp-vault-auth >/dev/null 2>&1; then
  echo "Wait for Grafana and the grafana-mcp-credentials Application before bootstrapping." >&2
  exit 1
fi

vault_status="$(k3s kubectl exec -n vault vault-0 -- vault status -format=json 2>/dev/null || true)"
if [[ -z "${vault_status}" ]] || ! grep -q '"initialized"[[:space:]]*:[[:space:]]*true' <<<"${vault_status}" || grep -q '"sealed"[[:space:]]*:[[:space:]]*true' <<<"${vault_status}"; then
  echo "Vault must be initialized and unsealed before configuring Grafana MCP." >&2
  exit 1
fi
unset vault_status

token_file=""
policy_file=""
payload_file=""
pod_token=""
pod_policy=""
pod_payload=""
forward_log=""
auth_config=""
vault_password_file=""
grafana_password=""
service_token=""
mcp_token=""
cleanup() {
  local remote_files=() local_files=()
  [[ -n "${pod_payload}" ]] && remote_files+=("${pod_payload}")
  [[ -n "${pod_policy}" ]] && remote_files+=("${pod_policy}")
  [[ -n "${pod_token}" ]] && remote_files+=("${pod_token}")
  if ((${#remote_files[@]})); then
    k3s kubectl exec -n vault vault-0 -- rm -f "${remote_files[@]}" >/dev/null 2>&1 || true
  fi
  [[ -n "${payload_file}" ]] && local_files+=("${payload_file}")
  [[ -n "${policy_file}" ]] && local_files+=("${policy_file}")
  [[ -n "${token_file}" ]] && local_files+=("${token_file}")
  [[ -n "${forward_log}" ]] && local_files+=("${forward_log}")
  [[ -n "${auth_config}" ]] && local_files+=("${auth_config}")
  [[ -n "${vault_password_file}" ]] && local_files+=("${vault_password_file}")
  ((${#local_files[@]})) && rm -f "${local_files[@]}"
  [[ -n "${port_forward_pid:-}" ]] && kill "${port_forward_pid}" >/dev/null 2>&1 || true
  unset VAULT_TOKEN admin_password grafana_password service_token mcp_token
}
trap cleanup EXIT

token_file="$(mktemp /dev/shm/vault-admin-token-grafana-mcp.XXXXXX)"
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
pod_token="/tmp/vault-admin-token-grafana-mcp-$(date +%s)-$$"
k3s kubectl cp "${token_file}" "vault/vault-0:${pod_token}"
vault_cmd() {
  k3s kubectl exec -n vault vault-0 -- sh -c 'token_file="$1"; shift; export VAULT_TOKEN="$(cat "$token_file")" VAULT_ADDR=http://127.0.0.1:8200; exec vault "$@"' sh "${pod_token}" "$@"
}

if vault_cmd kv metadata get -mount=kv monitoring/grafana-mcp >/dev/null 2>&1; then
  echo "Grafana MCP credentials already exist in Vault; refusing to issue another live token." >&2
  exit 1
fi

vault_password_file="$(mktemp /dev/shm/grafana-admin-password.XXXXXX)"
chmod 0600 "${vault_password_file}"
vault_cmd kv get -field=admin-password -mount=kv monitoring/grafana >"${vault_password_file}"
grafana_password="$(cat "${vault_password_file}")"
rm -f "${vault_password_file}"
vault_password_file=""
auth_config="$(mktemp /dev/shm/grafana-api-auth.XXXXXX.conf)"
chmod 0600 "${auth_config}"
printf 'user = "admin:%s"\n' "${grafana_password}" >"${auth_config}"

forward_log="$(mktemp /dev/shm/grafana-port-forward.XXXXXX.log)"
k3s kubectl -n monitoring port-forward service/monitoring-grafana 13000:80 >"${forward_log}" 2>&1 &
port_forward_pid=$!
ready=0
for _ in $(seq 1 30); do
  if curl --silent --fail http://127.0.0.1:13000/api/health >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "${ready}" != 1 ]]; then
  echo "Could not reach Grafana through kubectl port-forward." >&2
  exit 1
fi

service_account_id="$(curl --silent --show-error --fail --config "${auth_config}" \
  'http://127.0.0.1:13000/api/serviceaccounts/search?query=grafana-mcp' \
  | python3 -c 'import json,sys; data=json.load(sys.stdin); print(next((str(x["id"]) for x in data.get("serviceAccounts",[]) if x.get("name")=="grafana-mcp"),""))')"
if [[ -z "${service_account_id}" ]]; then
  service_account_id="$(curl --silent --show-error --fail --config "${auth_config}" \
    -H 'Content-Type: application/json' \
    -d '{"name":"grafana-mcp","role":"Editor","isDisabled":false}' \
    http://127.0.0.1:13000/api/serviceaccounts \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
fi
service_token="$(curl --silent --show-error --fail --config "${auth_config}" \
  -H 'Content-Type: application/json' \
  -d '{"name":"grafana-mcp-runtime"}' \
  "http://127.0.0.1:13000/api/serviceaccounts/${service_account_id}/tokens" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])')"
mcp_token="$(openssl rand -hex 32)"
unset grafana_password
rm -f "${auth_config}"
auth_config=""

payload_file="$(mktemp /dev/shm/grafana-mcp-credentials.XXXXXX.json)"
chmod 0600 "${payload_file}"
printf '%s\n%s\n' "${service_token}" "${mcp_token}" | python3 -c 'import json,sys; service,caller=sys.stdin.read().splitlines(); json.dump({"grafana_service_account_token":service,"mcp_server_token":caller},open(sys.argv[1],"w"))' "${payload_file}"
unset service_token mcp_token
pod_payload="/tmp/grafana-mcp-credentials-$(date +%s)-$$.json"
k3s kubectl cp "${payload_file}" "vault/vault-0:${pod_payload}"
vault_cmd kv put -mount=kv monitoring/grafana-mcp "@${pod_payload}" >/dev/null

policy_file="$(mktemp /dev/shm/grafana-mcp-policy.XXXXXX.hcl)"
chmod 0600 "${policy_file}"
cat >"${policy_file}" <<'POLICY'
path "kv/data/monitoring/grafana-mcp" {
  capabilities = ["read"]
}
POLICY
pod_policy="/tmp/grafana-mcp-policy-$(date +%s)-$$.hcl"
k3s kubectl cp "${policy_file}" "vault/vault-0:${pod_policy}"
vault_cmd policy write grafana-mcp-vso "${pod_policy}" >/dev/null
vault_cmd write auth/kubernetes/role/grafana-mcp-vso \
  bound_service_account_names=grafana-mcp-vault-auth \
  bound_service_account_namespaces=grafana-mcp \
  audience=vault \
  token_policies=grafana-mcp-vso \
  token_ttl=1h >/dev/null

echo "Created Grafana service account 'grafana-mcp' with Editor access and stored both MCP credentials in Vault."
echo "VSO will create the grafana-mcp-credentials Secret. The caller bearer token is available only from Vault at kv/monitoring/grafana-mcp (field mcp_server_token)."
