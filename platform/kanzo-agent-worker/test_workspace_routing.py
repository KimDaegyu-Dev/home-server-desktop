from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from worker import Worker
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


if __name__ == "__main__":
    unittest.main()
