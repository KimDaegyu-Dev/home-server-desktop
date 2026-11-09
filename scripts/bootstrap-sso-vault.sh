#!/usr/bin/env bash
set -euo pipefail
set +x

init_file=""
mode="all"
while (($#)); do
  case "$1" in
    --token-file)
      [[ $# -ge 2 ]] || { echo "--token-file requires a path." >&2; exit 2; }
      init_file="$2"
      shift 2
      ;;
    --seed-only)
      mode="seed-only"
      shift
      ;;
    --configure-only)
      mode="configure-only"
      shift
      ;;
    --rotate-secrets)
      mode="rotate-secrets"
      shift
      ;;
    *)
      echo "Usage: $0 [--token-file PATH] [--seed-only|--configure-only|--rotate-secrets]" >&2
      exit 2
      ;;
  esac
done

if ! command -v k3s >/dev/null 2>&1 || ! command -v openssl >/dev/null 2>&1; then
  echo "Run this script inside the configured Ubuntu host with K3s and OpenSSL installed." >&2
  exit 1
fi

vault_status="$(k3s kubectl exec -n vault vault-0 -- vault status -format=json 2>/dev/null || true)"
if [[ -z "${vault_status}" ]] || ! grep -q '"initialized"[[:space:]]*:[[:space:]]*true' <<<"${vault_status}" || grep -q '"sealed"[[:space:]]*:[[:space:]]*true' <<<"${vault_status}"; then
  echo "Vault must be initialized and unsealed before configuring OIDC." >&2
  exit 1
fi
unset vault_status

token_file=""
clients_file=""
grafana_file=""
vault_config_file=""
policy_file=""
argocd_patch_file=""
vault_role_file=""
pod_token=""
pod_clients=""
pod_grafana=""
pod_config=""
pod_role=""
pod_policy=""
cleanup() {
  local remote_files=() local_files=()
  [[ -n "${pod_clients}" ]] && remote_files+=("${pod_clients}")
  [[ -n "${pod_grafana}" ]] && remote_files+=("${pod_grafana}")
  [[ -n "${pod_config}" ]] && remote_files+=("${pod_config}")
  [[ -n "${pod_role}" ]] && remote_files+=("${pod_role}")
  [[ -n "${pod_policy}" ]] && remote_files+=("${pod_policy}")
  [[ -n "${pod_token}" ]] && remote_files+=("${pod_token}")
  if ((${#remote_files[@]})); then
    k3s kubectl exec -n vault vault-0 -- rm -f "${remote_files[@]}" >/dev/null 2>&1 || true
  fi
  [[ -n "${clients_file}" ]] && local_files+=("${clients_file}")
  [[ -n "${grafana_file}" ]] && local_files+=("${grafana_file}")
  [[ -n "${vault_config_file}" ]] && local_files+=("${vault_config_file}")
  [[ -n "${policy_file}" ]] && local_files+=("${policy_file}")
  [[ -n "${argocd_patch_file}" ]] && local_files+=("${argocd_patch_file}")
  [[ -n "${vault_role_file}" ]] && local_files+=("${vault_role_file}")
  [[ -n "${token_file}" ]] && local_files+=("${token_file}")
  ((${#local_files[@]})) && rm -f "${local_files[@]}"
  unset VAULT_TOKEN grafana_secret argocd_secret vault_secret
}
trap cleanup EXIT

token_file="$(mktemp /dev/shm/vault-admin-token-sso.XXXXXX)"
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
pod_token="/tmp/vault-admin-token-sso-$(date +%s)-$$"
k3s kubectl cp "${token_file}" "vault/vault-0:${pod_token}"
vault_cmd() {
  k3s kubectl exec -n vault vault-0 -- sh -c 'token_file="$1"; shift; export VAULT_TOKEN="$(cat "$token_file")" VAULT_ADDR=http://127.0.0.1:8200; exec vault "$@"' sh "${pod_token}" "$@"
}

if [[ "${mode}" == "rotate-secrets" ]]; then
  grafana_secret="$(openssl rand -hex 32)"
  argocd_secret="$(openssl rand -hex 32)"
  vault_secret="$(openssl rand -hex 32)"
  clients_file="$(mktemp /dev/shm/authentik-sso-clients.XXXXXX.json)"
  grafana_file="$(mktemp /dev/shm/grafana-oidc.XXXXXX.json)"
  chmod 0600 "${clients_file}" "${grafana_file}"
  printf '%s\n%s\n%s\n' "${grafana_secret}" "${argocd_secret}" "${vault_secret}" | python3 -c 'import json,sys; grafana,argocd,vault=sys.stdin.read().splitlines(); json.dump({"grafana_client_secret":grafana,"argocd_client_secret":argocd,"vault_client_secret":vault},open(sys.argv[1],"w"))' "${clients_file}"
  printf '%s' "${grafana_secret}" | python3 -c 'import json,sys; json.dump({"client_id":"grafana","client_secret":sys.stdin.read()},open(sys.argv[1],"w"))' "${grafana_file}"
  pod_clients="/tmp/authentik-sso-clients-$(date +%s)-$$.json"
  pod_grafana="/tmp/grafana-oidc-$(date +%s)-$$.json"
  k3s kubectl cp "${clients_file}" "vault/vault-0:${pod_clients}"
  k3s kubectl cp "${grafana_file}" "vault/vault-0:${pod_grafana}"
  vault_cmd kv put -mount=kv authentik/sso-clients "@${pod_clients}" >/dev/null
  vault_cmd kv put -mount=kv monitoring/grafana-oidc "@${pod_grafana}" >/dev/null
  echo "Rotated the Authentik OIDC client secrets in Vault."
  exit 0
fi

if vault_cmd kv metadata get -mount=kv authentik/sso-clients >/dev/null 2>&1; then
  grafana_secret="$(vault_cmd kv get -field=grafana_client_secret -mount=kv authentik/sso-clients)"
  argocd_secret="$(vault_cmd kv get -field=argocd_client_secret -mount=kv authentik/sso-clients)"
  vault_secret="$(vault_cmd kv get -field=vault_client_secret -mount=kv authentik/sso-clients)"
else
  grafana_secret="$(openssl rand -hex 32)"
  argocd_secret="$(openssl rand -hex 32)"
  vault_secret="$(openssl rand -hex 32)"
  clients_file="$(mktemp /dev/shm/authentik-sso-clients.XXXXXX.json)"
  chmod 0600 "${clients_file}"
  printf '%s\n%s\n%s\n' "${grafana_secret}" "${argocd_secret}" "${vault_secret}" | python3 -c 'import json,sys; grafana,argocd,vault=sys.stdin.read().splitlines(); json.dump({"grafana_client_secret":grafana,"argocd_client_secret":argocd,"vault_client_secret":vault},open(sys.argv[1],"w"))' "${clients_file}"
  pod_clients="/tmp/authentik-sso-clients-$(date +%s)-$$.json"
  k3s kubectl cp "${clients_file}" "vault/vault-0:${pod_clients}"
  vault_cmd kv put -mount=kv authentik/sso-clients "@${pod_clients}" >/dev/null
fi

if ! vault_cmd kv metadata get -mount=kv monitoring/grafana-oidc >/dev/null 2>&1; then
  grafana_file="$(mktemp /dev/shm/grafana-oidc.XXXXXX.json)"
  chmod 0600 "${grafana_file}"
  printf '%s' "${grafana_secret}" | python3 -c 'import json,sys; json.dump({"client_id":"grafana","client_secret":sys.stdin.read()},open(sys.argv[1],"w"))' "${grafana_file}"
  pod_grafana="/tmp/grafana-oidc-$(date +%s)-$$.json"
  k3s kubectl cp "${grafana_file}" "vault/vault-0:${pod_grafana}"
  vault_cmd kv put -mount=kv monitoring/grafana-oidc "@${pod_grafana}" >/dev/null
fi

policy_file="$(mktemp /dev/shm/monitoring-policy.XXXXXX.hcl)"
chmod 0600 "${policy_file}"
cat >"${policy_file}" <<'POLICY'
path "kv/data/monitoring/grafana" {
  capabilities = ["read"]
}
path "kv/data/monitoring/grafana-oidc" {
  capabilities = ["read"]
}
POLICY
pod_policy="/tmp/monitoring-policy-sso-$(date +%s)-$$.hcl"
k3s kubectl cp "${policy_file}" "vault/vault-0:${pod_policy}"
vault_cmd policy write monitoring-vso "${pod_policy}" >/dev/null
rm -f "${policy_file}"
policy_file=""
k3s kubectl exec -n vault vault-0 -- rm -f "${pod_policy}" >/dev/null 2>&1 || true
pod_policy=""

if [[ "${mode}" == "seed-only" ]]; then
  echo "Stored the OIDC client secrets in Vault and updated the Grafana VSO read policy."
  echo "Run this script again with --configure-only after Authentik has created the OIDC providers."
  exit 0
fi

if ! grep -q '"oidc/"' < <(vault_cmd auth list -format=json); then
  vault_cmd auth enable -path=oidc oidc >/dev/null
fi

vault_config_file="$(mktemp /dev/shm/vault-oidc-config.XXXXXX.json)"
chmod 0600 "${vault_config_file}"
printf '%s' "${vault_secret}" | python3 -c 'import json,sys; json.dump({"oidc_discovery_url":"https://authentik.tail5389e9.ts.net/application/o/vault/","oidc_client_id":"vault","oidc_client_secret":sys.stdin.read(),"default_role":"home-server-admin"},open(sys.argv[1],"w"))' "${vault_config_file}"
pod_config="/tmp/vault-oidc-config-$(date +%s)-$$.json"
k3s kubectl cp "${vault_config_file}" "vault/vault-0:${pod_config}"
vault_cmd write auth/oidc/config "@${pod_config}" >/dev/null

policy_file="$(mktemp /dev/shm/vault-oidc-admin.XXXXXX.hcl)"
chmod 0600 "${policy_file}"
cat >"${policy_file}" <<'POLICY'
path "*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}
POLICY
pod_policy="/tmp/vault-oidc-admin-$(date +%s)-$$.hcl"
k3s kubectl cp "${policy_file}" "vault/vault-0:${pod_policy}"
vault_cmd policy write home-server-oidc-admin "${pod_policy}" >/dev/null
rm -f "${policy_file}"
policy_file=""
k3s kubectl exec -n vault vault-0 -- rm -f "${pod_policy}" >/dev/null 2>&1 || true
pod_policy=""
vault_role_file="$(mktemp /dev/shm/vault-oidc-role.XXXXXX.json)"
chmod 0600 "${vault_role_file}"
python3 -c 'import json,sys; json.dump({"role_type":"oidc","user_claim":"preferred_username","bound_audiences":["vault"],"bound_claims":{"preferred_username":["akadmin"]},"groups_claim":"groups","token_policies":["home-server-oidc-admin"],"allowed_redirect_uris":["https://vault.tail5389e9.ts.net/ui/vault/auth/oidc/oidc/callback","http://localhost:8250/oidc/callback"],"oidc_scopes":["openid","profile","email"],"ttl":"1h"},open(sys.argv[1],"w"))' "${vault_role_file}"
pod_role="/tmp/vault-oidc-role-$(date +%s)-$$.json"
k3s kubectl cp "${vault_role_file}" "vault/vault-0:${pod_role}"
vault_cmd write auth/oidc/role/home-server-admin "@${pod_role}" >/dev/null
rm -f "${vault_role_file}"
vault_role_file=""

argocd_secret="$(vault_cmd kv get -field=argocd_client_secret -mount=kv authentik/sso-clients)"
argocd_patch_file="$(mktemp /dev/shm/argocd-oidc-secret.XXXXXX.json)"
chmod 0600 "${argocd_patch_file}"
printf '%s' "${argocd_secret}" | python3 -c 'import json,sys; json.dump({"stringData":{"oidc.authentik.clientSecret":sys.stdin.read()}},open(sys.argv[1],"w"))' "${argocd_patch_file}"
k3s kubectl -n argocd patch secret argocd-secret --type=merge --patch-file="${argocd_patch_file}" >/dev/null

echo "Stored Authentik OIDC client secrets in Vault, configured Vault OIDC, and provisioned Argo CD's OIDC secret."
echo "The Authentik and Grafana OAuth clients are reconciled by the GitOps blueprint and Vault Secrets Operator."
echo "Vault retains its local token login as a recovery path."
