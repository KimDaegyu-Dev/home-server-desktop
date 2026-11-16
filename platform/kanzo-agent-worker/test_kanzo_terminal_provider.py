"""Tests for Kanzo broker lifecycle hooks without importing Hermes runtime code."""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_provider():
    agent = types.ModuleType("agent")
    agent.__path__ = []
    terminal = types.ModuleType("agent.terminal_env_provider")
    terminal.TerminalEnvironmentProvider = type("TerminalEnvironmentProvider", (), {})
    gateway = types.ModuleType("gateway")
    gateway.__path__ = []
    gateway.session_context = types.SimpleNamespace(get_session_env=lambda name, default="": default)
    modules = {
        "agent": agent,
        "agent.terminal_env_provider": terminal,
        "gateway": gateway,
        "gateway.session_context": gateway.session_context,
    }
    source = Path(__file__).with_name("kanzo_terminal_provider.py")
    spec = importlib.util.spec_from_file_location("kanzo_terminal_provider_tested", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class KanzoDelegateHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = _load_provider()

    def test_subagent_hooks_register_and_close_exact_session_pair(self) -> None:
        calls = []

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            def post(self, path, payload):
                calls.append((path, payload))
                return {"state": "bound"}

        env = {"KANZO_BROKER_URL": "http://broker.svc:8080", "KANZO_BROKER_TOKEN": "opaque-test-token"}
        with (patch.dict(os.environ, env),
              patch.object(self.provider, "_kanzo_automation_active", return_value=True),
              patch.object(self.provider, "_BrokerClient", Client)):
            self.provider._register_delegated_child(
                parent_session_id="root-session", child_session_id="child-session"
            )
            self.provider._close_delegated_child(
                parent_session_id="root-session", child_session_id="child-session"
            )

        self.assertEqual(calls, [
            ("/v1/terminal/child/register", {
                "parent_session_id": "root-session", "child_session_id": "child-session",
            }),
            ("/v1/terminal/child/close", {
                "parent_session_id": "root-session", "child_session_id": "child-session",
            }),
        ])

    def test_hook_does_not_register_non_kanzo_sessions(self) -> None:
        with patch.object(self.provider, "_kanzo_automation_active", return_value=False), \
                patch.object(self.provider, "_BrokerClient") as client:
            self.provider._register_delegated_child(
                parent_session_id="root-session", child_session_id="child-session"
            )
        client.assert_not_called()

    def test_child_stop_restores_only_the_task_local_parent_session(self) -> None:
        calls = []

        class SessionVar:
            def __init__(self):
                self.value = "child-session"

            def set(self, value):
                self.value = value
                calls.append(("contextvar", value))

        session_var = SessionVar()
        context = types.SimpleNamespace(
            get_session_env=lambda name, default="": session_var.value,
            _SESSION_ID=session_var,
        )

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            def post(self, path, payload):
                calls.append((path, payload))
                return {"state": "closed"}

        env = {"KANZO_BROKER_URL": "http://broker.svc:8080", "KANZO_BROKER_TOKEN": "opaque-test-token"}
        with (patch.dict(os.environ, env),
              patch.object(self.provider, "session_context", context),
              patch.object(self.provider, "_kanzo_automation_active", return_value=True),
              patch.object(self.provider, "_BrokerClient", Client),
              patch.object(self.provider._LOG, "warning")):
            self.provider._close_delegated_child(
                parent_session_id="root-session", child_session_id="child-session"
            )

        self.assertEqual(session_var.value, "root-session")
        self.assertIn(("contextvar", "root-session"), calls)

    def test_child_stop_does_not_restore_if_broker_revocation_fails(self) -> None:
        class SessionVar:
            value = "child-session"

            def set(self, value):
                self.value = value

        session_var = SessionVar()
        context = types.SimpleNamespace(
            get_session_env=lambda name, default="": session_var.value,
            _SESSION_ID=session_var,
        )

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            def post(self, path, payload):
                raise RuntimeError("broker unavailable")

        env = {"KANZO_BROKER_URL": "http://broker.svc:8080", "KANZO_BROKER_TOKEN": "opaque-test-token"}
        with (patch.dict(os.environ, env),
              patch.object(self.provider, "session_context", context),
              patch.object(self.provider, "_kanzo_automation_active", return_value=True),
              patch.object(self.provider, "_BrokerClient", Client),
              patch.object(self.provider._LOG, "warning")):
            self.provider._close_delegated_child(
                parent_session_id="root-session", child_session_id="child-session"
            )

        self.assertEqual(session_var.value, "child-session")


if __name__ == "__main__":
    unittest.main()
