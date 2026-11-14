#!/usr/bin/env bash
set -euo pipefail
set +x

[[ $EUID -eq 0 ]] || { echo "Run inside Ubuntu with sudo." >&2; exit 1; }
command -v buildah >/dev/null 2>&1 || { echo "Buildah is required; Docker Engine is not used." >&2; exit 1; }
command -v k3s >/dev/null 2>&1 || { echo "Run on the configured K3s host." >&2; exit 1; }

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
versions="${repo_root}/versions.yml"
image="$(awk -F '"' '$1 ~ /^kanzo_sandbox_image:/ {print $2}' "${versions}")"
base_image="$(awk -F '"' '$1 ~ /^kanzo_sandbox_base_image:/ {print $2}' "${versions}")"
pnpm_version="$(awk -F '"' '$1 ~ /^kanzo_sandbox_pnpm_version:/ {print $2}' "${versions}")"
broker_image="$(awk -F '"' '$1 ~ /^kanzo_sandbox_broker_image:/ {print $2}' "${versions}")"
broker_base_image="$(awk -F '"' '$1 ~ /^kanzo_sandbox_broker_base_image:/ {print $2}' "${versions}")"
[[ -n "${image}" && -n "${base_image}" && -n "${pnpm_version}" && -n "${broker_image}" && -n "${broker_base_image}" ]] || {
  echo "Sandbox/broker images, pinned bases, and pnpm version are required in versions.yml." >&2
  exit 1
}

archive="$(mktemp /dev/shm/kanzo-sandbox.XXXXXX.oci.tar)"
broker_archive="$(mktemp /dev/shm/kanzo-sandbox-broker.XXXXXX.oci.tar)"
runner_context="$(mktemp -d /dev/shm/kanzo-sandbox-context.XXXXXX)"
broker_context="$(mktemp -d /dev/shm/kanzo-sandbox-broker-context.XXXXXX)"
cleanup() {
  rm -f "${archive}" "${broker_archive}"
  rm -rf "${runner_context}" "${broker_context}"
}
trap cleanup EXIT

cp "${repo_root}/images/kanzo-sandbox/Dockerfile" "${runner_context}/Dockerfile"
cp "${repo_root}/platform/kanzo-sandbox/sandbox_init.py" "${runner_context}/sandbox_init.py"
cp "${repo_root}/platform/kanzo-sandbox/sandbox_runner.py" "${runner_context}/sandbox_runner.py"
cp "${repo_root}/images/kanzo-sandbox-broker/Dockerfile" "${broker_context}/Dockerfile"

buildah bud --format oci --platform linux/amd64 \
  --build-arg "NODE_BASE_IMAGE=${base_image}" \
  --build-arg "PNPM_VERSION=${pnpm_version}" \
  --file "${runner_context}/Dockerfile" \
  --tag "${image}" "${runner_context}"
buildah push "${image}" "oci-archive:${archive}:${image}"
k3s ctr -n k8s.io images import "${archive}"
k3s ctr -n k8s.io images list -q | grep -Fxq "${image}"

buildah bud --format oci --platform linux/amd64 \
  --build-arg "PYTHON_BASE_IMAGE=${broker_base_image}" \
  --file "${broker_context}/Dockerfile" \
  --tag "${broker_image}" "${broker_context}"
buildah push "${broker_image}" "oci-archive:${broker_archive}:${broker_image}"
k3s ctr -n k8s.io images import "${broker_archive}"
k3s ctr -n k8s.io images list -q | grep -Fxq "${broker_image}"
echo "Built and imported Hermes-independent images ${image} and ${broker_image}."
