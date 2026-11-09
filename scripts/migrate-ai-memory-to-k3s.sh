#!/usr/bin/env bash
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run inside Ubuntu with sudo." >&2; exit 1; }
command -v k3s >/dev/null 2>&1 || { echo "K3s is required on this WSL host." >&2; exit 1; }

source_data=/home/eorbr/ai-memory/ai-memory/data
backup=/home/eorbr/ai-memory/backups/ai-memory-pre-migration-2026-11-10.tar.gz
backup_checksum="${backup}.sha256"
namespace=ai-memory
claim=ai-memory-data
job=ai-memory-import

[[ -s "$backup" ]] || { echo "Verified pre-migration backup is missing: $backup" >&2; exit 1; }
[[ -s "$backup_checksum" ]] || { echo "Backup checksum file is missing: $backup_checksum" >&2; exit 1; }
(cd "$(dirname "$backup")" && sha256sum -c "$(basename "$backup_checksum")" >/dev/null) || {
  echo "The pre-migration backup checksum does not match." >&2; exit 1;
}
tar -tzf "$backup" >/dev/null || { echo "The pre-migration backup archive is invalid." >&2; exit 1; }
[[ -s "$source_data/db/memory.sqlite" && -d "$source_data/wiki" && -d "$source_data/models" ]] || {
  echo "Source data is incomplete; refusing to migrate." >&2; exit 1;
}
if pgrep -x ai-memory >/dev/null || ss -ltnp 2>/dev/null | grep -qE '[:.]49374[[:space:]]'; then
  echo "ai-memory is running; stop it before migrating to avoid a changing source." >&2
  exit 1
fi

tmp="$(mktemp -d /dev/shm/ai-memory-import.XXXXXX)"
chmod 0700 "$tmp"
cleanup() {
  k3s kubectl -n "$namespace" delete job "$job" --ignore-not-found=true --wait=false >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

k3s kubectl -n "$namespace" wait --for=jsonpath='{.status.phase}'=Bound pvc/"$claim" --timeout=180s >/dev/null
k3s kubectl -n "$namespace" delete job "$job" --ignore-not-found=true --wait=true >/dev/null
cat >"$tmp/job.yaml" <<'YAML'
apiVersion: batch/v1
kind: Job
metadata:
  name: ai-memory-import
  namespace: ai-memory
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 1800
  template:
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
      containers:
        - name: import
          image: python:3.13-slim@sha256:48d884813cf44e927cf64c5529dd630dd3b18338c6dc727991eef9b71e934a20
          imagePullPolicy: IfNotPresent
          command: [python3, -c]
          args:
            - |
              import hashlib, os, shutil, sys
              source, data = '/source', '/data'
              db = os.path.join(source, 'db/memory.sqlite')
              assert os.path.getsize(db) > 0, 'source SQLite database is missing or empty'
              assert not os.path.exists(os.path.join(data, 'db/memory.sqlite')), 'PVC already contains a database; refusing to overwrite it'
              stage = os.path.join(data, '.migration-staging')
              shutil.rmtree(stage, ignore_errors=True)
              shutil.copytree(source, stage, copy_function=shutil.copy2)
              def manifest(root):
                  result = {}
                  for base, _, names in os.walk(root):
                      for name in names:
                          path = os.path.join(base, name)
                          rel = os.path.relpath(path, root)
                          digest = hashlib.sha256()
                          with open(path, 'rb') as stream:
                              for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                                  digest.update(chunk)
                          result[rel] = digest.hexdigest()
                  return result
              original = manifest(source)
              staged = manifest(stage)
              assert original == staged, 'staged file checksums differ from source'
              for name in os.listdir(stage):
                  os.rename(os.path.join(stage, name), os.path.join(data, name))
              os.rmdir(stage)
              assert manifest(data) == original, 'PVC file checksums differ from source'
              print('Imported and checksum-verified %d files.' % len(original))
          volumeMounts:
            - name: source
              mountPath: /source
              readOnly: true
            - name: data
              mountPath: /data
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: [ALL]
      volumes:
        - name: source
          hostPath:
            path: /home/eorbr/ai-memory/ai-memory/data
            type: Directory
        - name: data
          persistentVolumeClaim:
            claimName: ai-memory-data
YAML

k3s kubectl apply -f "$tmp/job.yaml" >/dev/null
k3s kubectl -n "$namespace" wait --for=condition=complete job/"$job" --timeout=1800s >/dev/null || {
  k3s kubectl -n "$namespace" logs job/"$job" >&2 || true
  echo "Import failed. The original data and backup were left untouched." >&2
  exit 1
}
k3s kubectl -n "$namespace" logs job/"$job"
echo "ai-memory data is checksum-verified on the K3s PVC. The WSL source and backup remain intact."
