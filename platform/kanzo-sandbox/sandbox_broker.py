#!/usr/bin/env python3
"""Fail-closed Kanzo run registry and internal sandbox broker API.

This stdlib-only service implements durable run/session/lease state, bounded
single-use source transfer, broker-built per-run Kubernetes Pods, and a
lease-checked authenticated runner protocol for terminal execution and output
transfer. No caller-supplied Kubernetes objects or runner commands are accepted.

Required environment:
  KANZO_BROKER_WORKER_TOKEN  bearer token for run lifecycle/source preparation
  KANZO_BROKER_GATEWAY_TOKEN bearer token for Hermes terminal operations
Optional environment:
  KANZO_BROKER_DATA_DIR (default /var/lib/kanzo-broker)
  KANZO_BROKER_DB_PATH   (default DATA_DIR/broker.sqlite3)
  KANZO_BROKER_BIND      (default 0.0.0.0)
  KANZO_BROKER_PORT      (default 8080)
  KANZO_BROKER_INTERNAL_URL (default http://kanzo-sandbox-broker.mcp.svc:8080)

No caller-supplied Pod configuration is accepted. Each run receives a broker-
built Pod; its runner credential is kept out of child process environments,
and all runner calls are lease checked.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import base64
import http.client
import ssl
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any


MAX_JSON_BYTES = 1 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_UNPACKED_BYTES = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_ARGV_ITEMS = 256
MAX_ARGV_BYTES = 1 * 1024 * 1024
MAX_STDIN_BYTES = 8 * 1024 * 1024
MAX_EXEC_SECONDS = 3600
MAX_RUNNER_RESPONSE_BYTES = 2 * 1024 * 1024
RUN_TTL_SECONDS = 180
PREPARE_TTL_SECONDS = 15 * 60
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:#-]{0,255}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class BrokerError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


class KubernetesAPI:
    """Small in-cluster Kubernetes client; accepts only broker-built objects."""

    def __init__(self):
        self.base = os.environ.get("KUBERNETES_SERVICE_HOST", "")
        self.port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        if not self.base:
            raise RuntimeError("Kubernetes API service is unavailable")
        self.namespace = "kanzo-sandboxes"
        self.token_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
        self.ca_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        token = self.token_path.read_text(encoding="ascii").strip()
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8") if payload is not None else None
        context = ssl.create_default_context(cafile=str(self.ca_path))
        connection = http.client.HTTPSConnection(self.base, int(self.port), timeout=10, context=context)
        try:
            connection.request(method, path, body=body, headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
            response = connection.getresponse()
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise BrokerError(502, "kubernetes_response_too_large")
            try:
                value = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                value = {}
            if not isinstance(value, dict):
                value = {}
            return response.status, value
        finally:
            connection.close()

    def create_run_pod(self, *, pod_name: str, runner_token: str,
                       source_url: str, source_token: str, source_sha256: str,
                       read_only: bool) -> None:
        configmap = os.environ.get("KANZO_SANDBOX_CODE_CONFIGMAP", "kanzo-sandbox-code")
        image = os.environ.get("KANZO_SANDBOX_IMAGE", "")
        if not image or not re.fullmatch(r"[A-Za-z0-9./:_-]{1,256}", image):
            raise BrokerError(503, "sandbox_image_not_configured")
        code = {"name": "sandbox-code", "configMap": {"name": configmap, "defaultMode": 292}}
        security = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]},
                    "readOnlyRootFilesystem": True}
        source_mounts = [
            {"name": "sandbox-code", "mountPath": "/opt/kanzo", "readOnly": True},
            {"name": "run-workspace", "mountPath": "/workspace"},
            {"name": "tmp", "mountPath": "/tmp"},
        ]
        runner_mounts = [
            {"name": "sandbox-code", "mountPath": "/opt/kanzo", "readOnly": True},
            {"name": "run-workspace", "mountPath": "/workspace", "readOnly": read_only},
            {"name": "tmp", "mountPath": "/tmp"},
        ]
        volumes = [
            code,
            {"name": "run-workspace", "emptyDir": {"sizeLimit": "8Gi"}},
            {"name": "tmp", "emptyDir": {"sizeLimit": "1Gi"}},
            {"name": "scratch", "emptyDir": {"sizeLimit": "4Gi"}},
        ]
        resources_init = {"requests": {"cpu": "100m", "memory": "256Mi", "ephemeral-storage": "256Mi"},
                          "limits": {"cpu": "1", "memory": "2Gi", "ephemeral-storage": "8Gi"}}
        resources_runner = {"requests": {"cpu": "100m", "memory": "256Mi", "ephemeral-storage": "256Mi"},
                            "limits": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "8Gi"}}
        source_init = {
            "name": "source-init", "image": image, "imagePullPolicy": "Never",
            "command": ["python3", "/opt/kanzo/sandbox_init.py"],
            "env": [
                {"name": "KANZO_SOURCE_URL", "value": source_url},
                {"name": "KANZO_SOURCE_TOKEN", "value": source_token},
                {"name": "KANZO_SOURCE_SHA256", "value": source_sha256},
            ],
            "securityContext": security, "resources": resources_init,
            "volumeMounts": source_mounts,
        }
        runner = {
            "name": "runner", "image": image, "imagePullPolicy": "Never",
            "command": ["python3", "/opt/kanzo/sandbox_runner.py"],
            "ports": [{"name": "runner", "containerPort": 8000, "protocol": "TCP"}],
            "securityContext": security, "resources": resources_runner,
            "env": [{"name": "HOME", "value": "/scratch"}, {"name": "TMPDIR", "value": "/scratch"},
                    {"name": "KANZO_RUNNER_TOKEN", "value": runner_token}],
            "volumeMounts": runner_mounts + [{"name": "scratch", "mountPath": "/scratch"}],
        }
        pod = {
            "apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": pod_name, "namespace": self.namespace,
                         "labels": {"app.kubernetes.io/name": "kanzo-sandbox-runner",
                                    "app.kubernetes.io/part-of": "kanzo-sandbox"}},
            "spec": {
                "restartPolicy": "Never", "automountServiceAccountToken": False, "enableServiceLinks": False,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 10000, "runAsGroup": 10000, "fsGroup": 10000,
                                    "seccompProfile": {"type": "RuntimeDefault"}},
                "initContainers": [source_init], "containers": [runner], "volumes": volumes,
            },
        }
        status, _ = self.request("POST", f"/api/v1/namespaces/{self.namespace}/pods", pod)
        if status not in (200, 201):
            raise BrokerError(503, "sandbox_pod_create_failed")

    def pod(self, pod_name: str) -> dict[str, Any]:
        status, value = self.request("GET", f"/api/v1/namespaces/{self.namespace}/pods/{pod_name}")
        if status == 404:
            raise BrokerError(410, "sandbox_pod_missing")
        if status != 200:
            raise BrokerError(503, "sandbox_pod_lookup_failed")
        return value

    def delete_pod(self, pod_name: str) -> None:
        status, _ = self.request("DELETE", f"/api/v1/namespaces/{self.namespace}/pods/{pod_name}",
                                 {"apiVersion": "v1", "kind": "DeleteOptions", "gracePeriodSeconds": 2})
        if status not in (200, 202, 404):
            raise BrokerError(503, "sandbox_pod_delete_failed")

def _now() -> int:
    return int(time.time())


def _valid_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise BrokerError(400, f"invalid_{name}")
    return value


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_token_env(name: str) -> str:
    value = os.environ.get(name, "")
    if len(value) < 24:
        raise RuntimeError(f"{name} must be configured with at least 24 characters")
    return value


def _archive_validate(data: bytes) -> None:
    """Validate gzip tar structure without extracting or following links."""
    unpacked = 0
    count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member in archive:
                count += 1
                if count > MAX_ARCHIVE_MEMBERS:
                    raise BrokerError(413, "archive_member_limit")
                name = member.name
                clean_name = name[:-1] if member.isdir() and name.endswith("/") else name
                path = PurePosixPath(clean_name)
                if (not name or name.startswith("/") or "\x00" in name
                        or "\\" in name or any(part in ("", ".", "..") for part in clean_name.split("/"))
                        or path.is_absolute()):
                    raise BrokerError(400, "archive_unsafe_path")
                # Directories and regular files only. Links and special files
                # can escape the workspace or redirect later extraction writes.
                if not (member.isdir() or member.isreg()):
                    raise BrokerError(400, "archive_unsupported_member")
                if member.size < 0:
                    raise BrokerError(400, "archive_invalid_member_size")
                unpacked += member.size
                if unpacked > MAX_UNPACKED_BYTES:
                    raise BrokerError(413, "archive_unpacked_limit")
    except BrokerError:
        raise
    except (tarfile.TarError, OSError, EOFError, gzip.BadGzipFile):
        raise BrokerError(400, "invalid_gzip_tar") from None


class BrokerStore:
    def __init__(self, data_dir: Path, db_path: Path, lease_key: bytes):
        self.data_dir = data_dir
        self.archive_dir = data_dir / "archives"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.data_dir, 0o700)
            os.chmod(self.archive_dir, 0o700)
        except OSError:
            pass
        self.db_path = db_path
        self.lease_key = lease_key
        self.kube = KubernetesAPI()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @contextmanager
    def session(self):
        db = self.connect()
        try:
            yield db
        finally:
            db.close()

    def _init_db(self) -> None:
        with self.session() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    archive_sha256 TEXT NOT NULL,
                    source_git_sha TEXT NOT NULL,
                    read_only INTEGER NOT NULL CHECK(read_only IN (0,1)),
                    lease_hash TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('prepared','bound','closing','closed')),
                    archive_name TEXT NOT NULL,
                    source_token_hash TEXT NOT NULL,
                    lease_nonce TEXT NOT NULL,
                    source_consumed INTEGER NOT NULL DEFAULT 0 CHECK(source_consumed IN (0,1)),
                    hermes_session_id TEXT UNIQUE,
                    profile TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    closed_at INTEGER,
                    PRIMARY KEY(task_id, run_id)
                );
                CREATE TABLE IF NOT EXISTS environments (
                    handle TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    hermes_session_id TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    created_at INTEGER NOT NULL,
                    closed_at INTEGER,
                    FOREIGN KEY(task_id, run_id) REFERENCES runs(task_id, run_id)
                );
                CREATE TABLE IF NOT EXISTS sandbox_pods (
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    pod_name TEXT NOT NULL UNIQUE,
                    pod_ip TEXT,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY(task_id,run_id),
                    FOREIGN KEY(task_id,run_id) REFERENCES runs(task_id,run_id)
                );
                CREATE INDEX IF NOT EXISTS runs_expiry_idx ON runs(state, expires_at);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(runs)")}
            if "archive_sha256" not in columns:
                if "source_sha256" in columns:
                    db.execute("ALTER TABLE runs RENAME COLUMN source_sha256 TO archive_sha256")
                else:
                    db.execute("ALTER TABLE runs ADD COLUMN archive_sha256 TEXT NOT NULL DEFAULT ''")
            if "source_git_sha" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN source_git_sha TEXT NOT NULL DEFAULT ''")
            if "lease_nonce" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN lease_nonce TEXT NOT NULL DEFAULT ''")

    def prepare(self, task_id: str, run_id: str, archive_sha: str, source_git_sha: str, read_only: bool,
                archive: bytes) -> dict[str, Any]:
        _archive_validate(archive)
        archive_name = secrets.token_hex(24) + ".tar.gz"
        target = self.archive_dir / archive_name
        fd, tmp_name = tempfile.mkstemp(prefix=".incoming-", dir=self.archive_dir)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(archive)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_name, target)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

        lease_nonce = secrets.token_urlsafe(24)
        lease = self._derive_lease(task_id, run_id, lease_nonce)
        source_token = secrets.token_urlsafe(32)
        now = _now()
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT * FROM runs WHERE task_id=? AND run_id=?", (task_id, run_id)
            ).fetchone()
            if current and current["state"] not in ("prepared", "closing", "closed"):
                raise BrokerError(409, "run_already_bound")
            if current and current["state"] == "prepared":
                old_archive = current["archive_name"]
                db.execute("""UPDATE runs SET archive_sha256=?, source_git_sha=?, read_only=?, lease_hash=?,
                    archive_name=?, source_token_hash=?, lease_nonce=?, source_consumed=0, updated_at=?,
                    expires_at=? WHERE task_id=? AND run_id=?""",
                    (archive_sha, source_git_sha, int(read_only), _digest(lease), archive_name,
                     _digest(source_token), lease_nonce,
                     now, now + PREPARE_TTL_SECONDS, task_id, run_id))
            elif current:
                raise BrokerError(409, "run_closed")
            else:
                old_archive = None
                db.execute("""INSERT INTO runs(task_id,run_id,archive_sha256,source_git_sha,read_only,
                    lease_hash,state,archive_name,source_token_hash,lease_nonce,created_at,updated_at,expires_at)
                    VALUES(?,?,?,?,?,?,'prepared',?,?,?,?,?,?)""",
                    (task_id, run_id, archive_sha, source_git_sha, int(read_only), _digest(lease), archive_name,
                     _digest(source_token), lease_nonce, now, now, now + PREPARE_TTL_SECONDS))
            db.execute("COMMIT")
            if old_archive:
                self._unlink_archive(old_archive)
        except Exception:
            try:
                db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            self._unlink_archive(archive_name)
            raise
        finally:
            db.close()
        internal = os.environ.get(
            "KANZO_BROKER_INTERNAL_URL",
            "http://kanzo-sandbox-broker.mcp.svc:8080",
        ).rstrip("/")
        pod_name = "kanzo-run-" + hashlib.sha256(f"{task_id}:{run_id}".encode()).hexdigest()[:20]
        runner_token = self._derive_runner_token(task_id, run_id, lease_nonce)
        with self.session() as db:
            db.execute("INSERT OR REPLACE INTO sandbox_pods(task_id,run_id,pod_name,created_at) "
                       "VALUES(?,?,?,?)", (task_id, run_id, pod_name, now))
        try:
            self.kube.create_run_pod(
                pod_name=pod_name, runner_token=runner_token,
                source_url=f"{internal}/v1/runs/source/{source_token}", source_token=source_token,
                source_sha256=archive_sha, read_only=read_only,
            )
        except Exception:
            self.finalize(task_id, run_id, lease)
            raise
        return {
            "task_id": task_id,
            "run_id": run_id,
            "lease": lease,
            "source_url": f"{internal}/v1/runs/source/{source_token}",
            "archive_sha256": archive_sha,
            "source_git_sha": source_git_sha,
            "expires_at": now + RUN_TTL_SECONDS,
        }

    def bind(self, task_id: str, run_id: str, lease: str,
             session_id: str, profile: str) -> dict[str, Any]:
        now = _now()
        with self.session() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._authorized_run(db, task_id, run_id, lease)
            if run["state"] == "bound" and run["hermes_session_id"] == session_id:
                if run["profile"] != profile:
                    raise BrokerError(409, "session_profile_mismatch")
                db.execute("COMMIT")
                return {"state": "bound", "hermes_session_id": session_id}
            if run["state"] != "prepared":
                raise BrokerError(409, "run_not_bindable")
            if run["expires_at"] <= now:
                raise BrokerError(410, "run_expired")
            try:
                db.execute("UPDATE runs SET hermes_session_id=?, profile=?, state='bound', updated_at=?, expires_at=? "
                           "WHERE task_id=? AND run_id=? AND state='prepared'",
                           (session_id, profile, now, now + RUN_TTL_SECONDS, task_id, run_id))
            except sqlite3.IntegrityError:
                raise BrokerError(409, "session_already_bound") from None
            db.execute("COMMIT")
        return {"state": "bound", "hermes_session_id": session_id}

    def resolve(self, session_id: str) -> dict[str, Any]:
        with self.session() as db:
            rows = db.execute("""SELECT * FROM runs WHERE hermes_session_id=?
                AND state='bound' AND expires_at>?""", (session_id, _now())).fetchall()
            if len(rows) != 1:
                raise BrokerError(404, "session_mapping_not_found_or_ambiguous")
            run = rows[0]
            lease = self._derive_lease(run["task_id"], run["run_id"], run["lease_nonce"])
            if not hmac.compare_digest(run["lease_hash"], _digest(lease)):
                raise BrokerError(503, "run_lease_integrity_error")
            pod_row = db.execute("SELECT pod_name FROM sandbox_pods WHERE task_id=? AND run_id=?",
                                 (run["task_id"], run["run_id"])).fetchone()
            if not pod_row:
                raise BrokerError(503, "sandbox_pod_not_registered")
            pod = self.kube.pod(pod_row["pod_name"])
            phase = pod.get("status", {}).get("phase")
            if phase != "Running" or not pod.get("status", {}).get("podIP"):
                if phase in ("Failed", "Succeeded"):
                    raise BrokerError(503, "sandbox_pod_initialization_failed")
                raise BrokerError(503, "sandbox_pod_not_ready")
            handle = secrets.token_urlsafe(32)
            db.execute("INSERT INTO environments(handle,task_id,run_id,hermes_session_id,created_at) "
                       "VALUES(?,?,?,?,?)", (handle, run["task_id"], run["run_id"], session_id, _now()))
            return {
                "hermes_session_id": session_id,
                "task_id": run["task_id"],
                "run_id": run["run_id"],
                "lease": lease,
                "environment_handle": handle,
                "cwd": "/workspace",
                # Hermes execute_code uses the broker-provided temp_dir as its
                # remote working directory. The runner only accepts cwd under
                # /workspace or /scratch; /scratch is writable even when the
                # workspace is mounted read-only.
                "temp_dir": "/scratch",
            }

    def runner_address(self, payload: dict[str, Any], *, require_handle: bool = True) -> tuple[str, str]:
        run = self.authorize(payload, require_handle=require_handle)
        with self.session() as db:
            row = db.execute("SELECT pod_name FROM sandbox_pods WHERE task_id=? AND run_id=?",
                             (run["task_id"], run["run_id"])).fetchone()
        if not row:
            raise BrokerError(503, "sandbox_pod_not_registered")
        pod = self.kube.pod(row["pod_name"])
        if pod.get("status", {}).get("phase") != "Running":
            raise BrokerError(503, "sandbox_pod_not_ready")
        ip = pod.get("status", {}).get("podIP")
        if not isinstance(ip, str) or not re.fullmatch(r"[0-9a-fA-F:.]{2,64}", ip):
            raise BrokerError(503, "sandbox_pod_address_invalid")
        token = self._derive_runner_token(run["task_id"], run["run_id"], run["lease_nonce"])
        return ip, token

    def authorize_session(self, session_id: str) -> dict[str, Any]:
        """Resolve a live Gateway session to its bound Kanzo run without creating a handle."""
        with self.session() as db:
            rows = db.execute(
                "SELECT * FROM runs WHERE hermes_session_id=? AND state='bound' AND expires_at>?",
                (session_id, _now()),
            ).fetchall()
            if len(rows) != 1:
                raise BrokerError(404, "session_mapping_not_found_or_ambiguous")
            run = rows[0]
            lease = self._derive_lease(run["task_id"], run["run_id"], run["lease_nonce"])
            if not hmac.compare_digest(run["lease_hash"], _digest(lease)):
                raise BrokerError(503, "run_lease_integrity_error")
            return {
                "hermes_session_id": session_id,
                "task_id": run["task_id"],
                "run_id": run["run_id"],
                "workspace": "/workspace",
                "temp_dir": "/scratch",
            }

    def run_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = _valid_id(payload.get("task_id"), "task_id")
        run_id = _valid_id(payload.get("run_id"), "run_id")
        lease = payload.get("lease")
        if not isinstance(lease, str) or not 32 <= len(lease) <= 256:
            raise BrokerError(401, "invalid_lease")
        with self.session() as db:
            run = self._authorized_run(db, task_id, run_id, lease)
            if run["state"] not in ("prepared", "bound"):
                raise BrokerError(409, "run_not_active")
            if run["expires_at"] <= _now():
                raise BrokerError(410, "run_expired")
            row = db.execute("SELECT pod_name FROM sandbox_pods WHERE task_id=? AND run_id=?",
                             (task_id, run_id)).fetchone()
        if not row:
            raise BrokerError(503, "sandbox_pod_not_registered")
        pod = self.kube.pod(row["pod_name"])
        status = pod.get("status", {})
        phase = status.get("phase", "Unknown")
        ready = any(item.get("type") == "Ready" and item.get("status") == "True"
                    for item in status.get("conditions", []) if isinstance(item, dict))
        init_states = status.get("initContainerStatuses", [])
        init_failed = any(item.get("state", {}).get("terminated", {}).get("exitCode", 0) != 0
                          for item in init_states if isinstance(item, dict))
        if ready and phase == "Running":
            state = "ready"
        elif phase in ("Failed", "Succeeded") or init_failed:
            state = "failed"
        else:
            state = "starting"
        return {"state": state, "phase": phase}

    def heartbeat(self, task_id: str, run_id: str, lease: str) -> dict[str, Any]:
        """Extend only a live bound lease; dead workers expire quickly through the reaper."""
        now = _now()
        with self.session() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._authorized_run(db, task_id, run_id, lease)
            if run["state"] != "bound":
                raise BrokerError(409, "run_not_active")
            if run["expires_at"] <= now:
                raise BrokerError(410, "run_expired")
            expiry = now + RUN_TTL_SECONDS
            db.execute("UPDATE runs SET updated_at=?,expires_at=? WHERE task_id=? AND run_id=? AND state='bound'",
                       (now, expiry, task_id, run_id))
            db.execute("COMMIT")
        return {"state": "bound", "expires_at": expiry}

    def _derive_runner_token(self, task_id: str, run_id: str, nonce: str) -> str:
        message = f"kanzo-runner-token-v1\0{task_id}\0{run_id}\0{nonce}".encode("utf-8")
        return base64.urlsafe_b64encode(hmac.new(self.lease_key, message, hashlib.sha256).digest()).rstrip(b"=").decode("ascii")

    def cleanup_pod(self, task_id: str, run_id: str) -> None:
        with self.session() as db:
            row = db.execute("SELECT pod_name FROM sandbox_pods WHERE task_id=? AND run_id=?",
                             (task_id, run_id)).fetchone()
        if row:
            self.kube.delete_pod(row["pod_name"])
            with self.session() as db:
                db.execute("DELETE FROM sandbox_pods WHERE task_id=? AND run_id=?", (task_id, run_id))

    def authorize(self, payload: dict[str, Any], *, require_handle: bool = True) -> sqlite3.Row:
        task_id = _valid_id(payload.get("task_id"), "task_id")
        run_id = _valid_id(payload.get("run_id"), "run_id")
        session_id = _valid_id(payload.get("hermes_session_id"), "hermes_session_id")
        lease = payload.get("lease")
        if not isinstance(lease, str) or not 32 <= len(lease) <= 256:
            raise BrokerError(401, "invalid_lease")
        with self.session() as db:
            run = self._authorized_run(db, task_id, run_id, lease)
            if run["state"] != "bound" or run["hermes_session_id"] != session_id:
                raise BrokerError(409, "session_run_mismatch")
            if run["expires_at"] <= _now():
                raise BrokerError(410, "run_expired")
            if require_handle:
                handle = payload.get("environment_handle")
                if not isinstance(handle, str) or not handle:
                    raise BrokerError(400, "environment_handle_required")
                env = db.execute("""SELECT 1 FROM environments WHERE handle=? AND task_id=?
                    AND run_id=? AND hermes_session_id=? AND active=1""",
                    (handle, task_id, run_id, session_id)).fetchone()
                if not env:
                    raise BrokerError(409, "environment_handle_invalid")
            return run

    @staticmethod
    def _authorized_run(db: sqlite3.Connection, task_id: str, run_id: str,
                        lease: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM runs WHERE task_id=? AND run_id=?",
                         (task_id, run_id)).fetchone()
        if not row or not hmac.compare_digest(row["lease_hash"], _digest(lease)):
            raise BrokerError(401, "run_lease_invalid")
        return row

    def _derive_lease(self, task_id: str, run_id: str, nonce: str) -> str:
        message = f"kanzo-run-lease-v1\0{task_id}\0{run_id}\0{nonce}".encode("utf-8")
        raw = hmac.new(self.lease_key, message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    def cleanup_environment(self, payload: dict[str, Any]) -> None:
        self.authorize(payload, require_handle=False)
        handle = payload.get("environment_handle")
        if not isinstance(handle, str) or not handle:
            raise BrokerError(400, "environment_handle_required")
        with self.session() as db:
            row = db.execute("""SELECT 1 FROM environments WHERE handle=? AND task_id=?
                AND run_id=? AND hermes_session_id=?""",
                (handle, payload["task_id"], payload["run_id"],
                 payload["hermes_session_id"])).fetchone()
            if not row:
                raise BrokerError(409, "environment_handle_invalid")
            db.execute("UPDATE environments SET active=0,closed_at=COALESCE(closed_at,?) "
                       "WHERE handle=? AND active=1", (_now(), handle))

    def finalize(self, task_id: str, run_id: str, lease: str) -> dict[str, Any]:
        now = _now()
        with self.session() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._authorized_run(db, task_id, run_id, lease)
            if run["state"] == "closed":
                db.execute("COMMIT")
                self.cleanup_pod(task_id, run_id)
                return {"state": "closed", "idempotent": True}
            if run["state"] not in ("prepared", "bound", "closing"):
                raise BrokerError(409, "run_not_finalizable")
            db.execute("UPDATE runs SET state='closing',updated_at=? WHERE task_id=? AND run_id=?",
                       (now, task_id, run_id))
            db.execute("UPDATE environments SET active=0,closed_at=? WHERE task_id=? AND run_id=?",
                       (now, task_id, run_id))
            db.execute("UPDATE runs SET state='closed',closed_at=?,updated_at=? WHERE task_id=? AND run_id=?",
                       (now, now, task_id, run_id))
            db.execute("COMMIT")
        self._unlink_archive(run["archive_name"])
        self.cleanup_pod(task_id, run_id)
        return {"state": "closed", "idempotent": False}

    def validate_run_lease(self, task_id: str, run_id: str, lease: str) -> None:
        with self.session() as db:
            run = self._authorized_run(db, task_id, run_id, lease)
            if run["state"] == "closed":
                raise BrokerError(409, "run_already_closed")
            if run["expires_at"] <= _now():
                raise BrokerError(410, "run_expired")

    def consume_source(self, source_token: str) -> Path:
        token_hash = _digest(source_token)
        with self.session() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT archive_name FROM runs WHERE source_token_hash=?
                AND source_consumed=0 AND state IN ('prepared','bound') AND expires_at>?""",
                (token_hash, _now())).fetchone()
            if not row:
                raise BrokerError(404, "source_token_invalid_or_consumed")
            updated = db.execute("UPDATE runs SET source_consumed=1,updated_at=? "
                                 "WHERE source_token_hash=? AND source_consumed=0",
                                 (_now(), token_hash)).rowcount
            if updated != 1:
                raise BrokerError(404, "source_token_invalid_or_consumed")
            db.execute("COMMIT")
        path = self.archive_dir / row["archive_name"]
        if not path.is_file():
            raise BrokerError(410, "source_archive_unavailable")
        return path

    def reap(self) -> None:
        now = _now()
        with self.session() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT task_id,run_id,archive_name FROM runs "
                              "WHERE state!='closed' AND expires_at<=?", (now,)).fetchall()
            for row in rows:
                db.execute("UPDATE environments SET active=0,closed_at=? WHERE task_id=? AND run_id=?",
                           (now, row["task_id"], row["run_id"]))
                db.execute("UPDATE runs SET state='closed',closed_at=?,updated_at=? WHERE task_id=? AND run_id=?",
                           (now, now, row["task_id"], row["run_id"]))
            db.execute("COMMIT")
            cleanup_rows = db.execute("""SELECT p.task_id,p.run_id FROM sandbox_pods p
                JOIN runs r ON r.task_id=p.task_id AND r.run_id=p.run_id
                WHERE r.state='closed' OR r.expires_at<=?""", (now,)).fetchall()
        for row in rows:
            self._unlink_archive(row["archive_name"])
        for row in cleanup_rows:
            self.cleanup_pod(row["task_id"], row["run_id"])

    def _unlink_archive(self, name: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{48}\.tar\.gz", name):
            return
        try:
            (self.archive_dir / name).unlink(missing_ok=True)
        except OSError:
            pass


class BrokerHandler(BaseHTTPRequestHandler):
    server_version = "KanzoBroker/0.1"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        # Default HTTP logs include request paths; source URLs contain one-use
        # bearer-like tokens. Do not log paths, headers, or bodies.
        return

    @property
    def store(self) -> BrokerStore:
        return self.server.store  # type: ignore[attr-defined]

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, error: BrokerError) -> None:
        self._json(error.status, {"error": error.code})

    def _auth(self, expected: str) -> None:
        header = self.headers.get("Authorization", "")
        scheme, _, supplied = header.partition(" ")
        if scheme.lower() != "bearer" or not supplied or not hmac.compare_digest(supplied, expected):
            raise BrokerError(401, "unauthorized")

    def _body(self) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise BrokerError(400, "transfer_encoding_unsupported")
        raw_length = self.headers.get("Content-Length")
        if not raw_length or not raw_length.isdigit():
            raise BrokerError(411, "content_length_required")
        length = int(raw_length)
        if length > MAX_JSON_BYTES:
            raise BrokerError(413, "request_too_large")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise BrokerError(400, "incomplete_request")
        return raw

    def _json_body(self) -> dict[str, Any]:
        try:
            obj = json.loads(self._body().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BrokerError(400, "invalid_json") from None
        if not isinstance(obj, dict):
            raise BrokerError(400, "json_object_required")
        return obj

    def _worker_auth(self) -> None:
        self._auth(self.server.worker_token)  # type: ignore[attr-defined]

    def _gateway_auth(self) -> None:
        self._auth(self.server.gateway_token)  # type: ignore[attr-defined]

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/v1/runs/prepare":
                self._worker_auth()
                self._prepare()
            elif path == "/v1/runs/bind-session":
                self._worker_auth()
                self._bind()
            elif path == "/v1/runs/finalize":
                self._worker_auth()
                self._finalize()
            elif path == "/v1/runs/changes":
                self._worker_auth()
                self._changes()
            elif path == "/v1/runs/status":
                self._worker_auth()
                self._json(200, self.store.run_status(self._json_body()))
            elif path == "/v1/runs/heartbeat":
                self._worker_auth()
                p = self._json_body()
                task = _valid_id(p.get("task_id"), "task_id")
                run = _valid_id(p.get("run_id"), "run_id")
                lease = p.get("lease")
                if not isinstance(lease, str) or not 32 <= len(lease) <= 256:
                    raise BrokerError(400, "invalid_lease")
                self._json(200, self.store.heartbeat(task, run, lease))
            elif path == "/v1/terminal/resolve":
                self._gateway_auth()
                payload = self._json_body()
                sid = _valid_id(payload.get("hermes_session_id"), "hermes_session_id")
                self._json(200, self.store.resolve(sid))
            elif path == "/v1/terminal/authorize":
                self._gateway_auth()
                payload = self._json_body()
                sid = _valid_id(payload.get("hermes_session_id"), "hermes_session_id")
                self._json(200, self.store.authorize_session(sid))
            elif path in (
                "/v1/terminal/exec/start", "/v1/terminal/exec/poll",
                "/v1/terminal/exec/cancel", "/v1/terminal/environment/cleanup",
            ):
                self._gateway_auth()
                payload = self._json_body()
                self.store.authorize(payload)
                if path.endswith("/cleanup"):
                    self.store.cleanup_environment(payload)
                    self._json(200, {"state": "closed"})
                else:
                    self._validate_terminal_request(path, payload)
                    runner_path = path.rsplit("/", 1)[-1]
                    self._proxy_runner(payload, f"/exec/{runner_path}")
            else:
                raise BrokerError(404, "not_found")
        except BrokerError as exc:
            self._error(exc)
        except (sqlite3.Error, OSError):
            self._json(503, {"error": "broker_storage_unavailable"})
        except Exception:
            self._json(500, {"error": "internal_error"})

    def _prepare(self) -> None:
        if self.headers.get("Transfer-Encoding"):
            raise BrokerError(400, "transfer_encoding_unsupported")
        task_id = _valid_id(self.headers.get("X-Kanzo-Task-Id"), "task_id")
        run_id = _valid_id(self.headers.get("X-Kanzo-Run-Id"), "run_id")
        archive_sha = self.headers.get("X-Archive-SHA256", "")
        if not SHA256_RE.fullmatch(archive_sha):
            raise BrokerError(400, "invalid_archive_sha256")
        source_git_sha = self.headers.get("X-Source-Git-SHA", "")
        if not GIT_SHA_RE.fullmatch(source_git_sha):
            raise BrokerError(400, "invalid_source_git_sha")
        read_only_header = self.headers.get("X-Read-Only", "").lower()
        if read_only_header not in ("true", "false"):
            raise BrokerError(400, "invalid_read_only")
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or int(length) > MAX_ARCHIVE_BYTES:
            raise BrokerError(413, "archive_size_limit")
        archive = self.rfile.read(int(length))
        if len(archive) != int(length):
            raise BrokerError(400, "incomplete_archive")
        actual = hashlib.sha256(archive).hexdigest()
        if not hmac.compare_digest(actual, archive_sha):
            raise BrokerError(400, "archive_sha256_mismatch")
        result = self.store.prepare(task_id, run_id, archive_sha, source_git_sha,
                                    read_only_header == "true", archive)
        self._json(201, result)

    def _bind(self) -> None:
        p = self._json_body()
        task = _valid_id(p.get("task_id"), "task_id")
        run = _valid_id(p.get("run_id"), "run_id")
        session = _valid_id(p.get("hermes_session_id"), "hermes_session_id")
        profile = _valid_id(p.get("profile"), "profile")
        lease = p.get("lease")
        if not isinstance(lease, str) or not 32 <= len(lease) <= 256:
            raise BrokerError(400, "invalid_lease")
        self._json(200, self.store.bind(task, run, lease, session, profile))

    def _finalize(self) -> None:
        p = self._json_body()
        task = _valid_id(p.get("task_id"), "task_id")
        run = _valid_id(p.get("run_id"), "run_id")
        lease = p.get("lease")
        if not isinstance(lease, str) or not 32 <= len(lease) <= 256:
            raise BrokerError(400, "invalid_lease")
        self._json(200, self.store.finalize(task, run, lease))

    def _proxy_runner(self, payload: dict[str, Any], runner_path: str) -> None:
        ip, token = self.store.runner_address(payload)
        runner_payload = dict(payload)
        if runner_path == "/exec/start":
            runner_payload["exec_id"] = secrets.token_urlsafe(24)
        body = json.dumps(runner_payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"http://{ip}:8000{runner_path}", data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                raw = response.read(MAX_RUNNER_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RUNNER_RESPONSE_BYTES:
                    raise BrokerError(502, "runner_response_too_large")
                result = json.loads(raw.decode("utf-8"))
                if not isinstance(result, dict):
                    raise BrokerError(502, "runner_response_invalid")
                self._json(response.status, result)
        except urllib.error.HTTPError as exc:
            self._json(exc.code if exc.code in (400, 404, 409, 410, 413, 429) else 502,
                       {"error": "sandbox_runner_rejected_request"})
        except BrokerError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise BrokerError(503, "sandbox_runner_unavailable") from None

    def _changes(self) -> None:
        payload = self._json_body()
        ip, token = self.store.runner_address(payload, require_handle=False)
        request = urllib.request.Request(
            f"http://{ip}:8000/changes",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/gzip"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read(MAX_ARCHIVE_BYTES + 1)
                if len(data) > MAX_ARCHIVE_BYTES:
                    raise BrokerError(413, "output_archive_size_limit")
                self.send_response(response.status)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Archive-SHA256", hashlib.sha256(data).hexdigest())
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as exc:
            raise BrokerError(502 if exc.code >= 500 else exc.code, "sandbox_change_export_failed") from None
        except BrokerError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            raise BrokerError(503, "sandbox_runner_unavailable") from None

    @staticmethod
    def _validate_terminal_request(path: str, payload: dict[str, Any]) -> None:
        if path.endswith("/exec/start"):
            argv = payload.get("argv")
            if (not isinstance(argv, list) or not argv or len(argv) > MAX_ARGV_ITEMS
                    or any(not isinstance(item, str) or "\x00" in item for item in argv)):
                raise BrokerError(400, "invalid_argv")
            if sum(len(item.encode("utf-8")) for item in argv) > MAX_ARGV_BYTES:
                raise BrokerError(413, "argv_size_limit")
            stdin = payload.get("stdin")
            if stdin is not None and not isinstance(stdin, str):
                raise BrokerError(400, "invalid_stdin")
            if stdin is not None and len(stdin.encode("utf-8")) > MAX_STDIN_BYTES:
                raise BrokerError(413, "stdin_size_limit")
            cwd = payload.get("cwd")
            if (not isinstance(cwd, str) or not cwd.startswith("/") or "\x00" in cwd
                    or "\\" in cwd or any(part == ".." for part in cwd.split("/"))):
                raise BrokerError(400, "invalid_cwd")
            timeout = payload.get("timeout_seconds")
            if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_EXEC_SECONDS:
                raise BrokerError(400, "invalid_timeout")
        else:
            exec_id = payload.get("exec_id")
            if not isinstance(exec_id, str) or not exec_id or len(exec_id) > 4096:
                raise BrokerError(400, "invalid_exec_id")
            if path.endswith("/exec/poll"):
                cursor = payload.get("cursor")
                if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
                    raise BrokerError(400, "invalid_cursor")

    def do_GET(self) -> None:  # noqa: N802
        try:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(200, {"status": "ok", "execution": "per-run-pod"})
                return
            prefix = "/v1/runs/source/"
            if path.startswith(prefix):
                token = path[len(prefix):]
                if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
                    raise BrokerError(404, "source_token_invalid_or_consumed")
                file_path = self.store.consume_source(token)
                size = file_path.stat().st_size
                self.send_response(200)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                with file_path.open("rb") as stream:
                    shutil.copyfileobj(stream, self.wfile, length=1024 * 1024)
                return
            raise BrokerError(404, "not_found")
        except BrokerError as exc:
            self._error(exc)
        except (sqlite3.Error, OSError):
            self._json(503, {"error": "broker_storage_unavailable"})
        except Exception:
            self._json(500, {"error": "internal_error"})

    def do_PUT(self) -> None:  # noqa: N802
        self._json(405, {"error": "method_not_allowed"})

    def do_DELETE(self) -> None:  # noqa: N802
        self._json(405, {"error": "method_not_allowed"})


class BrokerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], store: BrokerStore,
                 worker_token: str, gateway_token: str):
        super().__init__(address, BrokerHandler)
        self.store = store
        self.worker_token = worker_token
        self.gateway_token = gateway_token


def _reaper(store: BrokerStore, stop: threading.Event) -> None:
    while not stop.wait(30):
        try:
            store.reap()
        except (sqlite3.Error, OSError):
            # Never print database contents, tokens, or task data.
            continue


def main() -> None:
    worker_token = _read_token_env("KANZO_BROKER_WORKER_TOKEN")
    gateway_token = _read_token_env("KANZO_BROKER_GATEWAY_TOKEN")
    if hmac.compare_digest(worker_token, gateway_token):
        raise RuntimeError("worker and gateway tokens must be distinct")
    lease_key_text = _read_token_env("KANZO_BROKER_LEASE_KEY")
    lease_key = lease_key_text.encode("utf-8")
    data_dir = Path(os.environ.get("KANZO_BROKER_DATA_DIR", "/var/lib/kanzo-broker")).resolve()
    db_path = Path(os.environ.get("KANZO_BROKER_DB_PATH", str(data_dir / "broker.sqlite3"))).resolve()
    bind = os.environ.get("KANZO_BROKER_BIND", "0.0.0.0")
    port = int(os.environ.get("KANZO_BROKER_PORT", "8080"))
    if not (1 <= port <= 65535):
        raise RuntimeError("KANZO_BROKER_PORT must be between 1 and 65535")
    store = BrokerStore(data_dir, db_path, lease_key)
    stop = threading.Event()
    threading.Thread(target=_reaper, args=(store, stop), daemon=True, name="run-reaper").start()
    server = BrokerServer((bind, port), store, worker_token, gateway_token)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
