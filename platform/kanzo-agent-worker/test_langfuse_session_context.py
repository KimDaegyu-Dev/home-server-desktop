"""Regression tests for per-session Langfuse identity isolation."""

from __future__ import annotations

import contextvars
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import langfuse_session_context


def load_patched_env_helper():
    namespace = {"os": os}
    exec(compile(langfuse_session_context.NEW, str(Path(langfuse_session_context.__file__)), "exec"), namespace)
    return namespace["_env"]


class LangfuseSessionContextTests(unittest.TestCase):
    def test_existing_session_patch_is_upgraded_to_no_global_id_fallback(self) -> None:
        updated, changed = langfuse_session_context.patch_plugin_text(langfuse_session_context.PREVIOUS)

        self.assertTrue(changed)
        self.assertEqual(updated, langfuse_session_context.NEW)
        self.assertIn("return default.strip()", updated)
        self.assertNotIn("            pass\n    return os.environ.get(name, default).strip()", updated)

    def test_current_patch_is_idempotent(self) -> None:
        updated, changed = langfuse_session_context.patch_plugin_text(langfuse_session_context.NEW)

        self.assertFalse(changed)
        self.assertEqual(updated, langfuse_session_context.NEW)

    def test_concurrent_sessions_keep_their_own_kanzo_ids(self) -> None:
        env_helper = load_patched_env_helper()
        session_values = contextvars.ContextVar("session_values", default={})
        session_env = types.ModuleType("agent.session_client_env")
        session_env.bound_client_env = session_values.get
        agent = types.ModuleType("agent")
        agent.__path__ = []
        agent.session_client_env = session_env

        context_a = contextvars.copy_context()
        context_a.run(session_values.set, {"KANZO_TASK_ID": "project#11", "KANZO_RUN_ID": "run-a"})
        context_b = contextvars.copy_context()
        context_b.run(session_values.set, {"KANZO_TASK_ID": "project#12", "KANZO_RUN_ID": "run-b"})

        def values():
            return (
                env_helper("HERMES_KANBAN_TASK"),
                env_helper("HERMES_KANBAN_RUN_ID"),
            )

        with patch.dict(sys.modules, {"agent": agent, "agent.session_client_env": session_env}):
            with ThreadPoolExecutor(max_workers=2) as pool:
                future_a = pool.submit(context_a.run, values)
                future_b = pool.submit(context_b.run, values)
                result_a = future_a.result()
                result_b = future_b.result()

        self.assertEqual(result_a, ("project#11", "run-a"))
        self.assertEqual(result_b, ("project#12", "run-b"))

    def test_missing_session_ids_never_fall_back_to_process_environment(self) -> None:
        env_helper = load_patched_env_helper()
        session_values = contextvars.ContextVar("session_values_missing", default=None)
        session_env = types.ModuleType("agent.session_client_env")
        session_env.bound_client_env = session_values.get
        agent = types.ModuleType("agent")
        agent.__path__ = []
        agent.session_client_env = session_env

        with patch.dict(
            sys.modules,
            {"agent": agent, "agent.session_client_env": session_env},
        ), patch.dict(
            os.environ,
            {"HERMES_KANBAN_TASK": "stale-task", "HERMES_KANBAN_RUN_ID": "stale-run"},
        ):
            self.assertEqual(env_helper("HERMES_KANBAN_TASK"), "")
            self.assertEqual(env_helper("HERMES_KANBAN_RUN_ID"), "")
            self.assertEqual(env_helper("LANGFUSE_HOST", "https://langfuse.invalid"), "https://langfuse.invalid")

    def test_unavailable_session_context_does_not_fall_back_to_process_environment(self) -> None:
        env_helper = load_patched_env_helper()
        with patch.dict(
            sys.modules,
            {"agent": None, "agent.session_client_env": None},
        ), patch.dict(
            os.environ,
            {"HERMES_KANBAN_TASK": "stale-task", "HERMES_KANBAN_RUN_ID": "stale-run"},
        ):
            self.assertEqual(env_helper("HERMES_KANBAN_TASK"), "")
            self.assertEqual(env_helper("HERMES_KANBAN_RUN_ID"), "")

    def test_profile_allowlist_contains_only_non_secret_kanzo_identifiers(self) -> None:
        config, changed = langfuse_session_context.ensure_kanzo_client_env(
            {"terminal": {"client_env_passthrough": ["CUSTOM_TOOL_PATH", "KANZO_TASK_ID"]}}
        )

        self.assertTrue(changed)
        self.assertEqual(
            config["terminal"]["client_env_passthrough"],
            ["CUSTOM_TOOL_PATH", "KANZO_TASK_ID", "KANZO_RUN_ID"],
        )

    def test_malformed_profile_allowlist_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            langfuse_session_context.ensure_kanzo_client_env(
                {"terminal": {"client_env_passthrough": "KANZO_TASK_ID"}}
            )


if __name__ == "__main__":
    unittest.main()
