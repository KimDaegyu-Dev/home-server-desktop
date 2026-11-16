"""Tests for waking the resident Gateway reconciliation loop on new ambiguity."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from executor_api import Store


class ReconciliationWakeTests(unittest.TestCase):
    def test_ambiguous_submit_wakes_reconciler_after_runtime_failure(self) -> None:
        wakeup = threading.Event()
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp), wakeup)
            try:
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

                self.assertTrue(wakeup.is_set())
                self.assertEqual(
                    [(row["task_id"], row["run_id"]) for row in store.ambiguous_submits()],
                    [("project#1", "run-1")],
                )
            finally:
                store.close()

    def test_confirmed_terminal_state_does_not_wake_reconciler(self) -> None:
        wakeup = threading.Event()
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp), wakeup)
            try:
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

                store.finish_submit("project#1", "run-1", "completed")

                self.assertFalse(wakeup.is_set())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
