"""Regression tests for Kanzo metadata bound to each Gateway session."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import gateway_session


class _GatewayClient:
    def __init__(self) -> None:
        self.created_with = None

    async def rpc(self, method: str, **kwargs):
        if method == "runtime.describe":
            return {"session_create": {
                "parameters": ["model", "provider", "reasoning", "toolsets", "source", "cwd", "client_env"],
                "sources": ["oneshot"],
            }}
        if method == "session.create":
            self.created_with = kwargs
            return {"stored_session_id": "session-1", "session_id": "session-1", "execution_generation": 0}
        raise AssertionError(f"unexpected RPC method: {method}")


class _Connected:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_args):
        return False


class GatewaySessionTests(unittest.TestCase):
    def test_session_create_rpc_carries_only_run_scoped_identity(self) -> None:
        client = _GatewayClient()
        endpoint = types.SimpleNamespace(profile_id=None)
        fake_modules = {}

        def module(name: str):
            value = types.ModuleType(name)
            fake_modules[name] = value
            return value

        agent = module("agent")
        agent.__path__ = []
        session_source = module("agent.session_source")
        session_source.create_surface = lambda _name: "oneshot"
        hermes_cli = module("hermes_cli")
        hermes_cli.__path__ = []
        gateway_chat = module("hermes_cli.gateway_chat")
        gateway_chat._requested_policy = lambda args: {
            "model": args.model,
            "provider": args.provider,
            "reasoning": args.reasoning,
            "toolsets": args.toolsets,
        }
        gateway_chat.client_env_policy = lambda _params: {
            "client_env": {
                "KANZO_TASK_ID": "stale-task",
                "KANZO_RUN_ID": "stale-run",
                "KANZO_BROKER_WORKER_TOKEN": "must-not-cross-boundary",
            }
        }
        gateway_client = module("hermes_cli.gateway_client")
        gateway_client.connect_gateway = lambda **_kwargs: _Connected(client)
        runtime = module("hermes_cli.gateway_runtime")

        def discover(_profile_home, timeout):
            self.assertEqual(timeout, 2.0)
            endpoint.profile_id = str(profile_home)
            return types.SimpleNamespace(endpoint=endpoint, state="ready")

        runtime.discover_gateway_endpoint = discover

        with tempfile.TemporaryDirectory() as temp:
            profile_home = Path(temp) / "profile"
            cwd = Path(temp) / "workspace"
            profile_home.mkdir()
            cwd.mkdir()
            with patch.dict(sys.modules, fake_modules):
                result = asyncio.run(gateway_session._create_session(
                    profile_home=profile_home,
                    task_id="project#41",
                    run_id="run-41",
                    model="iq_3s",
                    provider="openai-api",
                    reasoning="high",
                    toolsets=["terminal"],
                    cwd=cwd,
                    request_id="request-41",
                ))

        self.assertEqual(result["session_id"], "session-1")
        self.assertEqual(client.created_with["request_id"], "request-41")
        env = client.created_with["client_env"]
        self.assertEqual(env["KANZO_TASK_ID"], "project#41")
        self.assertEqual(env["KANZO_RUN_ID"], "run-41")
        self.assertEqual(env["KANZO_USER_ID"], "kanzo-owner")
        self.assertEqual(env["HERMES_KANBAN_TASK"], "project#41")
        self.assertEqual(env["HERMES_KANBAN_RUN_ID"], "run-41")
        self.assertEqual(env["HERMES_KANBAN_USER_ID"], "kanzo-owner")
        self.assertNotIn("KANZO_BROKER_WORKER_TOKEN", env)

    def test_binding_overwrites_stale_values_without_mutating_process_environment(self) -> None:
        env = {"KANZO_TASK_ID": "old-task", "KANZO_RUN_ID": "old-run"}
        process_env = {
            "KANZO_TASK_ID": os.environ.get("KANZO_TASK_ID"),
            "KANZO_RUN_ID": os.environ.get("KANZO_RUN_ID"),
        }
        gateway_session._bind_kanzo_session_env(env, "project#42", "run-42")
        self.assertEqual(env["KANZO_TASK_ID"], "project#42")
        self.assertEqual(env["KANZO_RUN_ID"], "run-42")
        self.assertEqual(env["KANZO_USER_ID"], "kanzo-owner")
        self.assertEqual(os.environ.get("KANZO_TASK_ID"), process_env["KANZO_TASK_ID"])
        self.assertEqual(os.environ.get("KANZO_RUN_ID"), process_env["KANZO_RUN_ID"])

    def test_user_identity_is_session_bound_and_rejects_invalid_value(self) -> None:
        env = {}
        gateway_session._bind_kanzo_session_env(env, "project#43", "run-43", "stable-owner")
        self.assertEqual(env["KANZO_USER_ID"], "stable-owner")
        self.assertEqual(env["HERMES_KANBAN_USER_ID"], "stable-owner")
        with self.assertRaises(ValueError):
            gateway_session._bind_kanzo_session_env({}, "project#43", "run-44", "bad\nidentity")


if __name__ == "__main__":
    unittest.main()
