"""Submit one Kanzo turn to its pre-bound Hermes Gateway session.

The admission receipt is written to stdout immediately after ``prompt.submit``
so the resident worker can durably attach it to the run before consuming the
model's reply. This module emits a small JSONL protocol and never prints prompt
text or Gateway error bodies.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any


HERMES_ROOT = Path("/opt/hermes")
MAX_PROMPT_BYTES = 4 * 1024 * 1024


def _prepare_imports() -> None:
    root = str(HERMES_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":"), ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _committed_completion_text(payload: dict[str, Any], admission_id: str) -> str | None:
    """Return the durable result carried by a successful completion event."""
    if payload.get("admission_id") != admission_id:
        return None
    if str(payload.get("status", "")).lower() != "complete":
        return None
    if str(payload.get("outcome", "")).lower() not in {"completed", "success"}:
        return None
    text = payload.get("text") or payload.get("content")
    if not isinstance(text, str) or not text.strip():
        return None
    return text


async def execute(profile_home: Path, session_id: str, prompt_path: Path) -> int:
    _prepare_imports()
    prompt_bytes = prompt_path.read_bytes()
    if len(prompt_bytes) > MAX_PROMPT_BYTES:
        raise RuntimeError("prompt_too_large")
    prompt = prompt_bytes.decode("utf-8")

    from hermes_cli.gateway_client import connect_gateway
    from hermes_cli.gateway_runtime import discover_gateway_endpoint

    discovery = await asyncio.to_thread(discover_gateway_endpoint, profile_home, timeout=2.0)
    endpoint = getattr(discovery, "endpoint", None)
    if endpoint is None or getattr(discovery, "state", None) != "ready":
        raise RuntimeError("profile_gateway_not_ready")
    try:
        requested_home = os.path.normcase(str(profile_home.resolve(strict=True)))
        endpoint_home = os.path.normcase(str(Path(endpoint.profile_id).resolve(strict=True)))
    except (OSError, RuntimeError, TypeError, ValueError):
        raise RuntimeError("profile_gateway_unverified") from None
    if requested_home != endpoint_home:
        raise RuntimeError("profile_gateway_mismatch")

    async with connect_gateway(endpoint=endpoint) as client:
        snapshot = await client.rpc("session.resume", session_id=session_id, omit_messages=True)
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("stored_session_id") != session_id
            or snapshot.get("session_id") != session_id
        ):
            raise RuntimeError("gateway_session_identity_mismatch")
        pending = snapshot.get("pending")
        if not isinstance(pending, list) or pending:
            raise RuntimeError("gateway_session_has_pending_admissions")

        receipt = await client.rpc(
            "prompt.submit",
            session_id=session_id,
            input_id=uuid.uuid4().hex,
            text=prompt,
            finite=True,
            unattended=True,
        )
        admission_id = receipt.get("admission_id") if isinstance(receipt, dict) else None
        if not isinstance(admission_id, str) or not admission_id:
            raise RuntimeError("gateway_admission_receipt_missing")
        event: dict[str, Any] = {
            "type": "admission",
            "session_id": session_id,
            "admission_id": admission_id,
        }
        _emit(event)

        deadline = asyncio.get_running_loop().time() + 2670
        final_event: dict[str, Any] | None = None
        while asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            try:
                incoming = await asyncio.wait_for(client.events.get(), timeout=min(remaining, 30))
            except asyncio.TimeoutError:
                # Keep HTTP/1.0 executor streams alive while a long-running tool call
                # produces no Gateway event. The dispatcher also uses this to renew
                # its run/sandbox lease and worker liveness timestamp.
                _emit({"type": "executor_heartbeat", "session_id": session_id})
                continue
            if isinstance(incoming, BaseException):
                raise RuntimeError("gateway_event_stream_failed")
            params = incoming.get("params") if isinstance(incoming, dict) else None
            if not isinstance(params, dict) or params.get("session_id") != session_id:
                continue
            kind = params.get("type")
            payload = params.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            event_admission = params.get("admission_id") or payload.get("admission_id")
            event_generation = params.get("execution_generation")
            if event_admission == admission_id and type(event_generation) is int and event_generation >= 0:
                _emit({
                    "type": "execution",
                    "session_id": session_id,
                    "admission_id": admission_id,
                    "execution_generation": event_generation,
                })
            if kind in {"approval.request", "clarify.request"}:
                if event_admission in (None, admission_id):
                    _emit({"type": "error_detail", "error": "Hermes requires human interaction."})
                    return 3
                continue
            if event_admission != admission_id:
                continue
            if kind == "message.delta":
                continue
            if kind == "message.complete":
                final_event = payload
                break

        if final_event is None:
            raise RuntimeError("gateway_turn_deadline_exceeded")

        text = _committed_completion_text(final_event, admission_id)
        if text is None:
            raise RuntimeError("gateway_turn_not_confirmed_successful")
        _emit({"type": "text", "text": text})
        _emit({"type": "executor_exit", "code": 0, "settled": True, "gateway_turn_settled": True})
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-home", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--query-file", required=True)
    args = parser.parse_args(argv)
    try:
        home = Path(args.profile_home).resolve(strict=True)
        session_id = args.session_id
        if not session_id or len(session_id) > 256 or any(ord(char) < 32 for char in session_id):
            raise RuntimeError("gateway_session_id_invalid")
        _prepare_imports()
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        home_token = set_hermes_home_override(home)
        try:
            return asyncio.run(execute(home, session_id, Path(args.query_file)))
        finally:
            reset_hermes_home_override(home_token)
    except Exception as exc:
        message = str(exc)
        safe = message if message.startswith(("gateway_", "profile_gateway_", "prompt_")) else f"gateway_execution_failed:{type(exc).__name__}"
        _emit({"type": "error_detail", "error": safe[:240]})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
