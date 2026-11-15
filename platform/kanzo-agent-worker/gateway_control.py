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
    requested_admission_id: str | None,
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
                        if requested_admission_id is not None:
                            matches = [
                                item for item in pending
                                if item.get("admission_id") == requested_admission_id
                            ]
                            if len(matches) != 1:
                                reason = "admission_not_found_or_ambiguous"
                            elif matches[0].get("status") == "queued":
                                admission_id = requested_admission_id
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
                            elif matches[0].get("status") == "started":
                                target_generation = matches[0].get("execution_generation")
                                if type(target_generation) is not int or target_generation < 0:
                                    reason = "admission_generation_unverified"
                                elif requested_generation is not None and requested_generation != target_generation:
                                    reason = "generation_mismatch"
                                elif snapshot.get("running") is not True:
                                    reason = "admission_not_running"
                                else:
                                    generation = target_generation
                                    action = "session.interrupt"
                                    try:
                                        handle = await client.rpc(
                                            "session.interrupt",
                                            session_id=session_id,
                                            execution_generation=target_generation,
                                        )
                                    except Exception:
                                        reason = "session_interrupt_failed"
                                    else:
                                        accepted = (
                                            isinstance(handle, dict)
                                            and handle.get("execution_generation") == target_generation
                                        )
                                        if not accepted:
                                            reason = "session_interrupt_unconfirmed"
                            else:
                                reason = "admission_not_active"
                        else:
                            if requested_generation is not None and requested_generation != generation:
                                reason = "generation_mismatch"
                            queued = [item for item in pending if item.get("status") == "queued"]
                            if reason is None and queued:
                                if len(queued) != 1:
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
                            elif reason is None:
                                started = any(
                                    item.get("status") == "started"
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


async def _reconcile_via_gateway(
    profile_home: Path,
    session_id: str,
    admission_id: str,
    execution_generation: int | None,
) -> dict[str, Any]:
    """Cancel one exact queued/running admission and prove its terminal receipt."""
    from hermes_cli.gateway_runtime import discover_gateway_endpoint
    from hermes_cli.gateway_client import connect_gateway

    discovery = await asyncio.to_thread(discover_gateway_endpoint, profile_home, timeout=2.0)
    endpoint = getattr(discovery, "endpoint", None)
    if endpoint is None or getattr(discovery, "state", None) != "ready":
        return _outcome(False, "none", "gateway_unavailable")
    try:
        if os.path.normcase(str(profile_home.resolve(strict=True))) != os.path.normcase(
            str(Path(endpoint.profile_id).resolve(strict=True))
        ):
            return _outcome(False, "none", "profile_mismatch")
    except (OSError, RuntimeError, TypeError, ValueError):
        return _outcome(False, "none", "profile_unverified")

    try:
        async with connect_gateway(endpoint=endpoint) as client:
            try:
                snapshot = await client.rpc("session.resume", session_id=session_id, omit_messages=True)
            except Exception:
                return _outcome(False, "none", "session_resume_failed")
            if (not isinstance(snapshot, dict)
                    or snapshot.get("stored_session_id") != session_id
                    or snapshot.get("session_id") != session_id):
                return _outcome(False, "none", "session_unverified")
            subscription = snapshot.get("subscription_id")
            if not isinstance(subscription, str) or not subscription:
                return _outcome(False, "none", "subscription_unverified")
            try:
                pending = snapshot.get("pending")
                if not isinstance(pending, list):
                    return _outcome(False, "none", "pending_state_unverified")
                matches = [item for item in pending if isinstance(item, dict)
                           and item.get("admission_id") == admission_id]
                if not matches:
                    # Completed admissions are no longer present in `pending`.
                    # Ask the Gateway for the exact durable receipt before leaving
                    # a terminal run ambiguous or attempting any cancellation.
                    try:
                        receipt = await client.rpc(
                            "prompt.receipt", session_id=session_id,
                            admission_id=admission_id, include_result=False,
                        )
                    except Exception:
                        return _outcome(False, "none", "admission_not_found_or_ambiguous",
                                        execution_generation, session_id=session_id)
                    receipt_generation = (
                        receipt.get("execution_generation") if isinstance(receipt, dict) else None
                    )
                    if (not isinstance(receipt, dict)
                            or receipt.get("admission_id") != admission_id
                            or receipt.get("status") != "terminal"
                            or type(receipt_generation) is not int or receipt_generation < 0
                            or (execution_generation is not None
                                and receipt_generation != execution_generation)):
                        return _outcome(False, "none", "admission_not_found_or_ambiguous",
                                        execution_generation, session_id=session_id)
                    return {
                        "accepted": True, "action": "already_terminal", "terminal": True,
                        "outcome": receipt.get("outcome", "unknown"),
                        "execution_generation": receipt_generation, "session_id": session_id,
                    }
                if len(matches) != 1:
                    return _outcome(False, "none", "admission_not_found_or_ambiguous")
                item = matches[0]
                status = item.get("status")
                action = "already_terminal"
                if status == "queued":
                    action = "prompt.cancel"
                    try:
                        receipt = await client.rpc("prompt.cancel", session_id=session_id,
                                                   admission_id=admission_id)
                    except Exception:
                        return _outcome(False, action, "prompt_cancel_failed", execution_generation, session_id=session_id)
                    if (not isinstance(receipt, dict) or receipt.get("admission_id") != admission_id
                            or receipt.get("status") != "terminal" or receipt.get("outcome") != "cancelled"):
                        return _outcome(False, action, "prompt_cancel_unconfirmed", execution_generation, session_id=session_id)
                    return {"accepted": True, "action": action, "terminal": True,
                            "outcome": "cancelled", "execution_generation": execution_generation,
                            "session_id": session_id}
                elif status == "started":
                    if (execution_generation is None or item.get("execution_generation") != execution_generation
                            or snapshot.get("running") is not True):
                        return _outcome(False, "none", "generation_or_running_state_mismatch", execution_generation, session_id=session_id)
                    action = "session.interrupt"
                    try:
                        handle = await client.rpc("session.interrupt", session_id=session_id,
                                                  execution_generation=execution_generation)
                    except Exception:
                        return _outcome(False, action, "session_interrupt_failed", execution_generation, session_id=session_id)
                    if not isinstance(handle, dict) or handle.get("execution_generation") != execution_generation:
                        return _outcome(False, action, "session_interrupt_unconfirmed", execution_generation, session_id=session_id)
                elif status == "terminal":
                    item_generation = item.get("execution_generation")
                    if (execution_generation is None or item_generation != execution_generation):
                        return _outcome(False, "none", "generation_mismatch", execution_generation, session_id=session_id)
                else:
                    return _outcome(False, "none", "admission_state_unverified", execution_generation, session_id=session_id)

                deadline = asyncio.get_running_loop().time() + (0 if status == "queued" else 12)
                while True:
                    try:
                        receipt = await client.rpc("prompt.receipt", session_id=session_id,
                                                   admission_id=admission_id, include_result=False)
                    except Exception:
                        receipt = None
                    if (isinstance(receipt, dict) and receipt.get("admission_id", admission_id) == admission_id
                            and receipt.get("status") == "terminal"
                            and type(receipt.get("execution_generation")) is int
                            and receipt.get("execution_generation") >= 0
                            and (execution_generation is None
                                 or receipt.get("execution_generation") == execution_generation)):
                        return {"accepted": True, "action": action, "terminal": True,
                                "outcome": receipt.get("outcome", "unknown"),
                                "execution_generation": receipt["execution_generation"], "session_id": session_id}
                    if asyncio.get_running_loop().time() >= deadline:
                        return _outcome(False, action, "terminal_state_unproven", execution_generation, session_id=session_id)
                    await asyncio.sleep(0.5)
            finally:
                try:
                    await client.rpc("session.detach", session_id=session_id, subscription_id=subscription)
                except Exception:
                    pass
    except Exception:
        return _outcome(False, "none", "gateway_unavailable", execution_generation, session_id=session_id)


def reconcile_session(
    profile_home: str | os.PathLike[str], session_id: str, admission_id: str,
    execution_generation: int | None,
) -> dict[str, Any]:
    """Fail-closed startup recovery for one durably identified submit."""
    for value, maximum in ((session_id, 256), (admission_id, 256)):
        if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
            return _outcome(False, "none", "identity_invalid")
    if execution_generation is not None and (type(execution_generation) is not int or execution_generation < 0):
        return _outcome(False, "none", "generation_invalid")
    try:
        home = Path(profile_home).expanduser().resolve(strict=True)
        if not home.is_dir():
            return _outcome(False, "none", "profile_invalid")
        _prepare_hermes()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return _outcome(False, "none", "event_loop_active")
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
        token = set_hermes_home_override(home)
        try:
            return asyncio.run(_reconcile_via_gateway(home, session_id, admission_id, execution_generation))
        finally:
            reset_hermes_home_override(token)
    except Exception:
        return _outcome(False, "none", "gateway_unavailable")


def interrupt_session(
    profile_home: str | os.PathLike[str],
    session_id: str,
    execution_generation: int | None = None,
    admission_id: str | None = None,
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
    if admission_id is not None and (
        not isinstance(admission_id, str) or not admission_id or len(admission_id) > 256
        or any(ord(char) < 32 for char in admission_id)
    ):
        return _outcome(False, "none", "admission_id_invalid")
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
            return asyncio.run(_interrupt_via_gateway(home, session_id, execution_generation, admission_id))
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
    interrupt_parser.add_argument("--admission-id")
    reconcile_parser = subparsers.add_parser("reconcile")
    reconcile_parser.add_argument("--profile-home", required=True)
    reconcile_parser.add_argument("--session-id", required=True)
    reconcile_parser.add_argument("--admission-id", required=True)
    reconcile_parser.add_argument("--generation", type=int)
    args = parser.parse_args(argv)

    if args.command == "reconcile":
        result = reconcile_session(args.profile_home, args.session_id, args.admission_id, args.generation)
    else:
        result = interrupt_session(args.profile_home, args.session_id, args.generation, args.admission_id)
    sys.stdout.write(json.dumps(result, separators=(",", ":"), ensure_ascii=True) + "\n")
    return 0 if result["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
