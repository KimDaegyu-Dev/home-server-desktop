from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workspace_registry import (
    WorkspaceBusyError,
    WorkspaceDeletedError,
    WorkspaceMergedError,
    WorkspaceRegistry,
    WorkspaceSourceMismatchError,
)


class WorkspaceRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.registry = WorkspaceRegistry(Path(self.temp.name) / "registry.sqlite3")

    def tearDown(self) -> None:
        self.registry.close()
        self.temp.cleanup()

    def acquire(self, task_id: str = "repo#1", run_id: str = "run-1", **overrides: str):
        fields = {
            "workspace_id": "shared",
            "project": "repo",
            "task_id": task_id,
            "run_id": run_id,
            "source_repo": "owner/repo",
            "source_ref": "main",
            "workspace_path": "/workspace/shared",
            "branch": "kanzo/workspaces/shared",
        }
        fields.update(overrides)
        return self.registry.acquire(**fields)

    def test_workspace_is_shared_sequentially_by_linked_tasks(self) -> None:
        first = self.acquire()
        self.assertEqual(first["linked_tasks"], ["repo#1"])
        self.registry.release("shared", "run-1", state="review", dirty=True)

        second = self.acquire(task_id="repo#2", run_id="run-2")
        self.assertEqual(second["linked_tasks"], ["repo#1", "repo#2"])
        self.assertEqual(second["workspace_path"], "/workspace/shared")
        self.assertEqual(second["branch"], "kanzo/workspaces/shared")

    def test_second_writer_is_rejected_atomically(self) -> None:
        self.acquire()
        with self.assertRaises(WorkspaceBusyError):
            self.acquire(task_id="repo#2", run_id="run-2")

    def test_different_source_cannot_reuse_workspace_id(self) -> None:
        self.acquire()
        self.registry.release("shared", "run-1", state="paused")
        with self.assertRaises(WorkspaceSourceMismatchError):
            self.acquire(run_id="run-2", source_repo="owner/other")
        with self.assertRaises(WorkspaceSourceMismatchError):
            self.acquire(run_id="run-2", source_ref="release/v2")

    def test_deleted_workspace_is_a_tombstone_and_cannot_be_reused(self) -> None:
        self.acquire()
        self.registry.release("shared", "run-1", state="review", dirty=False)
        self.registry.mark_merged("shared", head_sha="a" * 40)
        deleted = self.registry.mark_deleted("shared", head_sha="a" * 40)
        self.assertEqual(deleted["state"], "deleted")
        self.assertEqual(deleted["head_sha"], "a" * 40)
        self.assertEqual(self.registry.list(), [])
        self.assertEqual(self.registry.list(include_deleted=True)[0]["linked_tasks"], ["repo#1"])
        with self.assertRaises(WorkspaceDeletedError):
            self.acquire(run_id="run-2")

    def test_verified_merge_can_resume_cleanup_after_process_restart(self) -> None:
        self.acquire()
        self.registry.release("shared", "run-1", state="review", dirty=False)
        merged = self.registry.mark_merged("shared", head_sha="b" * 40)
        self.assertEqual(merged["state"], "merged")
        self.assertEqual(merged["head_sha"], "b" * 40)
        self.assertEqual(self.registry.list()[0]["workspace_id"], "shared")
        with self.assertRaises(WorkspaceMergedError):
            self.acquire(task_id="repo#2", run_id="run-2")
        deleted = self.registry.mark_deleted("shared", head_sha="b" * 40)
        self.assertEqual(deleted["state"], "deleted")
        self.assertEqual(self.registry.list(), [])

    def test_tombstone_requires_no_live_lease(self) -> None:
        self.acquire()
        with self.assertRaises(WorkspaceBusyError):
            self.registry.mark_deleted("shared")

    def test_workspace_cannot_be_deleted_before_verified_merge(self) -> None:
        self.acquire()
        self.registry.release("shared", "run-1", state="review")
        with self.assertRaises(Exception):
            self.registry.mark_deleted("shared")
        with self.assertRaises(Exception):
            self.registry.mark_merged("shared", head_sha="not-a-git-sha")

    def test_workspace_id_path_and_git_ref_characters_are_rejected(self) -> None:
        for workspace_id in ("../escape", "name..suffix", "trailing.", "name.lock", "name@{suffix}"):
            with self.subTest(workspace_id=workspace_id), self.assertRaises(Exception):
                self.acquire(workspace_id=workspace_id)

    def test_metrics_snapshot_reports_state_lease_and_dirty_counts(self) -> None:
        self.acquire()
        leased = self.registry.metrics_snapshot()
        self.assertEqual(leased["states"], {"leased": 1})
        self.assertEqual(leased["total"], 1)
        self.assertEqual(leased["active_leases"], 1)
        self.assertEqual(leased["dirty"], 0)

        self.registry.release("shared", "run-1", state="review", dirty=True)
        review = self.registry.metrics_snapshot()
        self.assertEqual(review["states"], {"review": 1})
        self.assertEqual(review["active_leases"], 0)
        self.assertEqual(review["dirty"], 1)


if __name__ == "__main__":
    unittest.main()
