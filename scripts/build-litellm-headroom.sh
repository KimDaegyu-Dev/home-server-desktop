#!/usr/bin/env bash
set -euo pipefail
set +x

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
versions_file="${repo_root}/versions.yml"
litellm_version="$(awk -F '"' '$1 ~ /^litellm_app:/ {print $2}' "${versions_file}")"
headroom_version="$(awk -F '"' '$1 ~ /^headroom_ai:/ {print $2}' "${versions_file}")"
ast_grep_version="$(awk -F '"' '$1 ~ /^headroom_ast_grep_cli:/ {print $2}' "${versions_file}")"
ast_grep_py_version="$(awk -F '"' '$1 ~ /^headroom_ast_grep_py:/ {print $2}' "${versions_file}")"
builder_image="$(awk -F '"' '$1 ~ /^headroom_builder_image:/ {print $2}' "${versions_file}")"
[[ -n "${litellm_version}" && -n "${headroom_version}" && -n "${ast_grep_version}" && -n "${ast_grep_py_version}" && -n "${builder_image}" ]] || { echo "LiteLLM/Headroom build versions are missing from versions.yml." >&2; exit 1; }

force="false"
if [[ "${1:-}" == "--force" ]]; then force="true"; shift; fi
[[ $# -eq 0 ]] || { echo "Usage: $0 [--force]" >&2; exit 2; }

image="localhost/home-server/litellm-headroom:${litellm_version}-headroom-${headroom_version}"
archive="$(mktemp /dev/shm/litellm-headroom.XXXXXX.oci.tar)"
cleanup() { rm -f "${archive}"; }
trap cleanup EXIT

command -v buildah >/dev/null 2>&1 || { echo "Install buildah first; Docker Engine is not required." >&2; exit 1; }
command -v k3s >/dev/null 2>&1 || { echo "Run on the Ubuntu K3s host." >&2; exit 1; }

if [[ "${force}" != "true" ]] && k3s ctr -n k8s.io images list -q | grep -Fxq "${image}"; then
  echo "LiteLLM Headroom image is already available in K3s: ${image}"
  exit 0
fi

buildah bud \
  --format oci \
  --build-arg "LITELLM_VERSION=${litellm_version}" \
  --build-arg "HEADROOM_VERSION=${headroom_version}" \
  --build-arg "HEADROOM_AST_GREP_VERSION=${ast_grep_version}" \
  --build-arg "HEADROOM_AST_GREP_PY_VERSION=${ast_grep_py_version}" \
  --build-arg "HEADROOM_BUILDER_IMAGE=${builder_image}" \
  --tag "${image}" \
  "${repo_root}/images/litellm-headroom"
buildah push "${image}" "oci-archive:${archive}:${image}"
if [[ "${force}" == "true" ]]; then k3s ctr -n k8s.io images rm "${image}" >/dev/null 2>&1 || true; fi
k3s ctr -n k8s.io images import "${archive}"

k3s ctr -n k8s.io images list -q | grep -Fxq "${image}"
echo "Imported ${image} into the K3s containerd image store."
