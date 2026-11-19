"""Tests for Kanzo broker lifecycle hooks without importing Hermes runtime code."""

from __future__ import annotations

import importlib.util
import os
import posixpath
import sys
import threading
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
              patch.object(self.provider, "_retire_delegated_child_environment"),
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
              patch.object(self.provider, "_retire_delegated_child_environment"),
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
              patch.object(self.provider, "_retire_delegated_child_environment"),
              patch.object(self.provider, "_BrokerClient", Client),
              patch.object(self.provider._LOG, "warning")):
            self.provider._close_delegated_child(
                parent_session_id="root-session", child_session_id="child-session"
            )

        self.assertEqual(session_var.value, "child-session")

    def test_child_stop_evicts_only_the_matching_cached_terminal_environment(self) -> None:
        calls = []

        class Environment:
            def __init__(self, session_id):
                self._hermes_backend_name = "kanzo_broker"
                self._session_id = session_id

            def cleanup(self):
                calls.append(("cleanup", self._session_id))

        child_env = Environment("child-session")
        parent_env = Environment("root-session")
        terminal_tool = types.SimpleNamespace(
            _active_environments={"child-key": child_env, "parent-key": parent_env},
            _last_activity={"child-key": 1.0, "parent-key": 2.0},
            _env_lock=threading.RLock(),
            _clear_file_ops_cache=lambda key: calls.append(("clear", key)),
        )
        tools_package = types.ModuleType("tools")
        tools_package.__path__ = []
        with patch.dict(sys.modules, {
            "tools": tools_package,
            "tools.terminal_tool": terminal_tool,
        }):
            self.provider._retire_delegated_child_environment({
                "parent_session_id": "root-session", "child_session_id": "child-session",
            })

        self.assertEqual(terminal_tool._active_environments, {"parent-key": parent_env})
        self.assertEqual(terminal_tool._last_activity, {"parent-key": 2.0})
        self.assertIn(("clear", "child-key"), calls)
        self.assertIn(("cleanup", "child-session"), calls)
        self.assertNotIn(("cleanup", "root-session"), calls)


class KanzoBrokerRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = _load_provider()

    def execute_with_broker(self, broker, **execute_kwargs):
        tools = types.ModuleType("tools")
        tools.__path__ = []
        interrupt = types.ModuleType("tools.interrupt")
        interrupt.is_interrupted = lambda: False
        interrupt.is_thread_interrupted = lambda _thread_id: False
        environments = types.ModuleType("tools.environments")
        environments.__path__ = []
        base = types.ModuleType("tools.environments.base")
        base.touch_activity_if_due = lambda *_args: None
        with patch.dict(sys.modules, {
            "tools": tools,
            "tools.interrupt": interrupt,
            "tools.environments": environments,
            "tools.environments.base": base,
        }), patch.object(self.provider.time, "sleep"), \
                patch.object(self.provider.os.path, "normpath", posixpath.normpath):
            env = self.provider.KanzoTerminalEnvironment(
                broker=broker,
                session_id="session-1",
                mapping={
                    "task_id": "test-1#1",
                    "run_id": "run-1",
                    "lease": "lease-1",
                    "environment_handle": "env-1",
                    "cwd": "/workspace",
                    "temp_dir": "/scratch",
                },
                cwd="/workspace",
                timeout=5,
            )
            return env.execute("echo hello", **execute_kwargs)

    def test_preflight_refusal_does_not_enter_hermes_retry_loop(self) -> None:
        class Broker:
            def __init__(self):
                self.calls = []

            def post(self, path, _payload):
                self.calls.append(path)
                raise AssertionError(f"broker must not be called: {path}")

        broker = Broker()
        result = self.execute_with_broker(broker, yield_handler=object())

        self.assertEqual(broker.calls, [])
        self.assertEqual(result["exit_code"], 1)
        self.assertTrue(result["kanzo_non_retryable"])
        self.assertIn("execution state=not_started", result["output"])

    def test_invalid_command_is_reported_as_not_started(self) -> None:
        broker = types.SimpleNamespace(post=lambda *_args: self.fail("broker must not be called"))
        tools = types.ModuleType("tools")
        tools.__path__ = []
        interrupt = types.ModuleType("tools.interrupt")
        interrupt.is_interrupted = lambda: False
        interrupt.is_thread_interrupted = lambda _thread_id: False
        with patch.dict(sys.modules, {"tools": tools, "tools.interrupt": interrupt}), \
                patch.object(self.provider.os.path, "normpath", posixpath.normpath):
            env = self.provider.KanzoTerminalEnvironment(
                broker=broker,
                session_id="session-1",
                mapping={
                    "task_id": "test-1#1", "run_id": "run-1", "lease": "lease-1",
                    "environment_handle": "env-1", "cwd": "/workspace", "temp_dir": "/scratch",
                },
                cwd="/workspace",
                timeout=5,
            )
            result = env.execute("")

        self.assertEqual(result["exit_code"], 1)
        self.assertTrue(result["kanzo_non_retryable"])
        self.assertIn("execution state=not_started", result["output"])

    def test_start_error_returns_non_retryable_result_after_one_request(self) -> None:
        class Broker:
            def __init__(self):
                self.calls = []

            def post(self, path, _payload):
                self.calls.append(path)
                raise self_error("broker returned HTTP 409")

        self_error = self.provider.KanzoBrokerError
        broker = Broker()
        result = self.execute_with_broker(broker)

        self.assertEqual(broker.calls, ["/v1/terminal/exec/start"])
        self.assertEqual(result["exit_code"], 1)
        self.assertTrue(result["kanzo_non_retryable"])
        self.assertIn("not retried automatically", result["output"])

    def test_poll_error_cancels_once_and_returns_without_replaying_start(self) -> None:
        class Broker:
            def __init__(self):
                self.calls = []

            def post(self, path, _payload):
                self.calls.append(path)
                if path.endswith("/start"):
                    return {"exec_id": "exec-1"}
                if path.endswith("/poll"):
                    raise self_error("broker poll unavailable")
                if path.endswith("/cancel"):
                    return {"state": "cancelled"}
                raise AssertionError(path)

        self_error = self.provider.KanzoBrokerError
        broker = Broker()
        result = self.execute_with_broker(broker)

        self.assertEqual(broker.calls, [
            "/v1/terminal/exec/start",
            "/v1/terminal/exec/poll",
            "/v1/terminal/exec/cancel",
        ])
        self.assertEqual(result["exit_code"], 1)
        self.assertTrue(result["kanzo_non_retryable"])
        self.assertIn("execution state=cancelled", result["output"])

    def test_completed_state_returns_final_result_instead_of_replaying(self) -> None:
        class Broker:
            def __init__(self):
                self.calls = []
                self.polls = 0

            def post(self, path, _payload):
                self.calls.append(path)
                if path.endswith("/start"):
                    return {"exec_id": "exec-1"}
                if path.endswith("/poll"):
                    self.polls += 1
                    if self.polls == 1:
                        raise self_error("transient poll error")
                    return {"state": "completed", "exit_code": 0, "output_chunk": "done"}
                if path.endswith("/cancel"):
                    return {"state": "completed"}
                raise AssertionError(path)

        self_error = self.provider.KanzoBrokerError
        broker = Broker()
        result = self.execute_with_broker(broker)

        self.assertEqual(broker.calls.count("/v1/terminal/exec/start"), 1)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["output"], "done")

    def test_unconfirmed_cancel_marks_state_unknown_without_replaying(self) -> None:
        class Broker:
            def __init__(self):
                self.calls = []

            def post(self, path, _payload):
                self.calls.append(path)
                if path.endswith("/start"):
                    return {"exec_id": "exec-1"}
                if path.endswith("/poll"):
                    raise self_error("poll response lost")
                if path.endswith("/cancel"):
                    raise self_error("cancel response lost")
                raise AssertionError(path)

        self_error = self.provider.KanzoBrokerError
        broker = Broker()
        result = self.execute_with_broker(broker)

        self.assertEqual(broker.calls, [
            "/v1/terminal/exec/start",
            "/v1/terminal/exec/poll",
            "/v1/terminal/exec/cancel",
        ])
        self.assertEqual(result["exit_code"], 1)
        self.assertTrue(result["kanzo_non_retryable"])
        self.assertIn("execution state=unknown", result["output"])


class KanzoFileToolRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = _load_provider()

    def _install_tools(self, environments):
        tools = types.ModuleType("tools")
        tools.__path__ = []
        terminal_tool = types.SimpleNamespace(
            _active_environments=environments,
            _last_activity={key: float(index) for index, key in enumerate(environments)},
            _env_lock=threading.RLock(),
            _start_cleanup_thread=lambda: None,
        )
        file_operations = types.ModuleType("tools.file_operations")

        class ShellFileOperations:
            def __init__(self, env, cwd=None):
                self.env = env
                self.cwd = cwd

        file_operations.ShellFileOperations = ShellFileOperations
        config = types.ModuleType("tools.terminal_tool_config")
        config._tenv = lambda _name, default: default
        return {
            "tools": tools,
            "tools.terminal_tool": terminal_tool,
            "tools.file_operations": file_operations,
            "tools.terminal_tool_config": config,
        }, terminal_tool

    def test_file_tools_reuse_only_the_current_sessions_broker_environment(self) -> None:
        class Environment:
            def __init__(self, session_id):
                self._hermes_backend_name = "kanzo_broker"
                self._session_id = session_id
                self._cleaned = False
                self.cwd = "/workspace"

        parent = Environment("parent-session")
        child = Environment("child-session")
        modules, terminal_tool = self._install_tools({
            "parent": parent,
            "child": child,
        })
        fallback = unittest.mock.Mock(side_effect=AssertionError("stock cache must not be used"))
        with patch.dict(sys.modules, modules), \
                patch.object(self.provider, "_kanzo_automation_active", return_value=True):
            self.provider.session_context = types.SimpleNamespace(
                get_session_env=lambda name, default="": "child-session"
            )
            result = self.provider._get_kanzo_file_ops("default", fallback)

        self.assertIs(result.env, child)
        self.assertEqual(result.cwd, "/workspace")
        fallback.assert_not_called()
        self.assertGreater(terminal_tool._last_activity["child"], 3.0)

    def test_file_tools_create_a_scoped_broker_environment_when_first_tool_is_file(self) -> None:
        class Environment:
            _hermes_backend_name = "kanzo_broker"
            _session_id = "new-session"
            _cleaned = False
            cwd = "/workspace"

            def cleanup(self):
                self._cleaned = True

        created = Environment()
        modules, terminal_tool = self._install_tools({})
        fallback = unittest.mock.Mock(side_effect=AssertionError("stock cache must not be used"))
        with patch.dict(sys.modules, modules), \
                patch.object(self.provider, "_kanzo_automation_active", return_value=True), \
                patch.object(self.provider, "KanzoTerminalProvider") as provider_class:
            self.provider.session_context = types.SimpleNamespace(
                get_session_env=lambda name, default="": "new-session"
            )
            provider_class.return_value.create_environment.return_value = created
            result = self.provider._get_kanzo_file_ops("default", fallback)

        self.assertIs(result.env, created)
        self.assertEqual(result.cwd, "/workspace")
        self.assertIs(terminal_tool._active_environments["kanzo-file-ops:new-session"], created)
        fallback.assert_not_called()

    def test_non_kanzo_file_tools_keep_the_hermes_default(self) -> None:
        fallback = unittest.mock.Mock(return_value="default-file-ops")
        client_env = types.ModuleType("agent.session_client_env")
        client_env.bound_client_env = lambda: {}
        with patch.dict(sys.modules, {"agent.session_client_env": client_env}), \
                patch.object(self.provider, "session_context", types.SimpleNamespace(
                    get_session_env=lambda name, default="": "desktop" if name == "HERMES_SESSION_SOURCE" else default
                )), \
                patch.object(self.provider, "_kanzo_automation_active", return_value=False):
            result = self.provider._get_kanzo_file_ops("task-1", fallback)
        self.assertEqual(result, "default-file-ops")
        fallback.assert_called_once_with("task-1")

    def test_unverified_automated_file_session_fails_closed(self) -> None:
        fallback = unittest.mock.Mock(side_effect=AssertionError("stock cache must not be used"))
        with patch.object(self.provider, "_kanzo_automation_active", return_value=False), \
                self.assertRaises(self.provider.KanzoBrokerError):
            self.provider._get_kanzo_file_ops("default", fallback)
        fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
