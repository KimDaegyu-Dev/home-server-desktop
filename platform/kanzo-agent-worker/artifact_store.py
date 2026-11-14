"""Durable, private storage for completed Hermes run artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any


_TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,126}#[0-9]{1,20}\Z")
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_MANIFEST_NAME = "manifest.json"
_RESULT_NAME = "result.md"


def _validate_task_id(value: str) -> str:
    if not isinstance(value, str) or not _TASK_ID_RE.fullmatch(value):
        raise ValueError("task_id must be a Kanzo project#number identifier")
    return value


def _validate_run_id(value: str) -> str:
    if not isinstance(value, str) or not _RUN_ID_RE.fullmatch(value):
        raise ValueError("run_id must be 1-128 safe ASCII letters, digits, dots, underscores, or hyphens")
    return value


def _ensure_private_dir(path: Path) -> None:
    """Create a directory with private permissions and reject symlinked components."""
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
    except OSError as exc:
        raise OSError(f"cannot create artifact directory {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"artifact path is not a real directory: {path}")
    os.chmod(path, 0o700)
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    """Persist directory-entry updates where the platform supports directory fsync."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ArtifactStore:
    """Atomically persist each run's full output and a digest-bearing manifest.

    The root should be a dedicated directory on the Hermes HOME persistent volume.
    Existing artifacts are retained indefinitely. A per-instance lock serializes
    writes made through this object; atomic replacement and manifest digest checks
    keep readers from accepting an interrupted or mismatched pair across processes.
    """

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).expanduser().absolute()
        _ensure_private_dir(self.root)
        self._lock = threading.RLock()

    def _run_dir(self, task_id: str, run_id: str) -> Path:
        task = _validate_task_id(task_id)
        run = _validate_run_id(run_id)
        task_dir = self.root / task
        run_dir = task_dir / run
        _ensure_private_dir(task_dir)
        _ensure_private_dir(run_dir)
        return run_dir

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            _fsync_directory(path.parent)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise

    def write_result(
        self,
        task_id: str,
        run_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store full Hermes output and metadata, returning result path and SHA-256."""
        if not isinstance(content, str):
            raise TypeError("content must be a string")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise TypeError("metadata must be a dictionary or None")

        # Validate serialization before touching the filesystem. This also prevents
        # non-standard JSON values such as NaN from entering the manifest.
        metadata_bytes = json.dumps(
            metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        content_bytes = content.encode("utf-8")
        digest = hashlib.sha256(content_bytes).hexdigest()

        with self._lock:
            run_dir = self._run_dir(task_id, run_id)
            result_path = run_dir / _RESULT_NAME
            manifest_path = run_dir / _MANIFEST_NAME
            manifest = {
                "schema_version": 1,
                "task_id": task_id,
                "run_id": run_id,
                "result_file": _RESULT_NAME,
                "sha256": digest,
                "source_sha": metadata.get("source_sha"),
                "metadata": json.loads(metadata_bytes.decode("utf-8")),
            }
            manifest_bytes = (
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
            ).encode("utf-8")

            # Publish the manifest last: its digest acts as the commit marker for
            # the corresponding result.md contents.
            self._atomic_write(result_path, content_bytes)
            self._atomic_write(manifest_path, manifest_bytes)

        return {
            "path": str(result_path.resolve()),
            "manifest_path": str(manifest_path.resolve()),
            "sha256": digest,
        }

    def read_result(self, task_id: str, run_id: str) -> dict[str, Any] | None:
        """Read a complete artifact, returning None when either file is absent."""
        task = _validate_task_id(task_id)
        run = _validate_run_id(run_id)
        run_dir = self.root / task / run
        try:
            for directory in (self.root, self.root / task, run_dir):
                info = directory.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise ValueError(f"artifact path is not a real directory: {directory}")
            manifest_path = run_dir / _MANIFEST_NAME
            result_path = run_dir / _RESULT_NAME
            for file_path in (manifest_path, result_path):
                info = file_path.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise ValueError(f"artifact path is not a regular file: {file_path}")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            content_bytes = result_path.read_bytes()
        except FileNotFoundError:
            return None

        if not isinstance(manifest, dict):
            raise ValueError("artifact manifest must be a JSON object")
        if (
            manifest.get("schema_version") != 1
            or manifest.get("task_id") != task_id
            or manifest.get("run_id") != run_id
            or manifest.get("result_file") != _RESULT_NAME
        ):
            raise ValueError("artifact manifest identity or schema does not match the requested run")
        digest = hashlib.sha256(content_bytes).hexdigest()
        if manifest.get("sha256") != digest:
            raise ValueError("artifact result SHA-256 does not match its manifest")
        metadata = manifest.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("artifact manifest metadata must be a JSON object")

        return {
            "task_id": task_id,
            "run_id": run_id,
            "content": content_bytes.decode("utf-8"),
            "metadata": metadata,
            "path": str(result_path.resolve()),
            "manifest_path": str(manifest_path.resolve()),
            "sha256": digest,
        }
