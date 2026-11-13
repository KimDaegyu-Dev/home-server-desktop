#!/usr/bin/env bash
set -euo pipefail
set +x

[[ $EUID -eq 0 ]] || { echo "Run inside Ubuntu with sudo." >&2; exit 1; }
command -v buildah >/dev/null 2>&1 || { echo "Install buildah first; Docker Engine is not required." >&2; exit 1; }
command -v k3s >/dev/null 2>&1 || { echo "Run on the configured K3s host." >&2; exit 1; }

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
versions="${repo_root}/versions.yml"
image="$(awk -F '"' '$1 ~ /^kanzo_agent_worker_image:/ {print $2}' "${versions}")"
base_name="$(awk -F '"' '$1 ~ /^hermes_agent_image:/ {print $2}' "${versions}")"
base_digest="$(awk -F '"' '$1 ~ /^hermes_agent_linux_amd64_digest:/ {print $2}' "${versions}")"
omh_commit="$(awk -F '"' '$1 ~ /^oh_my_hermes_source_commit:/ {print $2}' "${versions}")"
[[ -n "${image}" && -n "${base_name}" && -n "${base_digest}" && -n "${omh_commit}" ]] || { echo "Kanzo worker, Hermes image, and OMH source pins are required in versions.yml." >&2; exit 1; }
base_image="${base_name}@${base_digest}"

archive="$(mktemp /dev/shm/kanzo-agent-worker.XXXXXX.oci.tar)"
cleanup() { rm -f "${archive}"; }
trap cleanup EXIT

buildah bud --format oci --platform linux/amd64 \
  --build-arg "HERMES_BASE_IMAGE=${base_image}" \
  --build-arg "OMH_SOURCE_COMMIT=${omh_commit}" \
  --file "${repo_root}/images/kanzo-agent-worker/Dockerfile" \
  --tag "${image}" "${repo_root}/images/kanzo-agent-worker"
buildah push "${image}" "oci-archive:${archive}:${image}"
k3s ctr -n k8s.io images import "${archive}"
k3s ctr -n k8s.io images list -q | grep -Fxq "${image}"
k3s ctr -n k8s.io run --rm --user 10000:10000 \
  --env HERMES_HOME=/opt/hermes --env HOME=/opt/hermes \
  "${image}" kanzo-agent-worker-pmcheck \
  /opt/hermes/.venv/bin/python -c \
  'from pm.install import activate; problems = activate(allow_incomplete=True); print(problems); assert not problems, problems'
echo "Built and imported ${image} from ${base_image}."
