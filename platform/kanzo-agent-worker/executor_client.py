"""HTTP/1.0 client for the internal Kanzo Hermes executor API.

The submit response is a close-delimited NDJSON stream. Its first record must
be the admission receipt; later records are forwarded one at a time. POSTs are
never retried because a transport failure can happen after the server acted.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any


_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_NDJSON_LINE_BYTES = 4 * 1024 * 1024
_MAX_TIMEOUT = 300.0
_IDENTIFIER = re.compile(r"^[^\x00-\x20\x7f]{1,512}$")


class ExecutorClientError(RuntimeError):
    """The executor API could not be called or returned an invalid response."""


class _HTTP10Connection(http.client.HTTPConnection):
    _http_vsn = 10
    _http_vsn_str = "HTTP/1.0"


class _HTTPS10Connection(http.client.HTTPSConnection):
    _http_vsn = 10
    _http_vsn_str = "HTTP/1.0"


class _HTTP10Handler(urllib.request.HTTPHandler):
    handler_order = urllib.request.HTTPHandler.handler_order - 1

    def http_open(self, req: urllib.request.Request):
        return self.do_open(_HTTP10Connection, req)


class _HTTPS10Handler(urllib.request.HTTPSHandler):
    handler_order = urllib.request.HTTPSHandler.handler_order - 1

    def https_open(self, req: urllib.request.Request):
        context = self._context or ssl.create_default_context()
        return self.do_open(
            lambda host, **kwargs: _HTTPS10Connection(host, context=context, **kwargs),
            req,
        )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} must be a non-empty identifier")
    return value


def _bounded_timeout(value: float, name: str) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a positive finite number") from None
    if not 0 < timeout <= _MAX_TIMEOUT or timeout != timeout or timeout == float("inf"):
        raise ValueError(f"{name} must be greater than 0 and at most {_MAX_TIMEOUT:g} seconds")
    return timeout


class ExecutorClient:
    """Small stdlib-only client. Credentials and URL default to the named envs."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        request_timeout: float = 15.0,
        stream_timeout: float = 120.0,
    ) -> None:
        self.base_url = (base_url if base_url is not None else os.environ.get("KANZO_EXECUTOR_API_URL", "")).rstrip("/")
        self.token = token if token is not None else os.environ.get("KANZO_EXECUTOR_API_TOKEN", "")
        parsed = urllib.parse.urlsplit(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("KANZO_EXECUTOR_API_URL must be an http(s) origin without a path, query, or credentials")
        if not self.token or any(ord(char) < 0x21 or ord(char) == 0x7F for char in self.token):
            raise ValueError("KANZO_EXECUTOR_API_TOKEN is missing or invalid")
        self.request_timeout = _bounded_timeout(request_timeout, "request_timeout")
        self.stream_timeout = _bounded_timeout(stream_timeout, "stream_timeout")
        # Preserve urllib's normal proxy and TLS behavior while using HTTP/1.0.
        self._opener = urllib.request.build_opener(_HTTP10Handler(), _HTTPS10Handler(), _NoRedirect())

    def create_session(
        self,
        *,
        task_id: str,
        run_id: str,
        profile: str,
        provider: str,
        model: str,
        reasoning: str,
        tool_policy: str = "standard",
    ) -> dict[str, Any]:
        """Create one session and require a valid session_id in the JSON reply."""
        if tool_policy not in {"standard", "response_only"}:
            raise ValueError("tool_policy must be standard or response_only")
        payload = {
            "task_id": _validate_identifier(task_id, "task_id"),
            "run_id": _validate_identifier(run_id, "run_id"),
            "profile": _validate_identifier(profile, "profile"),
            "provider": _validate_identifier(provider, "provider"),
            "model": _validate_identifier(model, "model"),
            "reasoning": _validate_identifier(reasoning, "reasoning"),
            "tool_policy": tool_policy,
        }
        response = self._post_json("/v1/sessions", payload, self.request_timeout)
        session_id = response.get("session_id")
        _validate_identifier(session_id, "response.session_id")
        return response

    def auth_status(self, *, provider: str, profile: str) -> bool:
        """Ask the resident executor whether the selected profile has provider auth."""
        response = self._post_json(
            "/v1/auth/status",
            {
                "provider": _validate_identifier(provider, "provider"),
                "profile": _validate_identifier(profile, "profile"),
            },
            self.request_timeout,
        )
        ready = response.get("ready")
        if type(ready) is not bool:
            raise ExecutorClientError("executor auth status response is invalid")
        return ready

    def submit_events(
        self,
        *,
        session_id: str,
        task_id: str,
        run_id: str,
        prompt: str,
        on_event: Callable[[dict[str, Any]], Any] | None = None,
    ) -> Iterator[dict[str, Any]] | None:
        """Stream NDJSON incrementally, yielding events or invoking ``on_event``.

        Without a callback this returns a lazy iterator. With a callback it
        consumes the stream and calls the callback for each record, including
        the admission receipt before any output event.
        """
        events = self.iter_submit_events(
            session_id=session_id, task_id=task_id, run_id=run_id, prompt=prompt
        )
        if on_event is None:
            return events
        for event in events:
            on_event(event)
        return None

    def iter_submit_events(
        self,
        *,
        session_id: str,
        task_id: str,
        run_id: str,
        prompt: str,
    ) -> Iterator[dict[str, Any]]:
        """Return the admission receipt and subsequent events as they arrive."""
        session_id = _validate_identifier(session_id, "session_id")
        payload = {
            "task_id": _validate_identifier(task_id, "task_id"),
            "run_id": _validate_identifier(run_id, "run_id"),
            "prompt": prompt,
        }
        if not isinstance(prompt, str) or len(prompt.encode("utf-8")) > _MAX_JSON_BYTES:
            raise ValueError("prompt must be UTF-8 text no larger than 4 MiB")
        body = self._encode_json(payload)
        request = self._request(
            f"/v1/sessions/{urllib.parse.quote(session_id, safe='')}/submit",
            body,
            "application/json",
            "application/x-ndjson",
        )
        try:
            response = self._opener.open(request, timeout=self.stream_timeout)
        except urllib.error.HTTPError as exc:
            raise ExecutorClientError(f"executor submit returned HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            raise ExecutorClientError(f"executor submit transport failed ({type(exc).__name__}); POST was not retried") from None
        with response:
            if response.status < 200 or response.status >= 300:
                raise ExecutorClientError(f"executor submit returned HTTP {response.status}")
            first = True
            terminal = False
            while True:
                line = response.readline(_MAX_NDJSON_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > _MAX_NDJSON_LINE_BYTES:
                    raise ExecutorClientError("executor NDJSON event exceeds 4 MiB")
                if not line.strip():
                    continue
                try:
                    event = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise ExecutorClientError("executor returned invalid NDJSON") from None
                if not isinstance(event, dict):
                    raise ExecutorClientError("executor NDJSON records must be JSON objects")
                if terminal:
                    raise ExecutorClientError("executor emitted data after its terminal event")
                if first:
                    admission_id = event.get("admission_id")
                    if event.get("type") != "admission" or event.get("session_id") != session_id:
                        raise ExecutorClientError("first executor event is not the matching admission receipt")
                    try:
                        _validate_identifier(admission_id, "admission_id")
                    except ValueError:
                        raise ExecutorClientError("executor admission receipt has an invalid admission_id") from None
                    first = False
                else:
                    event_session = event.get("session_id")
                    if event_session is not None and event_session != session_id:
                        raise ExecutorClientError("executor event belongs to a different session")
                    if event.get("type") == "error":
                        raise ExecutorClientError("executor reported an incomplete or ambiguous execution")
                    if event.get("type") == "executor_exit":
                        terminal = True
                        if event.get("settled") is not True:
                            raise ExecutorClientError("executor turn was not confirmed settled")
                        if type(event.get("code")) is not int or event["code"] != 0:
                            raise ExecutorClientError("executor turn did not complete successfully")
                yield event
            if first:
                raise ExecutorClientError("executor stream ended before the admission receipt")
            if not terminal:
                raise ExecutorClientError("executor stream ended without a settled terminal event")

    def cancel(
        self,
        *,
        task_id: str,
        run_id: str,
        session_id: str,
        admission_id: str | None,
        execution_generation: int | None,
    ) -> dict[str, Any]:
        """Cancel only the exact task/run/session/admission/generation tuple."""
        if execution_generation is not None and (type(execution_generation) is not int or execution_generation < 0):
            raise ValueError("execution_generation must be null or a non-negative integer")
        payload = {
            "task_id": _validate_identifier(task_id, "task_id"),
            "run_id": _validate_identifier(run_id, "run_id"),
            "session_id": _validate_identifier(session_id, "session_id"),
            "admission_id": (
                _validate_identifier(admission_id, "admission_id")
                if admission_id is not None else None
            ),
            "execution_generation": execution_generation,
        }
        return self._post_json("/v1/cancel", payload, self.request_timeout)

    def _request(self, route: str, body: bytes, content_type: str, accept: str) -> urllib.request.Request:
        return urllib.request.Request(
            self.base_url + route,
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": content_type,
                "Accept": accept,
                "Connection": "close",
            },
            method="POST",
        )

    @staticmethod
    def _encode_json(payload: dict[str, Any]) -> bytes:
        try:
            encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError):
            raise ValueError("request payload is not valid JSON") from None
        if len(encoded) > _MAX_JSON_BYTES:
            raise ValueError("request JSON exceeds 4 MiB")
        return encoded

    def _post_json(self, route: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        request = self._request(route, self._encode_json(payload), "application/json", "application/json")
        try:
            response = self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            raise ExecutorClientError(f"executor request returned HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            raise ExecutorClientError(f"executor request transport failed ({type(exc).__name__}); POST was not retried") from None
        with response:
            if response.status < 200 or response.status >= 300:
                raise ExecutorClientError(f"executor request returned HTTP {response.status}")
            raw = response.read(_MAX_JSON_BYTES + 1)
        if len(raw) > _MAX_JSON_BYTES:
            raise ExecutorClientError("executor JSON response exceeds 4 MiB")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ExecutorClientError("executor returned invalid JSON") from None
        if not isinstance(decoded, dict):
            raise ExecutorClientError("executor JSON response must be an object")
        return decoded
