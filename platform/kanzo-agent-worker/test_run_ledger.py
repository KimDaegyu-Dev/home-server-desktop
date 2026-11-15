"""Focused tests for durable Kanzo claim recovery."""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from run_ledger import ActiveRunError, DuplicateRunError, RunLedger
from worker import Worker


class RunLedgerClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.temp_dir.name) / "run-ledger.sqlite3"
        self.ledger = RunLedger(self.ledger_path)

    def tearDown(self) -> None:
        self.ledger.close()
        self.temp_dir.cleanup()

    def test_prepare_claim_is_idempotent_for_the_same_request(self) -> None:
        first = self.ledger.prepare_claim("run-1", "project-a", "-is:human", {"lane": "analysis"})
        second = self.ledger.prepare_claim("run-1", "project-a", "-is:human", {"lane": "analysis"})

        self.assertEqual(first, second)
        self.assertEqual([claim["run_id"] for claim in self.ledger.pending_claims()], ["run-1"])

    def test_prepare_claim_rejects_rebinding_a_run_id(self) -> None:
        self.ledger.prepare_claim("run-1", "project-a", "-is:human")

        with self.assertRaises(DuplicateRunError):
            self.ledger.prepare_claim("run-1", "project-b", "-is:human")

    def test_start_claimed_atomically_moves_intent_to_run(self) -> None:
        self.ledger.prepare_claim("run-1", "project-a", "-is:human", {"lane": "analysis"})

        run = self.ledger.start_claimed("project-a#7", "run-1")

        self.assertEqual((run["run_id"], run["task_id"], run["state"]), ("run-1", "project-a#7", "claimed"))
        self.assertEqual(run["metadata"], {"lane": "analysis"})
        self.assertEqual(self.ledger.pending_claims(), [])

    def test_active_run_leaves_claim_intent_pending(self) -> None:
        self.ledger.prepare_claim("run-1", "project-a", "-is:human")
        self.ledger.start("project-a#1", "existing-run")

        with self.assertRaises(ActiveRunError):
            self.ledger.start_claimed("project-a#7", "run-1")

        self.assertEqual([claim["run_id"] for claim in self.ledger.pending_claims()], ["run-1"])
        self.assertIsNone(self.ledger.get("run-1"))

    def test_v1_ledger_migrates_without_losing_runs(self) -> None:
        self.ledger.close()
        legacy_path = Path(self.temp_dir.name) / "legacy-ledger.sqlite3"
        with sqlite3.connect(legacy_path) as connection:
            connection.executescript(
                """
                CREATE TABLE runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN (
                        'claimed','preparing','running','finalizing','succeeded',
                        'review','waiting','cancelled','failed','orphaned'
                    )),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    result_json TEXT,
                    publication_json TEXT NOT NULL
                );
                CREATE INDEX runs_state_created ON runs(state, created_at);
                PRAGMA user_version = 1;
                """
            )
            connection.execute(
                """INSERT INTO runs VALUES (
                    'old-run', 'project-a#1', 'succeeded', 'created', 'updated',
                    '{}', '{}', NULL, '{}'
                )"""
            )
        connection.close()

        self.ledger = RunLedger(legacy_path)
        self.assertEqual(self.ledger.get("old-run")["task_id"], "project-a#1")
        self.ledger.prepare_claim("new-run", "project-a", "-is:human")
        self.assertEqual([claim["run_id"] for claim in self.ledger.pending_claims()], ["new-run"])


class CancellationLookupTests(unittest.TestCase):
    def test_transient_task_lookup_failure_is_not_treated_as_no_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            worker = Worker({"KANZO_ENABLED": "true", "MCPPROXY_KANZO_AGENT_TOKEN": "token", "KANZO_WORKER_STATE_DIR": state_dir})
            active = {"task_id": "project-a#7"}

            def fail_lookup(*_args):
                raise TimeoutError("temporary Kanzo outage")

            worker._call = fail_lookup
            self.assertIsNone(worker._active_task_cancel_requested(active))

            worker._call = lambda *_args: "status: review\nhuman: true"
            self.assertTrue(worker._active_task_cancel_requested(active))
            worker.ledger.close()

    def test_watcher_rechecks_cancel_after_transient_lookup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            worker = Worker({"KANZO_ENABLED": "true", "MCPPROXY_KANZO_AGENT_TOKEN": "token", "KANZO_WORKER_STATE_DIR": state_dir})
            calls = 0

            def lookup(*_args):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise TimeoutError("temporary Kanzo outage")
                return "status: review\nhuman: true"

            class ImmediateWait:
                @staticmethod
                def is_set() -> bool:
                    return False

                @staticmethod
                def wait(timeout: float | None = None) -> bool:
                    return False

            active = {
                "task_id": "project-a#7",
                "run_id": "run-1",
                "accept_cancel": True,
                "cancelled": threading.Event(),
                "stop_monitor": ImmediateWait(),
            }
            worker.active_run = active
            worker._call = lookup
            worker._cancel_active_executor = lambda _active: True
            worker.active_change.set()

            worker._watch_active_task(active)

            self.assertGreaterEqual(calls, 2)
            self.assertTrue(active["cancelled"].is_set())
            worker.ledger.close()


if __name__ == "__main__":
    unittest.main()
