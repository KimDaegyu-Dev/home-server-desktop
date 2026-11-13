#!/usr/bin/env bash
set -euo pipefail
set +x

[[ $EUID -eq 0 ]] || { echo "Run inside Ubuntu with sudo." >&2; exit 1; }
command -v buildah >/dev/null 2>&1 || { echo "Install buildah first; Docker Engine is not required." >&2; exit 1; }
command -v k3s >/dev/null 2>&1 || { echo "Run on the configured K3s host." >&2; exit 1; }
command -v git >/dev/null 2>&1 || { echo "Install git first." >&2; exit 1; }

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
versions="${repo_root}/versions.yml"
version="$(awk -F '"' '$1 ~ /^kanzo:/ {print $2}' "${versions}")"
source_commit="$(awk -F '"' '$1 ~ /^kanzo_source_commit:/ {print $2}' "${versions}")"
image="$(awk -F '"' '$1 ~ /^kanzo_agent_image:/ {print $2}' "${versions}")"
rust_builder="$(awk -F '"' '$1 ~ /^kanzo_rust_builder:/ {print $2}' "${versions}")"
[[ -n "${version}" && -n "${source_commit}" && -n "${image}" && -n "${rust_builder}" ]] || {
  echo "Kanzo version, source commit, image, and Rust builder pins are required in versions.yml." >&2
  exit 1
}

source_dir="$(mktemp -d /dev/shm/kanzo-source.XXXXXX)"
archive="$(mktemp /dev/shm/kanzo.XXXXXX.oci.tar)"
cleanup() { rm -rf "${source_dir}"; rm -f "${archive}"; }
trap cleanup EXIT

git clone --quiet --depth 1 --branch "v${version}" https://github.com/emanuele-em/kanzo.git "${source_dir}"
actual_commit="$(git -C "${source_dir}" rev-parse HEAD)"
[[ "${actual_commit}" == "${source_commit}" ]] || {
  echo "Kanzo tag commit mismatch: expected ${source_commit}, got ${actual_commit}." >&2
  exit 1
}
git -C "${source_dir}" apply "${repo_root}/platform/kanzo/kanzo-agent-routing.patch"
git -C "${source_dir}" apply "${repo_root}/platform/kanzo/kanzo-mcp-annotations.patch"

buildah bud --format oci --platform linux/amd64 \
  --build-arg "RUST_BUILDER=${rust_builder}" \
  --file "${repo_root}/images/kanzo/Dockerfile" \
  --tag "${image}" "${source_dir}"
buildah push "${image}" "oci-archive:${archive}:${image}"
k3s ctr -n k8s.io images import "${archive}"
k3s ctr -n k8s.io images list -q | grep -Fxq "${image}"
echo "Built and imported ${image} from Kanzo v${version} (${source_commit})."
