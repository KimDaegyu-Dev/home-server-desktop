#!/usr/bin/env bash
set -euo pipefail
set +x

[[ $EUID -eq 0 ]] || { echo "Run inside Ubuntu with sudo." >&2; exit 1; }
command -v buildah >/dev/null 2>&1 || { echo "Buildah is required; Docker Engine is not used." >&2; exit 1; }
command -v k3s >/dev/null 2>&1 || { echo "Run on the configured K3s host." >&2; exit 1; }

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fastembed_version="$(awk -F '"' '$1 ~ /^fastembed:/ {print $2}' "${repo_root}/versions.yml")"
image="$(awk -F '"' '$1 ~ /^fastembed_image:/ {print $2}' "${repo_root}/versions.yml")"
[[ -n "${fastembed_version}" && -n "${image}" ]] || { echo "FastEmbed build versions are missing from versions.yml." >&2; exit 1; }

if k3s ctr -n k8s.io images list -q | grep -Fxq "${image}"; then
  echo "Graphiti embedder image is already available in K3s: ${image}"
  exit 0
fi

if ! buildah images --format '{{.Name}}:{{.Tag}}' | grep -Fxq "${image}"; then
  buildah bud --format docker \
    --build-arg "FASTEMBED_VERSION=${fastembed_version}" \
    -f "${repo_root}/platform/graphiti/Dockerfile.embedder" \
    -t "${image}" "${repo_root}/platform/graphiti"
else
  echo "Reusing the locally built Graphiti embedder image: ${image}"
fi

archive="$(mktemp /dev/shm/graphiti-embedder.XXXXXX.docker.tar)"
trap 'rm -f "${archive}"' EXIT
buildah push "${image}" "docker-archive:${archive}:${image}"
k3s ctr -n k8s.io images import "${archive}"
if ! k3s ctr -n k8s.io images list -q | grep -Fxq "${image}"; then
  echo "K3s containerd did not register the expected image reference: ${image}" >&2
  exit 1
fi
echo "Imported ${image} into K3s containerd."
