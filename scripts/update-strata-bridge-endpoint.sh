#!/usr/bin/env bash
set -euo pipefail

command -v k3s >/dev/null 2>&1 || exit 0
gateway="$(ip -4 route show default | awk 'NR==1 {print $3}')"
[[ "$gateway" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo "Could not determine the WSL Windows-host gateway." >&2; exit 1; }

cat <<EOF | k3s kubectl apply -f - >/dev/null
apiVersion: v1
kind: Endpoints
metadata:
  name: strata-bridge
  namespace: litellm
subsets:
  - addresses:
      - ip: ${gateway}
    ports:
      - name: openai-api
        port: 18081
        protocol: TCP
EOF
