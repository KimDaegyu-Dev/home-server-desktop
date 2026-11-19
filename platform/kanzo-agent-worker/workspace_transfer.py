"""Safe, deterministic transfer of a Git checkout to and from a Kanzo broker.

The output format is a gzip-compressed tar archive containing regular files to
replace and one UTF-8 ``kanzo-output.json`` manifest, for example::

    {"schema": "kanzo-output/v1", "deleted": ["old/path.py"]}

Only paths named by the archive or manifest are changed. Ownership and all
permission bits are ignored; replacement files keep only the executable bit.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable


_MANIFEST = "kanzo-output.json"
_SCHEMA = "kanzo-output/v1"
_DEFAULT_COMPRESSED = 256 * 1024 * 1024
_DEFAULT_SOURCE_EXPANDED = 4 * 1024 * 1024 * 1024
_DEFAULT_SOURCE_FILES = 20_000
_DEFAULT_OUTPUT_EXPANDED = 1024 * 1024 * 1024
_MANIFEST_LIMIT = 16 * 1024 * 1024
_COPY_CHUNK = 1024 * 1024


class WorkspaceTransferError(ValueError):
    """Raised when a checkout or broker archive violates the transfer contract."""


def build_source_archive(
    checkout: Path,
    expected_git_sha: str,
    *,
    max_compressed_bytes: int = _DEFAULT_COMPRESSED,
    max_expanded_bytes: int = _DEFAULT_SOURCE_EXPANDED,
    max_files: int = _DEFAULT_SOURCE_FILES,
) -> tuple[bytes, str]:
    """Return a deterministic gzip tar snapshot of tracked files at ``expected_git_sha``.

    The checkout must be clean. The index is used to establish the exact tracked
    path set and Git blob IDs; files are opened without following symlinks and
    their contents are checked against those blob IDs while being archived.
    """
    _validate_limits(max_compressed_bytes, max_expanded_bytes)
    if isinstance(max_files, bool) or not isinstance(max_files, int) or max_files < 0:
        raise WorkspaceTransferError("max_files must be a non-negative integer")
    root = _checkout_root(checkout)
    expected_git_sha = expected_git_sha.strip().lower()
    if len(expected_git_sha) not in (40, 64) or any(c not in "0123456789abcdef" for c in expected_git_sha):
        raise WorkspaceTransferError("expected_git_sha must be a full Git object ID")

    actual_sha = _git(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip().lower()
    if actual_sha != expected_git_sha:
        raise WorkspaceTransferError(f"checkout HEAD mismatch: expected {expected_git_sha}, got {actual_sha}")
    object_format = _git(root, "rev-parse", "--show-object-format").decode("ascii").strip()
    if object_format not in ("sha1", "sha256"):
        raise WorkspaceTransferError(f"unsupported Git object format: {object_format}")
    _require_clean_checkout(root)

    listed = _git(root, "ls-files", "-z").split(b"\0")
    indexed = _git(root, "ls-files", "--stage", "-z").split(b"\0")
    listed_paths = {_decode_git_path(p) for p in listed if p}
    entries: dict[str, tuple[str, str]] = {}
    for record in indexed:
        if not record:
            continue
        metadata, sep, raw_path = record.partition(b"\t")
        if not sep:
            raise WorkspaceTransferError("could not parse git ls-files --stage output")
        fields = metadata.decode("ascii").split()
        if len(fields) != 3:
            raise WorkspaceTransferError("unexpected Git index entry")
        mode, object_id, stage = fields
        path = _decode_git_path(raw_path)
        if stage != "0":
            raise WorkspaceTransferError(f"checkout has an unmerged index entry: {path}")
        if mode not in ("100644", "100755"):
            raise WorkspaceTransferError(f"tracked path is not a regular file: {path}")
        if _has_git_metadata_component(path):
            continue
        _validate_relative_path(path)
        if path in entries:
            raise WorkspaceTransferError(f"duplicate tracked path: {path}")
        entries[path] = (mode, object_id)
        if len(entries) > max_files:
            raise WorkspaceTransferError("source archive exceeds max_files")
    if set(entries) != {p for p in listed_paths if not _has_git_metadata_component(p)}:
        raise WorkspaceTransferError("Git tracked path list and index entries disagree")

    digest_name = "sha1" if object_format == "sha1" else "sha256"
    sink = _LimitedBytesIO(max_compressed_bytes)
    expanded = 0
    try:
        with gzip.GzipFile(fileobj=sink, mode="wb", filename="", mtime=0, compresslevel=6) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in sorted(entries, key=lambda p: p.encode("utf-8")):
                    mode, expected_blob = entries[path]
                    source = _open_checkout_file(root, path)
                    try:
                        file_stat = os.fstat(source.fileno())
                        if not stat.S_ISREG(file_stat.st_mode):
                            raise WorkspaceTransferError(f"tracked path is not a regular file: {path}")
                        if expanded + file_stat.st_size > max_expanded_bytes:
                            raise WorkspaceTransferError("source archive exceeds max_expanded_bytes")
                        info = tarfile.TarInfo(path)
                        info.type = tarfile.REGTYPE
                        info.size = file_stat.st_size
                        info.mode = 0o755 if mode == "100755" else 0o644
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        info.mtime = 0
                        blob_hash = hashlib.new(digest_name)
                        blob_hash.update(f"blob {file_stat.st_size}\0".encode("ascii"))
                        reader = _GitHashingReader(source, blob_hash)
                        archive.addfile(info, reader)
                        after = os.fstat(source.fileno())
                        if _stat_signature(after) != _stat_signature(file_stat):
                            raise WorkspaceTransferError(f"tracked file changed while archiving: {path}")
                        if reader.bytes_read != file_stat.st_size or blob_hash.hexdigest() != expected_blob:
                            raise WorkspaceTransferError(f"tracked file does not match the Git index: {path}")
                        expanded += reader.bytes_read
                    finally:
                        source.close()
        _require_clean_checkout(root)
        final_sha = _git(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip().lower()
        if final_sha != expected_git_sha:
            raise WorkspaceTransferError("checkout HEAD changed while archiving")
    except _LimitExceeded as exc:
        raise WorkspaceTransferError(str(exc)) from exc
    data = sink.getvalue()
    if len(data) > max_compressed_bytes:
        raise WorkspaceTransferError("source archive exceeds max_compressed_bytes")
    return data, hashlib.sha256(data).hexdigest()


def build_manifest_archive(
    checkout: Path,
    expected_git_sha: str,
    sources: Iterable[tuple[str, Path, str]],
    *,
    max_compressed_bytes: int = _DEFAULT_COMPRESSED,
    max_expanded_bytes: int = _DEFAULT_SOURCE_EXPANDED,
    max_files: int = _DEFAULT_SOURCE_FILES,
) -> tuple[bytes, str]:
    """Archive primary files at `/` and pinned secondary repos under a reserved prefix."""
    source_list = list(sources)
    if len(source_list) > 4:
        raise WorkspaceTransferError("source manifest exceeds four secondary sources")
    names: set[str] = set()
    archives: list[tuple[str, bytes]] = []
    compressed_total = 0
    expanded_total = 0
    file_total = 0
    pinned_sources = [("", checkout, expected_git_sha), *source_list]
    for name, path, revision in pinned_sources:
        if name:
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name) or name.casefold() in names:
                raise WorkspaceTransferError("source manifest has an invalid or duplicate name")
            names.add(name.casefold())
        if compressed_total >= max_compressed_bytes:
            raise WorkspaceTransferError("combined source archive exceeds max_compressed_bytes")
        if expanded_total > max_expanded_bytes or file_total > max_files:
            raise WorkspaceTransferError("combined source archive exceeds configured limits")
        archive_bytes, _ = build_source_archive(
            path, revision,
            max_compressed_bytes=max_compressed_bytes - compressed_total,
            max_expanded_bytes=max_expanded_bytes - expanded_total,
            max_files=max_files - file_total,
        )
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as source_archive:
            for member in source_archive:
                if not member.isreg():
                    raise WorkspaceTransferError("source manifest contains a non-regular entry")
                file_total += 1
                expanded_total += member.size
                if file_total > max_files:
                    raise WorkspaceTransferError("combined source archive exceeds max_files")
                if expanded_total > max_expanded_bytes:
                    raise WorkspaceTransferError("combined source archive exceeds max_expanded_bytes")
        compressed_total += len(archive_bytes)
        archives.append((f".kanzo-sources/{name}/" if name else "", archive_bytes))

    sink = _LimitedBytesIO(max_compressed_bytes)
    count = 0
    expanded = 0
    seen_paths: set[str] = set()
    try:
        with gzip.GzipFile(fileobj=sink, mode="wb", filename="", mtime=0, compresslevel=6) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as output:
                for prefix, archive_bytes in archives:
                    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as source_archive:
                        for member in source_archive:
                            path = _validate_relative_path(member.name)
                            if not member.isreg() or _has_git_metadata_component(path):
                                raise WorkspaceTransferError("source manifest contains a non-regular or Git metadata entry")
                            count += 1
                            if count > max_files:
                                raise WorkspaceTransferError("combined source archive exceeds max_files")
                            if expanded + member.size > max_expanded_bytes:
                                raise WorkspaceTransferError("combined source archive exceeds max_expanded_bytes")
                            stream = source_archive.extractfile(member)
                            if stream is None:
                                raise WorkspaceTransferError(f"could not read source member: {path}")
                            data = stream.read(member.size + 1)
                            if len(data) != member.size:
                                raise WorkspaceTransferError(f"source member size changed: {path}")
                            target_path = _validate_relative_path(prefix + path)
                            if target_path in seen_paths:
                                raise WorkspaceTransferError(f"source manifest path collision: {target_path}")
                            seen_paths.add(target_path)
                            info = tarfile.TarInfo(target_path)
                            info.type = tarfile.REGTYPE
                            info.size = len(data)
                            info.mode = 0o755 if member.mode & 0o111 else 0o644
                            info.uid = info.gid = 0
                            info.uname = info.gname = ""
                            info.mtime = 0
                            output.addfile(info, io.BytesIO(data))
                            expanded += len(data)
    except _LimitExceeded as exc:
        raise WorkspaceTransferError(str(exc)) from exc
    result = sink.getvalue()
    return result, hashlib.sha256(result).hexdigest()


def apply_changed_archive(
    checkout: Path,
    archive_bytes: bytes,
    *,
    max_compressed_bytes: int = _DEFAULT_COMPRESSED,
    max_expanded_bytes: int = _DEFAULT_OUTPUT_EXPANDED,
    max_files: int = 20000,
    protected_prefixes: Iterable[str] = (),
) -> dict[str, Any]:
    """Validate, stage, then apply regular-file replacements and manifest deletions.

    The returned summary contains sorted changed paths and the SHA-256 of the
    exact broker archive. All archive content and destination paths are checked
    before the first checkout file is replaced or deleted.
    """
    _validate_limits(max_compressed_bytes, max_expanded_bytes)
    if isinstance(max_files, bool) or not isinstance(max_files, int) or max_files < 0:
        raise WorkspaceTransferError("max_files must be a non-negative integer")
    if not isinstance(archive_bytes, bytes):
        raise WorkspaceTransferError("archive_bytes must be bytes")
    protected = tuple(_validate_relative_path(item.rstrip("/")) for item in protected_prefixes)
    if len(archive_bytes) > max_compressed_bytes:
        raise WorkspaceTransferError("output archive exceeds max_compressed_bytes")

    root = _checkout_root(checkout)
    stage_path = Path(tempfile.mkdtemp(prefix=".kanzo-transfer-", dir=root))
    staged: dict[str, tuple[str, bool]] = {}
    deleted: list[str] = []
    try:
        manifest_bytes: bytes | None = None
        expanded = 0
        member_count = 0
        compressed_stream = gzip.GzipFile(fileobj=io.BytesIO(archive_bytes), mode="rb")
        limited_stream = _ExpandedLimitReader(compressed_stream, max_expanded_bytes)
        try:
            with tarfile.open(fileobj=limited_stream, mode="r|") as archive:
                for member in archive:
                    member_count += 1
                    if member_count > max_files + 1:
                        raise WorkspaceTransferError("output archive exceeds max_files")
                    path = _validate_relative_path(member.name)
                    if path in staged or path == _MANIFEST and manifest_bytes is not None:
                        raise WorkspaceTransferError(f"duplicate archive path: {path}")
                    if _has_git_metadata_component(path):
                        raise WorkspaceTransferError("output archive may not modify Git metadata")
                    if not member.isreg() or member.issym() or member.islnk() or member.isdev() or member.isfifo():
                        raise WorkspaceTransferError(f"archive member is not a regular file: {path}")
                    if _has_sparse_metadata(member):
                        raise WorkspaceTransferError(f"sparse archive member is not allowed: {path}")
                    if member.linkname:
                        raise WorkspaceTransferError(f"linked archive member is not allowed: {path}")
                    if member.size < 0:
                        raise WorkspaceTransferError(f"negative member size: {path}")
                    if path == _MANIFEST:
                        if member.size > _MANIFEST_LIMIT:
                            raise WorkspaceTransferError("output manifest is too large")
                        stream = archive.extractfile(member)
                        if stream is None:
                            raise WorkspaceTransferError("could not read output manifest")
                        manifest_bytes = _read_member(stream, member.size, max_expanded_bytes, expanded)
                        expanded += len(manifest_bytes)
                        continue
                    if len(staged) >= max_files:
                        raise WorkspaceTransferError("output archive exceeds max_files")
                    if expanded + member.size > max_expanded_bytes:
                        raise WorkspaceTransferError("output archive exceeds max_expanded_bytes")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise WorkspaceTransferError(f"could not read archive member: {path}")
                    stage_name = f"file-{len(staged):08d}"
                    staged_file = stage_path / stage_name
                    with staged_file.open("xb") as target:
                        _copy_member(stream, target, member.size)
                    expanded += member.size
                    # No archive-provided owner or permission bits are applied.
                    staged[path] = (stage_name, bool(member.mode & 0o111))
            while limited_stream.read(_COPY_CHUNK):
                pass
        except (tarfile.TarError, EOFError, OSError, _LimitExceeded) as exc:
            if isinstance(exc, WorkspaceTransferError):
                raise
            raise WorkspaceTransferError(f"invalid or oversized output archive: {exc}") from exc
        finally:
            limited_stream.close()

        if manifest_bytes is None:
            raise WorkspaceTransferError("output archive is missing kanzo-output.json")
        deleted = _parse_manifest(manifest_bytes)
        if len(staged) > max_files:
            raise WorkspaceTransferError("output archive exceeds max_files")
        _validate_change_set(staged.keys(), deleted)
        for path in (*staged.keys(), *deleted):
            if any(path == prefix or path.startswith(prefix + "/") for prefix in protected):
                raise WorkspaceTransferError(f"output attempts to modify read-only source path: {path}")
        _preflight_destinations(root, staged.keys(), deleted)
        _apply_staged_changes(root, stage_path, staged, deleted)
        return {
            "files_written": len(staged),
            "files_deleted": len(deleted),
            "written": sorted(staged, key=lambda p: p.encode("utf-8")),
            "deleted": sorted(deleted, key=lambda p: p.encode("utf-8")),
            "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        }
    finally:
        shutil.rmtree(stage_path, ignore_errors=True)


def _validate_limits(max_compressed: int, max_expanded: int) -> None:
    for name, value in (("max_compressed_bytes", max_compressed), ("max_expanded_bytes", max_expanded)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise WorkspaceTransferError(f"{name} must be a non-negative integer")


def _checkout_root(checkout: Path) -> Path:
    root = Path(checkout).absolute()
    try:
        st = root.lstat()
    except OSError as exc:
        raise WorkspaceTransferError(f"checkout is unavailable: {root}") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise WorkspaceTransferError("checkout must be a real directory, not a symlink")
    resolved = root.resolve(strict=True)
    if resolved != root:
        # Reject symlinked parent components as well as a symlink at checkout.
        _assert_no_symlink_ancestors(root)
    return root


def _assert_no_symlink_ancestors(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise WorkspaceTransferError(f"path contains a symlink component: {current}")
        except FileNotFoundError as exc:
            raise WorkspaceTransferError(f"path component does not exist: {current}") from exc


def _git(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, check=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkspaceTransferError(f"Git command failed ({' '.join(args)}): {exc}") from exc
    return result.stdout


def _require_clean_checkout(root: Path) -> None:
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none")
    if status:
        preview = status[:300].decode("utf-8", errors="replace").replace("\0", " ").strip()
        raise WorkspaceTransferError(f"checkout is not clean: {preview}")


def _decode_git_path(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceTransferError("tracked path is not valid UTF-8") from exc
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
        raise WorkspaceTransferError("tracked path contains undecodable bytes")
    return value


def _has_git_metadata_component(path: str) -> bool:
    return any(part.casefold() == ".git" for part in path.split("/"))


def _validate_relative_path(path: str) -> str:
    if not isinstance(path, str) or not path or "\x00" in path:
        raise WorkspaceTransferError("archive path is empty or invalid")
    if "\\" in path or path.startswith("/") or path.startswith("//"):
        raise WorkspaceTransferError(f"absolute or platform-ambiguous path is not allowed: {path!r}")
    if len(path) >= 2 and path[1] == ":":
        raise WorkspaceTransferError(f"drive-qualified path is not allowed: {path!r}")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise WorkspaceTransferError(f"non-canonical or traversal path is not allowed: {path!r}")
    for part in parts:
        if any(character in part for character in '<>:"|?*') or part.endswith((" ", ".")):
            raise WorkspaceTransferError(f"platform-ambiguous path component is not allowed: {path!r}")
        device_name = part.split(".", 1)[0].upper()
        if device_name in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise WorkspaceTransferError(f"reserved device path is not allowed: {path!r}")
    if PurePosixPath(path).is_absolute():
        raise WorkspaceTransferError(f"absolute path is not allowed: {path!r}")
    if any(unicodedata.normalize("NFC", part) != part for part in parts):
        raise WorkspaceTransferError(f"non-canonical Unicode path is not allowed: {path!r}")
    return path


def _open_checkout_file(root: Path, relative: str) -> BinaryIO:
    parts = relative.split("/")
    if os.name == "posix" and os.open in getattr(os, "supports_dir_fd", set()):
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        root_fd = os.open(root, flags)
        current_fd = root_fd
        try:
            for part in parts[:-1]:
                next_fd = os.open(part, flags, dir_fd=current_fd)
                if current_fd != root_fd:
                    os.close(current_fd)
                current_fd = next_fd
            metadata = os.stat(parts[-1], dir_fd=current_fd, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise WorkspaceTransferError(f"tracked path is a symlink or special file: {relative}")
            fd = os.open(
                parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=current_fd
            )
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or _stat_signature(opened) != _stat_signature(metadata):
                os.close(fd)
                raise WorkspaceTransferError(f"tracked path changed while opening: {relative}")
            return os.fdopen(fd, "rb")
        except OSError as exc:
            raise WorkspaceTransferError(f"could not safely open tracked path {relative}: {exc}") from exc
        finally:
            if current_fd != root_fd:
                os.close(current_fd)
            os.close(root_fd)
    current = root
    for part in parts[:-1]:
        current = current / part
        try:
            st = current.lstat()
        except OSError as exc:
            raise WorkspaceTransferError(f"tracked path component is missing: {relative}") from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise WorkspaceTransferError(f"tracked path has a symlink or non-directory parent: {relative}")
    target = current / parts[-1]
    try:
        st = target.lstat()
    except OSError as exc:
        raise WorkspaceTransferError(f"tracked path is missing: {relative}") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise WorkspaceTransferError(f"tracked path is a symlink or special file: {relative}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags)
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or _stat_signature(opened) != _stat_signature(st):
            os.close(fd)
            raise WorkspaceTransferError(f"tracked path changed while opening: {relative}")
        return os.fdopen(fd, "rb")
    except OSError as exc:
        raise WorkspaceTransferError(f"could not safely open tracked path {relative}: {exc}") from exc


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


class _GitHashingReader:
    def __init__(self, source: BinaryIO, digest: Any):
        self.source = source
        self.digest = digest
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = self.source.read(size)
        self.digest.update(data)
        self.bytes_read += len(data)
        return data


class _LimitExceeded(Exception):
    pass


class _LimitedBytesIO(io.BytesIO):
    def __init__(self, limit: int):
        super().__init__()
        self.limit = limit

    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > self.limit:
            raise _LimitExceeded("source archive exceeds max_compressed_bytes")
        return super().write(data)


class _ExpandedLimitReader:
    def __init__(self, source: BinaryIO, limit: int):
        self.source = source
        self.limit = limit
        self.total = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self.limit - self.total + 1
        else:
            size = min(size, self.limit - self.total + 1)
        data = self.source.read(size)
        self.total += len(data)
        if self.total > self.limit:
            raise _LimitExceeded("output archive exceeds max_expanded_bytes")
        return data

    def close(self) -> None:
        self.source.close()


def _has_sparse_metadata(member: tarfile.TarInfo) -> bool:
    for key in member.pax_headers:
        lowered = key.casefold()
        if "sparse" in lowered or lowered in ("schily.realsize", "gnu.realsize"):
            return True
    return False


def _read_member(stream: BinaryIO, size: int, limit: int, expanded: int) -> bytes:
    if expanded + size > limit:
        raise WorkspaceTransferError("output archive exceeds max_expanded_bytes")
    chunks = bytearray()
    remaining = size
    while remaining:
        chunk = stream.read(min(_COPY_CHUNK, remaining))
        if not chunk:
            raise WorkspaceTransferError("truncated archive member")
        chunks.extend(chunk)
        remaining -= len(chunk)
    return bytes(chunks)


def _copy_member(source: BinaryIO, destination: BinaryIO, size: int) -> None:
    remaining = size
    while remaining:
        chunk = source.read(min(_COPY_CHUNK, remaining))
        if not chunk:
            raise WorkspaceTransferError("truncated archive member")
        destination.write(chunk)
        remaining -= len(chunk)


def _json_no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkspaceTransferError(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def _parse_manifest(data: bytes) -> list[str]:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_json_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkspaceTransferError(f"invalid output manifest: {exc}") from exc
    if not isinstance(value, dict) or set(value) != {"schema", "deleted"} or value.get("schema") != _SCHEMA:
        raise WorkspaceTransferError("output manifest must contain exactly schema=kanzo-output/v1 and deleted")
    paths = value["deleted"]
    if not isinstance(paths, list):
        raise WorkspaceTransferError("manifest deleted must be a list of relative paths")
    result: list[str] = []
    seen: set[str] = set()
    for path in paths:
        normalized = _validate_relative_path(path)
        if normalized == _MANIFEST:
            raise WorkspaceTransferError("manifest may not delete itself")
        if normalized in seen:
            raise WorkspaceTransferError(f"duplicate deletion path: {normalized}")
        seen.add(normalized)
        result.append(normalized)
    return result


def _validate_change_set(writes: Iterable[str], deletes: list[str]) -> None:
    operations = list(writes) + deletes
    exact: set[str] = set()
    folded: dict[str, str] = {}
    for path in operations:
        _validate_relative_path(path)
        if path in exact:
            raise WorkspaceTransferError(f"path appears more than once in output changes: {path}")
        exact.add(path)
        key = unicodedata.normalize("NFC", path).casefold()
        prior = folded.get(key)
        if prior is not None and prior != path:
            raise WorkspaceTransferError(f"case-insensitive path collision: {prior} and {path}")
        folded[key] = path
    ordered = sorted(exact, key=lambda p: (p.count("/"), p.casefold(), p))
    for index, path in enumerate(ordered):
        prefix = path + "/"
        for other in ordered[index + 1:]:
            if other.startswith(prefix) or other.casefold().startswith(prefix.casefold()):
                raise WorkspaceTransferError(f"conflicting file paths: {path} and {other}")


def _preflight_destinations(root: Path, writes: Iterable[str], deletes: list[str]) -> None:
    for relative in list(writes) + deletes:
        current = root
        parts = relative.split("/")
        for index, part in enumerate(parts):
            current = current / part
            try:
                st = current.lstat()
            except FileNotFoundError:
                break
            except OSError as exc:
                raise WorkspaceTransferError(f"cannot inspect destination {relative}: {exc}") from exc
            if stat.S_ISLNK(st.st_mode):
                raise WorkspaceTransferError(f"destination contains a symlink: {relative}")
            final = index == len(parts) - 1
            if final:
                if stat.S_ISDIR(st.st_mode):
                    raise WorkspaceTransferError(f"file operation conflicts with a directory: {relative}")
                if not stat.S_ISREG(st.st_mode):
                    raise WorkspaceTransferError(f"destination is a special file: {relative}")
            elif not stat.S_ISDIR(st.st_mode):
                raise WorkspaceTransferError(f"destination parent is not a directory: {relative}")
        # Resolve existing parents to prove containment before touching anything.
        parent = (root / Path(*parts[:-1])).resolve(strict=False)
        if not _is_within(parent, root.resolve(strict=True)):
            raise WorkspaceTransferError(f"destination resolves outside checkout: {relative}")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _apply_staged_changes(
    root: Path,
    stage_path: Path,
    staged: dict[str, tuple[str, bool]],
    deleted: list[str],
) -> None:
    if os.name == "posix" and os.open in getattr(os, "supports_dir_fd", set()):
        _apply_with_dir_fds(root, stage_path, staged, deleted)
    else:
        _apply_portably(root, stage_path, staged, deleted)


def _apply_with_dir_fds(
    root: Path,
    stage_path: Path,
    staged: dict[str, tuple[str, bool]],
    deleted: list[str],
) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(root, os.O_RDONLY | directory_flag)
    stage_fd = os.open(stage_path, os.O_RDONLY | directory_flag, dir_fd=root_fd)
    try:
        for relative, (stage_name, executable) in staged.items():
            parent_fd, leaf = _open_parent_fd(root_fd, relative, create=True)
            try:
                staged_fd = os.open(stage_name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=stage_fd)
                try:
                    os.fchmod(staged_fd, 0o755 if executable else 0o644)
                    os.fsync(staged_fd)
                finally:
                    os.close(staged_fd)
                _check_leaf_at(parent_fd, leaf)
                os.replace(stage_name, leaf, src_dir_fd=stage_fd, dst_dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
        for relative in deleted:
            parent_fd, leaf = _open_parent_fd(root_fd, relative, create=False)
            if parent_fd is None:
                continue
            try:
                mode = _check_leaf_at(parent_fd, leaf, missing_ok=True)
                if mode is not None:
                    if not stat.S_ISREG(mode):
                        raise WorkspaceTransferError(f"refusing to delete a non-regular path: {relative}")
                    os.unlink(leaf, dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
    finally:
        os.close(stage_fd)
        os.close(root_fd)


def _open_parent_fd(root_fd: int, relative: str, *, create: bool) -> tuple[int | None, str]:
    parts = relative.split("/")
    current_fd = os.dup(root_fd)
    for part in parts[:-1]:
        try:
            next_fd = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=current_fd)
        except FileNotFoundError:
            if not create:
                os.close(current_fd)
                return None, parts[-1]
            try:
                os.mkdir(part, mode=0o755, dir_fd=current_fd)
            except FileExistsError:
                pass
            next_fd = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=current_fd)
        os.close(current_fd)
        current_fd = next_fd
    return current_fd, parts[-1]


def _check_leaf_at(parent_fd: int, leaf: str, *, missing_ok: bool = False) -> int | None:
    try:
        metadata = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise WorkspaceTransferError(f"refusing to replace a symlink or non-regular destination: {leaf}")
    return metadata.st_mode


def _apply_portably(
    root: Path,
    stage_path: Path,
    staged: dict[str, tuple[str, bool]],
    deleted: list[str],
) -> None:
    root_real = root.resolve(strict=True)
    for relative, (stage_name, executable) in staged.items():
        destination = root.joinpath(*relative.split("/"))
        _assert_safe_destination(root, destination, allow_missing_leaf=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _assert_safe_destination(root, destination, allow_missing_leaf=True)
        staged_file = stage_path / stage_name
        os.chmod(staged_file, 0o755 if executable else 0o644)
        os.replace(staged_file, destination)
    for relative in deleted:
        destination = root.joinpath(*relative.split("/"))
        _assert_safe_destination(root, destination, allow_missing_leaf=True)
        try:
            if destination.exists() or destination.is_symlink():
                if destination.is_symlink() or not destination.is_file():
                    raise WorkspaceTransferError(f"refusing to delete a symlink or non-file: {relative}")
                destination.unlink()
        except FileNotFoundError:
            pass
    if root.resolve(strict=True) != root_real:
        raise WorkspaceTransferError("checkout root changed while applying output archive")


def _assert_safe_destination(root: Path, destination: Path, *, allow_missing_leaf: bool) -> None:
    root_real = root.resolve(strict=True)
    current = root
    relative = destination.relative_to(root)
    parts = relative.parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if index == len(parts) - 1 and not allow_missing_leaf:
                raise WorkspaceTransferError(f"destination is missing: {destination}")
            continue
        if stat.S_ISLNK(info.st_mode):
            raise WorkspaceTransferError(f"destination contains a symlink: {destination}")
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise WorkspaceTransferError(f"destination parent is not a directory: {destination}")
    if not _is_within(destination.parent.resolve(strict=False), root_real):
        raise WorkspaceTransferError(f"destination resolves outside checkout: {destination}")
