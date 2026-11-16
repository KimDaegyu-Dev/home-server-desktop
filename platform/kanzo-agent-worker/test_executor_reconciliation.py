"""Tests for waking the resident Gateway reconciliation loop on new ambiguity."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from executor_api import Store, _reconcile_startup


class ReconciliationWakeTests(unittest.TestCase):
    @staticmethod
    def _make_ambiguous_submit(store: Store) -> None:
        payload = {
            "provider": "openai-api",
            "model": "test-model",
            "reasoning": "high",
            "tool_policy": "standard",
        }
        store.begin_create("project#1", "run-1", "researcher", "digest", payload)
        store.finish_create("project#1", "run-1", "session-1", 1)
        store.begin_submit("project#1", "run-1")
        store.save_admission("project#1", "run-1", "session-1", "admission-1")
        store.finish_submit("project#1", "run-1", "ambiguous")

    def test_ambiguous_submit_wakes_reconciler_after_runtime_failure(self) -> None:
        wakeup = threading.Event()
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp), wakeup)
            try:
                self._make_ambiguous_submit(store)

                self.assertTrue(wakeup.is_set())
                self.assertEqual(
                    [(row["task_id"], row["run_id"]) for row in store.ambiguous_submits()],
                    [("project#1", "run-1")],
                )
            finally:
                store.close()

    def test_terminal_reconciliation_settles_ledger_without_prompt_replay(self) -> None:
        wakeup = threading.Event()
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp), wakeup)
            try:
                self._make_ambiguous_submit(store)
                terminal_receipt = {
                    "accepted": True,
                    "terminal": True,
                    "session_id": "session-1",
                    "execution_generation": 1,
                    "outcome": "completed",
                }
                with patch("executor_api._profile_home", return_value=Path(temp)), patch(
                    "executor_api._run_helper",
                    return_value=(0, json.dumps(terminal_receipt).encode(), b""),
                ) as run_helper:
                    _reconcile_startup(store)

                self.assertEqual(store.get("project#1", "run-1")["submit_state"], "reconciled_terminal")
                self.assertEqual(run_helper.call_count, 1)
                args = run_helper.call_args.args[0]
                self.assertIn("reconcile", args)
                self.assertNotIn("prompt.submit", args)
                self.assertNotIn("--query-file", args)
            finally:
                store.close()

    def test_confirmed_terminal_state_does_not_wake_reconciler(self) -> None:
        wakeup = threading.Event()
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp), wakeup)
            try:
                self._make_ambiguous_submit(store)
                wakeup.clear()

                store.finish_submit("project#1", "run-1", "completed")

                self.assertFalse(wakeup.is_set())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
