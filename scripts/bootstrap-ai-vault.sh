#!/usr/bin/env bash
set -euo pipefail
set +x

mode="seed"
init_file=""
while (($#)); do
  case "$1" in
    --token-file)
      [[ $# -ge 2 ]] || { echo "--token-file requires a path." >&2; exit 2; }
      init_file="$2"
      shift 2
      ;;
    --langfuse-project-keys)
      mode="langfuse-project-keys"
      shift
      ;;
    *)
      echo "Usage: $0 [--token-file PATH] [--langfuse-project-keys]" >&2
      exit 2
      ;;
  esac
done

command -v k3s >/dev/null 2>&1 || { echo "Run inside the Ubuntu K3s host." >&2; exit 1; }
command -v openssl >/dev/null 2>&1 || { echo "OpenSSL is required." >&2; exit 1; }

tmp="$(mktemp -d /dev/shm/home-server-ai.XXXXXX)"
chmod 0700 "$tmp"
pod_token="/tmp/home-server-ai-token-$(date +%s)-$$"
pod_policy="/tmp/home-server-ai-policy-$(date +%s)-$$.hcl"
cleanup() {
  k3s kubectl exec -n vault vault-0 -- rm -f "$pod_token" "$pod_policy" >/dev/null 2>&1 || true
  rm -rf "$tmp"
  unset VAULT_TOKEN
}
trap cleanup EXIT

if [[ -n "$init_file" ]]; then
  [[ -r "$init_file" ]] || { echo "Cannot read Vault token file." >&2; exit 1; }
  python3 -c 'import json,sys; t=json.load(open(sys.argv[1])).get("root_token"); assert t; print(t,end="")' "$init_file" >"$tmp/token"
else
  read -r -s -p "Vault admin token (hidden): " VAULT_TOKEN
  echo
  printf '%s' "$VAULT_TOKEN" >"$tmp/token"
  unset VAULT_TOKEN
fi
chmod 0600 "$tmp/token"
k3s kubectl cp "$tmp/token" "vault/vault-0:$pod_token"
vault_cmd() {
  k3s kubectl exec -n vault vault-0 -- sh -c 'token_file="$1"; shift; export VAULT_TOKEN="$(cat "$token_file")" VAULT_ADDR=http://127.0.0.1:8200; exec vault "$@"' sh "$pod_token" "$@"
}

if [[ "$mode" == "langfuse-project-keys" ]]; then
  read -r -s -p "Langfuse project public key (hidden): " langfuse_public
  echo
  read -r -s -p "Langfuse project secret key (hidden): " langfuse_secret
  echo
  [[ -n "$langfuse_public" && -n "$langfuse_secret" ]] || { echo "Both Langfuse keys are required." >&2; exit 2; }
  LANGFUSE_PUBLIC_KEY="$langfuse_public" LANGFUSE_SECRET_KEY="$langfuse_secret" python3 - "$tmp/exporter.json" <<'PY'
import json, os, sys
with open(sys.argv[1], "w") as f:
    json.dump({"publicKey": os.environ["LANGFUSE_PUBLIC_KEY"], "secretKey": os.environ["LANGFUSE_SECRET_KEY"]}, f)
PY
  unset langfuse_public langfuse_secret LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY
  chmod 0600 "$tmp/exporter.json"
  vault_cmd kv put -mount=kv ai/langfuse-exporter "@$tmp/exporter.json" >/dev/null
  if ! k3s kubectl -n monitoring get vaultstaticsecret langfuse-exporter >/dev/null 2>&1; then
    echo "Stored the key, but the Langfuse exporter VaultStaticSecret is not installed yet; retry after AI telemetry syncs." >&2
    exit 1
  fi
  k3s kubectl -n monitoring annotate vaultstaticsecret langfuse-exporter "force-sync=$(date +%s)" --overwrite >/dev/null
  ready=""
  for _ in {1..60}; do
    ready="$(k3s kubectl -n monitoring get vaultstaticsecret langfuse-exporter -o 'jsonpath={.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
    if [[ "$ready" == "True" ]] && k3s kubectl -n monitoring get secret langfuse-exporter >/dev/null 2>&1; then break; fi
    sleep 2
  done
  if [[ "$ready" != "True" ]]; then echo "Langfuse exporter VaultStaticSecret did not become Ready." >&2; exit 1; fi
  k3s kubectl -n monitoring rollout restart deployment/alloy >/dev/null
  k3s kubectl -n monitoring rollout status deployment/alloy --timeout=180s >/dev/null
  echo "Stored Langfuse exporter credentials in Vault at kv/ai/langfuse-exporter."
  exit 0
fi

vault_status="$(vault_cmd status -format=json 2>/dev/null || true)"
if [[ -z "$vault_status" ]] || ! grep -q '"initialized"[[:space:]]*:[[:space:]]*true' <<<"$vault_status" || grep -q '"sealed"[[:space:]]*:[[:space:]]*true' <<<"$vault_status"; then
  echo "Vault must be initialized and unsealed." >&2
  exit 1
fi
unset vault_status

if vault_cmd kv metadata get -mount=kv ai/langfuse >/dev/null 2>&1 || vault_cmd kv metadata get -mount=kv ai/litellm >/dev/null 2>&1 || vault_cmd kv metadata get -mount=kv ai/langfuse-exporter >/dev/null 2>&1; then
  echo "AI bootstrap data already exists. Refusing to rotate credentials; use Vault's normal rotation procedure." >&2
  exit 1
fi

langfuse_salt="$(openssl rand -base64 32 | tr -d '\n')"
langfuse_encryption="$(openssl rand -hex 32)"
langfuse_nextauth="$(openssl rand -hex 32)"
postgres_password="$(openssl rand -hex 32)"
clickhouse_password="$(openssl rand -hex 32)"
valkey_password="$(openssl rand -hex 32)"
s3_user="$(openssl rand -hex 16)"
s3_password="$(openssl rand -hex 32)"
s3_config="$(S3_USER="$s3_user" S3_PASSWORD="$s3_password" python3 -c 'import json,os; print(json.dumps({"identities":[{"name":os.environ["S3_USER"],"credentials":[{"accessKey":os.environ["S3_USER"],"secretKey":os.environ["S3_PASSWORD"]}],"actions":["Admin","Read","Write","List","Tagging"]}]},separators=(",",":"))')"
langfuse_client="$(openssl rand -hex 32)"
litellm_client="$(openssl rand -hex 32)"
litellm_master="sk-$(openssl rand -hex 32)"
litellm_salt="$(openssl rand -hex 32)"
bridge_token="$(openssl rand -hex 32)"
lite_pg_password="$(openssl rand -hex 32)"

SALT="$langfuse_salt" ENCRYPTION="$langfuse_encryption" NEXTAUTH="$langfuse_nextauth" PG="$postgres_password" CH="$clickhouse_password" VALKEY="$valkey_password" S3_USER="$s3_user" S3_PASSWORD="$s3_password" S3_CONFIG="$s3_config" python3 - "$tmp/langfuse.json" <<'PY'
import json, os, sys
data = {
  "salt": os.environ["SALT"], "encryption-key": os.environ["ENCRYPTION"], "nextauth-secret": os.environ["NEXTAUTH"],
  "postgresql-password": os.environ["PG"], "POSTGRES_USER": "postgres", "POSTGRES_PASSWORD": os.environ["PG"],
  "POSTGRES_DB": "langfuse", "USERDB_USER": "langfuse", "USERDB_PASSWORD": os.environ["PG"],
  "clickhouse-password": os.environ["CH"], "redis-password": os.environ["VALKEY"], "default": os.environ["VALKEY"],
  "s3-user": os.environ["S3_USER"], "s3-password": os.environ["S3_PASSWORD"], "seaweedfs_s3_config": os.environ["S3_CONFIG"]
}
with open(sys.argv[1], "w") as f: json.dump(data, f)
PY
MASTER="$litellm_master" SALT="$litellm_salt" BRIDGE="$bridge_token" PG="$lite_pg_password" SSO="$litellm_client" python3 - "$tmp/litellm.json" <<'PY'
import json, os, sys
data = {"LITELLM_MASTER_KEY": os.environ["MASTER"], "PROXY_MASTER_KEY": os.environ["MASTER"], "LITELLM_SALT_KEY": os.environ["SALT"],
        "STRATA_BRIDGE_TOKEN": os.environ["BRIDGE"], "POSTGRES_USER": "litellm", "POSTGRES_PASSWORD": os.environ["PG"],
        "GENERIC_CLIENT_SECRET": os.environ["SSO"]}
with open(sys.argv[1], "w") as f: json.dump(data, f)
PY
chmod 0600 "$tmp"/*.json
vault_cmd kv put -mount=kv ai/langfuse "@$tmp/langfuse.json" >/dev/null
vault_cmd kv put -mount=kv ai/litellm "@$tmp/litellm.json" >/dev/null
python3 - "$tmp/langfuse-exporter.json" <<'PY'
import json, sys
with open(sys.argv[1], "w") as f: json.dump({"publicKey": "pending-vault-sync", "secretKey": "pending-vault-sync"}, f)
PY
chmod 0600 "$tmp/langfuse-exporter.json"
vault_cmd kv put -mount=kv ai/langfuse-exporter "@$tmp/langfuse-exporter.json" >/dev/null

LANGFUSE_CLIENT="$langfuse_client" python3 - "$tmp/langfuse-auth.json" <<'PY'
import json, os, sys
with open(sys.argv[1], "w") as f: json.dump({"AUTH_AUTHENTIK_CLIENT_SECRET": os.environ["LANGFUSE_CLIENT"]}, f)
PY
chmod 0600 "$tmp/langfuse-auth.json"
vault_cmd kv put -mount=kv ai/langfuse-auth "@$tmp/langfuse-auth.json" >/dev/null

vault_cmd kv get -format=json -mount=kv authentik/sso-clients >"$tmp/sso-current.json"
LANGFUSE_CLIENT="$langfuse_client" LITELLM_CLIENT="$litellm_client" python3 - "$tmp/sso-current.json" "$tmp/sso-updated.json" <<'PY'
import json, os, sys
with open(sys.argv[1]) as f: data = json.load(f)["data"]["data"]
data["langfuse_client_secret"] = os.environ["LANGFUSE_CLIENT"]
data["litellm_client_secret"] = os.environ["LITELLM_CLIENT"]
with open(sys.argv[2], "w") as f: json.dump(data, f)
PY
chmod 0600 "$tmp/sso-updated.json"
vault_cmd kv put -mount=kv authentik/sso-clients "@$tmp/sso-updated.json" >/dev/null

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
k3s kubectl cp "$tmp/langfuse-policy.hcl" "vault/vault-0:$pod_policy"
vault_cmd policy write langfuse-vso "$pod_policy" >/dev/null
k3s kubectl exec -n vault vault-0 -- rm -f "$pod_policy" >/dev/null
k3s kubectl cp "$tmp/litellm-policy.hcl" "vault/vault-0:$pod_policy"
vault_cmd policy write litellm-vso "$pod_policy" >/dev/null
k3s kubectl exec -n vault vault-0 -- rm -f "$pod_policy" >/dev/null
vault_cmd write auth/kubernetes/role/langfuse-vso bound_service_account_names=ai-vault-auth bound_service_account_namespaces=langfuse audience=vault token_policies=langfuse-vso token_ttl=1h >/dev/null
vault_cmd write auth/kubernetes/role/litellm-vso bound_service_account_names=ai-vault-auth bound_service_account_namespaces=litellm audience=vault token_policies=litellm-vso token_ttl=1h >/dev/null
k3s kubectl cp "$tmp/monitoring-policy.hcl" "vault/vault-0:$pod_policy"
vault_cmd policy write monitoring-vso "$pod_policy" >/dev/null
vault_cmd write auth/kubernetes/role/monitoring-vso bound_service_account_names=monitoring-vault-auth bound_service_account_namespaces=monitoring audience=vault token_policies=monitoring-vso token_ttl=1h >/dev/null

k3s kubectl -n authentik annotate vaultstaticsecret authentik-sso-clients "force-sync=$(date +%s)" --overwrite >/dev/null
ready=""
for _ in {1..60}; do
  ready="$(k3s kubectl -n authentik get vaultstaticsecret authentik-sso-clients -o 'jsonpath={.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
  if [[ "$ready" == "True" ]] && k3s kubectl -n authentik get secret authentik-sso-clients >/dev/null 2>&1; then break; fi
  sleep 2
done
if [[ "$ready" != "True" ]]; then echo "Authentik SSO VaultStaticSecret did not become Ready." >&2; exit 1; fi
k3s kubectl -n authentik rollout restart deployment/authentik-server deployment/authentik-worker >/dev/null
k3s kubectl -n authentik rollout status deployment/authentik-server --timeout=180s >/dev/null
k3s kubectl -n authentik rollout status deployment/authentik-worker --timeout=180s >/dev/null
k3s kubectl -n authentik exec deployment/authentik-worker -- python manage.py shell -c 'import os; from authentik.providers.oauth2.models import OAuth2Provider; pairs=[("grafana","AUTHENTIK_OIDC_GRAFANA_CLIENT_SECRET"),("argocd","AUTHENTIK_OIDC_ARGOCD_CLIENT_SECRET"),("vault","AUTHENTIK_OIDC_VAULT_CLIENT_SECRET"),("langfuse","AUTHENTIK_OIDC_LANGFUSE_CLIENT_SECRET"),("litellm","AUTHENTIK_OIDC_LITELLM_CLIENT_SECRET")]; assert all(OAuth2Provider.objects.filter(client_id=client).update(client_secret=os.environ[env]) == 1 for client,env in pairs), "Expected exactly one OAuth provider for every client id"' >/dev/null

echo "Seeded Langfuse, LiteLLM, Authentik, and initial Langfuse exporter credentials in Vault; configured narrow VSO policies and roles."
echo "Wait for the Authentik and AI VaultStaticSecrets to become Ready. For Authentik, restart server/worker after the new VSO keys appear."
echo "After first Langfuse UI setup, create a project API key and run this script with --langfuse-project-keys."
