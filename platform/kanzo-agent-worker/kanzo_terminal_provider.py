"""Hermes 0.23.1 terminal provider backed by an internal Kanzo broker.

Minimal broker contract (all paths are relative to ``KANZO_BROKER_URL``):

* ``POST /v1/terminal/authorize`` with service Bearer auth and a Hermes session
  ID proves that the broker has a live task/run binding for this exact session.
  It does not create an environment handle or authorize execution; the provider
  must still resolve and authorize every operation normally.
* ``POST /v1/terminal/resolve`` with service Bearer auth and
  ``{"hermes_session_id": "..."}`` returns HTTP 200 JSON:
  ``{"hermes_session_id": "...", "task_id": "...", "run_id": "...",
  "lease": "opaque-current-lease", "environment_handle": "opaque-handle",
  "cwd": "/workspace", "temp_dir": "/scratch"}``.
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
Kanzo-scoped file writes are admitted by Hermes' host-side safe-root check only
inside a broker-attributed one-shot session. Actual file operations still use
the terminal provider and are enforced by the run pod's workspace mount.
Hermes delegate sessions are registered under their already-bound parent run
and revoked at subagent_stop. Failed registration remains fail-closed.
"""

from __future__ import annotations

import json
import logging
import os
import re
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
        self._temp_dir = _clean_absolute_path(mapping.get("temp_dir"), "temp_dir")
        if self.cwd != "/workspace" or self._temp_dir != "/scratch":
            raise KanzoBrokerError("broker returned an unsupported sandbox workspace or temp directory")
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
        try:
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
            # Hermes execute_code's staging and cleanup helpers deliberately run
            # absolute-path operations with cwd="/". The per-run runner accepts
            # only /workspace and /scratch as initial working directories, so map
            # that root cwd request to the broker-provided writable scratch area.
            # Commands still operate on their explicit absolute paths.
            if effective_cwd == "/":
                effective_cwd = self._temp_dir
            try:
                effective_timeout = int(timeout or self.timeout or _DEFAULT_TIMEOUT_SECONDS)
            except (TypeError, ValueError):
                effective_timeout = _DEFAULT_TIMEOUT_SECONDS
            effective_timeout = max(1, min(effective_timeout, _MAX_TIMEOUT_SECONDS))
            argv = self._argv(command)
            authority = self._authority()
            try:
                from tools.interrupt import is_interrupted, is_thread_interrupted
            except ImportError as exc:
                raise KanzoBrokerError(
                    "Hermes interrupt API is unavailable; refusing uncancellable remote execution"
                ) from exc
        except (KanzoBrokerError, TypeError, ValueError) as exc:
            return self._failed_result(
                type(exc).__name__, outcome_uncertain=False, execution_state="not_started"
            )
        caller_thread_id = threading.current_thread().ident
        with self._active_lock:
            if self._closing or self._cleaned:
                return self._failed_result(
                    "EnvironmentClosing", outcome_uncertain=False, execution_state="not_started"
                )
            self._pending_starts += 1
        try:
            start = self._broker.post(
                "/v1/terminal/exec/start",
                {
                    **authority,
                    "argv": argv,
                    "stdin": stdin_data,
                    "cwd": effective_cwd,
                    "timeout_seconds": effective_timeout,
                },
            )
        except Exception as exc:
            with self._active_lock:
                self._pending_starts -= 1
                self._active_lock.notify_all()
            # The broker may have admitted the command before the response was
            # lost. Return a failed terminal result instead of raising into
            # Hermes' generic exception retry loop and possibly starting it twice.
            return self._failed_result(type(exc).__name__, outcome_uncertain=True)
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
            return self._failed_result("InvalidStartResponse", outcome_uncertain=True)
        with self._active_lock:
            self._pending_starts -= 1
            closing = self._closing or self._cleaned
            if not closing:
                self._active_exec_ids.add(exec_id)
            self._active_lock.notify_all()
        if closing:
            try:
                state = self._cancel_exec(exec_id)
                if state == "completed":
                    final = self._poll_exec(exec_id, 0)
                    return self._completed_result(final, [], 0)
            except Exception as exc:
                return self._failed_result(type(exc).__name__, outcome_uncertain=True)
            return self._failed_result("EnvironmentClosing", outcome_uncertain=False)

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
        except Exception as exc:
            # Do not let Hermes' generic retry loop repeat a command after a
            # broker failure: start/poll may have reached the remote runner.
            try:
                state = self._cancel_exec(exec_id)
                if state == "completed":
                    final = self._poll_exec(exec_id, cursor)
                    return self._completed_result(final, chunks, output_size)
                return self._failed_result(type(exc).__name__, outcome_uncertain=False, partial="".join(chunks))
            except Exception:
                return self._failed_result(type(exc).__name__, outcome_uncertain=True, partial="".join(chunks))
        except BaseException as exc:
            # Preserve cancellation/exit semantics, while still trying to stop
            # the remote process before propagating a non-Exception signal.
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

    @staticmethod
    def _failed_result(
        error_type: str, *, outcome_uncertain: bool, partial: str = "", execution_state: str | None = None
    ) -> dict[str, Any]:
        state = execution_state or ("unknown" if outcome_uncertain else "cancelled")
        output = (
            partial
            + f"\n[Kanzo broker {error_type}; remote execution state={state}. "
            "This command was not retried automatically. Check the Kanzo run state before invoking it again.]"
        )
        return {
            "output": output,
            "exit_code": 1,
            "returncode": 1,
            "error": f"non_retryable_broker_error:{error_type}",
            "kanzo_non_retryable": True,
        }

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
    ctx.register_hook("subagent_start", _register_delegated_child)
    ctx.register_hook("subagent_stop", _close_delegated_child)
    # Hermes' ShellFileOperations applies the host safe-root check before it
    # dispatches a write to the selected terminal environment. Keep the global
    # HERMES_WRITE_SAFE_ROOT unchanged; bypass only that one denial when the
    # active environment for this exact session is the broker and the broker
    # resolves the target inside the run's workspace or scratch directory.
    from agent import file_safety
    from tools import file_operations

    current_write_denial = file_operations.get_write_denied_error
    if not getattr(current_write_denial, "_kanzo_broker_workspace", False):
        def _kanzo_write_denial(path: str, *, verb: str = "Write", entry: bool = False) -> str | None:
            denial = current_write_denial(path, verb=verb, entry=entry)
            if denial is None or file_safety._classify_write_denial(path, entry=entry) != "safe_root":
                return denial

            normalized = os.path.normpath(os.path.abspath(os.path.expanduser(str(path))))
            if not any(
                normalized == root or normalized.startswith(root + os.sep)
                for root in ("/workspace", "/scratch")
            ):
                return denial
            session_id = session_context.get_session_env("HERMES_SESSION_ID", "")
            base_url = os.getenv("KANZO_BROKER_URL", "").strip()
            token = os.getenv("KANZO_BROKER_TOKEN", "")
            if (
                not isinstance(session_id, str) or not session_id.strip()
                or len(session_id) > 256
                or any(ord(char) < 0x20 for char in session_id)
                or not base_url or not token
            ):
                _LOG.warning("file safe-root authorization denied (session or broker credentials unavailable)")
                return denial
            try:
                # Match the selected backend to the session, not just the
                # configured default. This prevents authorizing a host/local
                # file operation with an unrelated broker session proof.
                from tools.terminal_tool import _active_environments, _env_lock
                with _env_lock:
                    matches = [
                        env for env in _active_environments.values()
                        if getattr(env, "_hermes_backend_name", "") == "kanzo_broker"
                        and getattr(env, "_session_id", "") == session_id
                    ]
                if len(matches) != 1:
                    _LOG.warning(
                        "file safe-root authorization denied (active broker environment count=%d)",
                        len(matches),
                    )
                    return denial
                environment = matches[0]
                proof = _BrokerClient(
                    base_url, token, request_timeout=3
                ).post(
                    "/v1/terminal/authorize",
                    {"hermes_session_id": session_id},
                )
                resolved = environment.execute(
                    ["/usr/bin/realpath", "-m", "--", normalized],
                    cwd="/workspace",
                    timeout=5,
                )
            except Exception as exc:
                # Broker outage or an unbound session retains the host denial.
                _LOG.warning("file safe-root authorization failed (%s)", type(exc).__name__)
                return denial
            resolved_path = resolved.get("output", "").strip()
            if resolved.get("exit_code") != 0 or "\n" in resolved_path:
                return denial
            if not any(
                resolved_path == root or resolved_path.startswith(root + os.sep)
                for root in ("/workspace", "/scratch")
            ):
                _LOG.warning("file safe-root authorization denied (resolved target outside sandbox roots)")
                return denial
            if (
                proof.get("hermes_session_id") == session_id
                and isinstance(proof.get("task_id"), str) and proof["task_id"].strip()
                and isinstance(proof.get("run_id"), str) and proof["run_id"].strip()
                and proof.get("workspace") == "/workspace"
                and proof.get("temp_dir") == "/scratch"
            ):
                _LOG.info("file safe-root authorization allowed for broker sandbox session")
                return None
            _LOG.warning("file safe-root authorization denied (broker session proof mismatch)")
            return denial

        _kanzo_write_denial._kanzo_broker_workspace = True  # type: ignore[attr-defined]
        file_operations.get_write_denied_error = _kanzo_write_denial

    # Hermes' remote execute_code session kernel is a detached child process.
    # Kanzo's runner deliberately terminates the whole command process group
    # after each request, so that kernel cannot survive between commands. Tell
    # Hermes to use its bounded per-call script path for this backend; that
    # path still routes every command through the same run lease and sandbox.
    from tools import code_kernel_remote

    current = code_kernel_remote.execute_in_remote_kernel
    if not getattr(current, "_kanzo_broker_oneshot", False):
        def _execute_in_remote_kernel(*args: Any, **kwargs: Any) -> Any:
            # Hermes' caller passes `code` positionally and `env` by keyword.
            # Accept the original call shape unchanged; a fixed first parameter
            # here binds `code` as env and then raises on the env keyword.
            env = kwargs.get("env")
            backend = getattr(env, "_hermes_backend_name", "")
            if isinstance(backend, str) and backend.strip().lower() == "kanzo_broker":
                return None
            return current(*args, **kwargs)

        _execute_in_remote_kernel._kanzo_broker_oneshot = True  # type: ignore[attr-defined]
        code_kernel_remote.execute_in_remote_kernel = _execute_in_remote_kernel


def _kanzo_automation_active() -> bool:
    try:
        from agent.session_client_env import bound_client_env

        client_env = bound_client_env()
    except (ImportError, RuntimeError):
        return False
    return isinstance(client_env, dict) and client_env.get("KANZO_AUTOMATED_RUN") == "1"


def _delegate_sessions(payload: dict[str, Any], *, close: bool) -> bool:
    if not _kanzo_automation_active():
        return False
    parent = payload.get("parent_session_id")
    child = payload.get("child_session_id")
    if not all(
        isinstance(value, str) and value.strip() and len(value) <= 256
        and not any(ord(char) < 0x20 for char in value)
        for value in (parent, child)
    ):
        _LOG.warning("Kanzo delegate scope hook refused an invalid child session identity")
        return False
    base_url = os.getenv("KANZO_BROKER_URL", "").strip()
    token = os.getenv("KANZO_BROKER_TOKEN", "")
    if not base_url or not token:
        _LOG.warning("Kanzo delegate scope hook could not reach the configured broker")
        return False
    endpoint = "/v1/terminal/child/close" if close else "/v1/terminal/child/register"
    try:
        _BrokerClient(base_url, token, request_timeout=10).post(endpoint, {
            "parent_session_id": parent,
            "child_session_id": child,
        })
        return True
    except Exception:
        # Lifecycle hooks are observers and Hermes intentionally does not abort
        # delegation on observer failure. Leave access denied by the broker.
        _LOG.warning("Kanzo delegate scope hook failed; child access remains fail-closed", exc_info=True)
        return False


def _register_delegated_child(**payload: Any) -> None:
    _delegate_sessions(payload, close=False)


def _retire_delegated_child_environment(payload: dict[str, Any]) -> None:
    """Evict the child terminal object before its broker session is revoked.

    Hermes may retain a per-session terminal object in its cache after the child
    returns. If the resumed parent is routed through that cached object, it keeps
    using the child's now-revoked broker identity. Remove only objects bound to
    this exact child session; parent and sibling environments remain untouched.
    """
    child = payload.get("child_session_id")
    if not isinstance(child, str) or not child.strip():
        return
    try:
        from tools import terminal_tool

        active = terminal_tool._active_environments
        lock = terminal_tool._env_lock
    except (ImportError, AttributeError):
        _LOG.warning("could not inspect Hermes terminal cache for delegated child")
        return
    with lock:
        matches = [
            (key, env) for key, env in active.items()
            if isinstance(key, str)
            and getattr(env, "_hermes_backend_name", "") == "kanzo_broker"
            and getattr(env, "_session_id", "") == child
        ]
        for key, _env in matches:
            active.pop(key, None)
            getattr(terminal_tool, "_last_activity", {}).pop(key, None)
    clear_cache = getattr(terminal_tool, "_clear_file_ops_cache", None)
    for _key, env in matches:
        if callable(clear_cache):
            clear_cache(_key)
        try:
            env.cleanup()
        except Exception:
            # Revocation still follows; run finalization remains the last-resort
            # cleanup for this environment handle.
            _LOG.warning("could not clean the retired child terminal handle", exc_info=True)
    if matches:
        _LOG.info("retired delegated child terminal environment count=%d", len(matches))


def _restore_parent_session_context(payload: dict[str, Any]) -> None:
    """Restore the task-local Hermes session after a child-stop hook.

    Hermes' delegated child may leave HERMES_SESSION_ID set to the child in the
    resuming parent context. Use the ContextVar directly: the public setter also
    writes os.environ outside child contexts, which could leak identity across
    concurrent Gateway sessions.
    """
    parent = payload.get("parent_session_id")
    child = payload.get("child_session_id")
    if not isinstance(parent, str) or not isinstance(child, str):
        return
    try:
        current = session_context.get_session_env("HERMES_SESSION_ID", "")
        session_id_var = getattr(session_context, "_SESSION_ID", None)
        set_current = getattr(session_id_var, "set", None)
        if current == child and callable(set_current):
            set_current(parent)
            _LOG.info("restored parent Hermes session context after delegation")
    except Exception:
        _LOG.warning("could not restore parent Hermes session context after delegation", exc_info=True)


def _close_delegated_child(**payload: Any) -> None:
    _retire_delegated_child_environment(payload)
    if _delegate_sessions(payload, close=True):
        _restore_parent_session_context(payload)
