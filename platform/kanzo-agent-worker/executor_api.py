#!/usr/bin/env python3
"""Authenticated, bounded HTTP facade for the resident Hermes Gateway helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import selectors
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from runtime_lock import acquire_lock, release_lock


ROOT = Path(__file__).resolve().parent
SESSION_HELPER = ROOT / "gateway_session.py"
EXECUTION_HELPER = ROOT / "gateway_execution.py"
CONTROL_HELPER = ROOT / "gateway_control.py"
HERMES_PYTHON = os.environ.get("HERMES_PYTHON", "/opt/hermes/.venv/bin/python")
MAX_BODY = 4 * 1024 * 1024 + 64 * 1024
MAX_PROMPT = 4 * 1024 * 1024
MAX_OUTPUT = 8 * 1024 * 1024
MAX_CHILD_SECONDS = 2700
SHUTDOWN_DRAIN_SECONDS = MAX_CHILD_SECONDS + 60
PROFILE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
IDENTITY_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")
SAFE_ERROR = "executor_operation_failed"

# Tool access is server policy. Callers cannot supply toolsets, cwd, or any path.
TOOLSETS = [
    "search", "memory", "skills", "todo", "terminal", "file",
    "delegation", "code_execution", "clarify", "kanban", "mcp-home_server",
]
TOOLSET_POLICIES = {
    "standard": tuple(TOOLSETS),
    # Hermes interprets an empty list as "no native tools, include profile MCPs".
    # The no_mcp sentinel suppresses those defaults as well as native tools.
    "response_only": ("no_mcp",),
}
PROFILES = {"default", "researcher", "builder", "reviewer"}
REASONING = {"low", "medium", "high", "xhigh", "extra_high"}


class ApiError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _profile_home(name: str) -> Path:
    """Resolve a profile from the fixed allowlist beneath the configured Hermes home."""
    if name not in PROFILES:
        raise ApiError(400, "profile_not_allowed")
    base_value = os.environ.get("HERMES_HOME", "/opt/data/home")
    base = Path(base_value).resolve(strict=True)
    home = base if name == "default" else base / "profiles" / name
    try:
        resolved = home.resolve(strict=True)
        resolved.relative_to(base)
    except (OSError, ValueError, RuntimeError):
        raise ApiError(503, "profile_unavailable") from None
    if not resolved.is_dir():
        raise ApiError(503, "profile_unavailable")
    return resolved


def _valid_identity(value: Any, maximum: int) -> bool:
    return isinstance(value, str) and len(value) <= maximum and bool(IDENTITY_RE.fullmatch(value))


def _gateway_ready(profile_home: Path) -> bool:
    """Check the canonical Gateway endpoint without creating a session or ticket."""
    probe = (
        "import sys; from pathlib import Path; "
        "from hermes_cli.gateway_runtime import discover_gateway_endpoint; "
        "result=discover_gateway_endpoint(Path(sys.argv[1]), timeout=1.0); "
        "raise SystemExit(0 if getattr(result, 'state', None) == 'ready' else 1)"
    )
    try:
        code, _, _ = _run_helper(
            [HERMES_PYTHON, "-c", probe, str(profile_home)],
            timeout=4,
            output_limit=4096,
            cwd="/opt/hermes",
        )
    except (ApiError, OSError, subprocess.SubprocessError):
        return False
    return code == 0


class Store:
    def __init__(self, data_dir: Path, reconcile_wakeup: threading.Event | None = None):
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.reconcile_wakeup = reconcile_wakeup
        try:
            os.chmod(data_dir, 0o700)
        except OSError:
            pass
        # The persistent SQLite ledger must have one process owner. During a
        # Kubernetes Recreate rollout the old Pod can still be draining after
        # its replacement has started; without this lock the new Store would
        # mark the old process's admitted submission ambiguous prematurely.
        self.owner_lock_fd: int | None = acquire_lock(data_dir / "executor-api.lock")
        self.path = data_dir / "executor.sqlite3"
        self.lock = threading.RLock()
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                task_id TEXT NOT NULL, run_id TEXT NOT NULL,
                profile TEXT NOT NULL, request_hash TEXT NOT NULL,
                create_request_id TEXT, create_payload TEXT,
                session_id TEXT, create_generation INTEGER, create_state TEXT NOT NULL,
                submit_state TEXT NOT NULL DEFAULT 'not_started',
                admission_id TEXT, execution_generation INTEGER,
                updated REAL NOT NULL, PRIMARY KEY(task_id, run_id),
                UNIQUE(session_id)
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
            if "create_generation" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN create_generation INTEGER")
            if "create_request_id" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN create_request_id TEXT")
            if "create_payload" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN create_payload TEXT")
            # A process restart cannot establish whether an in-flight helper crossed
            # the Gateway boundary. Such operations must never be replayed implicitly.
            db.execute("UPDATE runs SET create_state='ambiguous', updated=? WHERE create_state='creating'", (time.time(),))
            db.execute("UPDATE runs SET submit_state='ambiguous', updated=? WHERE submit_state IN ('submitting','admitted')", (time.time(),))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level="IMMEDIATE")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @contextmanager
    def connection(self):
        """Commit/rollback and always close a short-lived SQLite connection."""
        db = self.connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def close(self) -> None:
        if self.owner_lock_fd is not None:
            release_lock(self.owner_lock_fd)
            os.close(self.owner_lock_fd)
            self.owner_lock_fd = None

    def get(self, task: str, run: str) -> sqlite3.Row | None:
        with self.lock, self.connection() as db:
            return db.execute("SELECT * FROM runs WHERE task_id=? AND run_id=?", (task, run)).fetchone()

    def begin_create(self, task: str, run: str, profile: str, digest: str,
                     payload: dict[str, str]) -> tuple[bool, str]:
        with self.lock, self.connection() as db:
            row = db.execute("SELECT * FROM runs WHERE task_id=? AND run_id=?", (task, run)).fetchone()
            if row:
                if row["request_hash"] != digest or row["profile"] != profile:
                    raise ApiError(409, "run_identity_conflict")
                if row["create_state"] == "created" and row["session_id"]:
                    return False, row["create_request_id"] or ""
                if (row["create_state"] == "ambiguous" and row["create_request_id"]
                        and row["create_payload"] == json.dumps(payload, sort_keys=True, separators=(",", ":"))):
                    db.execute("UPDATE runs SET create_state='creating',updated=? WHERE task_id=? AND run_id=? AND create_state='ambiguous'",
                               (time.time(), task, run))
                    return True, row["create_request_id"]
                raise ApiError(409, "create_non_retryable")
            request_id = uuid.uuid4().hex
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            db.execute("INSERT INTO runs(task_id,run_id,profile,request_hash,create_request_id,create_payload,create_state,updated) VALUES(?,?,?,?,?,?,?,?)",
                       (task, run, profile, digest, request_id, payload_json, "creating", time.time()))
            return True, request_id

    def ambiguous_creates(self) -> list[sqlite3.Row]:
        with self.lock, self.connection() as db:
            return list(db.execute("SELECT * FROM runs WHERE create_state='ambiguous' AND create_request_id IS NOT NULL AND create_payload IS NOT NULL"))

    def retry_create(self, task: str, run: str) -> bool:
        with self.lock, self.connection() as db:
            cur = db.execute("UPDATE runs SET create_state='creating',updated=? WHERE task_id=? AND run_id=? AND create_state='ambiguous' AND create_request_id IS NOT NULL AND create_payload IS NOT NULL",
                             (time.time(), task, run))
            return cur.rowcount == 1

    def ambiguous_submits(self) -> list[sqlite3.Row]:
        with self.lock, self.connection() as db:
            return list(db.execute("SELECT * FROM runs WHERE submit_state='ambiguous'"))

    def finish_create(self, task: str, run: str, session: str, generation: int) -> None:
        with self.lock, self.connection() as db:
            cur = db.execute("UPDATE runs SET session_id=?,create_generation=?,create_state='created',updated=? WHERE task_id=? AND run_id=? AND create_state='creating'",
                             (session, generation, time.time(), task, run))
            if cur.rowcount != 1:
                raise ApiError(503, "session_persistence_failed")

    def fail_create(self, task: str, run: str) -> None:
        with self.lock, self.connection() as db:
            db.execute("UPDATE runs SET create_state='ambiguous',updated=? WHERE task_id=? AND run_id=? AND create_state='creating'",
                       (time.time(), task, run))

    def begin_submit(self, task: str, run: str) -> sqlite3.Row:
        with self.lock, self.connection() as db:
            row = db.execute("SELECT * FROM runs WHERE task_id=? AND run_id=?", (task, run)).fetchone()
            if not row or row["create_state"] != "created" or not row["session_id"]:
                raise ApiError(409, "session_not_created")
            if row["submit_state"] != "not_started":
                raise ApiError(409, "submit_non_retryable")
            db.execute("UPDATE runs SET submit_state='submitting',updated=? WHERE task_id=? AND run_id=? AND submit_state='not_started'",
                       (time.time(), task, run))
            return row

    def save_admission(self, task: str, run: str, session: str, admission: str) -> None:
        with self.lock, self.connection() as db:
            cur = db.execute("UPDATE runs SET admission_id=?,submit_state='admitted',updated=? WHERE task_id=? AND run_id=? AND session_id=? AND submit_state='submitting'",
                             (admission, time.time(), task, run, session))
            if cur.rowcount != 1:
                raise ApiError(503, "admission_persistence_failed")

    def set_generation(self, task: str, run: str, session: str, admission: str, generation: int) -> None:
        with self.lock, self.connection() as db:
            db.execute("UPDATE runs SET execution_generation=?,updated=? WHERE task_id=? AND run_id=? AND session_id=? AND admission_id=?",
                       (generation, time.time(), task, run, session, admission))

    def finish_submit(self, task: str, run: str, state: str) -> None:
        with self.lock, self.connection() as db:
            db.execute("UPDATE runs SET submit_state=?,updated=? WHERE task_id=? AND run_id=?",
                       (state, time.time(), task, run))
        if state == "ambiguous" and self.reconcile_wakeup is not None:
            self.reconcile_wakeup.set()


def _run_helper(args: list[str], *, timeout: int = 30,
                output_limit: int = 1024 * 1024,
                cwd: str = "/workspace") -> tuple[int, bytes, bytes]:
    """Run a fixed local helper with bounded time/output and process-group cleanup."""
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": cwd,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, **kwargs)
    assert proc.stdout is not None and proc.stderr is not None
    selector = selectors.DefaultSelector()
    output = bytearray()
    errors = bytearray()
    deadline = time.monotonic() + timeout
    try:
        selector.register(proc.stdout, selectors.EVENT_READ, output)
        selector.register(proc.stderr, selectors.EVENT_READ, errors)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ApiError(504, "helper_timeout")
            for key, _ in selector.select(min(remaining, 1.0)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = key.data
                target.extend(chunk)
                cap = output_limit if target is output else 64 * 1024
                if len(target) > cap:
                    raise ApiError(502, "helper_output_limit")
        return proc.wait(timeout=max(0.1, deadline - time.monotonic())), bytes(output), bytes(errors)
    except BaseException:
        _kill_process_group(proc)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        raise
    finally:
        selector.close()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            # The leader may exit while a descendant keeps the process group alive.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, *args: Any, max_threads: int = 32, **kwargs: Any):
        self._slots = threading.BoundedSemaphore(max_threads)
        self.stopping = threading.Event()
        self._children_lock = threading.RLock()
        self._children_changed = threading.Condition(self._children_lock)
        self._children: dict[tuple[str, str], subprocess.Popen[bytes] | None] = {}
        super().__init__(*args, **kwargs)

    def register_submission(self, task: str, run: str) -> bool:
        with self._children_changed:
            if self.stopping.is_set():
                return False
            self._children[(task, run)] = None
            return True

    def register_child(self, task: str, run: str, proc: subprocess.Popen[bytes]) -> bool:
        with self._children_changed:
            # A submission accepted before SIGTERM remains part of the drain.
            if (task, run) not in self._children:
                return False
            self._children[(task, run)] = proc
            self._children_changed.notify_all()
            return True

    def unregister_submission(self, task: str, run: str) -> None:
        with self._children_changed:
            self._children.pop((task, run), None)
            self._children_changed.notify_all()

    def request_shutdown(self) -> None:
        # Signal handlers only set the gate and delegate blocking cleanup.
        if self.stopping.is_set():
            return
        self.stopping.set()
        threading.Thread(target=self._shutdown, name="executor-shutdown", daemon=True).start()

    def _shutdown(self) -> None:
        deadline = time.monotonic() + SHUTDOWN_DRAIN_SECONDS
        with self._children_changed:
            while self._children:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._children_changed.wait(timeout=min(remaining, 1.0))
            children = list(self._children.items())
        for (task, run), proc in children:
            if proc is not None and proc.poll() is not None:
                # Let its request thread publish the terminal receipt it already observed.
                continue
            state = "ambiguous"
            try:
                row = Handler.store.get(task, run)
                if row and row["session_id"] and row["admission_id"]:
                    home = _profile_home(row["profile"])
                    args = [HERMES_PYTHON, str(CONTROL_HELPER), "interrupt", "--profile-home", str(home),
                            "--session-id", row["session_id"], "--admission-id", row["admission_id"]]
                    generation = row["execution_generation"]
                    if generation is not None:
                        args.extend(["--generation", str(generation)])
                    code, output, _ = _run_helper(args, timeout=8, output_limit=64 * 1024)
                    receipt = json.loads(output)
                    if code == 0 and isinstance(receipt, dict) and receipt.get("accepted") is True:
                        state = "cancelled"
            except Exception:
                # Preserve ambiguity if exact remote cancellation cannot be confirmed.
                state = "ambiguous"
            try:
                Handler.store.finish_submit(task, run, state)
            except Exception:
                pass
            if proc is not None:
                _kill_process_group(proc)
        self.shutdown()

    def process_request(self, request: Any, client_address: Any) -> None:
        if self.stopping.is_set():
            request.close()
            return
        if not self._slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "KanzoExecutor"
    sys_version = ""
    store: Store
    token: bytes

    def log_message(self, fmt: str, *args: Any) -> None:
        # BaseHTTPRequestHandler includes URL/client-controlled values in logs.
        return

    def _auth(self) -> None:
        header = self.headers.get("Authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value or not hmac.compare_digest(value.encode(), self.token):
            raise ApiError(401, "unauthorized")

    def _body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if not raw_length or not raw_length.isdecimal():
            raise ApiError(411, "content_length_required")
        length = int(raw_length)
        if length > MAX_BODY:
            raise ApiError(413, "request_too_large")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ApiError(400, "request_incomplete")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "invalid_json") from None
        if not isinstance(value, dict):
            raise ApiError(400, "json_object_required")
        return value

    def _send_json(self, status: int, value: dict[str, Any]) -> None:
        data = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()
        self._response_started = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _start_event_stream(self) -> None:
        if self._response_started:
            return
        self._response_started = True
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _send_stream_error(self) -> None:
        if not self._response_started:
            return
        try:
            self.wfile.write(b'{"type":"error","error":"execution_incomplete"}\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _send_error(self, error: ApiError) -> None:
        self._send_json(error.status, {"error": error.code})

    def _session_path(self) -> tuple[str, str]:
        parts = urlsplit(self.path).path.split("/")
        if len(parts) != 5 or parts[:3] != ["", "v1", "sessions"] or parts[4] != "submit":
            raise ApiError(404, "not_found")
        session = unquote(parts[3])
        if not _valid_identity(session, 256):
            raise ApiError(400, "session_id_invalid")
        return session, parts[3]

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/livez":
            self._send_json(200, {"status": "alive"})
            return
        if path == "/readyz":
            try:
                if self.server.stopping.is_set():
                    raise RuntimeError("server is stopping")
                with self.store.connection() as db:
                    db.execute("SELECT 1").fetchone()
                for profile in PROFILES:
                    _profile_home(profile)
                if not _gateway_ready(_profile_home("default")):
                    raise RuntimeError("Gateway is not ready")
                self._send_json(200, {"status": "ready"})
            except Exception:
                self._send_json(503, {"status": "unavailable"})
            return
        if path != "/healthz":
            self._send_json(404, {"error": "not_found"})
            return
        try:
            self._auth()
        except ApiError as error:
            self._send_error(error)
            return
        try:
            with self.store.connection() as db:
                db.execute("SELECT 1").fetchone()
            _profile_home("default")
            self._send_json(200, {"status": "ok"})
        except Exception:
            self._send_json(503, {"status": "unavailable"})

    def do_POST(self) -> None:
        self._response_started = False
        try:
            self._auth()
            path = urlsplit(self.path).path
            if path == "/v1/sessions":
                self._create()
            elif path == "/v1/auth/status":
                self._auth_status()
            elif path.startswith("/v1/sessions/") and path.endswith("/submit"):
                self._submit()
            elif path == "/v1/cancel":
                self._cancel()
            else:
                raise ApiError(404, "not_found")
        except ApiError as error:
            if self._response_started:
                self.close_connection = True
                return
            try:
                self._send_error(error)
            except (BrokenPipeError, ConnectionResetError):
                pass
        except Exception:
            if self._response_started:
                self.close_connection = True
                return
            try:
                self._send_json(500, {"error": SAFE_ERROR})
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _auth_status(self) -> None:
        body = self._body()
        provider = body.get("provider")
        profile = body.get("profile")
        if not isinstance(provider, str) or not _valid_identity(provider, 128):
            raise ApiError(400, "provider_invalid")
        if not isinstance(profile, str) or profile not in PROFILES:
            raise ApiError(400, "profile_not_allowed")
        _profile_home(profile)
        try:
            code, stdout, stderr = _run_helper(
                ["hermes", "--profile", profile, "auth", "status", provider],
                timeout=30,
                output_limit=64 * 1024,
                cwd="/opt/hermes",
            )
        except ApiError:
            raise
        except (OSError, subprocess.SubprocessError):
            raise ApiError(503, "auth_status_unavailable") from None
        output = (stdout + b"\n" + stderr).decode("utf-8", errors="replace").lower()
        ready = code == 0 and any(marker in output for marker in ("authenticated", "logged in", "ready"))
        self._send_json(200, {"ready": ready})

    def _create(self) -> None:
        body = self._body()
        required = {"task_id", "run_id", "profile", "provider", "model", "reasoning"}
        if not required.issubset(body) or set(body) - required - {"tool_policy"}:
            raise ApiError(400, "invalid_create_fields")
        tool_policy = body.get("tool_policy", "standard")
        if not isinstance(tool_policy, str) or tool_policy not in TOOLSET_POLICIES:
            raise ApiError(400, "tool_policy_invalid")
        task, run, profile = body["task_id"], body["run_id"], body["profile"]
        if not _valid_identity(task, 256) or not _valid_identity(run, 128):
            raise ApiError(400, "run_identity_invalid")
        if not isinstance(profile, str) or not PROFILE_RE.fullmatch(profile) or profile not in PROFILES:
            raise ApiError(400, "profile_not_allowed")
        for field in ("provider", "model", "reasoning"):
            value = body[field]
            if not isinstance(value, str) or not value or len(value) > 128 or any(ord(c) < 32 for c in value):
                raise ApiError(400, "route_invalid")
        if body["reasoning"].lower() not in REASONING:
            raise ApiError(400, "reasoning_invalid")
        home = _profile_home(profile)
        safe_body = {key: body[key] for key in ("task_id", "run_id", "profile", "provider", "model", "reasoning")}
        safe_body["tool_policy"] = tool_policy
        digest = hashlib.sha256(json.dumps(safe_body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        create_payload = {
            "provider": body["provider"], "model": body["model"],
            "reasoning": body["reasoning"], "tool_policy": tool_policy,
        }
        should_create, create_request_id = self.store.begin_create(
            task, run, profile, digest, create_payload
        )
        if not should_create:
            row = self.store.get(task, run)
            self._send_json(200, {"session_id": row["session_id"],
                                  "execution_generation": row["create_generation"], "idempotent": True})
            return
        args = [HERMES_PYTHON, str(SESSION_HELPER), "--profile-home", str(home),
                "--task-id", task, "--run-id", run, "--provider", body["provider"],
                "--model", body["model"], "--reasoning", body["reasoning"],
                "--toolsets", ",".join(TOOLSET_POLICIES[tool_policy]), "--cwd", "/workspace",
                "--request-id", create_request_id]
        try:
            code, out, _ = _run_helper(args, timeout=60, output_limit=64 * 1024)
            result = json.loads(out)
            if code != 0 or not isinstance(result, dict) or result.get("ok") is not True:
                raise ApiError(502, "gateway_session_create_failed")
            session, generation = result.get("session_id"), result.get("execution_generation")
            if not _valid_identity(session, 256) or type(generation) is not int or generation < 0:
                raise ApiError(502, "gateway_session_identity_invalid")
            self.store.finish_create(task, run, session, generation)
        except Exception:
            self.store.fail_create(task, run)
            if self.store.reconcile_wakeup is not None:
                self.store.reconcile_wakeup.set()
            raise
        self._send_json(201, {"session_id": session, "execution_generation": generation, "idempotent": False})

    def _submit(self) -> None:
        session, _ = self._session_path()
        body = self._body()
        if set(body) != {"task_id", "run_id", "prompt"}:
            raise ApiError(400, "invalid_submit_fields")
        task, run, prompt = body["task_id"], body["run_id"], body["prompt"]
        if not _valid_identity(task, 256) or not _valid_identity(run, 128):
            raise ApiError(400, "run_identity_invalid")
        if not isinstance(prompt, str) or not prompt:
            raise ApiError(400, "prompt_invalid")
        prompt_bytes = prompt.encode("utf-8")
        if len(prompt_bytes) > MAX_PROMPT:
            raise ApiError(413, "prompt_too_large")
        row = self.store.get(task, run)
        if not row or row["session_id"] != session:
            raise ApiError(404, "run_session_not_found")
        if self.server.stopping.is_set():
            raise ApiError(503, "executor_shutting_down")
        row = self.store.begin_submit(task, run)
        if not self.server.register_submission(task, run):
            self.store.finish_submit(task, run, "ambiguous")
            raise ApiError(503, "executor_shutting_down")
        temp_path: str | None = None
        proc: subprocess.Popen[bytes] | None = None
        selector: selectors.BaseSelector | None = None
        sent_bytes = 0
        terminal_event: dict[str, Any] | None = None
        try:
            profile_home = _profile_home(row["profile"])
            # File is private, fixed under the configured executor data directory and removed.
            fd, temp_path = tempfile.mkstemp(prefix="prompt-", dir=self.store.path.parent)
            os.chmod(temp_path, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(prompt_bytes)
            args = [HERMES_PYTHON, str(EXECUTION_HELPER), "--profile-home", str(profile_home),
                    "--session-id", session, "--query-file", temp_path]
            kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                                      "stderr": subprocess.DEVNULL, "cwd": "/workspace", "close_fds": True}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(args, **kwargs)
            if not self.server.register_child(task, run, proc):
                _kill_process_group(proc)
                raise ApiError(503, "executor_shutting_down")
            deadline = time.monotonic() + MAX_CHILD_SECONDS
            selector = selectors.DefaultSelector()
            selector.register(proc.stdout, selectors.EVENT_READ)
            pending = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    _kill_process_group(proc)
                    raise ApiError(504, "execution_timeout")
                ready = selector.select(min(deadline - time.monotonic(), 1.0))
                if not ready:
                    if proc.poll() is not None:
                        break
                    continue
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                pending.extend(chunk)
                sent_bytes += len(chunk)
                if sent_bytes > MAX_OUTPUT or (len(pending) > MAX_OUTPUT and b"\n" not in pending):
                    _kill_process_group(proc)
                    raise ApiError(502, "execution_output_limit")
                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    pending = bytearray(rest)
                    if len(line) > MAX_OUTPUT:
                        _kill_process_group(proc)
                        raise ApiError(502, "execution_output_limit")
                    forwarded = self._forward_event(line + b"\n", session, task, run)
                    if forwarded and forwarded.get("type") == "executor_exit":
                        terminal_event = forwarded
            if pending:
                forwarded = self._forward_event(bytes(pending), session, task, run)
                if forwarded and forwarded.get("type") == "executor_exit":
                    terminal_event = forwarded
            code = proc.wait(timeout=5)
            if (terminal_event is None or terminal_event.get("settled") is not True
                    or type(terminal_event.get("code")) is not int):
                self.store.finish_submit(task, run, "ambiguous")
                raise ApiError(502, "execution_terminal_event_missing")
            if code != 0 or terminal_event["code"] != 0:
                state = "failed" if code == 0 and terminal_event["settled"] is True else "ambiguous"
                self.store.finish_submit(task, run, state)
                if code != 0:
                    raise ApiError(502, "execution_child_failed")
            else:
                current = self.store.get(task, run)
                if not current or not current["admission_id"]:
                    self.store.finish_submit(task, run, "ambiguous")
                    raise ApiError(502, "execution_admission_missing")
                self.store.finish_submit(task, run, "completed")
        except (BrokenPipeError, ConnectionResetError):
            if proc and proc.poll() is None:
                _kill_process_group(proc)
            self.store.finish_submit(task, run, "ambiguous")
        except Exception:
            if proc and proc.poll() is None:
                _kill_process_group(proc)
            self.store.finish_submit(task, run, "ambiguous")
            if self._response_started:
                self._send_stream_error()
            else:
                raise
        finally:
            self.server.unregister_submission(task, run)
            if selector is not None:
                selector.close()
            if proc and proc.stdout:
                try:
                    proc.stdout.close()
                except OSError:
                    pass
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def _forward_event(self, line: bytes, session: str, task: str, run: str) -> dict[str, Any] | None:
        try:
            event = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            raise ApiError(502, "execution_protocol_invalid") from None
        if not isinstance(event, dict):
            raise ApiError(502, "execution_protocol_invalid")
        kind = event.get("type")
        if getattr(self, "_terminal_seen", False):
            raise ApiError(502, "execution_protocol_invalid")
        if kind == "admission":
            admission = event.get("admission_id")
            if event.get("session_id") != session or not _valid_identity(admission, 256):
                raise ApiError(502, "admission_receipt_invalid")
            # Commit first; only then may the admission receipt reach the caller.
            self.store.save_admission(task, run, session, admission)
        elif kind == "execution":
            if event.get("session_id") != session or not _valid_identity(event.get("admission_id"), 256):
                raise ApiError(502, "execution_receipt_invalid")
            generation = event.get("execution_generation")
            if type(generation) is not int or generation < 0:
                raise ApiError(502, "execution_receipt_invalid")
            current = self.store.get(task, run)
            if not current or current["admission_id"] != event["admission_id"]:
                raise ApiError(409, "execution_admission_mismatch")
            self.store.set_generation(task, run, session, event["admission_id"], generation)
        elif kind == "text":
            if not isinstance(event.get("text"), str):
                raise ApiError(502, "execution_protocol_invalid")
        elif kind == "executor_exit":
            if (type(event.get("code")) is not int or event.get("settled") is not True):
                raise ApiError(502, "execution_terminal_event_invalid")
            event = {
                "type": "executor_exit", "code": event["code"], "settled": True,
                "gateway_turn_settled": event.get("settled") is True,
            }
        elif kind == "executor_heartbeat":
            if event.get("session_id") != session:
                raise ApiError(502, "execution_session_mismatch")
            event = {"type": "executor_heartbeat", "session_id": session}
        elif kind == "error_detail":
            event = {"type": "error", "error": SAFE_ERROR}
        else:
            raise ApiError(502, "execution_protocol_invalid")
        if kind == "executor_exit":
            self._terminal_seen = True
        self._start_event_stream()
        self.wfile.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")).encode() + b"\n")
        self.wfile.flush()
        return event

    def _cancel(self) -> None:
        body = self._body()
        required = {"task_id", "run_id", "session_id", "admission_id", "execution_generation"}
        if set(body) != required:
            raise ApiError(400, "invalid_cancel_fields")
        task, run, session, admission = (body[name] for name in ("task_id", "run_id", "session_id", "admission_id"))
        generation = body["execution_generation"]
        if not _valid_identity(task, 256) or not _valid_identity(run, 128):
            raise ApiError(400, "run_identity_invalid")
        if not _valid_identity(session, 256) or (
            admission is not None and not _valid_identity(admission, 256)
        ):
            raise ApiError(400, "cancel_identity_invalid")
        if generation is not None and (type(generation) is not int or generation < 0):
            raise ApiError(400, "execution_generation_invalid")
        row = self.store.get(task, run)
        if (not row or row["session_id"] != session
                or (admission is not None and row["admission_id"] != admission)
                or (generation is not None and row["execution_generation"] != generation)):
            raise ApiError(409, "cancel_identity_mismatch")
        admission = row["admission_id"]
        if not _valid_identity(admission, 256):
            raise ApiError(409, "cancel_admission_not_recorded")
        # If the worker has not consumed the execution receipt yet, use the
        # generation already persisted by this API. The exact admission ID
        # still prevents cancelling any other turn in the session.
        authoritative_generation = row["execution_generation"]
        home = _profile_home(row["profile"])
        args = [HERMES_PYTHON, str(CONTROL_HELPER), "interrupt", "--profile-home", str(home),
                "--session-id", session, "--admission-id", admission]
        if authoritative_generation is not None:
            args.extend(["--generation", str(authoritative_generation)])
        code, out, _ = _run_helper(args, timeout=30, output_limit=64 * 1024)
        try:
            result = json.loads(out)
        except (ValueError, UnicodeDecodeError):
            raise ApiError(502, "cancel_response_invalid") from None
        if not isinstance(result, dict) or type(result.get("accepted")) is not bool:
            raise ApiError(502, "cancel_response_invalid")
        self._send_json(200 if code == 0 and result["accepted"] else 409,
                        {"accepted": result["accepted"], "action": result.get("action", "none"),
                         "reason": result.get("reason", "cancel_rejected")})


def _reconcile_startup(store: Store) -> None:
    """Recover only by exact Gateway identities; never replay a prompt."""
    for row in store.ambiguous_creates():
        task, run = row["task_id"], row["run_id"]
        try:
            payload = json.loads(row["create_payload"])
            if (not isinstance(payload, dict)
                    or set(payload) != {"provider", "model", "reasoning", "tool_policy"}
                    or payload.get("tool_policy") not in TOOLSET_POLICIES
                    or not all(isinstance(payload.get(key), str) and payload[key]
                               for key in ("provider", "model", "reasoning"))
                    or not row["create_request_id"]):
                continue
            if not store.retry_create(task, run):
                continue
            args = [HERMES_PYTHON, str(SESSION_HELPER), "--profile-home", str(_profile_home(row["profile"])),
                    "--task-id", task, "--run-id", run, "--provider", payload["provider"],
                    "--model", payload["model"], "--reasoning", payload["reasoning"],
                    "--toolsets", ",".join(TOOLSET_POLICIES[payload["tool_policy"]]),
                    "--cwd", "/workspace", "--request-id", row["create_request_id"]]
            code, output, _ = _run_helper(args, timeout=60, output_limit=64 * 1024)
            result = json.loads(output)
            session = result.get("session_id") if isinstance(result, dict) else None
            generation = result.get("execution_generation") if isinstance(result, dict) else None
            if (code != 0 or not isinstance(result, dict) or result.get("ok") is not True
                    or not _valid_identity(session, 256)
                    or type(generation) is not int or generation < 0):
                store.fail_create(task, run)
                continue
            store.finish_create(task, run, session, generation)
        except Exception:
            try:
                store.fail_create(task, run)
            except Exception:
                pass

    for row in store.ambiguous_submits():
        session = row["session_id"]
        admission = row["admission_id"]
        generation = row["execution_generation"]
        # Admission is required for exact addressing. A missing generation can
        # still be recovered from a terminal receipt, but a running turn cannot
        # be interrupted without the stored generation.
        if (not _valid_identity(session, 256) or not _valid_identity(admission, 256)
                or (generation is not None and (type(generation) is not int or generation < 0))):
            continue
        try:
            args = [HERMES_PYTHON, str(CONTROL_HELPER), "reconcile", "--profile-home",
                    str(_profile_home(row["profile"])), "--session-id", session,
                    "--admission-id", admission]
            if generation is not None:
                args.extend(["--generation", str(generation)])
            code, output, _ = _run_helper(args, timeout=30, output_limit=64 * 1024)
            result = json.loads(output)
            recovered_generation = (
                result.get("execution_generation") if isinstance(result, dict) else None
            )
            if (code == 0 and isinstance(result, dict) and result.get("accepted") is True
                    and result.get("terminal") is True and result.get("session_id") == session
                    and type(recovered_generation) is int and recovered_generation >= 0
                    and (generation is None or recovered_generation == generation)):
                outcome = result.get("outcome")
                state = "cancelled" if outcome in {"cancelled", "interrupted"} else "reconciled_terminal"
                if generation is None:
                    store.set_generation(row["task_id"], row["run_id"], session, admission,
                                         recovered_generation)
                store.finish_submit(row["task_id"], row["run_id"], state)
            # Any uncertain Gateway response intentionally leaves this row ambiguous.
        except Exception:
            continue


def _reconciliation_loop(store: Store, wakeup: threading.Event) -> None:
    """Reconcile ambiguous records at startup and when a live request becomes uncertain."""
    while True:
        wakeup.clear()
        retry_after: float | None = 60
        try:
            _reconcile_startup(store)
            retryable_create = bool(store.ambiguous_creates())
            retryable_submit = any(
                _valid_identity(row["session_id"], 256)
                and _valid_identity(row["admission_id"], 256)
                for row in store.ambiguous_submits()
            )
            if not retryable_create and not retryable_submit:
                retry_after = None
        except Exception:
            # Keep the service available and try again after transient store or
            # Gateway startup errors. Unaddressable rows remain human-reviewable.
            pass
        wakeup.wait(retry_after)


def main() -> int:
    token = os.environ.get("KANZO_EXECUTOR_API_TOKEN", "")
    data_dir_value = os.environ.get("KANZO_EXECUTOR_DATA_DIR", "")
    if len(token) < 32 or not data_dir_value:
        print("executor_configuration_invalid", file=sys.stderr)
        return 2
    try:
        data_dir = Path(data_dir_value).resolve()
        reconciliation_wakeup = threading.Event()
        store = Store(data_dir, reconciliation_wakeup)
        Handler.store = store
        Handler.token = token.encode()
        host = os.environ.get("KANZO_EXECUTOR_API_HOST", "0.0.0.0")
        port = int(os.environ.get("KANZO_EXECUTOR_API_PORT", "8765"))
        max_threads = int(os.environ.get("KANZO_EXECUTOR_API_MAX_THREADS", "16"))
        if not 1 <= port <= 65535 or not 1 <= max_threads <= 64:
            raise ValueError("configuration")
        server = BoundedServer((host, port), Handler, max_threads=max_threads)
    except Exception:
        print("executor_configuration_invalid", file=sys.stderr)
        return 2
    threading.Thread(
        target=_reconciliation_loop, args=(store, reconciliation_wakeup),
        name="kanzo-run-reconciliation", daemon=True
    ).start()
    previous_handlers: dict[int, Any] = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, lambda _signum, _frame, current=server: current.request_shutdown())
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.stopping.set()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        server.server_close()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
