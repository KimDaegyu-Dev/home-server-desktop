#!/usr/bin/env bash
set -euo pipefail
set +x

profile="${HOME_SERVER_PROFILE:-wsl}"
destination=""
while (($#)); do
  case "$1" in
    --destination)
      [[ $# -ge 2 ]] || { echo "--destination requires a directory." >&2; exit 2; }
      destination="$2"
      shift 2
      ;;
    *) echo "Usage: $0 --destination /path/to/external/backup" >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "Run with sudo to read the local-path PVC directory." >&2; exit 1; }
[[ -n "$destination" && -d "$destination" ]] || { echo "Create the external backup directory first and pass --destination." >&2; exit 2; }
case "$profile" in
  wsl) source_dir=/var/lib/home-server-desktop/k3s-local-path ;;
  dualboot) source_dir=/srv/home-server-desktop/k3s-local-path ;;
  *) echo "HOME_SERVER_PROFILE must be wsl or dualboot." >&2; exit 2 ;;
esac
[[ -d "$source_dir" ]] || { echo "Local-path directory not found: $source_dir" >&2; exit 1; }
if systemctl is-active --quiet k3s; then
  echo "Stop K3s first so PostgreSQL, ClickHouse, SeaweedFS, Vault, and other PVC files are quiescent." >&2
  exit 1
fi

source_real="$(realpath "$source_dir")"
destination_real="$(realpath "$destination")"
case "$destination_real/" in
  "$source_real/"*) echo "Backup destination must be outside the K3s local-path directory." >&2; exit 2 ;;
esac
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$destination_real/home-server-k3s-local-path-$profile-$stamp.tar.gz"
temporary="$archive.partial"
umask 077
trap 'rm -f "$temporary"' EXIT
tar -czf "$temporary" -C "$(dirname "$source_real")" "$(basename "$source_real")"
chmod 0600 "$temporary"
mv "$temporary" "$archive"
printf 'Cold PVC archive created at %s\n' "$archive"
