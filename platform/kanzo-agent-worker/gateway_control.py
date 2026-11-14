#!/usr/bin/env python3
"""Safely control a Kanzo run through its profile's Hermes canonical Gateway."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


_HERMES_ROOT = Path("/opt/hermes")


def _prepare_hermes() -> None:
    """Make Hermes modules importable independent of the helper's current working directory."""
    root = str(_HERMES_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _outcome(
    accepted: bool,
    action: str,
    reason: str | None = None,
    execution_generation: int | None = None,
    subscription_detached: bool | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"accepted": accepted, "action": action}
    if reason:
        result["reason"] = reason
    if session_id is not None:
        result["session_id"] = session_id
    if execution_generation is not None:
        result["execution_generation"] = execution_generation
    if subscription_detached is not None:
        result["subscription_detached"] = subscription_detached
    return result


async def _interrupt_via_gateway(
    profile_home: Path,
    session_id: str,
    requested_generation: int | None,
) -> dict[str, Any]:
    from hermes_cli.gateway_runtime import discover_gateway_endpoint
    from hermes_cli.gateway_client import connect_gateway

    discovery = await asyncio.to_thread(discover_gateway_endpoint, profile_home, timeout=2.0)
    endpoint = getattr(discovery, "endpoint", None)
    state = getattr(discovery, "state", None)
    if endpoint is None or state != "ready":
        safe_states = {"missing", "starting", "draining", "inaccessible", "incompatible", "conflict"}
        state_name = state if isinstance(state, str) else "inaccessible"
        suffix = state_name if state_name in safe_states else "inaccessible"
        return _outcome(False, "none", f"gateway_{suffix}")

    # Discovery validates the owner; this additional comparison prevents a valid endpoint for a
    # different profile from being used if discovery's contract changes or is wrapped incorrectly.
    try:
        requested_home = os.path.normcase(str(profile_home.resolve(strict=True)))
        endpoint_home = os.path.normcase(str(Path(endpoint.profile_id).resolve(strict=True)))
    except (OSError, RuntimeError, TypeError, ValueError):
        return _outcome(False, "none", "profile_unverified")
    if requested_home != endpoint_home:
        return _outcome(False, "none", "profile_mismatch")

    snapshot: dict[str, Any] | None = None
    detach_session_id: str | None = None
    subscription_id: str | None = None
    generation: int | None = None
    verified_session_id: str | None = None
    action = "none"
    accepted = False
    reason: str | None = None
    detached: bool | None = None

    try:
        async with connect_gateway(endpoint=endpoint) as client:
            try:
                value = await client.rpc(
                    "session.resume",
                    session_id=session_id,
                    omit_messages=True,
                )
            except Exception:
                return _outcome(False, "none", "session_resume_failed")

            if not isinstance(value, dict):
                return _outcome(False, "none", "session_snapshot_invalid")
            snapshot = value
            returned_id = snapshot.get("stored_session_id")
            runtime_session_id = snapshot.get("session_id")
            candidate_subscription = snapshot.get("subscription_id")
            # Remember only the exact subscription returned by this helper's resume on this
            # connection. Even an unverified snapshot can then be detached without touching a
            # worker CLI subscription.
            if isinstance(runtime_session_id, str) and runtime_session_id:
                detach_session_id = runtime_session_id
            if isinstance(candidate_subscription, str) and candidate_subscription:
                subscription_id = candidate_subscription

            try:
                if (
                    returned_id != session_id
                    or runtime_session_id != session_id
                    or not subscription_id
                ):
                    reason = "session_unverified"
                else:
                    verified_session_id = session_id
                    generation_value = snapshot.get("execution_generation")
                    if type(generation_value) is not int or generation_value < 0:
                        reason = "generation_unverified"
                    else:
                        generation = generation_value

                    if reason is None and requested_generation is not None and requested_generation != generation:
                        reason = "generation_mismatch"

                    pending = snapshot.get("pending")
                    if (
                        reason is None
                        and (
                            not isinstance(pending, list)
                            or any(
                                not isinstance(item, dict)
                                or item.get("status") not in {"queued", "started", "terminal"}
                                or not isinstance(item.get("admission_id"), str)
                                or not item.get("admission_id")
                                for item in pending
                            )
                            or type(snapshot.get("running")) is not bool
                        )
                    ):
                        reason = "pending_state_unverified"

                    if reason is None:
                        queued = [
                            item for item in pending
                            if isinstance(item, dict) and item.get("status") == "queued"
                        ]
                        if queued:
                            if (
                                len(queued) != 1
                                or not isinstance(queued[0].get("admission_id"), str)
                                or not queued[0].get("admission_id")
                            ):
                                reason = "queued_admission_ambiguous"
                            else:
                                admission_id = queued[0]["admission_id"]
                                action = "prompt.cancel"
                                try:
                                    receipt = await client.rpc(
                                        "prompt.cancel",
                                        session_id=session_id,
                                        admission_id=admission_id,
                                    )
                                except Exception:
                                    reason = "prompt_cancel_failed"
                                else:
                                    accepted = (
                                        isinstance(receipt, dict)
                                        and receipt.get("admission_id") == admission_id
                                        and receipt.get("status") == "terminal"
                                        and receipt.get("outcome") == "cancelled"
                                    )
                                    if not accepted:
                                        reason = "prompt_cancel_unconfirmed"
                        else:
                            started = any(
                                isinstance(item, dict)
                                and item.get("status") == "started"
                                and item.get("execution_generation") == generation
                                for item in pending
                            )
                            if snapshot.get("running") is True or started:
                                action = "session.interrupt"
                                try:
                                    handle = await client.rpc(
                                        "session.interrupt",
                                        session_id=session_id,
                                        execution_generation=generation,
                                    )
                                except Exception:
                                    reason = "session_interrupt_failed"
                                else:
                                    accepted = (
                                        isinstance(handle, dict)
                                        and handle.get("execution_generation") == generation
                                    )
                                    if not accepted:
                                        reason = "session_interrupt_unconfirmed"
                            else:
                                reason = "session_idle"
            finally:
                if detach_session_id and subscription_id:
                    try:
                        detach_result = await client.rpc(
                            "session.detach",
                            session_id=detach_session_id,
                            subscription_id=subscription_id,
                        )
                        detached = isinstance(detach_result, dict) and detach_result.get("detached") is True
                    except Exception:
                        detached = False
    except Exception:
        return _outcome(
            False,
            action,
            reason or "gateway_unavailable",
            generation,
            detached,
            verified_session_id,
        )
    if detached is False and reason is None:
        reason = "subscription_detach_failed"
    return _outcome(accepted, action, reason, generation, detached, verified_session_id)


def interrupt_session(
    profile_home: str | os.PathLike[str],
    session_id: str,
    execution_generation: int | None = None,
) -> dict[str, Any]:
    """Cancel this stored Hermes session through its verified profile Gateway.

    A queued canonical admission is cancelled by its admission ID. A running turn is interrupted
    only with the execution generation from ``session.resume``. All refusals return a small,
    secret-free JSON-safe object.
    """
    if not isinstance(session_id, str) or not session_id or len(session_id) > 256 or any(
        ord(char) < 32 for char in session_id
    ):
        return _outcome(False, "none", "session_id_invalid")
    if execution_generation is not None and (
        type(execution_generation) is not int or execution_generation < 0
    ):
        return _outcome(False, "none", "generation_invalid")
    try:
        home = Path(profile_home).expanduser().resolve(strict=True)
        if not home.is_dir():
            return _outcome(False, "none", "profile_invalid")
    except (OSError, RuntimeError, TypeError, ValueError):
        return _outcome(False, "none", "profile_invalid")

    # Use Hermes' context-local home override: it scopes ticket creation to this profile without
    # changing process environment observed by a resident worker or its other Hermes operations.
    _prepare_hermes()
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return _outcome(False, "none", "event_loop_active")
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        token = set_hermes_home_override(home)
        try:
            return asyncio.run(_interrupt_via_gateway(home, session_id, execution_generation))
        finally:
            reset_hermes_home_override(token)
    except Exception:
        return _outcome(False, "none", "gateway_unavailable")


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message  # argparse detail can include caller-controlled input; keep stderr/stdout bounded.
        sys.stdout.write(json.dumps(_outcome(False, "none", "arguments_invalid"), separators=(",", ":")) + "\n")
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = _JsonArgumentParser(description="Safely interrupt a Hermes canonical Gateway session")
    subparsers = parser.add_subparsers(dest="command", required=True)
    interrupt_parser = subparsers.add_parser("interrupt")
    interrupt_parser.add_argument("--profile-home", required=True)
    interrupt_parser.add_argument("--session-id", required=True)
    interrupt_parser.add_argument("--generation", type=int)
    args = parser.parse_args(argv)

    result = interrupt_session(
        args.profile_home,
        args.session_id,
        args.generation,
    )
    sys.stdout.write(json.dumps(result, separators=(",", ":"), ensure_ascii=True) + "\n")
    return 0 if result["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
