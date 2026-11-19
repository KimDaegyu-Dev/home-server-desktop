from __future__ import annotations

import unittest
import tempfile
import subprocess
import threading
import os
from pathlib import Path
from unittest.mock import patch

from worker import Task, Worker
from worker import (
    ProjectRoute,
    SourceSpec,
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

    def test_catalog_parses_a_bounded_mixed_source_manifest(self) -> None:
        route = parse_project_route(
            "demo",
            'agent_config: {"slug":"demo","repo":"org/primary","model_lane":"coding",'
            '"read_only":false,"sources":[{"name":"api","repo":"org/api","ref":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},'
            '{"name":"shared","path":"/mnt/c/Github/shared","ref":"main",'
            '"runner_id":"wsl-home","runner_os":"wsl"}]}',
        )
        self.assertEqual(len(route.sources), 2)
        self.assertEqual((route.sources[0].name, route.sources[0].repo, route.sources[0].ref), ("api", "org/api", "a" * 40))
        self.assertEqual((route.sources[1].path, route.sources[1].runner_id, route.sources[1].runner_os),
                         ("/mnt/c/Github/shared", "wsl-home", "wsl"))

    def test_per_task_model_turn_budget_is_bounded_and_inheritable(self) -> None:
        route = parse_project_route(
            "demo",
            'agent_config: {"slug":"demo","repo":"org/repo","model_lane":"coding",'
            '"read_only":false,"max_turns":8}',
        )
        self.assertEqual(route.max_turns, 8)
        inherited = apply_task_source_overrides(route, 'agent_config: {"max_turns":""}')
        self.assertEqual(inherited.max_turns, 8)
        overridden = apply_task_source_overrides(route, 'agent_config: {"max_turns":72}')
        self.assertEqual(overridden.max_turns, 72)
        with self.assertRaises(WorkerError):
            parse_project_route(
                "demo",
                'agent_config: {"slug":"demo","repo":"org/repo","model_lane":"coding",'
                '"read_only":false,"max_turns":81}',
            )

    def test_task_agent_config_overrides_readonly_profile_lane_and_local_runner(self) -> None:
        route = parse_project_route(
            "demo",
            'agent_config: {"slug":"demo","repo":"org/primary","model_lane":"coding",'
            '"provider":"openai-api","model":"iq_3s","reason":"high",'
            '"profile":"builder","read_only":false,"source_kind":"git",'
            '"max_turns":80}',
        )
        overridden = apply_task_source_overrides(
            route,
            'agent_config: {"slug":"demo","repo":"","model_lane":"analysis",'
            '"provider":"openai-api","model":"iq_3s","reason":"high",'
            '"profile":"researcher","read_only":true,"source_kind":"local",'
            '"source_path":"/mnt/c/Github/repo","runner_id":"wsl-home",'
            '"runner_os":"wsl","workspace_id":"shared-analysis",'
            '"max_turns":4,"sources":[]}',
        )
        self.assertEqual(overridden.repo, "org/primary")
        self.assertEqual((overridden.model_lane, overridden.provider, overridden.model),
                         ("analysis", "openai-api", "iq_3s"))
        self.assertEqual((overridden.profile, overridden.reason, overridden.read_only),
                         ("researcher", "high", True))
        self.assertEqual((overridden.source_kind, overridden.source_path, overridden.runner_id, overridden.runner_os),
                         ("local", "/mnt/c/Github/repo", "wsl-home", "wsl"))
        self.assertEqual(overridden.workspace_id, "shared-analysis")
        self.assertEqual(overridden.max_turns, 4)
        self.assertEqual(overridden.sources, ())

    def test_task_agent_config_rejects_invalid_readonly_and_project_slug(self) -> None:
        route = parse_project_route(
            "demo", 'agent_config: {"slug":"demo","repo":"org/primary",'
            '"model_lane":"coding","read_only":false}',
        )
        for config in (
            '{"read_only":"sometimes"}',
            '{"slug":"other","read_only":true}',
        ):
            with self.subTest(config=config), self.assertRaises(WorkerError):
                apply_task_source_overrides(route, f"agent_config: {config}")

    def test_catalog_rejects_unpinned_unsafe_duplicate_or_excess_sources(self) -> None:
        prefix = '{"slug":"demo","repo":"org/primary","model_lane":"coding","read_only":false,"sources":'
        cases = [
            '[{"name":"api","repo":"org/api"}]',
            '[{"name":"../api","repo":"org/api","ref":"main"}]',
            '[{"name":"api","repo":"org/api","ref":"main"},{"name":"API","repo":"org/api","ref":"main"}]',
            '[{"name":"local","path":"/mnt/c/Github/../secret","ref":"main","runner_id":"wsl-home","runner_os":"wsl"}]',
            '[{"name":"local","path":"/mnt/c/Github/repo","ref":"main"}]',
            '[{"name":"a","repo":"org/a","ref":"main"},{"name":"b","repo":"org/b","ref":"main"},'
            '{"name":"c","repo":"org/c","ref":"main"},{"name":"d","repo":"org/d","ref":"main"},'
            '{"name":"e","repo":"org/e","ref":"main"}]',
        ]
        for sources in cases:
            with self.subTest(sources=sources), self.assertRaises(WorkerError):
                parse_project_route("demo", f"agent_config: {prefix}{sources}}}")

    def test_task_sources_override_project_manifest_and_empty_list_clears_it(self) -> None:
        route = parse_project_route(
            "demo",
            'agent_config: {"slug":"demo","repo":"org/primary","model_lane":"coding",'
            '"read_only":false,"sources":[{"name":"api","repo":"org/api","ref":"main"}]}',
        )
        overridden = apply_task_source_overrides(
            route,
            'agent_config: {"sources":[{"name":"client","repo":"org/client","ref":"v1"}]}',
        )
        self.assertEqual([(item.name, item.repo) for item in overridden.sources], [("client", "org/client")])
        cleared = apply_task_source_overrides(route, 'agent_config: {"sources":[]}')
        self.assertEqual(cleared.sources, ())

    def test_same_runner_local_secondary_source_is_pinned_and_copied_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = root / "sources"
            source = allowed / "shared"
            source.mkdir(parents=True)
            subprocess.run(["git", "init", "--initial-branch=main", str(source)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Manifest Test"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "manifest@example.invalid"], check=True)
            (source / "shared.py").write_text("value = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "shared.py"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-m", "base"], check=True, capture_output=True)
            sha = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            route = ProjectRoute(
                slug="demo", repo="org/primary", model_lane="coding", provider="openai-api",
                model="iq_3s", reason="high", profile="builder", read_only=False,
                sources=(SourceSpec("shared", None, "main", str(source), "wsl-home", "wsl"),),
            )
            worker = Worker({
                "KANZO_RUNNER_ID": "wsl-home", "KANZO_RUNNER_OS": "wsl",
                "KANZO_LOCAL_SOURCE_ROOTS": str(allowed),
                "KANZO_WORKER_STATE_DIR": str(root / "state"),
                "KANZO_WORKSPACE": str(root / "workspace"),
            })
            worker._refresh_cancel_state = lambda _active: None
            staging = root / "staging"
            staging.mkdir()
            try:
                resolved, metadata = worker._resolve_secondary_sources(route, {"run_id": "a" * 12}, staging)
                self.assertEqual(resolved[0][2], sha)
                self.assertEqual(metadata[0]["sha"], sha)
                self.assertEqual((resolved[0][1] / "shared.py").read_text(encoding="utf-8"), "value = 1\n")
                self.assertEqual(subprocess.run(
                    ["git", "-C", str(source), "status", "--porcelain"], check=True,
                    capture_output=True, text=True,
                ).stdout, "")
            finally:
                worker.ledger.close()
                if worker._workspace_registry is not None:
                    worker._workspace_registry.close()

    def test_local_secondary_source_fails_closed_outside_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = root / "allowed"
            source = root / "outside"
            allowed.mkdir()
            source.mkdir()
            route = ProjectRoute(
                slug="demo", repo="org/primary", model_lane="coding", provider="openai-api",
                model="iq_3s", reason="high", profile="builder", read_only=False,
                sources=(SourceSpec("shared", None, "main", str(source), "wsl-home", "wsl"),),
            )
            worker = Worker({
                "KANZO_RUNNER_ID": "wsl-home", "KANZO_RUNNER_OS": "wsl",
                "KANZO_LOCAL_SOURCE_ROOTS": str(allowed),
                "KANZO_WORKER_STATE_DIR": str(root / "state"),
                "KANZO_WORKSPACE": str(root / "workspace"),
            })
            try:
                with self.assertRaisesRegex(WorkerError, "outside configured source roots"):
                    worker._resolve_secondary_sources(route, {"run_id": "a" * 12}, root)
            finally:
                worker.ledger.close()
                if worker._workspace_registry is not None:
                    worker._workspace_registry.close()

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
            remote = root_path / "origin.git"
            workspace = root_path / "workspace"
            state = root_path / "state"
            source.mkdir()
            subprocess.run(["git", "init", "--initial-branch=main", str(source)], check=True, capture_output=True, text=True)
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Workspace Test"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "workspace-test@example.invalid"], check=True)
            (source / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-m", "base"], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(source), "remote", "add", "origin", "https://github.com/owner/repo.git"], check=True)
            rewrite = root_path / "gitconfig"
            rewrite.write_text(
                f'[url "{remote.as_uri()}"]\n\tinsteadOf = https://github.com/owner/repo.git\n',
                encoding="utf-8",
            )

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
            worker._hermes_env = lambda: {**os.environ, "GIT_CONFIG_GLOBAL": str(rewrite)}
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

    def test_existing_workspace_fast_forwards_to_remote_commit_before_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, remote, writer = root / "source", root / "origin.git", root / "writer"
            workspace, state = root / "workspace", root / "state"
            subprocess.run(["git", "init", "--initial-branch=main", str(source)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Workspace Test"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "workspace-test@example.invalid"], check=True)
            (source / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-m", "base"], check=True, capture_output=True)
            subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True)
            github_url = "https://github.com/owner/repo.git"
            subprocess.run(["git", "-C", str(source), "remote", "add", "origin", github_url], check=True)
            rewrite = root / "gitconfig"
            rewrite.write_text(f'[url "{remote.as_uri()}"]\n\tinsteadOf = {github_url}\n', encoding="utf-8")

            route = ProjectRoute(
                slug="demo", repo="owner/repo", model_lane="coding", provider="openai-api",
                model="coder", reason="high", profile="builder", read_only=False,
                source_ref="main", workspace_id="shared-feature", source_kind="local",
                source_path=str(source), runner_id="wsl-home", runner_os="wsl",
            )
            worker = Worker({
                "GITHUB_TOKEN": "test-token", "KANZO_WORKSPACE": str(workspace),
                "KANZO_WORKER_STATE_DIR": str(state), "KANZO_RUNNER_ID": "wsl-home",
            })
            worker._current_route = route
            worker._refresh_cancel_state = lambda _active: None
            task = Task("demo#1", "demo", "doing", "Implementation: shared workspace", ("code",), "", route)
            try:
                git_env = {**os.environ, "GIT_CONFIG_GLOBAL": str(rewrite)}
                with patch.object(worker, "_hermes_env", return_value=git_env):
                    first = {"run_id": "111111111111", "cancelled": threading.Event()}
                    worker.ledger.prepare_claim(first["run_id"], "demo", "-is:human", {})
                    worker.ledger.start_claimed("demo#1", first["run_id"])
                    checkout = worker._checkout_repo(task, first)
                    branch = "kanzo/workspaces/shared-feature"
                    subprocess.run(
                        ["git", "-C", str(checkout), "push", "origin", "main:main"],
                        check=True, capture_output=True, env=git_env,
                    )

                    subprocess.run(["git", "clone", str(source), str(writer)], check=True, capture_output=True)
                    subprocess.run(["git", "-C", str(writer), "remote", "set-url", "origin", github_url], check=True)
                    subprocess.run(["git", "-C", str(writer), "checkout", "-b", branch, "main"], check=True, capture_output=True)
                    marker = writer / ".kanzo-canary" / "resume.txt"
                    marker.parent.mkdir()
                    marker.write_text("created-by-task=first\n", encoding="utf-8")
                    subprocess.run(["git", "-C", str(writer), "config", "user.name", "Workspace Test"], check=True)
                    subprocess.run(["git", "-C", str(writer), "config", "user.email", "workspace-test@example.invalid"], check=True)
                    subprocess.run(["git", "-C", str(writer), "add", "-A"], check=True)
                    subprocess.run(["git", "-C", str(writer), "commit", "-m", "first task"], check=True, capture_output=True)
                    subprocess.run(
                        ["git", "-C", str(writer), "push", "origin", "HEAD"],
                        check=True, capture_output=True, env=git_env,
                    )
                    remote_sha = subprocess.run(
                        ["git", "-C", str(writer), "rev-parse", "HEAD"], check=True,
                        capture_output=True, text=True,
                    ).stdout.strip()

                    worker.workspace_registry.release("shared-feature", first["run_id"], state="ready", dirty=False)
                    worker.ledger.transition(first["run_id"], "preparing")
                    worker.ledger.transition(first["run_id"], "review")
                    second = {"run_id": "222222222222", "cancelled": threading.Event()}
                    worker.ledger.prepare_claim(second["run_id"], "demo", "-is:human", {})
                    worker.ledger.start_claimed("demo#1", second["run_id"])
                    resumed = worker._checkout_repo(task, second)

                    self.assertEqual(resumed, checkout)
                    self.assertEqual((resumed / ".kanzo-canary" / "resume.txt").read_text(encoding="utf-8"), "created-by-task=first\n")
                    actual_sha = subprocess.run(
                        ["git", "-C", str(resumed), "rev-parse", "HEAD"], check=True,
                        capture_output=True, text=True,
                    ).stdout.strip()
                    self.assertEqual(actual_sha, remote_sha)
                    self.assertEqual(second["source_sha"], remote_sha)

                    worker.workspace_registry.release("shared-feature", second["run_id"], state="ready", dirty=False)
                    worker.ledger.transition(second["run_id"], "preparing")
                    worker.ledger.transition(second["run_id"], "review")
                    checkpoint = resumed / "checkpoint.txt"
                    checkpoint.write_text("preserve this unfinished work\n", encoding="utf-8")
                    (writer / "remote-followup.txt").write_text("remote follow-up\n", encoding="utf-8")
                    subprocess.run(["git", "-C", str(writer), "add", "-A"], check=True)
                    subprocess.run(["git", "-C", str(writer), "commit", "-m", "remote follow-up"], check=True, capture_output=True)
                    subprocess.run(
                        ["git", "-C", str(writer), "push", "origin", "HEAD"],
                        check=True, capture_output=True, env=git_env,
                    )
                    third = {"run_id": "333333333333", "cancelled": threading.Event()}
                    worker.ledger.prepare_claim(third["run_id"], "demo", "-is:human", {})
                    worker.ledger.start_claimed("demo#1", third["run_id"])
                    with self.assertRaisesRegex(WorkerError, "uncommitted checkpoint files"):
                        worker._checkout_repo(task, third)
                    self.assertEqual(checkpoint.read_text(encoding="utf-8"), "preserve this unfinished work\n")
                    self.assertEqual(
                        subprocess.run(["git", "-C", str(resumed), "rev-parse", "HEAD"], check=True,
                                       capture_output=True, text=True).stdout.strip(),
                        remote_sha,
                    )
                    worker.workspace_registry.release("shared-feature", third["run_id"], state="review", dirty=True)
                    worker.ledger.transition(third["run_id"], "preparing")
                    worker.ledger.transition(third["run_id"], "review")
            finally:
                if worker._workspace_registry is not None:
                    worker._workspace_registry.close()
                worker.ledger.close()


if __name__ == "__main__":
    unittest.main()
