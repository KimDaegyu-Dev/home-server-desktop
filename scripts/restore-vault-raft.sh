#!/usr/bin/env bash
set -euo pipefail
set +x

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /path/to/source-raft-snapshot.snap" >&2
  exit 2
fi

if ! command -v k3s >/dev/null 2>&1; then
  echo "Run this script inside the configured Ubuntu host with K3s installed." >&2
  exit 1
fi

snapshot_path="$(realpath "$1")"
if [[ ! -f "${snapshot_path}" ]]; then
  echo "Snapshot file does not exist: ${snapshot_path}" >&2
  exit 1
fi

if ! k3s kubectl -n vault get pod vault-0 >/dev/null 2>&1; then
  echo "The Vault Helm release is not ready. Wait for the vault-0 Pod." >&2
  exit 1
fi

vault_status="$(k3s kubectl exec -n vault vault-0 -- vault status -format=json 2>/dev/null || true)"
if [[ -z "${vault_status}" ]] || ! grep -q '"initialized"[[:space:]]*:[[:space:]]*false' <<<"${vault_status}"; then
  echo "Refusing to restore: destination Vault must be reachable and uninitialized." >&2
  echo "This script initializes an empty destination and force-restores the provided snapshot." >&2
  exit 1
fi
unset vault_status

k3s kubectl cp "${snapshot_path}" vault/vault-0:/tmp/vault-raft-source.snap
k3s kubectl exec -n vault vault-0 -- vault operator raft snapshot inspect /tmp/vault-raft-source.snap >/dev/null
k3s kubectl exec -n vault vault-0 -- sh -c 'umask 077; vault operator init -key-shares=1 -key-threshold=1 -format=json > /tmp/vault-destination-init.json'

remote_restore_script='set -eu
init_file=/tmp/vault-destination-init.json
snapshot_file=/tmp/vault-raft-source.snap
key="$(sed -n '\''/"unseal_keys_b64": \[/,/]/s/^[[:space:]]*"\([^"]*\)".*/\1/p'\'' "$init_file" | head -n 1)"
token="$(sed -n '\''s/.*"root_token":[[:space:]]*"\([^"]*\)".*/\1/p'\'' "$init_file")"
if [ -z "$key" ] || [ -z "$token" ]; then
  echo "Could not read the temporary initialization output." >&2
  exit 1
fi
vault operator unseal "$key" >/dev/null
VAULT_TOKEN="$token" vault operator raft snapshot restore -force "$snapshot_file"
unset key token'

k3s kubectl exec -n vault vault-0 -- sh -c "${remote_restore_script}"
unset remote_restore_script
echo "Snapshot restore accepted. Wait for the Vault restore logs, then unseal with the original source key shares."
