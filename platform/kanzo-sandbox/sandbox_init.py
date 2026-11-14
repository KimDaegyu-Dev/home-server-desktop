"""Fetch and safely install one run-scoped source archive before execution."""

from __future__ import annotations

import hashlib
import hmac
import gzip
import io
import json
import os
import re
import stat
import tarfile
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import BinaryIO


WORKSPACE = Path("/workspace")
TMP_ROOT = Path("/tmp")
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 4 * 1024 * 1024 * 1024
MAX_TAR_OVERHEAD_BYTES = 256 * 1024 * 1024
MAX_TAR_READ_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_FILES = 20_000
MAX_BASELINE_BYTES = 64 * 1024 * 1024
BASELINE_FILE = Path("/tmp/kanzo-baseline.json")
MAX_TOKEN_BYTES = 4096
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class InitError(RuntimeError):
    """A source archive could not be authenticated and installed safely."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _BoundedTarReader:
    """Limit gzip expansion and reject oversized single tar metadata reads."""

    def __init__(self, source: BinaryIO):
        self.source = source
        self.total_read = 0
        self.limit = MAX_EXPANDED_BYTES + MAX_TAR_OVERHEAD_BYTES

    def read(self, size: int = -1) -> bytes:
        if size < 0 or size > MAX_TAR_READ_BYTES:
            raise InitError("archive contains an oversized tar metadata record")
        data = self.source.read(size)
        self.total_read += len(data)
        if self.total_read > self.limit:
            raise InitError("archive expanded tar stream exceeds the size limit")
        return data


def _source_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise InitError("source URL must be an internal HTTP(S) URL without credentials or query")
    hostname = parsed.hostname.lower().rstrip(".")
    if not (hostname.endswith(".svc") or hostname.endswith(".svc.cluster.local")):
        raise InitError("source URL host must be a Kubernetes service DNS name")
    return value


def _read_source(url: str, token: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/gzip, application/octet-stream",
            "Cache-Control": "no-store",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            if response.status != 200:
                raise InitError("broker refused the one-use source download")
            declared = response.headers.get("Content-Length")
            if declared is not None:
                if not declared.isascii() or not declared.isdecimal() or int(declared) > MAX_ARCHIVE_BYTES:
                    raise InitError("source archive exceeds the compressed size limit")
            chunks = bytearray()
            while True:
                block = response.read(min(64 * 1024, MAX_ARCHIVE_BYTES + 1 - len(chunks)))
                if not block:
                    break
                chunks.extend(block)
                if len(chunks) > MAX_ARCHIVE_BYTES:
                    raise InitError("source archive exceeds the compressed size limit")
            return bytes(chunks)
    except InitError:
        raise
    except urllib.error.HTTPError as exc:
        # Do not include the URL, headers, or response body in diagnostics.
        raise InitError(f"broker source request failed with HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise InitError(f"broker source request failed ({type(exc).__name__})") from None


def _safe_member_path(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        raise InitError("archive contains an invalid path")
    if name.startswith("/") or "//" in name:
        raise InitError("archive contains an absolute path")
    parts = name.rstrip("/").split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise InitError("archive contains a traversal path")
    for part in parts:
        if any(character in part for character in '<>:"|?*') or part.endswith((" ", ".")):
            raise InitError("archive contains a platform-ambiguous path")
        device_name = part.split(".", 1)[0].upper()
        if device_name in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise InitError("archive contains a reserved device path")
        if unicodedata.normalize("NFC", part) != part:
            raise InitError("archive contains a non-canonical Unicode path")
    if len(parts[0]) >= 2 and parts[0][1] == ":":
        raise InitError("archive contains a drive-qualified path")
    normalized = PurePosixPath(*parts).as_posix()
    if len(normalized) > 4096:
        raise InitError("archive path exceeds the limit")
    return normalized


def _copy_member(source: BinaryIO, destination: Path, size: int, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(destination, flags, mode)
    remaining = size
    with os.fdopen(descriptor, "wb") as target:
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                raise InitError("archive ended before a file was complete")
            target.write(chunk)
            remaining -= len(chunk)


def _extract_gzip_tar(archive_bytes: bytes, destination: Path) -> int:
    files = 0
    expanded = 0
    seen: set[str] = set()
    file_paths: set[str] = set()
    ancestor_paths: set[str] = set()
    compressed = gzip.GzipFile(fileobj=io.BytesIO(archive_bytes), mode="rb")
    limited_tar = _BoundedTarReader(compressed)
    try:
        with tarfile.open(fileobj=limited_tar, mode="r|") as archive:
            for member in archive:
                files += 1
                if files > MAX_ARCHIVE_FILES:
                    raise InitError("archive contains too many entries")
                relative = _safe_member_path(member.name)
                if relative == "kanzo-output.json":
                    raise InitError("source archive uses the reserved output metadata path")
                if relative in seen:
                    raise InitError("archive contains duplicate paths")
                seen.add(relative)
                if member.isdir():
                    kind = "dir"
                elif member.isreg() and not getattr(member, "sparse", None):
                    if any(key.startswith("GNU.sparse") for key in member.pax_headers):
                        raise InitError("archive contains a sparse file")
                    kind = "file"
                else:
                    # Reject symlinks, hardlinks, devices, FIFOs, and special entries.
                    raise InitError("archive contains an unsupported entry type")
                parts = PurePosixPath(relative).parts
                for index in range(1, len(parts)):
                    parent = "/".join(parts[:index])
                    if parent in file_paths:
                        raise InitError("archive has a file-directory path conflict")
                    ancestor_paths.add(parent)
                if relative in file_paths or (kind == "file" and relative in ancestor_paths):
                    raise InitError("archive has a file-directory path conflict")

                target = destination.joinpath(*parts)
                if kind == "dir":
                    target.mkdir(parents=True, exist_ok=True, mode=0o755)
                    continue
                file_paths.add(relative)
                if member.size < 0 or expanded + member.size > MAX_EXPANDED_BYTES:
                    raise InitError("archive expanded size exceeds the limit")
                expanded += member.size
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                source = archive.extractfile(member)
                if source is None:
                    raise InitError("archive file content is unavailable")
                mode = 0o755 if member.mode & 0o111 else 0o644
                _copy_member(source, target, member.size, mode)
                source.close()
    finally:
        compressed.close()
    return files


def _install_source(archive_bytes: bytes) -> int:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    if stat.S_ISLNK(WORKSPACE.lstat().st_mode) or not WORKSPACE.is_dir():
        raise InitError("/workspace must be a real directory")
    if stat.S_ISLNK(TMP_ROOT.lstat().st_mode) or not TMP_ROOT.is_dir():
        raise InitError("/tmp must be a real directory")
    if any(WORKSPACE.iterdir()):
        raise InitError("/workspace must be empty before source initialization")
    if not os.access(WORKSPACE, os.W_OK) or not os.access(TMP_ROOT, os.W_OK):
        raise InitError("source init requires writable workspace and temporary mounts")
    installed = False
    try:
        # Extract directly onto the per-run workspace. Staging a second 4 GiB
        # tree under /tmp would exceed that mount's smaller ephemeral limit.
        count = _extract_gzip_tar(archive_bytes, WORKSPACE)
        baseline = _capture_baseline(WORKSPACE)
        _write_baseline(baseline)
        installed = True
        return count
    finally:
        if not installed:
            # Init failure prevents the runner from starting; remove any partial
            # workspace population so no caller can mistake it for a valid tree.
            for entry in list(WORKSPACE.iterdir()):
                try:
                    if entry.is_symlink() or entry.is_file():
                        entry.unlink()
                    elif entry.is_dir():
                        _remove_tree(entry)
                except OSError:
                    pass
            try:
                BASELINE_FILE.unlink(missing_ok=True)
            except OSError:
                pass


def _remove_tree(path: Path) -> None:
    for entry in path.iterdir():
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
        elif entry.is_dir():
            _remove_tree(entry)
    path.rmdir()


def _capture_baseline(root: Path) -> dict[str, dict[str, object]]:
    entries: dict[str, dict[str, object]] = {}
    stack = [root]
    total_bytes = 0
    while stack:
        directory = stack.pop()
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError:
            raise InitError("cannot capture source baseline") from None
        for child in children:
            path = Path(child.path)
            relative = path.relative_to(root).as_posix()
            try:
                info = child.stat(follow_symlinks=False)
            except OSError:
                raise InitError("cannot inspect source baseline entry") from None
            if stat.S_ISDIR(info.st_mode):
                entries[relative] = {"kind": "dir"}
                stack.append(path)
            elif stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                try:
                    with path.open("rb") as source:
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            digest.update(chunk)
                            total_bytes += len(chunk)
                except OSError:
                    raise InitError("cannot hash source baseline file") from None
                if total_bytes > MAX_EXPANDED_BYTES:
                    raise InitError("source baseline exceeds the expanded size limit")
                entries[relative] = {
                    "kind": "file",
                    "sha256": digest.hexdigest(),
                    "executable": bool(info.st_mode & 0o111),
                }
            else:
                raise InitError("source baseline contains a special file")
            if len(entries) > MAX_ARCHIVE_FILES:
                raise InitError("source baseline contains too many entries")
    encoded = json.dumps(
        {"schema": "kanzo-baseline/v1", "entries": entries},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    if len(encoded) > MAX_BASELINE_BYTES:
        raise InitError("source baseline manifest exceeds its size limit")
    return {"schema": "kanzo-baseline/v1", "entries": entries}


def _write_baseline(baseline: dict[str, object]) -> None:
    if BASELINE_FILE.exists() or BASELINE_FILE.is_symlink():
        raise InitError("source baseline file already exists")
    encoded = json.dumps(baseline, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(encoded) > MAX_BASELINE_BYTES:
        raise InitError("source baseline manifest exceeds its size limit")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(BASELINE_FILE, flags, 0o400)
        with os.fdopen(descriptor, "wb") as target:
            target.write(encoded)
    except OSError:
        raise InitError("cannot persist source baseline") from None


def main() -> int:
    # Remove secrets from the Python process environment as soon as read. This
    # program starts no child processes and never logs either value.
    source_url = os.environ.pop("KANZO_SOURCE_URL", "")
    source_token = os.environ.pop("KANZO_SOURCE_TOKEN", "")
    expected_sha = os.environ.pop("KANZO_SOURCE_SHA256", "")
    if not source_url or not source_token or not expected_sha:
        raise InitError("required source initialization settings are missing")
    if len(source_token.encode("utf-8")) > MAX_TOKEN_BYTES or len(source_token) < 32:
        raise InitError("one-use source token is invalid")
    if not SHA256_RE.fullmatch(expected_sha):
        raise InitError("source SHA256 is invalid")
    trusted_url = _source_url(source_url)
    archive_bytes = _read_source(trusted_url, source_token)
    actual_sha = hashlib.sha256(archive_bytes).hexdigest()
    if not hmac.compare_digest(actual_sha, expected_sha.lower()):
        raise InitError("source archive SHA256 mismatch")
    file_count = _install_source(archive_bytes)
    print(f"Kanzo source initialized: sha256={actual_sha} entries={file_count}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InitError as exc:
        # Messages are fixed and never interpolate URLs, tokens, or response data.
        print(f"Kanzo source initialization failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print("Kanzo source initialization failed: internal error", file=sys.stderr)
        raise SystemExit(1)
