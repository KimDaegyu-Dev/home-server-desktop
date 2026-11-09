#!/usr/bin/env bash
set -euo pipefail
set +x

[[ $EUID -eq 0 ]] || { echo "Run inside Ubuntu with sudo." >&2; exit 1; }
command -v buildah >/dev/null 2>&1 || { echo "Buildah is required; Docker Engine is not used." >&2; exit 1; }
command -v k3s >/dev/null 2>&1 || { echo "Run on the configured K3s host." >&2; exit 1; }

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
versions_file="${repo_root}/versions.yml"
agent_browser_version="$(awk -F '"' '$1 ~ /^agent_browser:/ {print $2}' "${versions_file}")"
agent_browser_sha="$(awk -F '"' '$1 ~ /^agent_browser_linux_amd64_sha256:/ {print $2}' "${versions_file}")"
supergateway_version="$(awk -F '"' '$1 ~ /^supergateway:/ {print $2}' "${versions_file}")"
supergateway_sha="$(awk -F '"' '$1 ~ /^supergateway_linux_amd64_sha256:/ {print $2}' "${versions_file}")"
[[ -n "${agent_browser_version}" && -n "${agent_browser_sha}" && -n "${supergateway_version}" && -n "${supergateway_sha}" ]] || { echo "Agent Browser build versions are missing from versions.yml." >&2; exit 1; }

image="localhost/home-server/agent-browser:${agent_browser_version}-supergateway-${supergateway_version}"
if k3s ctr -n k8s.io images list -q | grep -Fxq "${image}"; then
  echo "Agent Browser image is already available in K3s: ${image}"
  exit 0
fi

buildah bud --format docker \
  --build-arg "AGENT_BROWSER_VERSION=${agent_browser_version}" \
  --build-arg "AGENT_BROWSER_SHA256=${agent_browser_sha}" \
  --build-arg "SUPERGATEWAY_VERSION=${supergateway_version}" \
  --build-arg "SUPERGATEWAY_SHA256=${supergateway_sha}" \
  -f "${repo_root}/platform/agent-browser/Dockerfile" \
  -t "${image}" "${repo_root}/platform/agent-browser"

archive="$(mktemp /dev/shm/agent-browser.XXXXXX.oci.tar)"
trap 'rm -f "${archive}"' EXIT
buildah push "${image}" "oci-archive:${archive}"
k3s ctr -n k8s.io images import "${archive}"
echo "Imported ${image} into K3s containerd."
