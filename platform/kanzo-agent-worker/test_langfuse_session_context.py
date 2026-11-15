"""Regression tests for per-session Langfuse identity isolation."""

from __future__ import annotations

import ast
import contextvars
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).with_name("langfuse_session_context.py")


def load_patched_env_helper():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    value = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "NEW" for target in node.targets)
    )
    namespace = {"os": os}
    exec(compile(value, str(SOURCE), "exec"), namespace)
    return namespace["_env"]


class LangfuseSessionContextTests(unittest.TestCase):
    def test_concurrent_sessions_keep_their_own_kanzo_ids(self) -> None:
        env_helper = load_patched_env_helper()
        session_values = contextvars.ContextVar("session_values", default={})
        gateway = types.ModuleType("gateway")
        session_context = types.ModuleType("gateway.session_context")
        session_context.get_session_env = lambda name, default="": session_values.get().get(name, default)
        gateway.session_context = session_context

        context_a = contextvars.copy_context()
        context_a.run(session_values.set, {"KANZO_TASK_ID": "project#11", "KANZO_RUN_ID": "run-a"})
        context_b = contextvars.copy_context()
        context_b.run(session_values.set, {"KANZO_TASK_ID": "project#12", "KANZO_RUN_ID": "run-b"})

        def values():
            return (
                env_helper("HERMES_KANBAN_TASK"),
                env_helper("HERMES_KANBAN_RUN_ID"),
            )

        with patch.dict(sys.modules, {"gateway": gateway, "gateway.session_context": session_context}):
            with ThreadPoolExecutor(max_workers=2) as pool:
                future_a = pool.submit(context_a.run, values)
                future_b = pool.submit(context_b.run, values)
                result_a = future_a.result()
                result_b = future_b.result()

        self.assertEqual(result_a, ("project#11", "run-a"))
        self.assertEqual(result_b, ("project#12", "run-b"))

    def test_missing_session_ids_never_fall_back_to_process_environment(self) -> None:
        env_helper = load_patched_env_helper()
        gateway = types.ModuleType("gateway")
        session_context = types.ModuleType("gateway.session_context")
        session_context.get_session_env = lambda _name, default="": default
        gateway.session_context = session_context

        with patch.dict(
            sys.modules,
            {"gateway": gateway, "gateway.session_context": session_context},
        ), patch.dict(
            os.environ,
            {"HERMES_KANBAN_TASK": "stale-task", "HERMES_KANBAN_RUN_ID": "stale-run"},
        ):
            self.assertEqual(env_helper("HERMES_KANBAN_TASK"), "")
            self.assertEqual(env_helper("HERMES_KANBAN_RUN_ID"), "")
            self.assertEqual(env_helper("LANGFUSE_HOST", "https://langfuse.invalid"), "https://langfuse.invalid")


if __name__ == "__main__":
    unittest.main()
