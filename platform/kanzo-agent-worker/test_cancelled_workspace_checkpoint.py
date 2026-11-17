from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from worker import ProjectRoute, Task, Worker


class FakeBroker:
    def __init__(self, result=None, error=None):
        self.result = result or {"files_written": 2, "files_deleted": 1}
        self.error = error
        self.calls = []

    def apply_changes(self, lease, session_id, checkout):
        self.calls.append((lease, session_id, checkout))
        if self.error:
            raise self.error
        return self.result


class CancelledWorkspaceCheckpointTests(unittest.TestCase):
    def make_task(self, read_only=False):
        route = ProjectRoute(
            slug="demo", repo="owner/repo", model_lane="coding", provider="openai-api",
            model="iq_3s", reason="high", profile="builder", read_only=read_only,
        )
        return Task("demo#8", "demo", "review", "Cancelled work", ("code",), "", route)

    def make_worker(self):
        worker = object.__new__(Worker)
        return worker

    def test_confirmed_writer_cancellation_transfers_workspace_snapshot(self):
        worker = self.make_worker()
        broker = FakeBroker()
        active = {
            "workspace_acquired": True, "workspace_id": "shared-change", "broker_client": broker,
            "broker_lease": {"run_id": "run-1"}, "gateway_session_id": "session-1",
        }
        with tempfile.TemporaryDirectory() as root:
            note = worker._preserve_cancelled_workspace_checkpoint(self.make_task(), Path(root), active)

        self.assertEqual(broker.calls[0][1], "session-1")
        self.assertIn("2 files written, 1 deleted", note)
        self.assertEqual(active["sandbox_change_count"], 3)

    def test_readonly_or_unconfirmed_run_does_not_transfer_snapshot(self):
        worker = self.make_worker()
        broker = FakeBroker()
        active = {
            "workspace_acquired": True, "workspace_id": "shared-change", "broker_client": broker,
            "broker_lease": {"run_id": "run-1"}, "gateway_session_id": "session-1",
            "remote_execution_uncertain": True,
        }
        with tempfile.TemporaryDirectory() as root:
            note = worker._preserve_cancelled_workspace_checkpoint(self.make_task(read_only=True), Path(root), active)

        self.assertEqual(broker.calls, [])
        self.assertEqual(note, "")

    def test_unconfirmed_remote_run_is_never_snapshotted(self):
        worker = self.make_worker()
        broker = FakeBroker()
        active = {
            "workspace_acquired": True, "workspace_id": "shared-change", "broker_client": broker,
            "broker_lease": {"run_id": "run-1"}, "gateway_session_id": "session-1",
            "remote_execution_uncertain": True,
        }
        with tempfile.TemporaryDirectory() as root:
            note = worker._preserve_cancelled_workspace_checkpoint(self.make_task(), Path(root), active)

        self.assertEqual(broker.calls, [])
        self.assertIn("run is unconfirmed", note)
        self.assertIn("workspace remains quarantined", note)

    def test_failed_snapshot_transfer_is_reported_without_claiming_checkpoint_saved(self):
        worker = self.make_worker()
        broker = FakeBroker(error=RuntimeError("broker unavailable"))
        active = {
            "workspace_acquired": True, "workspace_id": "shared-change", "broker_client": broker,
            "broker_lease": {"run_id": "run-1"}, "gateway_session_id": "session-1",
        }
        with tempfile.TemporaryDirectory() as root, self.assertLogs("kanzo-agent-worker", level="ERROR"):
            note = worker._preserve_cancelled_workspace_checkpoint(self.make_task(), Path(root), active)

        self.assertIn("untransferred sandbox edits may be missing", note)


if __name__ == "__main__":
    unittest.main()
