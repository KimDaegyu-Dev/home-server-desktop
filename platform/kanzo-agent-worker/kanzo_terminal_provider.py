"""Draft Hermes 0.23.1 terminal provider backed by an internal Kanzo broker.

Minimal broker contract (all paths are relative to ``KANZO_BROKER_URL``):

* ``POST /v1/terminal/resolve`` with service Bearer auth and
  ``{"hermes_session_id": "..."}`` returns HTTP 200 JSON:
  ``{"hermes_session_id": "...", "task_id": "...", "run_id": "...",
  "lease": "opaque-current-lease", "environment_handle": "opaque-handle",
  "cwd": "/workspace", "temp_dir": "/tmp"}``.
  A missing, stale, or ambiguous mapping MUST be a non-2xx response. The broker
  binds the returned lease to this exact Hermes session, Kanzo task/run, and
  sandbox; the provider rejects incomplete or mismatched responses.
* ``POST /v1/terminal/exec/start`` with service Bearer auth and JSON
  ``{"hermes_session_id": ..., "task_id": ..., "run_id": ..., "lease": ...,
  "environment_handle": ..., "argv": [...], "stdin": null|string,
  "cwd": "/workspace", "timeout_seconds": 120}`` returns HTTP 200 JSON
  ``{"exec_id": "opaque-exec-id"}`` quickly, without waiting for the command.
  The broker MUST execute argv with ``subprocess.Popen(argv, shell=False, ...)``
  (never interpolate argv or stdin into a shell command). Hermes supplies terminal
  commands as shell text, represented as ``["/bin/bash", "-lc", command]``;
  the broker still uses ``shell=False`` and the sandbox must contain bash. An
  input list[str] is also accepted and forwarded unchanged. ``stdin_data`` is
  transported separately as ``stdin``.
* The provider polls ``POST /v1/terminal/exec/poll`` with the same authority
  fields plus ``exec_id`` and integer ``cursor``. HTTP 200 JSON is either
  ``{"state": "running", "output_chunk": "...", "next_cursor": N}`` or
  ``{"state": "completed", "output_chunk": "...", "next_cursor": N,
  "exit_code": 0, "cwd": "/workspace/next"}``. Output chunks are append-only,
  cursor-addressed, and bounded. The broker MUST revalidate the run lease and
  environment handle on every start/poll/cancel request and cap output.
* On Hermes interruption or timeout, the provider calls
  ``POST /v1/terminal/exec/cancel`` with the authority fields and ``exec_id``.
  HTTP 200 ``{"state": "cancelled"}`` means the process group has stopped;
  ``{"state": "completed"}`` means it raced to completion and the provider
  fetches its final poll result. Cancellation MUST be idempotent and stop the
  remote process group before acknowledging ``cancelled``.
* ``POST /v1/terminal/environment/cleanup`` with the same authority fields and
  ``environment_handle`` returns HTTP 200/204 after stopping work owned by that
  handle and detaching only that environment object. It MUST NOT close the run
  lease or delete/close the shared run sandbox; other environment handles for
  that run remain usable. Run-wide sandbox reclamation is a separate broker
  lease/reaper responsibility.

The broker service credential is read only by this in-process provider from
``KANZO_BROKER_TOKEN`` and is sent only as an HTTPS/cluster-internal HTTP
Authorization header. It is never forwarded in execute requests or child
process environments. Redirects are rejected to prevent credential forwarding.
This file is an integration draft, not a production-ready sandbox boundary.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from agent.terminal_env_provider import TerminalEnvironmentProvider
from gateway import session_context


_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_REQUEST_BYTES = 10 * 1024 * 1024
_MAX_ARGUMENTS = 256
_MAX_ARGUMENT_BYTES = 1024 * 1024
_MAX_STDIN_BYTES = 8 * 1024 * 1024
_DEFAULT_TIMEOUT_SECONDS = 120
_MAX_TIMEOUT_SECONDS = 3600
_POLL_INTERVAL_SECONDS = 0.25
_MAX_EXEC_OUTPUT_BYTES = 2 * 1024 * 1024
_LOG = logging.getLogger("kanzo-broker-terminal")
_LOCAL_INTERACTIVE_SOURCES = frozenset({"acp", "cli", "desktop", "gui", "tui"})
_SECRET_ENV_KEYS = frozenset(
    {
        "KANZO_BROKER_TOKEN",
        "KANZO_BROKER_CLIENT_SECRET",
        "KANZO_TASK_TOKEN",
        "KANZO_WORKER_TOKEN",
        "KANZO_READ_TOKEN",
        "KANZO_WRITE_TOKEN",
        "LITELLM_MASTER_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "KUBECONFIG",
        "VAULT_TOKEN",
    }
)


class KanzoBrokerError(RuntimeError):
    """Broker rejected or could not safely service a terminal operation."""


def _clean_absolute_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise KanzoBrokerError(f"broker returned invalid {field}")
    if "\x00" in value or "\\" in value:
        raise KanzoBrokerError(f"broker returned invalid {field}")
    normalized = os.path.normpath(value)
    if normalized != value.rstrip("/") and not (value == "/" and normalized == "/"):
        raise KanzoBrokerError(f"broker returned non-canonical {field}")
    return normalized


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _BrokerClient:
    def __init__(self, base_url: str, token: str, request_timeout: float):
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise KanzoBrokerError("KANZO_BROKER_URL must be an http(s) origin/path without credentials or query")
        host = parsed.hostname.lower()
        if parsed.scheme == "http" and not (
            host == "localhost" or host.endswith(".svc") or host.endswith(".svc.cluster.local")
        ):
            raise KanzoBrokerError("plain HTTP is allowed only for loopback or Kubernetes service DNS")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.request_timeout = request_timeout
        self.opener = urllib.request.build_opener(_NoRedirect())

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(body) > _MAX_REQUEST_BYTES:
            raise KanzoBrokerError("broker request exceeded the size limit")
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.request_timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise KanzoBrokerError("broker response exceeded the size limit")
                if response.status not in (200, 201, 202, 204):
                    raise KanzoBrokerError(f"broker returned HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            # Broker errors are fixed machine codes, not task content. Preserve
            # only a tightly validated code so request-boundary failures can be
            # diagnosed without exposing response bodies or credentials.
            error_code = ""
            try:
                raw_error = exc.read(4096)
                error_payload = json.loads(raw_error.decode("utf-8"))
                candidate = error_payload.get("error") if isinstance(error_payload, dict) else None
                if isinstance(candidate, str) and re.fullmatch(r"[a-z0-9_]{1,80}", candidate):
                    error_code = candidate
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                pass
            suffix = f" ({error_code})" if error_code else ""
            raise KanzoBrokerError(f"broker returned HTTP {exc.code}{suffix}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise KanzoBrokerError(f"broker request failed: {type(exc).__name__}") from None
        if not raw:
            return {}
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise KanzoBrokerError("broker returned invalid JSON") from None
        if not isinstance(result, dict):
            raise KanzoBrokerError("broker response must be a JSON object")
        return result


class KanzoTerminalEnvironment:
    """One session-to-run-bound environment; all execution is broker mediated."""

    def __init__(
        self,
        *,
        broker: _BrokerClient,
        session_id: str,
        mapping: dict[str, Any],
        cwd: str,
        timeout: int,
    ):
        self._broker = broker
        self._session_id = session_id
        self._task_id = mapping["task_id"]
        self._run_id = mapping["run_id"]
        self._lease = mapping["lease"]
        self._environment_handle = mapping["environment_handle"]
        self.cwd = _clean_absolute_path(mapping.get("cwd", cwd), "cwd")
        self._temp_dir = _clean_absolute_path(mapping.get("temp_dir", "/tmp"), "temp_dir")
        self.timeout = timeout
        self._cleaned = False
        self._closing = False
        self._active_exec_ids: set[str] = set()
        self._pending_starts = 0
        self._active_lock = threading.Condition(threading.RLock())

    def _authority(self) -> dict[str, str]:
        return {
            "hermes_session_id": self._session_id,
            "task_id": self._task_id,
            "run_id": self._run_id,
            "lease": self._lease,
            "environment_handle": self._environment_handle,
        }

    def get_temp_dir(self) -> str:
        return self._temp_dir

    @staticmethod
    def _argv(command: Any) -> list[str]:
        if isinstance(command, str):
            if not command.strip() or "\x00" in command or len(command.encode("utf-8")) > _MAX_ARGUMENT_BYTES:
                raise ValueError("command must be non-empty text without NUL bytes")
            # Keep normal Hermes terminal shell semantics while transporting the
            # script as one argv element; broker invocation itself is shell=False.
            return ["/bin/bash", "-lc", command]
        if isinstance(command, (list, tuple)) and command and len(command) <= _MAX_ARGUMENTS and all(
            isinstance(part, str)
            and "\x00" not in part
            and len(part.encode("utf-8")) <= _MAX_ARGUMENT_BYTES
            for part in command
        ):
            argv = list(command)
            if argv[0]:
                return argv
        raise ValueError("command must be non-empty shell text or a non-empty argv of strings")

    def execute(
        self,
        command: Any,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if kwargs:
            # Hermes may add optional execution hints over time. Do not silently
            # reinterpret them as process/environment controls.
            allowed = {"rewrite_compound_background", "bounded_capture", "yield_handler"}
            unknown = set(kwargs) - allowed
            if unknown:
                raise TypeError(f"unsupported execute options: {', '.join(sorted(unknown))}")
            if kwargs.get("yield_handler") is not None:
                raise KanzoBrokerError("broker backend cannot hand a remote command to local background execution")
        if self._cleaned:
            raise KanzoBrokerError("environment has already been cleaned up")
        if stdin_data is not None and not isinstance(stdin_data, str):
            raise ValueError("stdin_data must be text or None")
        if stdin_data is not None and len(stdin_data.encode("utf-8")) > _MAX_STDIN_BYTES:
            raise ValueError("stdin_data exceeds the size limit")
        effective_cwd = _clean_absolute_path(cwd or self.cwd, "cwd")
        try:
            effective_timeout = int(timeout or self.timeout or _DEFAULT_TIMEOUT_SECONDS)
        except (TypeError, ValueError):
            effective_timeout = _DEFAULT_TIMEOUT_SECONDS
        effective_timeout = max(1, min(effective_timeout, _MAX_TIMEOUT_SECONDS))
        try:
            from tools.interrupt import is_interrupted, is_thread_interrupted
        except ImportError as exc:
            raise KanzoBrokerError("Hermes interrupt API is unavailable; refusing uncancellable remote execution") from exc
        caller_thread_id = threading.current_thread().ident
        with self._active_lock:
            if self._closing or self._cleaned:
                raise KanzoBrokerError("environment cleanup has started; refusing new execution")
            self._pending_starts += 1
        try:
            start = self._broker.post(
                "/v1/terminal/exec/start",
                {
                    **self._authority(),
                    "argv": self._argv(command),
                    "stdin": stdin_data,
                    "cwd": effective_cwd,
                    "timeout_seconds": effective_timeout,
                },
            )
        except BaseException:
            with self._active_lock:
                self._pending_starts -= 1
                self._active_lock.notify_all()
            raise
        exec_id = start.get("exec_id")
        if not isinstance(exec_id, str) or not exec_id.strip() or len(exec_id) > 4096:
            with self._active_lock:
                self._pending_starts -= 1
                self._active_lock.notify_all()
            raise KanzoBrokerError("broker start response lacks a valid exec_id")
        with self._active_lock:
            self._pending_starts -= 1
            closing = self._closing or self._cleaned
            if not closing:
                self._active_exec_ids.add(exec_id)
            self._active_lock.notify_all()
        if closing:
            try:
                self._cancel_exec(exec_id)
            except Exception as exc:
                raise KanzoBrokerError(
                    "environment cleanup raced with command start; remote execution may still be running"
                ) from exc
            raise KanzoBrokerError("environment was cleaned while execution was starting")

        cursor = 0
        chunks: list[str] = []
        output_size = 0
        deadline = time.monotonic() + effective_timeout
        activity_started = time.monotonic()
        activity_state = {"start": activity_started, "last_touch": activity_started}
        from tools.environments.base import touch_activity_if_due
        try:
            while True:
                interrupted = bool(is_interrupted()) or bool(
                    caller_thread_id is not None and is_thread_interrupted(caller_thread_id)
                )
                if interrupted:
                    state = self._cancel_exec(exec_id)
                    if state == "completed":
                        final = self._poll_exec(exec_id, cursor)
                        result = self._completed_result(final, chunks, output_size)
                        result["hermes_interrupted"] = True
                        return result
                    return {
                        "output": "".join(chunks) + "\n[Command interrupted]",
                        "exit_code": 130,
                        "returncode": 130,
                        "hermes_interrupted": True,
                    }
                if time.monotonic() >= deadline:
                    state = self._cancel_exec(exec_id)
                    if state == "completed":
                        final = self._poll_exec(exec_id, cursor)
                        return self._completed_result(final, chunks, output_size)
                    return {
                        "output": "".join(chunks) + f"\n[Command timed out after {effective_timeout}s]",
                        "exit_code": 124,
                        "returncode": 124,
                        "hermes_timed_out": True,
                    }
                response = self._poll_exec(exec_id, cursor)
                touch_activity_if_due(activity_state, "Kanzo broker command")
                chunk = response.get("output_chunk", "")
                next_cursor = response.get("next_cursor")
                if not isinstance(chunk, str) or isinstance(next_cursor, bool) or not isinstance(next_cursor, int):
                    raise KanzoBrokerError("broker poll response has invalid output chunk or cursor")
                chunk_size = len(chunk.encode("utf-8"))
                if chunk_size > _MAX_EXEC_OUTPUT_BYTES or output_size + chunk_size > _MAX_EXEC_OUTPUT_BYTES:
                    self._cancel_exec(exec_id)
                    raise KanzoBrokerError("remote command output exceeded the size limit")
                chunks.append(chunk)
                output_size += chunk_size
                if next_cursor < cursor:
                    raise KanzoBrokerError("broker output cursor moved backwards")
                if chunk and next_cursor == cursor:
                    raise KanzoBrokerError("broker repeated output without advancing its cursor")
                cursor = next_cursor
                state = response.get("state")
                if state == "completed":
                    return self._completed_result({**response, "output_chunk": ""}, chunks, output_size)
                if state != "running":
                    raise KanzoBrokerError("broker returned an unknown execution state")
                time.sleep(_POLL_INTERVAL_SECONDS)
        except KeyboardInterrupt as exc:
            try:
                self._cancel_exec(exec_id)
            except Exception as cancel_error:
                raise KanzoBrokerError(
                    "remote command cancellation could not be confirmed; broker reaper must recover the run"
                ) from cancel_error
            return {
                "output": "".join(chunks) + "\n[Command interrupted]",
                "exit_code": 130,
                "returncode": 130,
                "hermes_interrupted": True,
            }
        except BaseException as exc:
            # Do not leave an orphaned remote process when the caller exits or
            # a broker operation fails after start. Preserve the original error.
            try:
                self._cancel_exec(exec_id)
            except Exception as cancel_error:
                raise KanzoBrokerError(
                    "remote command may still be running because broker cancellation was not confirmed"
                ) from cancel_error
            raise exc
        finally:
            with self._active_lock:
                self._active_exec_ids.discard(exec_id)

    def _poll_exec(self, exec_id: str, cursor: int) -> dict[str, Any]:
        return self._broker.post(
            "/v1/terminal/exec/poll",
            {**self._authority(), "exec_id": exec_id, "cursor": cursor},
        )

    def _cancel_exec(self, exec_id: str) -> str:
        response = self._broker.post(
            "/v1/terminal/exec/cancel",
            {**self._authority(), "exec_id": exec_id},
        )
        state = response.get("state")
        if state not in {"cancelled", "completed"}:
            raise KanzoBrokerError("broker did not confirm remote execution cancellation")
        return state

    def _completed_result(
        self, response: dict[str, Any], chunks: list[str], output_size: int
    ) -> dict[str, Any]:
        code = response.get("exit_code")
        if isinstance(code, bool) or not isinstance(code, int):
            raise KanzoBrokerError("completed broker response lacks integer exit_code")
        output = "".join(chunks)
        final_chunk = response.get("output_chunk", "")
        if not isinstance(final_chunk, str):
            raise KanzoBrokerError("broker completion output_chunk is invalid")
        if final_chunk:
            if output_size + len(final_chunk.encode("utf-8")) > _MAX_EXEC_OUTPUT_BYTES:
                raise KanzoBrokerError("remote command output exceeded the size limit")
            output += final_chunk
        result: dict[str, Any] = {"output": output, "exit_code": code, "returncode": code}
        next_cwd = response.get("cwd")
        if next_cwd is not None:
            self.cwd = _clean_absolute_path(next_cwd, "cwd")
            result.update({"cwd": self.cwd, "cwd_observed": True})
        return result

    def cleanup(self) -> None:
        with self._active_lock:
            if self._cleaned:
                return
            self._closing = True
            deadline = time.monotonic() + 15
            while self._pending_starts:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise KanzoBrokerError("cleanup could not fence an in-flight command start")
                self._active_lock.wait(remaining)
            active_exec_ids = list(self._active_exec_ids)
        for exec_id in active_exec_ids:
            self._cancel_exec(exec_id)
        # This endpoint is scoped to this opaque environment handle and must
        # never release the run lease or close the shared run sandbox.
        self._broker.post(
            "/v1/terminal/environment/cleanup",
            self._authority(),
        )
        with self._active_lock:
            self._cleaned = True


class KanzoTerminalProvider(TerminalEnvironmentProvider):
    name = "kanzo_broker"
    display_name = "Kanzo Broker"
    is_remote = True
    is_container = True
    session_isolated_when_nonpersistent = True

    @property
    def description(self) -> str:
        return "Run commands in the sandbox leased to the active Kanzo task."

    @property
    def skip_container_guards(self) -> bool:
        # A broker response alone does not prove that the run sandbox is disposable or
        # correctly isolated. Keep Hermes' dangerous-command checks until the deployed
        # broker and Pod policy have been verified end to end.
        return False

    @property
    def default_cwd(self) -> str:
        return "/workspace"

    @property
    def strip_env_keys(self) -> frozenset[str]:
        configured = os.getenv("KANZO_BROKER_SECRET_ENV_KEYS", "")
        extra = {item.strip() for item in configured.split(",") if item.strip()}
        credential_markers = (
            "KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL",
            "AUTH", "BEARER", "PRIVATE", "SIGNING", "COOKIE", "CERTIFICATE",
        )
        secret_named = {
            key for key in os.environ if any(marker in key.upper() for marker in credential_markers)
        }
        return _SECRET_ENV_KEYS | frozenset(extra) | frozenset(secret_named)

    def is_available(self) -> bool:
        # Cheap picker check only. Session mapping is resolved at environment
        # creation, where failures must prevent tool execution.
        return bool(os.getenv("KANZO_BROKER_URL") and os.getenv("KANZO_BROKER_TOKEN"))

    def create_environment(
        self,
        *,
        cwd: str,
        timeout: int,
        task_id: str = "default",
        image: str | None = None,
        container_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        del kwargs  # Hermes may add provider options; they do not grant broker authority.
        source = session_context.get_session_env("HERMES_SESSION_SOURCE", "")
        source_value = getattr(source, "value", source)
        try:
            from agent.session_client_env import bound_client_env

            raw_client_env = bound_client_env()
        except (ImportError, RuntimeError):
            raw_client_env = None
        client_env = raw_client_env if isinstance(raw_client_env, dict) else {}
        automated = client_env.get("KANZO_AUTOMATED_RUN") == "1"
        task_marker = client_env.get("KANZO_TASK_ID", "")
        run_marker = client_env.get("KANZO_RUN_ID", "")
        kanban_task_marker = client_env.get("HERMES_KANBAN_TASK", "")
        kanban_run_marker = client_env.get("HERMES_KANBAN_RUN_ID", "")
        has_automation_marker = bool(
            automated or source_value == "oneshot" or task_marker or run_marker
            or kanban_task_marker or kanban_run_marker
        )

        if not has_automation_marker and source_value in _LOCAL_INTERACTIVE_SOURCES:
            # Preserve local terminal use for explicitly identified human-facing
            # surfaces. Unknown or missing sources fail closed below.
            from tools.terminal_tool_backends import _create_environment

            _LOG.info("terminal backend resolved backend=local source=%s", source_value)
            return _create_environment(
                "local",
                image or "",
                cwd,
                timeout,
                container_config=container_config,
                task_id=task_id,
            )

        if not has_automation_marker:
            _LOG.error("terminal backend refused backend=none source=%s", source_value)
            raise KanzoBrokerError("refusing terminal environment for unknown Hermes session source")

        expected_task_id = task_marker
        expected_run_id = run_marker
        if (
            not automated
            or not isinstance(expected_task_id, str) or not expected_task_id.strip()
            or not isinstance(expected_run_id, str) or not expected_run_id.strip()
        ):
            _LOG.error("terminal backend refused backend=none source=%s task_id=%s run_id=%s",
                       source_value, task_marker or "-", run_marker or "-")
            raise KanzoBrokerError("refusing automated session without its Kanzo task/run identity")
        if (
            (kanban_task_marker and kanban_task_marker != expected_task_id)
            or (kanban_run_marker and kanban_run_marker != expected_run_id)
        ):
            _LOG.error("terminal backend refused backend=none source=%s task_id=%s run_id=%s reason=identity_alias_mismatch",
                       source_value, expected_task_id, expected_run_id)
            raise KanzoBrokerError("Kanzo session identity aliases do not match")
        session_id = session_context.get_session_env("HERMES_SESSION_ID", "")
        if (
            not isinstance(session_id, str)
            or not session_id.strip()
            or len(session_id) > 256
            or any(ord(char) < 0x20 for char in session_id)
        ):
            _LOG.error("terminal backend refused backend=none task_id=%s run_id=%s reason=missing_session_id",
                       expected_task_id, expected_run_id)
            raise KanzoBrokerError("refusing terminal environment without an active Hermes session id")
        base_url = os.getenv("KANZO_BROKER_URL", "").strip()
        token = os.getenv("KANZO_BROKER_TOKEN", "")
        if not base_url or not token:
            raise KanzoBrokerError("Kanzo broker URL/token are not configured")
        # Each call is short and independently cancellable; command duration is
        # enforced by the local poll deadline plus broker-side timeout.
        client = _BrokerClient(base_url, token, request_timeout=10)
        mapping = client.post(
            "/v1/terminal/resolve",
            {"hermes_session_id": session_id},
        )
        if mapping.get("hermes_session_id") != session_id:
            raise KanzoBrokerError("broker mapping does not match the active Hermes session")
        if mapping.get("task_id") != expected_task_id or mapping.get("run_id") != expected_run_id:
            raise KanzoBrokerError("broker mapping does not match the active Kanzo task/run")
        for key in ("task_id", "run_id", "lease", "environment_handle"):
            value = mapping.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > 4096:
                raise KanzoBrokerError(f"broker mapping lacks a valid {key}")
        # Never trust a caller-provided cwd over the broker's leased workspace.
        mapped_cwd = _clean_absolute_path(mapping.get("cwd"), "cwd")
        _clean_absolute_path(mapping.get("temp_dir"), "temp_dir")
        _LOG.info("terminal backend resolved backend=kanzo_broker task_id=%s run_id=%s",
                  expected_task_id, expected_run_id)
        return KanzoTerminalEnvironment(
            broker=client,
            session_id=session_id,
            mapping=mapping,
            cwd=mapped_cwd,
            timeout=int(timeout or _DEFAULT_TIMEOUT_SECONDS),
        )


def register(ctx: Any) -> None:
    """Hermes user-plugin entry point."""
    ctx.register_terminal_environment_provider(KanzoTerminalProvider())
