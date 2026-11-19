"""Small authenticated client for one run-scoped Kanzo sandbox lease."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import gzip
import io
import tarfile
from pathlib import Path
from typing import Any, Iterable

from workspace_transfer import apply_changed_archive, build_manifest_archive, build_source_archive


MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


class BrokerClientError(RuntimeError):
    pass


class BrokerClient:
    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = (base_url or os.environ.get("KANZO_BROKER_URL", "")).rstrip("/")
        self.token = token or os.environ.get("KANZO_BROKER_WORKER_TOKEN", "")
        parsed = urllib.parse.urlsplit(self.base_url)
        internal_service = bool(
            parsed.hostname
            and (parsed.hostname.endswith(".svc") or parsed.hostname.endswith(".svc.cluster.local"))
        )
        tailnet_service = bool(
            os.environ.get("KANZO_BROKER_ALLOW_TAILNET", "").lower() == "true"
            and parsed.scheme == "https"
            and parsed.hostname
            and parsed.hostname.endswith(".ts.net")
        )
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not (internal_service or tailnet_service)
        ):
            raise BrokerClientError("Kanzo broker URL must be an internal service or an explicitly enabled HTTPS tailnet endpoint")
        if len(self.token) < 24:
            raise BrokerClientError("Kanzo broker worker credential is unavailable")
        self._opener = urllib.request.build_opener(_NoRedirect())

    def prepare(self, task_id: str, run_id: str, checkout: Path, source_sha: str, read_only: bool,
                *, additional_sources: Iterable[tuple[str, Path, str]] = ()) -> dict[str, Any]:
        source_list = list(additional_sources)
        if source_list:
            archive, digest = build_manifest_archive(checkout, source_sha, source_list)
        else:
            archive, digest = build_source_archive(checkout, source_sha)
        return self._prepare_archive(task_id, run_id, source_sha, read_only, archive, digest)

    def prepare_empty(self, task_id: str, run_id: str, read_only: bool) -> dict[str, Any]:
        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0, compresslevel=6) as compressed:
            with tarfile.open(fileobj=compressed, mode="w"):
                pass
        archive = buffer.getvalue()
        source_id = hashlib.sha1(b"kanzo-empty-source-v1").hexdigest()
        digest = hashlib.sha256(archive).hexdigest()
        return self._prepare_archive(task_id, run_id, source_id, read_only, archive, digest)

    def heartbeat_runner(self, runner_id: str, runner_os: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", runner_id):
            raise BrokerClientError("runner id is invalid")
        if runner_os not in {"linux", "wsl"}:
            raise BrokerClientError("runner OS is invalid")
        return self._post_json(
            "/v1/runners/heartbeat",
            {"runner_id": runner_id, "runner_os": runner_os},
            timeout=10,
        )

    def _prepare_archive(self, task_id: str, run_id: str, source_sha: str, read_only: bool,
                         archive: bytes, digest: str) -> dict[str, Any]:
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise BrokerClientError("source archive exceeds the configured size limit")
        request = urllib.request.Request(
            self.base_url + "/v1/runs/prepare",
            data=archive,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/gzip",
                "X-Kanzo-Task-Id": task_id,
                "X-Kanzo-Run-Id": run_id,
                "X-Archive-SHA256": digest,
                "X-Source-Git-SHA": source_sha,
                "X-Read-Only": "true" if read_only else "false",
            },
            method="POST",
        )
        result = self._json_request(request, timeout=180)
        result["source_archive_sha256"] = digest
        return result

    def wait_ready(self, lease: dict[str, Any], *, timeout: int = 600,
                   cancelled: Any = None, progress: Any = None) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancelled is not None and cancelled.is_set():
                raise BrokerClientError("sandbox preparation cancelled")
            result = self._post_json("/v1/runs/status", self._run_authority(lease), timeout=10)
            state = result.get("state")
            if state == "ready":
                return
            if state == "failed":
                raise BrokerClientError("sandbox source initialization failed")
            if progress is not None:
                progress()
            time.sleep(2)
        raise BrokerClientError("sandbox did not become ready before the preparation deadline")

    def bind(self, lease: dict[str, Any], session_id: str, profile: str) -> None:
        payload = {**self._run_authority(lease), "hermes_session_id": session_id, "profile": profile}
        self._post_json("/v1/runs/bind-session", payload)

    def heartbeat(self, lease: dict[str, Any]) -> None:
        """Renew the bound sandbox lease while its worker is demonstrably alive."""
        self._post_json("/v1/runs/heartbeat", self._run_authority(lease), timeout=10)

    def apply_changes(self, lease: dict[str, Any], session_id: str, checkout: Path) -> dict[str, Any]:
        payload = {**self._run_authority(lease), "hermes_session_id": session_id}
        request = urllib.request.Request(
            self.base_url + "/v1/runs/changes",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/gzip",
            },
            method="POST",
        )
        with self._open(request, 60) as response:
            archive = response.read(MAX_ARCHIVE_BYTES + 1)
            if len(archive) > MAX_ARCHIVE_BYTES:
                raise BrokerClientError("output archive exceeds the configured size limit")
            claimed = response.headers.get("X-Archive-SHA256", "")
        actual = hashlib.sha256(archive).hexdigest()
        if not claimed or not hmac.compare_digest(actual, claimed):
            raise BrokerClientError("output archive digest mismatch")
        protected = lease.get("protected_prefixes", [])
        if not isinstance(protected, list) or any(not isinstance(item, str) for item in protected):
            raise BrokerClientError("sandbox lease has invalid protected source paths")
        return apply_changed_archive(checkout, archive, protected_prefixes=protected)

    def finalize(self, lease: dict[str, Any]) -> None:
        self._post_json("/v1/runs/finalize", self._run_authority(lease), timeout=20)

    def resource_usage(self, lease: dict[str, Any], session_id: str) -> dict[str, Any]:
        payload = {**self._run_authority(lease), "hermes_session_id": session_id}
        result = self._post_json("/v1/runs/resource-usage", payload, timeout=10)
        if result.get("schema") != "kanzo-sandbox-resource-usage/v1":
            raise BrokerClientError("broker returned invalid sandbox resource metrics")
        return result

    @staticmethod
    def _run_authority(lease: dict[str, Any]) -> dict[str, str]:
        result = {}
        for name in ("task_id", "run_id", "lease"):
            value = lease.get(name)
            if not isinstance(value, str) or not value:
                raise BrokerClientError("broker lease is incomplete")
            result[name] = value
        return result

    def _post_json(self, path: str, payload: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        return self._json_request(request, timeout)

    def _json_request(self, request: urllib.request.Request, timeout: int) -> dict[str, Any]:
        with self._open(request, timeout) as response:
            data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise BrokerClientError("broker response exceeds the size limit")
        try:
            result = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BrokerClientError("broker returned invalid JSON") from None
        if not isinstance(result, dict):
            raise BrokerClientError("broker response is not an object")
        return result

    def _open(self, request: urllib.request.Request, timeout: int):
        try:
            return self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            # Avoid reflecting broker bodies, source URLs, and lease material.
            raise BrokerClientError(f"Kanzo broker returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BrokerClientError(f"Kanzo broker request failed ({type(exc).__name__})") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
