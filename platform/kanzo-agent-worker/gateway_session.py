#!/usr/bin/env python3
"""Create a prepared, empty Hermes Gateway session for a Kanzo run.

This helper deliberately stops after ``session.create``. The caller must persist
the returned session ID and bind it to the run's broker lease before submitting
the prompt. That ordering ensures the first terminal tool call cannot race ahead
of its sandbox authorization.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


HERMES_ROOT = Path("/opt/hermes")


def _bind_kanzo_session_env(
    client_env: dict[str, Any], task_id: str, run_id: str, user_id: str | None = None,
) -> None:
    """Bind Kanzo attribution to this Gateway session, never the process env."""
    bound_user_id = user_id if user_id is not None else os.environ.get("KANZO_LANGFUSE_USER_ID", "kanzo-owner")
    bound_user_id = bound_user_id.strip() or "kanzo-owner"
    if len(bound_user_id) > 200 or any(ord(char) < 32 for char in bound_user_id):
        raise ValueError("invalid Langfuse user identity")
    for secret_name in (
        "KANZO_BROKER_WORKER_TOKEN",
        "KANZO_BROKER_TOKEN",
        "KANZO_BROKER_LEASE_KEY",
    ):
        client_env.pop(secret_name, None)
    client_env.update({
        "KANZO_AUTOMATED_RUN": "1",
        "KANZO_TASK_ID": task_id,
        "KANZO_RUN_ID": run_id,
        "KANZO_USER_ID": bound_user_id,
        "HERMES_KANBAN_TASK": task_id,
        "HERMES_KANBAN_RUN_ID": run_id,
        "HERMES_KANBAN_USER_ID": bound_user_id,
    })


def _prepare_imports() -> None:
    root = str(HERMES_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


async def _create_session(
    *,
    profile_home: Path,
    task_id: str,
    run_id: str,
    model: str,
    provider: str,
    reasoning: str,
    toolsets: list[str],
    cwd: Path,
    request_id: str,
) -> dict[str, Any]:
    _prepare_imports()
    from agent.session_source import create_surface
    from hermes_cli.gateway_chat import _requested_policy, client_env_policy
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

    args = SimpleNamespace(
        model=model,
        provider=provider,
        reasoning=reasoning,
        toolsets=toolsets,
    )
    policy = _requested_policy(args)
    policy["source"] = create_surface("oneshot")

    async with connect_gateway(endpoint=endpoint) as client:
        description = await client.rpc("runtime.describe")
        contract = description.get("session_create") if isinstance(description, dict) else None
        if not isinstance(contract, dict):
            raise RuntimeError("gateway_session_create_contract_missing")
        parameters = contract.get("parameters")
        sources = contract.get("sources")
        if not isinstance(parameters, list) or not isinstance(sources, list):
            raise RuntimeError("gateway_session_create_contract_invalid")
        if "oneshot" not in sources:
            raise RuntimeError("gateway_oneshot_source_unsupported")

        if "cwd" in parameters:
            policy["cwd"] = str(cwd.resolve(strict=True))
        policy.update(client_env_policy(parameters))
        client_env = policy.get("client_env")
        if not isinstance(client_env, dict):
            raise RuntimeError("gateway_session_client_env_unsupported")
        _bind_kanzo_session_env(client_env, task_id, run_id)
        unsupported = sorted(set(policy) - set(parameters))
        if unsupported:
            raise RuntimeError("gateway_session_policy_unsupported:" + ",".join(unsupported))

        snapshot = await client.rpc(
            "session.create",
            request_id=request_id,
            **policy,
        )

    if not isinstance(snapshot, dict):
        raise RuntimeError("gateway_session_create_response_invalid")
    stored_id = snapshot.get("stored_session_id")
    runtime_id = snapshot.get("session_id")
    generation = snapshot.get("execution_generation")
    if (
        not isinstance(stored_id, str)
        or not stored_id
        or runtime_id != stored_id
        or type(generation) is not int
        or generation < 0
    ):
        raise RuntimeError("gateway_session_identity_unverified")
    return {
        "session_id": stored_id,
        "execution_generation": generation,
        "profile_home": str(profile_home.resolve(strict=True)),
        "source": "oneshot",
    }


def create_session(
    *,
    profile_home: str | os.PathLike[str],
    task_id: str,
    run_id: str,
    model: str,
    provider: str,
    reasoning: str,
    toolsets: list[str],
    cwd: str | os.PathLike[str],
    request_id: str,
) -> dict[str, Any]:
    """Create a new canonical Gateway session without submitting a prompt."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("gateway_session_helper_event_loop_active")
    home = Path(profile_home).expanduser().resolve(strict=True)
    working_dir = Path(cwd).expanduser().resolve(strict=True)
    if not home.is_dir() or not working_dir.is_dir():
        raise RuntimeError("gateway_session_path_invalid")
    if not model.strip() or not provider.strip() or not reasoning.strip():
        raise RuntimeError("gateway_session_route_invalid")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128 or any(
        ord(char) < 0x20 for char in request_id
    ):
        raise RuntimeError("gateway_session_request_id_invalid")
    if (
        not task_id.strip() or len(task_id) > 256
        or not run_id.strip() or len(run_id) > 128
        or any(ord(char) < 0x20 for char in task_id + run_id)
    ):
        raise RuntimeError("gateway_session_run_identity_invalid")
    if not isinstance(toolsets, list) or any(not isinstance(item, str) or not item.strip() for item in toolsets):
        raise RuntimeError("gateway_session_toolsets_invalid")
    _prepare_imports()
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home_token = set_hermes_home_override(home)
    try:
        return asyncio.run(
            _create_session(
                profile_home=home,
                task_id=task_id.strip(),
                run_id=run_id.strip(),
                model=model.strip(),
                provider=provider.strip(),
                reasoning=reasoning.strip(),
                toolsets=[item.strip() for item in toolsets],
                cwd=working_dir,
                request_id=request_id,
            )
        )
    finally:
        reset_hermes_home_override(home_token)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-home", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--reasoning", required=True)
    parser.add_argument("--toolsets", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args()
    try:
        result = create_session(
            profile_home=args.profile_home,
            task_id=args.task_id,
            run_id=args.run_id,
            model=args.model,
            provider=args.provider,
            reasoning=args.reasoning,
            toolsets=[part.strip() for part in args.toolsets.split(",") if part.strip()],
            cwd=args.cwd,
            request_id=args.request_id,
        )
    except Exception as exc:
        # Gateway exceptions can include server response details. Emit only
        # this helper's fixed diagnostics or an exception type, never response
        # bodies, configuration, credentials, or session content.
        message = str(exc)
        safe = (
            message if message.startswith("gateway_") and len(message) <= 240
            else f"gateway_session_create_failed:{type(exc).__name__}"
        )
        print(json.dumps({"ok": False, "error": safe}, separators=(",", ":")))
        return 1
    print(json.dumps({"ok": True, **result}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
