from __future__ import annotations

import unittest
import tempfile
import subprocess
import threading
from pathlib import Path

from worker import Task, Worker
from worker import (
    ProjectRoute,
    WorkerError,
    apply_task_source_overrides,
    parse_project_route,
)


class WorkspaceRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.route = ProjectRoute(
            slug="demo",
            repo="org/default-repo",
            model_lane="analysis",
            provider="openai-api",
            model="iq_3s",
            reason="high",
            profile="researcher",
            read_only=True,
            source_ref="main",
        )

    def test_catalog_ref_is_parsed(self) -> None:
        route = parse_project_route(
            "demo",
            "agent_config: {\"slug\":\"demo\",\"repo\":\"org/repo\",\"model_lane\":\"analysis\",\"read_only\":true,\"ref\":\"release/v2\"}",
        )
        self.assertEqual(route.source_ref, "release/v2")

    def test_task_repo_and_ref_override_project_defaults(self) -> None:
        task_route = apply_task_source_overrides(
            self.route,
            'agent_config: {"repo":"https://github.com/org/client.git","ref":"feature/fix","workspace_id":"meal-plan-v2"}',
        )
        self.assertEqual(task_route.repo, "org/client")
        self.assertEqual(task_route.source_ref, "feature/fix")
        self.assertEqual(task_route.workspace_id, "meal-plan-v2")
        self.assertEqual(task_route.profile, self.route.profile)
        self.assertTrue(task_route.read_only)

    def test_task_source_settings_inherit_project_defaults(self) -> None:
        task_route = apply_task_source_overrides(
            self.route,
            "Acceptance: inspect the API\n\nComments (1):\nworker note\nrepo: attacker/other",
        )
        self.assertEqual(task_route.repo, self.route.repo)
        self.assertEqual(task_route.source_ref, self.route.source_ref)

    def test_invalid_repo_or_ref_fails_closed(self) -> None:
        with self.assertRaises(WorkerError):
            apply_task_source_overrides(self.route, 'agent_config: {"repo":"../outside","ref":"main"}')
        with self.assertRaises(WorkerError):
            apply_task_source_overrides(self.route, "ref: --upload-pack=bad")
        with self.assertRaises(WorkerError):
            apply_task_source_overrides(self.route, "workspace_id: ../outside")

    def test_worker_restart_releases_only_its_stale_workspace_lease(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            worker = Worker({
                "KANZO_WORKSPACE": str(root_path / "workspace"),
                "KANZO_WORKER_STATE_DIR": str(root_path / "state"),
            })
            run_id = "a" * 12
            try:
                worker.workspace_registry.acquire(
                    workspace_id="restart-test",
                    project="demo",
                    task_id="demo#3",
                    run_id=run_id,
                    source_repo="org/repo",
                    source_ref="main",
                    workspace_path=str(root_path / "workspace" / "ws-restart-test"),
                    branch="kanzo/workspaces/restart-test",
                )
                worker._recover_interrupted_workspace_lease(run_id, {"workspace_id": "restart-test"})
                recovered = worker.workspace_registry.get("restart-test")
                self.assertEqual(recovered["state"], "conflict")
                self.assertIsNone(recovered["lease_run_id"])
                self.assertTrue(recovered["dirty"])
            finally:
                if worker._workspace_registry is not None:
                    worker._workspace_registry.close()
                worker.ledger.close()

    def test_linked_code_tasks_reuse_same_dirty_checkout_and_branch(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            source = root_path / "source"
            remote = root_path / "remote.git"
            workspace = root_path / "workspace"
            state = root_path / "state"
            source.mkdir()
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
            subprocess.run(["git", "init", "--initial-branch=main", str(source)], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Workspace Test"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "workspace-test@example.invalid"], check=True)
            (source / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-m", "base"], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(source), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(source), "push", "-u", "origin", "main"], check=True, capture_output=True, text=True)

            route = ProjectRoute(
                slug="demo", repo="owner/repo", model_lane="coding", provider="openai-api",
                model="coder", reason="high", profile="builder", read_only=False,
                source_ref="main", workspace_id="shared-feature",
            )
            worker = Worker({
                "GITHUB_TOKEN": "test-token",
                "KANZO_WORKSPACE": str(workspace),
                "KANZO_WORKER_STATE_DIR": str(state),
            })
            worker._current_route = route
            worker._refresh_cancel_state = lambda _active: None
            original_run = worker._run_active_command

            def run_command(command, active, *, cwd=None, env=None, timeout=None):
                if command[:3] == ["gh", "repo", "clone"]:
                    target = Path(command[4])
                    cloned = subprocess.run(
                        ["git", "clone", "--origin", "origin", str(remote), str(target)],
                        capture_output=True, text=True, check=False,
                    )
                    if cloned.returncode == 0:
                        subprocess.run(
                            ["git", "-C", str(target), "remote", "set-url", "origin", "https://github.com/owner/repo.git"],
                            check=True, capture_output=True, text=True,
                        )
                    return cloned
                return original_run(command, active, cwd=cwd, env=env, timeout=timeout)

            worker._run_active_command = run_command
            task = Task("demo#1", "demo", "doing", "Implementation: shared workspace", ("code",), "", route)
            try:
                first_active = {"run_id": "111111111111", "cancelled": threading.Event()}
                worker.ledger.prepare_claim(first_active["run_id"], "demo", "-is:human", {})
                worker.ledger.start_claimed("demo#1", first_active["run_id"])
                first_checkout = worker._checkout_repo(task, first_active)
                expected_branch = "kanzo/workspaces/shared-feature"
                self.assertEqual(first_active["branch"], expected_branch)
                with (first_checkout / "README.md").open("a", encoding="utf-8") as stream:
                    stream.write("in progress\n")
                (first_checkout / "checkpoint.txt").write_text("keep me\n", encoding="utf-8")
                worker.workspace_registry.release("shared-feature", "111111111111", state="review", dirty=True)
                worker.ledger.transition(first_active["run_id"], "preparing")
                worker.ledger.transition(first_active["run_id"], "review")

                second_active = {"run_id": "222222222222", "cancelled": threading.Event()}
                worker.ledger.prepare_claim(second_active["run_id"], "demo", "-is:human", {})
                worker.ledger.start_claimed("demo#1", second_active["run_id"])
                second_checkout = worker._checkout_repo(task, second_active)
                self.assertEqual(second_checkout, first_checkout)
                self.assertEqual(second_active["branch"], expected_branch)
                self.assertEqual((second_checkout / "README.md").read_text(encoding="utf-8"), "base\nin progress\n")
                self.assertEqual((second_checkout / "checkpoint.txt").read_text(encoding="utf-8"), "keep me\n")
            finally:
                if worker._workspace_registry is not None:
                    worker._workspace_registry.close()
                worker.ledger.close()


if __name__ == "__main__":
    unittest.main()
