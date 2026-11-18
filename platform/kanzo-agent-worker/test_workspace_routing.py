from __future__ import annotations

import unittest
import tempfile
import subprocess
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

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

    def test_latest_catalog_agent_config_overrides_stale_duplicate(self) -> None:
        route = parse_project_route(
            "demo",
            "\n".join((
                'agent_config: {"slug":"demo","repo":"org/repo","model_lane":"coding",'
                '"provider":"openai-api","model":"coder","reason":"high",'
                '"profile":"builder","read_only":false}',
                'agent_config: {"slug":"demo","repo":"org/repo","model_lane":"coding",'
                '"provider":"openai-api","model":"iq_3s","reason":"high",'
                '"profile":"builder","read_only":false}',
            )),
        )
        self.assertEqual(route.model, "iq_3s")
        self.assertEqual(route.reason, "high")

    def test_catalog_ref_is_parsed(self) -> None:
        route = parse_project_route(
            "demo",
            "agent_config: {\"slug\":\"demo\",\"repo\":\"org/repo\",\"model_lane\":\"analysis\",\"read_only\":true,\"ref\":\"release/v2\"}",
        )
        self.assertEqual(route.source_ref, "release/v2")

    def test_empty_catalog_form_fields_use_documented_defaults(self) -> None:
        route = parse_project_route(
            "demo",
            'agent_config: {"slug":"demo","repo":"org/repo","model_lane":"analysis",'
            '"read_only":true,"provider":"","model":"","reason":"","profile":"",'
            '"source_kind":"","source_path":"","runner_id":"","runner_os":"","ref":""}',
        )
        self.assertEqual(route.provider, "openai-api")
        self.assertEqual(route.model, "iq_3s")
        self.assertEqual(route.reason, "high")
        self.assertEqual(route.profile, "researcher")
        self.assertEqual(route.source_kind, "git")
        self.assertIsNone(route.source_path)
        self.assertEqual(route.runner_id, "k3s")
        self.assertEqual(route.runner_os, "linux")

    def test_empty_task_repo_field_inherits_project_default(self) -> None:
        task_route = apply_task_source_overrides(
            self.route,
            'agent_config: {"repo":"","ref":"","workspace_id":""}',
        )
        self.assertEqual(task_route.repo, "org/default-repo")
        self.assertEqual(task_route.source_ref, "main")
        self.assertIsNone(task_route.workspace_id)

    def test_k3s_worker_leaves_wsl_project_queued_when_runner_is_offline(self) -> None:
        state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(state_dir.cleanup)
        env = {
            "KANZO_ENABLED": "true",
            "MCPPROXY_KANZO_AGENT_TOKEN": "token",
            "KANZO_CATALOG_PROJECT": "kanzo-project-catalog",
            "KANZO_RUNNER_ID": "k3s",
            "KANZO_WORKER_STATE_DIR": state_dir.name,
        }
        worker = Worker(env)
        calls = []
        statuses = {"demo#8": "todo"}

        def kanzo_call(tool, args):
            calls.append((tool, args))
            if tool == "list_tasks":
                return "kanzo-project-catalog#1 [doing] demo {project-catalog}"
            if tool == "get_task":
                return (
                    'agent_config: {"slug":"demo","repo":"org/repo",'
                    '"model_lane":"coding","read_only":false,"source_kind":"local",'
                    '"source_path":"/home/eorbr/repo","runner_id":"wsl-home","runner_os":"wsl"}'
                )
            raise AssertionError(f"Unexpected Kanzo tool {tool}")

        worker._call = kanzo_call
        fake_client = type("FakeClient", (), {"__init__": lambda self, *args: None, "connect": lambda self: None})
        try:
            with patch("worker.McpClient", fake_client):
                self.assertFalse(worker.process_one())
            self.assertEqual(statuses["demo#8"], "todo")
            self.assertNotIn("next_task", [tool for tool, _ in calls])
            self.assertNotIn("update_task", [tool for tool, _ in calls])
        finally:
            worker.ledger.close()
            if worker._workspace_registry is not None:
                worker._workspace_registry.close()

    def test_catalog_local_source_keeps_absolute_path_and_runner_identity(self) -> None:
        route = parse_project_route(
            "demo",
            'agent_config: {"slug":"demo","repo":"org/repo","model_lane":"coding",'
            '"read_only":false,"source_kind":"local","source_path":"/mnt/c/Github/Babmukdang",'
            '"runner_id":"wsl-home","runner_os":"wsl"}',
        )
        self.assertEqual(route.source_kind, "local")
        self.assertEqual(route.source_path, "/mnt/c/Github/Babmukdang")
        self.assertEqual(route.runner_id, "wsl-home")
        self.assertEqual(route.runner_os, "wsl")

    def test_catalog_local_source_requires_absolute_path_and_supported_runner(self) -> None:
        prefix = 'agent_config: {"slug":"demo","repo":"org/repo","model_lane":"coding",'
        with self.assertRaises(WorkerError):
            parse_project_route("demo", prefix + '"read_only":false,"source_kind":"local","source_path":"repo"}')
        with self.assertRaises(WorkerError):
            parse_project_route(
                "demo",
                prefix + '"read_only":false,"source_kind":"local","source_path":"C:/repo",'
                '"runner_id":"win-home","runner_os":"windows"}',
            )

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

    def test_task_read_only_can_restrict_writable_project(self) -> None:
        writable = replace(self.route, read_only=False)
        task_route = apply_task_source_overrides(writable, 'agent_config: {"read_only":true}')
        self.assertTrue(task_route.read_only)

    def test_task_cannot_relax_project_read_only(self) -> None:
        task_route = apply_task_source_overrides(self.route, 'agent_config: {"read_only":false}')
        self.assertTrue(task_route.read_only)

    def test_invalid_task_read_only_fails_closed(self) -> None:
        with self.assertRaisesRegex(WorkerError, "Task read_only must be true or false"):
            apply_task_source_overrides(self.route, 'agent_config: {"read_only":"sometimes"}')

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

    def test_local_linked_code_tasks_reuse_same_dirty_checkout_and_branch(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            source = root_path / "source"
            workspace = root_path / "workspace"
            state = root_path / "state"
            source.mkdir()
            subprocess.run(["git", "init", "--initial-branch=main", str(source)], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Workspace Test"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "workspace-test@example.invalid"], check=True)
            (source / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-m", "base"], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(source), "remote", "add", "origin", "https://github.com/owner/repo.git"], check=True)

            route = ProjectRoute(
                slug="demo", repo="owner/repo", model_lane="coding", provider="openai-api",
                model="coder", reason="high", profile="builder", read_only=False,
                source_ref="main", workspace_id="shared-feature", source_kind="local",
                source_path=str(source), runner_id="wsl-home", runner_os="wsl",
            )
            worker = Worker({
                "GITHUB_TOKEN": "test-token",
                "KANZO_WORKSPACE": str(workspace),
                "KANZO_WORKER_STATE_DIR": str(state),
                "KANZO_RUNNER_ID": "wsl-home",
            })
            worker._current_route = route
            worker._refresh_cancel_state = lambda _active: None
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
                clean_source = subprocess.run(
                    ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"],
                    check=True, capture_output=True, text=True,
                )
                self.assertEqual(clean_source.stdout, "")
            finally:
                if worker._workspace_registry is not None:
                    worker._workspace_registry.close()
                worker.ledger.close()


if __name__ == "__main__":
    unittest.main()
