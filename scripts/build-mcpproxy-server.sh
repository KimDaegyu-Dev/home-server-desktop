#!/usr/bin/env bash
set -euo pipefail
set +x

[[ $EUID -eq 0 ]] || { echo "Run inside Ubuntu with sudo." >&2; exit 1; }
command -v buildah >/dev/null 2>&1 || { echo "Install buildah first; Docker Engine is not required." >&2; exit 1; }
command -v k3s >/dev/null 2>&1 || { echo "Run on the configured K3s host." >&2; exit 1; }
command -v git >/dev/null 2>&1 || { echo "Install git first." >&2; exit 1; }

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(awk -F '"' '$1 ~ /^mcpproxy_server:/ {print $2}' "${repo_root}/versions.yml")"
commit="$(awk -F '"' '$1 ~ /^mcpproxy_source_commit:/ {print $2}' "${repo_root}/versions.yml")"
[[ -n "${version}" && -n "${commit}" ]] || { echo "MCPProxy source version and commit are required in versions.yml." >&2; exit 1; }

image="localhost/home-server/mcpproxy-server:${version}"
if k3s ctr -n k8s.io images list -q | grep -Fxq "${image}"; then
  echo "MCPProxy Server image is already available in K3s: ${image}"
  exit 0
fi

tmp="$(mktemp -d /dev/shm/mcpproxy-build.XXXXXX)"
archive="$(mktemp /dev/shm/mcpproxy-server.XXXXXX.oci.tar)"
cleanup() { rm -rf "${tmp}"; rm -f "${archive}"; }
trap cleanup EXIT
src="${tmp}/src"
git clone --quiet --depth 1 --branch "${version}" https://github.com/smart-mcp-proxy/mcpproxy-go.git "${src}"
actual_commit="$(git -C "${src}" rev-parse HEAD)"
[[ "${actual_commit}" == "${commit}" ]] || {
  echo "Upstream tag resolved to ${actual_commit}, expected ${commit}; refusing to build." >&2
  exit 1
}
build_date="$(git -C "${src}" show -s --format=%cI HEAD)"

buildah bud --format oci --platform linux/amd64 \
  --build-arg "VERSION=${version}" \
  --build-arg "COMMIT=${commit}" \
  --build-arg "BUILD_DATE=${build_date}" \
  --file "${repo_root}/images/mcpproxy-server/Dockerfile" \
  --tag "${image}" "${src}"
buildah push "${image}" "oci-archive:${archive}:${image}"
k3s ctr -n k8s.io images import "${archive}"
k3s ctr -n k8s.io images list -q | grep -Fxq "${image}"
echo "Built pinned MCPProxy Server ${version} from ${commit} and imported it into K3s."
