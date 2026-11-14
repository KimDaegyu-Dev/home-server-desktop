"""Per-run Kanzo sandbox HTTP runner.

Source is installed by the separate init container before this server starts.
The runner never writes to /workspace and never receives broker source tokens.
It exposes only bounded argv execution below /workspace or /scratch.

Except GET /health, endpoints require Bearer auth from
the runner's protected startup environment:
  GET  /activity
  POST /exec/start   {exec_id, argv, cwd, stdin, timeout_seconds}
  POST /exec/poll    {exec_id, cursor}
  POST /exec/cancel  {exec_id}
  POST /exec/cleanup {}

The Pod must still provide OS-level resource/network/filesystem isolation. A
process that deliberately escapes its process group cannot be contained by
this stdlib-only service; cleanup/cancel therefore fail closed if group exit
cannot be confirmed.
"""

from __future__ import annotations

import ctypes
import hmac
import hashlib
import bisect
import http.server
import io
import json
import os
import re
import signal
import stat
import subprocess
import tarfile
import threading
import time
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit


HOST = "0.0.0.0"
PORT = 8000
TOKEN_ENV = "KANZO_RUNNER_TOKEN"
WORKSPACE = Path("/workspace")
SCRATCH = Path("/scratch")
BASELINE_FILE = Path("/tmp/kanzo-baseline.json")

MAX_HTTP_RESPONSE_BYTES = 64 * 1024
MAX_JSON_BYTES = 256 * 1024
MAX_ARGV_ITEMS = 256
MAX_ARGV_BYTES = 128 * 1024
MAX_STDIN_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_POLL_CHUNK_BYTES = 8 * 1024
MAX_CONCURRENT_EXECUTIONS = 4
MAX_RETAINED_EXECUTIONS = 32
MAX_HTTP_THREADS = 16
MAX_TIMEOUT_SECONDS = 3600
MAX_BASELINE_BYTES = 64 * 1024 * 1024
MAX_CHANGE_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_CHANGE_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_CHANGE_METADATA_BYTES = 8 * 1024 * 1024
MAX_WORKSPACE_SCAN_BYTES = 4 * 1024 * 1024 * 1024
EXEC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
SAFE_ENV = {
    "HOME": "/scratch",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "TMPDIR": "/scratch",
    "TMP": "/scratch",
    "TEMP": "/scratch",
}


class RequestError(Exception):
    def __init__(self, status: int, message: str, state: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.state = state


class BoundedBytesIO(io.BytesIO):
    """Memory buffer that refuses archives above the configured response cap."""

    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > MAX_CHANGE_ARCHIVE_BYTES:
            raise RequestError(413, "change archive exceeds the output size limit")
        return super().write(data)


def _validate_relative_path(value: str) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value:
        return False
    if "\\" in value or value.startswith("/") or (len(value) >= 2 and value[1] == ":"):
        return False
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    for part in parts:
        if any(character in part for character in '<>:"|?*') or part.endswith((" ", ".")):
            return False
        device_name = part.split(".", 1)[0].upper()
        if device_name in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            return False
        if unicodedata.normalize("NFC", part) != part:
            return False
    return PurePosixPath(value).as_posix() == value


class _DigestReader:
    def __init__(self, source: Any):
        self.source = source
        self.digest = hashlib.sha256()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = self.source.read(size)
        self.digest.update(data)
        self.bytes_read += len(data)
        return data


class Execution:
    def __init__(
        self,
        exec_id: str,
        process: subprocess.Popen[bytes],
        cwd: str,
        timeout: int,
        stdin_data: bytes,
    ):
        self.exec_id = exec_id
        self.process = process
        self.cwd = cwd
        self.observed_cwd = cwd
        self.timeout = timeout
        self.output = bytearray()
        self.output_lock = threading.Lock()
        self.state_lock = threading.RLock()
        self.state = "running"
        self.returncode: int | None = None
        self.cancel_requested = False
        self.output_limited = False
        self.started = time.monotonic()
        self.stdout_thread = threading.Thread(
            target=self._read_output, args=(process.stdout,), daemon=True
        )
        self.stderr_thread = threading.Thread(
            target=self._read_output, args=(process.stderr,), daemon=True
        )
        self.stdin_thread = threading.Thread(
            target=self._write_stdin, args=(process.stdin, stdin_data), daemon=True
        )
        self.stdout_thread.start()
        self.stderr_thread.start()
        self.stdin_thread.start()
        self.monitor_thread = threading.Thread(target=self._monitor, daemon=True)
        self.monitor_thread.start()

    def _read_output(self, stream: Any) -> None:
        try:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    break
                with self.output_lock:
                    remaining = MAX_OUTPUT_BYTES - len(self.output)
                    if remaining > 0:
                        self.output.extend(chunk[:remaining])
                    if len(chunk) > remaining and not self.output_limited:
                        self.output_limited = True
                        threading.Thread(target=self._terminate_group, daemon=True).start()
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    @staticmethod
    def _write_stdin(stream: Any, data: bytes) -> None:
        if stream is None:
            return
        try:
            if data:
                stream.write(data)
                stream.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        try:
            stream.close()
        except OSError:
            pass

    def _observe_cwd(self) -> None:
        # Linux exposes the live process cwd through /proc. Keep only paths
        # within the allowed roots; after exit, the last safe observation wins.
        try:
            candidate = Path(os.readlink(f"/proc/{self.process.pid}/cwd"))
            resolved = candidate.resolve(strict=True)
            roots = (WORKSPACE.resolve(strict=True), SCRATCH.resolve(strict=True))
            if any(resolved == root or root in resolved.parents for root in roots):
                self.observed_cwd = str(resolved)
        except (OSError, RuntimeError):
            pass

    def _monitor(self) -> None:
        deadline = self.started + self.timeout
        while self.process.poll() is None:
            self._observe_cwd()
            with self.state_lock:
                cancelled = self.cancel_requested
            if cancelled:
                self._terminate_group()
                break
            if time.monotonic() >= deadline:
                with self.state_lock:
                    if self.state == "running":
                        self.state = "timing_out"
                self._terminate_group()
                break
            time.sleep(0.05)
        self._observe_cwd()
        try:
            self.returncode = self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._kill_group()
            try:
                self.returncode = self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.returncode = None
        self.stdout_thread.join(timeout=2)
        self.stderr_thread.join(timeout=2)
        # A child may outlive the direct process while retaining its pipes.
        # Stop remaining group members before marking the execution complete.
        if self._group_exists():
            self._terminate_group()
            self.stdout_thread.join(timeout=2)
            self.stderr_thread.join(timeout=2)
        with self.state_lock:
            if self.state in {"running", "timing_out"}:
                self.state = "completed"

    def _group_exists(self) -> bool:
        try:
            os.killpg(self.process.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    def _signal_group(self, signum: int) -> None:
        try:
            os.killpg(self.process.pid, signum)
        except ProcessLookupError:
            pass
        except OSError:
            try:
                if signum == signal.SIGKILL:
                    self.process.kill()
                elif signum == signal.SIGTERM:
                    self.process.terminate()
            except OSError:
                pass

    def _terminate_group(self) -> None:
        self._signal_group(signal.SIGTERM)
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and self._group_exists():
            time.sleep(0.05)
        if self._group_exists():
            self._signal_group(signal.SIGKILL)
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    def cancel(self) -> bool:
        with self.state_lock:
            if self.state == "completed" and not self._group_exists():
                return False
            self.cancel_requested = True
        self._terminate_group()
        self.monitor_thread.join(timeout=6)
        if self.monitor_thread.is_alive() or self.process.poll() is None or self._group_exists():
            return False
        with self.state_lock:
            self.returncode = self.process.returncode
            self.state = "cancelled"
        return True

    def poll(self, cursor: int, wait_seconds: float = 0.4) -> dict[str, Any]:
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            with self.output_lock:
                has_data = len(self.output) > cursor
            with self.state_lock:
                still_running = self.state in {"running", "timing_out"}
            if has_data or not still_running:
                break
            time.sleep(0.02)
        with self.output_lock:
            end = len(self.output)
            if cursor < 0 or cursor > end:
                raise RequestError(400, "invalid output cursor")
            next_cursor = min(end, cursor + MAX_POLL_CHUNK_BYTES)
            chunk = bytes(self.output[cursor:next_cursor]).decode("utf-8", errors="replace")
        with self.state_lock:
            state = self.state
            result: dict[str, Any] = {
                "state": "running" if state in {"running", "timing_out"} else state,
                "output_chunk": chunk,
                "next_cursor": next_cursor,
                "activity": state in {"running", "timing_out"} or self._group_exists(),
                "retry_after_ms": 200 if not chunk and state in {"running", "timing_out"} else 0,
            }
            if state in {"completed", "cancelled"}:
                result.update(
                    {
                        "returncode": self.returncode,
                        "exit_code": self.returncode,
                        "cwd": self.observed_cwd,
                        "cwd_observed": True,
                        "output_limited": self.output_limited,
                    }
                )
            return result


class Runner:
    def __init__(self, token: str, baseline: dict[str, dict[str, Any]]):
        self._token = token.encode("utf-8")
        self._baseline = baseline
        self._executions: dict[str, Execution] = {}
        self._lock = threading.RLock()
        self._closing = False
        self._exporting = False

    def activity(self) -> dict[str, Any]:
        with self._lock:
            active = sum(
                execution.state not in {"completed", "cancelled"} or execution._group_exists()
                for execution in self._executions.values()
            )
            return {
                "accepting": not self._closing and not self._exporting,
                "active_executions": active,
                "exporting_changes": self._exporting,
            }

    def changes(self) -> bytes:
        with self._lock:
            if self._closing:
                raise RequestError(410, "runner is shutting down")
            if self._exporting:
                raise RequestError(409, "change export is already in progress")
            if any(
                execution.state not in {"completed", "cancelled"} or execution._group_exists()
                for execution in self._executions.values()
            ):
                raise RequestError(409, "all executions must be stopped before exporting changes")
            self._exporting = True
        try:
            current = self._scan_workspace()
            removed = sorted(
                (
                    path
                    for path, entry in self._baseline.items()
                    if entry["kind"] == "file"
                    and (path not in current or current[path]["kind"] != "file")
                ),
                key=lambda item: item,
            )
            changed = [
                path
                for path, entry in sorted(current.items())
                if entry["kind"] == "file"
                and (
                    path not in self._baseline
                    or self._baseline[path].get("kind") != "file"
                    or self._baseline[path].get("sha256") != entry["sha256"]
                    or self._baseline[path].get("executable") != entry["executable"]
                )
            ]
            if "kanzo-output.json" in current:
                raise RequestError(409, "workspace uses the reserved kanzo-output.json path")
            self._validate_change_paths(changed, removed)
            metadata = json.dumps(
                {"schema": "kanzo-output/v1", "deleted": removed},
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
            if len(metadata) > MAX_CHANGE_METADATA_BYTES:
                raise RequestError(413, "change metadata exceeds the output size limit")
            changed_size = sum(int(current[path]["size"]) for path in changed)
            if changed_size > MAX_CHANGE_EXPANDED_BYTES:
                raise RequestError(413, "changed files exceed the expanded output limit")

            output = BoundedBytesIO()
            try:
                with tarfile.open(
                    fileobj=output,
                    mode="w|gz",
                    format=tarfile.USTAR_FORMAT,
                    encoding="utf-8",
                    errors="strict",
                ) as archive:
                    self._add_tar_bytes(archive, "kanzo-output.json", metadata)
                    for path in changed:
                        if len(path.encode("utf-8")) > 255:
                            raise RequestError(413, "changed path exceeds the tar path limit")
                        info = current[path]
                        tar_info = tarfile.TarInfo(path)
                        tar_info.type = tarfile.REGTYPE
                        tar_info.size = int(info["size"])
                        tar_info.mode = 0o755 if info["executable"] else 0o644
                        tar_info.mtime = 0
                        descriptor = self._open_workspace_file(path)
                        with os.fdopen(descriptor, "rb") as source:
                            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                                raise RequestError(400, "workspace entry changed during export")
                            reader = _DigestReader(source)
                            archive.addfile(tar_info, reader)
                            if (
                                reader.bytes_read != tar_info.size
                                or reader.digest.hexdigest() != info["sha256"]
                            ):
                                raise RequestError(409, "workspace changed during export")
            except RequestError:
                raise
            except (OSError, tarfile.TarError, ValueError):
                raise RequestError(400, "could not safely create the change archive") from None
            return output.getvalue()
        finally:
            with self._lock:
                self._exporting = False

    @staticmethod
    def _add_tar_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
        info = tarfile.TarInfo(name)
        info.type = tarfile.REGTYPE
        info.size = len(data)
        info.mode = 0o644
        info.mtime = 0
        archive.addfile(info, io.BytesIO(data))

    @staticmethod
    def _validate_change_paths(writes: list[str], deletes: list[str]) -> None:
        exact: set[str] = set()
        folded: dict[str, str] = {}
        for path in [*writes, *deletes]:
            if path in exact:
                raise RequestError(409, "workspace path has conflicting write/delete operations")
            exact.add(path)
            key = unicodedata.normalize("NFC", path).casefold()
            previous = folded.get(key)
            if previous is not None and previous != path:
                raise RequestError(409, "workspace contains a case-insensitive path collision")
            folded[key] = path
        ordered = sorted(folded)
        for key in ordered:
            index = bisect.bisect_right(ordered, key)
            if index < len(ordered) and ordered[index].startswith(key + "/"):
                raise RequestError(409, "workspace change requires an unsupported file-directory transition")

    @staticmethod
    def _open_workspace_file(relative: str) -> int:
        parts = relative.split("/")
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        current_fd = os.open(WORKSPACE, directory_flags)
        try:
            for part in parts[:-1]:
                next_fd = os.open(part, directory_flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            return os.open(
                parts[-1],
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=current_fd,
            )
        finally:
            os.close(current_fd)

    @staticmethod
    def _scan_workspace() -> dict[str, dict[str, Any]]:
        entries: dict[str, dict[str, Any]] = {}
        pending = [WORKSPACE]
        total_size = 0
        while pending:
            directory = pending.pop()
            try:
                children = sorted(os.scandir(directory), key=lambda item: item.name)
            except OSError:
                raise RequestError(400, "cannot inspect workspace") from None
            for child in children:
                path = Path(child.path)
                relative = path.relative_to(WORKSPACE).as_posix()
                if not _validate_relative_path(relative):
                    raise RequestError(400, "workspace contains an invalid path")
                try:
                    info = child.stat(follow_symlinks=False)
                except OSError:
                    raise RequestError(400, "cannot inspect workspace entry") from None
                if stat.S_ISDIR(info.st_mode):
                    entries[relative] = {"kind": "dir"}
                    pending.append(path)
                elif stat.S_ISREG(info.st_mode):
                    digest = hashlib.sha256()
                    try:
                        descriptor = Runner._open_workspace_file(relative)
                        with os.fdopen(descriptor, "rb") as source:
                            opened = os.fstat(source.fileno())
                            if not stat.S_ISREG(opened.st_mode):
                                raise RequestError(400, "workspace entry changed while scanning")
                            bytes_read = 0
                            while True:
                                chunk = source.read(1024 * 1024)
                                if not chunk:
                                    break
                                digest.update(chunk)
                                bytes_read += len(chunk)
                            if bytes_read != opened.st_size:
                                raise RequestError(400, "workspace file changed while scanning")
                    except OSError:
                        raise RequestError(400, "cannot read workspace file") from None
                    total_size += bytes_read
                    if total_size > MAX_WORKSPACE_SCAN_BYTES:
                        raise RequestError(413, "workspace scan exceeds the expanded size limit")
                    entries[relative] = {
                        "kind": "file",
                        "sha256": digest.hexdigest(),
                        "executable": bool(info.st_mode & 0o111),
                        "size": bytes_read,
                    }
                else:
                    raise RequestError(400, "workspace contains a symlink or special file")
                if len(entries) > 20_000:
                    raise RequestError(413, "workspace contains too many entries")
        return entries

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        exec_id = payload.get("exec_id")
        if not isinstance(exec_id, str) or not EXEC_ID_RE.fullmatch(exec_id):
            raise RequestError(400, "exec_id must be a random URL-safe id of 22 to 128 characters")
        argv = payload.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or len(argv) > MAX_ARGV_ITEMS
            or any(not isinstance(arg, str) or "\x00" in arg for arg in argv)
        ):
            raise RequestError(400, "argv must be a non-empty array of strings")
        try:
            if sum(len(arg.encode("utf-8")) + 1 for arg in argv) > MAX_ARGV_BYTES:
                raise RequestError(413, "argv exceeds the size limit")
        except UnicodeEncodeError:
            raise RequestError(400, "argv must be valid UTF-8") from None
        if not argv[0]:
            raise RequestError(400, "argv[0] must not be empty")
        stdin = payload.get("stdin")
        if stdin is not None and not isinstance(stdin, str):
            raise RequestError(400, "stdin must be a string or null")
        try:
            stdin_bytes = stdin.encode("utf-8") if stdin is not None else b""
        except UnicodeEncodeError:
            raise RequestError(400, "stdin must be valid UTF-8") from None
        if len(stdin_bytes) > MAX_STDIN_BYTES:
            raise RequestError(413, "stdin exceeds the size limit")
        cwd = self._validate_cwd(payload.get("cwd"))
        timeout = payload.get("timeout_seconds", 120)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise RequestError(400, "timeout_seconds must be a number")
        if timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
            raise RequestError(400, "timeout_seconds is out of range")
        timeout = max(1, int(timeout))

        with self._lock:
            if self._closing:
                raise RequestError(410, "runner is shutting down")
            if self._exporting:
                raise RequestError(409, "change export is in progress")
            if exec_id in self._executions:
                raise RequestError(409, "exec_id already exists")
            if len(self._executions) >= MAX_RETAINED_EXECUTIONS:
                for old_id, old_execution in list(self._executions.items()):
                    if old_execution.state in {"completed", "cancelled"}:
                        del self._executions[old_id]
                        if len(self._executions) < MAX_RETAINED_EXECUTIONS:
                            break
            if len(self._executions) >= MAX_RETAINED_EXECUTIONS:
                raise RequestError(429, "execution history capacity is full")
            active = sum(
                item.state not in {"completed", "cancelled"}
                for item in self._executions.values()
            )
            if active >= MAX_CONCURRENT_EXECUTIONS:
                raise RequestError(429, "execution capacity is full")
            try:
                process = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=cwd,
                    env=dict(SAFE_ENV),
                    shell=False,
                    start_new_session=True,
                    close_fds=True,
                    bufsize=0,
                )
            except (OSError, ValueError):
                raise RequestError(400, "could not start the requested executable") from None
            execution = Execution(exec_id, process, cwd, timeout, stdin_bytes)
            self._executions[exec_id] = execution
        return {"exec_id": exec_id, "state": "running", "activity": True}

    @staticmethod
    def _validate_cwd(value: Any) -> str:
        if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
            raise RequestError(400, "cwd must be an absolute path under /workspace or /scratch")
        try:
            resolved = Path(value).resolve(strict=True)
            roots = (WORKSPACE.resolve(strict=True), SCRATCH.resolve(strict=True))
            allowed = any(resolved == root or root in resolved.parents for root in roots)
            if not allowed or not resolved.is_dir():
                raise RequestError(400, "cwd must be an existing directory under /workspace or /scratch")
        except (OSError, RuntimeError):
            raise RequestError(400, "cwd is unavailable") from None
        return str(resolved)

    def poll(self, payload: dict[str, Any]) -> dict[str, Any]:
        execution = self._get_execution(payload.get("exec_id"))
        cursor = payload.get("cursor", 0)
        if isinstance(cursor, bool) or not isinstance(cursor, int):
            raise RequestError(400, "cursor must be a non-negative integer")
        return execution.poll(cursor)

    def cancel(self, payload: dict[str, Any]) -> dict[str, Any]:
        execution = self._get_execution(payload.get("exec_id"))
        if execution.cancel():
            return {"state": "cancelled", "exec_id": execution.exec_id, "activity": False}
        if execution.process.poll() is not None and not execution._group_exists():
            return {"state": "completed", "exec_id": execution.exec_id, "activity": False}
        return {
            "state": "cancellation_pending",
            "exec_id": execution.exec_id,
            "activity": True,
        }

    def cleanup(self) -> dict[str, Any]:
        # Gate and snapshot under one lock: after _closing is set no later start
        # can get past the acceptance check.
        with self._lock:
            self._closing = True
            if self._exporting:
                return {"state": "cleanup_pending", "pending_exec_ids": [], "activity": True}
            executions = list(self._executions.values())
        pending: list[str] = []
        cancelled = 0
        for execution in executions:
            if execution.state in {"completed", "cancelled"} and not execution._group_exists():
                continue
            if execution.cancel():
                cancelled += 1
            else:
                pending.append(execution.exec_id)
        if pending:
            return {"state": "cleanup_pending", "pending_exec_ids": pending, "activity": True}
        return {"state": "cleaned", "cancelled": cancelled, "activity": False}

    def _get_execution(self, value: Any) -> Execution:
        if not isinstance(value, str) or not EXEC_ID_RE.fullmatch(value):
            raise RequestError(400, "invalid exec_id")
        with self._lock:
            execution = self._executions.get(value)
        if execution is None:
            raise RequestError(404, "execution not found")
        return execution


def _disable_dumpable() -> None:
    # Prevent same-UID task processes from reading the runner's original
    # environment or memory through /proc before the token is removed below.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE
        error = ctypes.get_errno()
        raise RuntimeError(f"cannot protect runner credentials (prctl errno={error})")


def _load_token() -> str:
    token = os.environ.pop(TOKEN_ENV, "").strip()
    if len(token) < 32 or len(token) > 4096 or "\n" in token or "\r" in token:
        raise RuntimeError("runner token environment is unavailable or invalid")
    return token


def _load_baseline() -> dict[str, dict[str, Any]]:
    try:
        info = BASELINE_FILE.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > MAX_BASELINE_BYTES:
            raise RuntimeError("source baseline file is invalid")
        raw = BASELINE_FILE.read_bytes()
    except OSError:
        raise RuntimeError("source baseline file is unavailable") from None
    finally:
        try:
            BASELINE_FILE.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise RuntimeError("source baseline file is invalid") from None
    if not isinstance(value, dict) or value.get("schema") != "kanzo-baseline/v1":
        raise RuntimeError("source baseline schema is invalid")
    entries = value.get("entries")
    if not isinstance(entries, dict) or len(entries) > 20_000:
        raise RuntimeError("source baseline entries are invalid")
    validated: dict[str, dict[str, Any]] = {}
    for path, entry in entries.items():
        if (
            not isinstance(path, str)
            or not _validate_relative_path(path)
            or not isinstance(entry, dict)
        ):
            raise RuntimeError("source baseline contains an invalid path")
        kind = entry.get("kind")
        if kind == "dir":
            validated[path] = {"kind": "dir"}
        elif (
            kind == "file"
            and isinstance(entry.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
            and isinstance(entry.get("executable"), bool)
        ):
            validated[path] = {
                "kind": "file",
                "sha256": entry["sha256"],
                "executable": entry["executable"],
            }
        else:
            raise RuntimeError("source baseline contains an invalid entry")
    return validated


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "KanzoSandboxRunner/1"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Never log paths, request bodies, arguments, output, or credentials.
        return

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        try:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        except (TypeError, ValueError):
            status, body = 500, b'{"error":"internal error"}'
        if len(body) > MAX_HTTP_RESPONSE_BYTES:
            status, body = 500, b'{"error":"response too large"}'
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _send_archive(self, archive: bytes) -> None:
        digest = hashlib.sha256(archive).hexdigest()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Length", str(len(archive)))
            self.send_header("Content-Disposition", 'attachment; filename="kanzo-changes.tar.gz"')
            self.send_header("X-Content-SHA256", digest)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(archive)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _authorized(self) -> bool:
        value = self.headers.get("Authorization", "")
        scheme, separator, supplied = value.partition(" ")
        return bool(
            separator
            and scheme.lower() == "bearer"
            and supplied
            and hmac.compare_digest(supplied.encode("utf-8"), self.server.runner._token)
        )

    def _read_json(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise RequestError(400, "transfer encoding is unsupported")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not raw_length.isascii() or not raw_length.isdecimal():
            raise RequestError(411, "content length is required")
        length = int(raw_length)
        if length > MAX_JSON_BYTES:
            raise RequestError(413, "request body exceeds the size limit")
        body = self.rfile.read(length)
        if len(body) != length:
            raise RequestError(400, "request body is incomplete")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            raise RequestError(400, "request body must be valid JSON") from None
        if not isinstance(payload, dict):
            raise RequestError(400, "request body must be a JSON object")
        return payload

    def _dispatch(self, method: str) -> None:
        try:
            target = urlsplit(self.path)
            if target.path == "/health" and method == "GET":
                self._send(200, {"status": "ok"})
                return
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            if method == "GET" and target.path == "/activity":
                self._send(200, self.server.runner.activity())
                return
            if method == "GET" and target.path == "/changes":
                self._send_archive(self.server.runner.changes())
                return
            if method == "POST" and target.path in {
                "/exec/start",
                "/exec/poll",
                "/exec/cancel",
                "/exec/cleanup",
            }:
                payload = self._read_json()
                if target.path == "/exec/start":
                    result = self.server.runner.start(payload)
                elif target.path == "/exec/poll":
                    result = self.server.runner.poll(payload)
                elif target.path == "/exec/cancel":
                    result = self.server.runner.cancel(payload)
                else:
                    result = self.server.runner.cleanup()
                status = 202 if result.get("state") in {"cancellation_pending", "cleanup_pending"} else 200
                self._send(status, result)
                return
            self._send(404, {"error": "not found"})
        except RequestError as exc:
            payload: dict[str, Any] = {"error": exc.message}
            if exc.state:
                payload["state"] = exc.state
            self._send(exc.status, payload)
        except (ValueError, OSError):
            self._send(400, {"error": "invalid request"})

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")


class ThreadingHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], runner: Runner):
        self.runner = runner
        self._request_slots = threading.BoundedSemaphore(MAX_HTTP_THREADS)
        super().__init__(address, Handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


def main() -> None:
    _disable_dumpable()
    token = _load_token()
    baseline = _load_baseline()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if not WORKSPACE.is_dir() or stat.S_ISLNK(WORKSPACE.lstat().st_mode):
        raise RuntimeError("source init did not prepare /workspace")
    server = ThreadingHTTPServer((HOST, PORT), Runner(token, baseline))
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.runner.cleanup()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
