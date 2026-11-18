from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worker import Worker, delete_remote_branch_with_lease, exact_branch_ref_sha
from workspace_registry import WorkspaceRegistry


class ExactBranchRefShaTests(unittest.TestCase):
    def test_returns_sha_for_exact_ref(self) -> None:
        sha = "a" * 40
        refs = [
            {"ref": "refs/heads/kanzo/workspaces/demo-extended", "object": {"sha": "b" * 40}},
            {"ref": "refs/heads/kanzo/workspaces/demo", "object": {"sha": sha}},
        ]
        self.assertEqual(exact_branch_ref_sha(refs, "refs/heads/kanzo/workspaces/demo"), sha)

    def test_prefix_match_is_not_treated_as_the_exact_branch(self) -> None:
        refs = [{"ref": "refs/heads/kanzo/workspaces/demo-extended", "object": {"sha": "a" * 40}}]
        self.assertIsNone(exact_branch_ref_sha(refs, "refs/heads/kanzo/workspaces/demo"))

    def test_missing_or_malformed_sha_fails_closed(self) -> None:
        self.assertEqual(exact_branch_ref_sha([{"ref": "refs/heads/demo", "object": {}}], "refs/heads/demo"), "")
        self.assertEqual(exact_branch_ref_sha({}, "refs/heads/demo"), "")


class ConditionalBranchDeleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.origin = Path(self.temp.name) / "origin.git"
        subprocess.run(["git", "init", "--bare", str(self.origin)], check=True, capture_output=True)
        self.source = Path(self.temp.name) / "source"
        self.source.mkdir()
        subprocess.run(["git", "init", str(self.source)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.source), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.source), "config", "user.email", "test@example.invalid"], check=True)
        (self.source / "file.txt").write_text("content" + chr(10))
        subprocess.run(["git", "-C", str(self.source), "add", "file.txt"], check=True)
        subprocess.run(["git", "-C", str(self.source), "commit", "-m", "initial"], check=True, capture_output=True)
        self.sha = subprocess.run(["git", "-C", str(self.source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "-C", str(self.source), "push", str(self.origin), "HEAD:refs/heads/kanzo/workspaces/demo"], check=True, capture_output=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_expected_sha_deletes_branch(self) -> None:
        result = delete_remote_branch_with_lease(str(self.origin), "kanzo/workspaces/demo", self.sha, {})
        self.assertEqual(result.returncode, 0, result.stderr)
        refs = subprocess.run(["git", "--git-dir", str(self.origin), "show-ref", "--verify", "refs/heads/kanzo/workspaces/demo"], capture_output=True)
        self.assertNotEqual(refs.returncode, 0)

    def test_stale_expected_sha_preserves_branch(self) -> None:
        result = delete_remote_branch_with_lease(str(self.origin), "kanzo/workspaces/demo", "f" * 40, {})
        self.assertNotEqual(result.returncode, 0)
        refs = subprocess.run(["git", "--git-dir", str(self.origin), "rev-parse", "refs/heads/kanzo/workspaces/demo"], check=True, capture_output=True, text=True)
        self.assertEqual(refs.stdout.strip(), self.sha)


class WorkspaceCleanupFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace_root = self.root / "workspace"
        self.workspace_root.mkdir()
        self.checkout = self.workspace_root / "ws-shared"
        self.checkout.mkdir()
        subprocess.run(["git", "init", str(self.checkout)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.checkout), "config", "user.name", "Cleanup Test"], check=True)
        subprocess.run(["git", "-C", str(self.checkout), "config", "user.email", "cleanup@example.invalid"], check=True)
        (self.checkout / "README.md").write_text("clean merged workspace\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.checkout), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(self.checkout), "commit", "-m", "merged"], check=True, capture_output=True)
        self.sha = subprocess.run(
            ["git", "-C", str(self.checkout), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.registry = WorkspaceRegistry(self.root / "registry.sqlite3")
        self.registry.acquire(
            workspace_id="shared", project="demo", task_id="demo#1", run_id="run-1",
            source_repo="owner/repo", source_ref="main", workspace_path=str(self.checkout),
            branch="kanzo/workspaces/shared",
        )
        self.registry.release(
            "shared", "run-1", state="review", head_sha=self.sha,
            pull_request="17", dirty=False,
        )
        self.worker = object.__new__(Worker)
        self.worker._workspace_registry = self.registry
        self.worker.workspace = self.workspace_root
        self.worker.env = {"GITHUB_TOKEN": "test-token"}
        self.worker._hermes_env = lambda: {}
        self.worker._call = lambda _name, _args: "status: done"

    def tearDown(self) -> None:
        self.registry.close()
        self.temp.cleanup()

    def test_verified_merged_pr_and_done_tasks_remove_workspace_and_keep_tombstone(self) -> None:
        real_run = subprocess.run
        branch = "kanzo/workspaces/shared"

        def fake_gh(command, *args, **kwargs):
            if command[:2] == ["gh", "pr"]:
                return subprocess.CompletedProcess(command, 0, json.dumps({
                    "state": "MERGED", "mergedAt": "2026-11-18T12:00:00Z",
                    "headRefName": branch, "headRefOid": self.sha,
                }), "")
            if command[:2] == ["gh", "api"]:
                return subprocess.CompletedProcess(command, 0, json.dumps([{
                    "ref": f"refs/heads/{branch}", "object": {"sha": self.sha},
                }]), "")
            return real_run(command, *args, **kwargs)

        with patch("worker.subprocess.run", side_effect=fake_gh), patch(
            "worker.delete_remote_branch_with_lease",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as delete_branch:
            self.worker._reconcile_workspace_cleanup()

        self.assertFalse(self.checkout.exists())
        delete_branch.assert_called_once()
        tombstone = self.registry.list(include_deleted=True)[0]
        self.assertEqual(tombstone["state"], "deleted")
        self.assertEqual(tombstone["head_sha"], self.sha)

    def test_workspace_is_preserved_until_every_linked_task_is_done(self) -> None:
        self.registry.acquire(
            workspace_id="shared", project="demo", task_id="demo#2", run_id="run-2",
            source_repo="owner/repo", source_ref="main", workspace_path=str(self.checkout),
            branch="kanzo/workspaces/shared",
        )
        self.registry.release("shared", "run-2", state="review", dirty=False)
        self.worker._call = lambda _name, args: (
            "status: review" if args["id"] == "demo#2" else "status: done"
        )

        with patch("worker.subprocess.run") as run, patch("worker.delete_remote_branch_with_lease") as delete_branch:
            self.worker._reconcile_workspace_cleanup()

        self.assertTrue(self.checkout.exists())
        self.assertEqual(self.registry.get("shared")["state"], "review")
        run.assert_not_called()
        delete_branch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
