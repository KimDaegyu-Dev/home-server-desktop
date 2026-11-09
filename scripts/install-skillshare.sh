#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
versions_file="${repo_root}/versions.yml"
version="$(awk -F '"' '$1 ~ /^skillshare_cli:/ {print $2}' "${versions_file}")"
expected_hash="$(awk -F '"' '$1 ~ /^skillshare_linux_amd64_sha256:/ {print $2}' "${versions_file}")"
[[ -n "${version}" && -n "${expected_hash}" ]] || { echo 'versions.yml is missing the Skillshare version or checksum.' >&2; exit 1; }
version="${version#v}"
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo 'This installer currently supports Linux AMD64 only.' >&2; exit 1; }

stage_dir="${HOME}/.local/share/skillshare-installer/${version}"
install_dir="${HOME}/.local/bin"
mkdir -p "${stage_dir}" "${install_dir}"
archive="${stage_dir}/skillshare.tar.gz"
url="https://github.com/runkids/skillshare/releases/download/v${version}/skillshare_${version}_linux_amd64.tar.gz"
curl --fail --location --silent --show-error "${url}" --output "${archive}"
printf '%s  %s\n' "${expected_hash}" "${archive}" | sha256sum --check --status || { echo 'Skillshare archive checksum mismatch.' >&2; exit 1; }
tar -xzf "${archive}" -C "${stage_dir}" skillshare
install -m 0755 "${stage_dir}/skillshare" "${install_dir}/skillshare"
"${install_dir}/skillshare" version
