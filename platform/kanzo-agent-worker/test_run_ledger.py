"""Focused tests for durable Kanzo claim recovery."""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from run_ledger import ActiveRunError, DuplicateRunError, RunLedger
from worker import RemoteCancellationUnconfirmed, TaskCancelled, Worker


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
        self.assertIn("claim_request_started_at", run["fields"])
        self.assertEqual([item["state"] for item in run["fields"]["state_history"]], ["claimed"])
        self.assertEqual(self.ledger.pending_claims(), [])

    def test_run_ledger_persists_ordered_state_timestamps_for_phase_timing(self) -> None:
        self.ledger.start("project-a#7", "run-1")
        for state in ("preparing", "running", "finalizing", "succeeded"):
            self.ledger.transition("run-1", state)

        run = self.ledger.get("run-1")
        history = run["fields"]["state_history"]
        self.assertEqual(
            [item["state"] for item in history],
            ["claimed", "preparing", "running", "finalizing", "succeeded"],
        )
        timestamps = [datetime.fromisoformat(item["at"].replace("Z", "+00:00")) for item in history]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertEqual(history[-1]["at"], run["updated_at"])

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


class CancellationAndClaimRecoveryTests(unittest.TestCase):
    def test_worker_restart_recovers_claim_committed_before_response_was_lost(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            env = {
                "KANZO_ENABLED": "true",
                "MCPPROXY_KANZO_AGENT_TOKEN": "token",
                "KANZO_CATALOG_PROJECT": "kanzo-project-catalog",
                "KANZO_WORKER_STATE_DIR": state_dir,
            }
            card_list = (
                "kanzo-project-catalog#1 [doing] Hermes Harness Lab {project-catalog}"
            )
            route_details = (
                "status: doing\nslug: hermes-harness-lab\nrepo: none\n"
                "model_lane: analysis\nread_only: true"
            )
            first_worker = Worker(env)
            first_calls = []
            kanzo_claims = {}
            task_status = {"hermes-harness-lab#4": "todo", "hermes-harness-lab#5": "todo"}
            task_line = "hermes-harness-lab#4 [doing] Inspect worker recovery"

            def interrupted_call(tool, args):
                first_calls.append((tool, args))
                if tool == "list_tasks":
                    return card_list
                if tool == "get_task":
                    return route_details
                if tool == "next_task":
                    task_id = kanzo_claims.get(args["run_id"])
                    if task_id is None:
                        task_id = next((key for key, status in task_status.items() if status == "todo"), None)
                        if task_id is None:
                            return "Nothing ready in todo for hermes-harness-lab"
                        task_status[task_id] = "doing"
                        kanzo_claims[args["run_id"]] = task_id
                    # Kanzo committed the claim, but the response was lost.
                    raise TimeoutError("MCP response lost after Kanzo may have claimed")
                raise AssertionError(f"Unexpected MCP tool: {tool}")

            first_worker._call = interrupted_call
            fake_client = type("FakeClient", (), {"__init__": lambda self, *args: None, "connect": lambda self: None})
            with patch("worker.McpClient", fake_client), self.assertRaises(TimeoutError):
                first_worker.process_one()
            first_worker.ledger.close()

            second_worker = Worker(env)
            retry_calls = []

            class StopAfterRecoveredClaim(BaseException):
                pass

            def recovered_call(tool, args):
                retry_calls.append((tool, args))
                if tool == "list_tasks":
                    return card_list
                if tool == "get_task":
                    if args["id"] == "hermes-harness-lab#4":
                        raise StopAfterRecoveredClaim()
                    return route_details
                if tool == "next_task":
                    task_id = kanzo_claims.get(args["run_id"])
                    self.assertEqual(task_id, "hermes-harness-lab#4")
                    return task_line
                raise AssertionError(f"Unexpected MCP tool: {tool}")

            second_worker._call = recovered_call
            with patch("worker.McpClient", fake_client), self.assertRaises(StopAfterRecoveredClaim):
                second_worker.process_one()

            first_run_id = next(args["run_id"] for tool, args in first_calls if tool == "next_task")
            retry = next(args for tool, args in retry_calls if tool == "next_task")
            self.assertEqual(retry["run_id"], first_run_id)
            self.assertEqual(retry["project"], "hermes-harness-lab")
            self.assertEqual(kanzo_claims, {first_run_id: "hermes-harness-lab#4"})
            self.assertEqual(task_status["hermes-harness-lab#4"], "doing")
            self.assertEqual(task_status["hermes-harness-lab#5"], "todo")
            self.assertEqual(second_worker.ledger.pending_claims(), [])
            recovered = second_worker.ledger.get(first_run_id)
            self.assertEqual((recovered["task_id"], recovered["state"]), ("hermes-harness-lab#4", "preparing"))
            second_worker.ledger.close()

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
                "cancel_pending": False,
                "cancelled": threading.Event(),
                "cancel_resolution": threading.Event(),
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

    def test_watcher_drops_retry_when_cancellation_acceptance_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            worker = Worker({"KANZO_ENABLED": "true", "MCPPROXY_KANZO_AGENT_TOKEN": "token", "KANZO_WORKER_STATE_DIR": state_dir})

            class StopAfterNextEventWait:
                def __init__(self, stop_event):
                    self.stop_event = stop_event
                    self.first_wait = True

                def set(self):
                    pass

                def clear(self):
                    pass

                def wait(self, timeout=None):
                    if self.first_wait:
                        self.first_wait = False
                        return True
                    self.stop_event.set()
                    return False

            stop_monitor = threading.Event()
            active = {
                "task_id": "project-a#7",
                "run_id": "run-1",
                "accept_cancel": True,
                "cancel_pending": False,
                "cancelled": threading.Event(),
                "cancel_resolution": threading.Event(),
                "stop_monitor": stop_monitor,
            }
            worker.active_run = active
            worker.active_change = StopAfterNextEventWait(stop_monitor)
            lookups = 0

            def failed_lookup(_active):
                nonlocal lookups
                lookups += 1
                worker._disable_active_cancel(active)
                return None

            worker._active_task_cancel_requested = failed_lookup
            worker._cancel_active_executor = lambda _active: self.fail("disabled cancellation must not stop the executor")
            worker.active_change.set()
            worker._watch_active_task(active)

            self.assertEqual(lookups, 1)
            self.assertFalse(active["cancel_pending"])
            self.assertFalse(active["cancelled"].is_set())
            worker.ledger.close()

    def test_watcher_stops_retry_when_run_monitor_is_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            worker = Worker({"KANZO_ENABLED": "true", "MCPPROXY_KANZO_AGENT_TOKEN": "token", "KANZO_WORKER_STATE_DIR": state_dir})

            class StopOnRetryWait:
                @staticmethod
                def is_set():
                    return False

                @staticmethod
                def wait(timeout=None):
                    return True

            active = {
                "task_id": "project-a#7",
                "run_id": "run-1",
                "accept_cancel": True,
                "cancel_pending": False,
                "cancelled": threading.Event(),
                "cancel_resolution": threading.Event(),
                "stop_monitor": StopOnRetryWait(),
            }
            worker.active_run = active
            worker.active_change.set()
            lookups = 0

            def failed_lookup(_active):
                nonlocal lookups
                lookups += 1
                return None

            worker._active_task_cancel_requested = failed_lookup
            worker._watch_active_task(active)

            self.assertEqual(lookups, 1)
            self.assertFalse(active["cancelled"].is_set())
            worker.ledger.close()

    def test_cancel_acceptance_requires_the_same_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            worker = Worker({"KANZO_ENABLED": "true", "MCPPROXY_KANZO_AGENT_TOKEN": "token", "KANZO_WORKER_STATE_DIR": state_dir})
            active = {"task_id": "project-a#7", "run_id": "run-1", "accept_cancel": True}
            worker.active_run = active

            self.assertTrue(worker._active_cancel_allowed(active))
            worker.active_run = {"task_id": "project-a#8", "run_id": "run-2", "accept_cancel": True}
            self.assertFalse(worker._active_cancel_allowed(active))
            worker.ledger.close()

    def test_refresh_queues_cancel_for_watcher_without_dispatching_it(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            worker = Worker({"KANZO_ENABLED": "true", "MCPPROXY_KANZO_AGENT_TOKEN": "token", "KANZO_WORKER_STATE_DIR": state_dir})
            active = {
                "task_id": "project-a#7",
                "run_id": "run-1",
                "accept_cancel": True,
                "cancel_pending": False,
                "cancelled": threading.Event(),
                "cancel_resolution": threading.Event(),
            }
            worker.active_run = active
            worker._call = lambda *_args: "status: review\nhuman: true"
            worker._cancel_active_executor = lambda _active: self.fail("refresh must leave cancellation dispatch to the watcher")

            def confirm_from_watcher():
                worker.active_change.wait(timeout=1)
                active["cancelled"].set()
                active["cancel_resolution"].set()

            watcher = threading.Thread(target=confirm_from_watcher)
            watcher.start()

            with self.assertRaises(TaskCancelled):
                worker._refresh_cancel_state(active)

            self.assertTrue(active["cancel_pending"])
            self.assertTrue(worker.active_change.is_set())
            watcher.join(timeout=1)
            worker.ledger.close()

    def test_refresh_stops_when_watcher_queues_cancel_during_its_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            worker = Worker({"KANZO_ENABLED": "true", "MCPPROXY_KANZO_AGENT_TOKEN": "token", "KANZO_WORKER_STATE_DIR": state_dir})
            active = {
                "task_id": "project-a#7",
                "run_id": "run-1",
                "accept_cancel": True,
                "cancel_pending": False,
                "cancelled": threading.Event(),
                "cancel_resolution": threading.Event(),
            }
            worker.active_run = active

            def watcher_queues_cancel(*_args):
                self.assertTrue(worker._queue_active_cancel(active))
                worker.active_change.set()
                return "status: review\nhuman: true"

            worker._call = watcher_queues_cancel
            def confirm_from_watcher():
                worker.active_change.wait(timeout=1)
                active["cancelled"].set()
                active["cancel_resolution"].set()

            watcher = threading.Thread(target=confirm_from_watcher)
            watcher.start()
            with self.assertRaises(TaskCancelled):
                worker._refresh_cancel_state(active)

            self.assertTrue(active["cancel_pending"])
            watcher.join(timeout=1)
            worker.ledger.close()

    def test_refresh_continues_if_watcher_confirms_cancel_was_withdrawn(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            worker = Worker({"KANZO_ENABLED": "true", "MCPPROXY_KANZO_AGENT_TOKEN": "token", "KANZO_WORKER_STATE_DIR": state_dir})

            class StopAfterIdleWait:
                def __init__(self, stop_event):
                    self.stop_event = stop_event

                def set(self):
                    pass

                def clear(self):
                    pass

                def wait(self, timeout=None):
                    self.stop_event.set()
                    return False

            stop_monitor = threading.Event()
            active = {
                "task_id": "project-a#7",
                "run_id": "run-1",
                "accept_cancel": True,
                "cancel_pending": True,
                "cancelled": threading.Event(),
                "cancel_resolution": threading.Event(),
                "stop_monitor": stop_monitor,
            }
            worker.active_run = active
            worker.active_change = StopAfterIdleWait(stop_monitor)
            worker._call = lambda *_args: "status: todo\nhuman: false"
            watcher = threading.Thread(target=worker._watch_active_task, args=(active,))
            watcher.start()
            worker._refresh_cancel_state(active)

            self.assertFalse(active["cancelled"].is_set())
            self.assertFalse(active["cancel_pending"])
            watcher.join(timeout=1)
            worker.ledger.close()

    def test_refresh_releases_when_watcher_marks_remote_cancel_uncertain(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            worker = Worker({"KANZO_ENABLED": "true", "MCPPROXY_KANZO_AGENT_TOKEN": "token", "KANZO_WORKER_STATE_DIR": state_dir})

            class StopAfterCancelAttempt:
                def __init__(self, stop_event):
                    self.stop_event = stop_event

                def is_set(self):
                    return self.stop_event.is_set()

                def wait(self, timeout=None):
                    self.stop_event.set()
                    return True

            stop_monitor = threading.Event()
            worker.ledger.start("project-a#7", "run-1")
            active = {
                "task_id": "project-a#7",
                "run_id": "run-1",
                "accept_cancel": True,
                "cancel_pending": True,
                "cancel_signal_at": time.monotonic() - 121,
                "cancelled": threading.Event(),
                "cancel_resolution": threading.Event(),
                "stop_monitor": StopAfterCancelAttempt(stop_monitor),
            }
            worker.active_run = active
            worker._call = lambda tool, _args: "status: review\nhuman: true" if tool == "get_task" else ""
            worker._cancel_active_executor = lambda _active: False
            watcher = threading.Thread(target=worker._watch_active_task, args=(active,))
            watcher.start()

            with self.assertRaises(RemoteCancellationUnconfirmed):
                worker._refresh_cancel_state(active)

            watcher.join(timeout=1)
            self.assertTrue(active["remote_execution_uncertain"])
            self.assertFalse(active["cancelled"].is_set())
            worker.ledger.close()


if __name__ == "__main__":
    unittest.main()
