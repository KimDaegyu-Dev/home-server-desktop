#!/usr/bin/env bash
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run inside Ubuntu with sudo." >&2; exit 1; }
[[ $# -eq 1 && -r "$1" ]] || { echo "Usage: sudo bash scripts/migrate-watch-skill-to-k3s.sh /path/to/watch-skill-backup.tar.gz" >&2; exit 2; }

archive="$(realpath "$1")"
checksum_file="${archive}.sha256"
[[ -r "$checksum_file" ]] || { echo "Backup checksum file is missing: $checksum_file" >&2; exit 1; }
(
  cd "$(dirname "$archive")"
  sha256sum -c "$(basename "$checksum_file")"
)
expected="$(sha256sum "$archive" | cut -d ' ' -f1)"

k3s kubectl -n watch-skill wait --for=jsonpath='{.status.phase}'=Bound pvc/watch-skill-data --timeout=120s >/dev/null
runtime_config="$(k3s kubectl -n watch-skill get deployment watch-skill -o 'jsonpath={.spec.template.spec.volumes[?(@.name=="runtime-config")].configMap.name}')"
[[ -n "$runtime_config" ]] || { echo "Watch Skill runtime ConfigMap is not installed yet." >&2; exit 1; }
job="watch-skill-restore-$(date -u +%Y%m%d%H%M%S)"
cleanup() {
  k3s kubectl -n watch-skill delete job "$job" --ignore-not-found=true --wait=false >/dev/null 2>&1 || true
}
trap cleanup EXIT

k3s kubectl apply -f - >/dev/null <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: ${job}
  namespace: watch-skill
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 900
  template:
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
      containers:
        - name: restore
          image: python:3.13-slim@sha256:48d884813cf44e927cf64c5529dd630dd3b18338c6dc727991eef9b71e934a20
          command: [/bin/sh, -ec]
          args: ["sleep 900"]
          volumeMounts:
            - name: data
              mountPath: /data
            - name: runtime-config
              mountPath: /runtime
              readOnly: true
            - name: tmp
              mountPath: /tmp
          resources:
            requests:
              cpu: 10m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 256Mi
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: [ALL]
            readOnlyRootFilesystem: true
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: watch-skill-data
        - name: runtime-config
          configMap:
            name: ${runtime_config}
            defaultMode: 0444
            items:
              - key: restore-watch-skill.py
                path: restore-watch-skill.py
        - name: tmp
          emptyDir:
            sizeLimit: 512Mi
YAML

k3s kubectl -n watch-skill wait --for=condition=Ready pod -l "job-name=$job" --timeout=180s >/dev/null
pod="$(k3s kubectl -n watch-skill get pod -l "job-name=$job" -o jsonpath='{.items[0].metadata.name}')"
k3s kubectl -n watch-skill exec -i "$pod" -- python3 -c 'import pathlib,sys; pathlib.Path("/tmp/watch-skill.tar.gz").write_bytes(sys.stdin.buffer.read())' <"$archive"
k3s kubectl -n watch-skill exec "$pod" -- python3 /runtime/restore-watch-skill.py /tmp/watch-skill.tar.gz "$expected"

echo "Watch Skill data was imported and SQLite integrity verified. The WSL source and archive remain untouched."
