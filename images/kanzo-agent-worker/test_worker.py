import unittest
import tempfile
from unittest.mock import patch
from pathlib import Path

import readonly_executor
from worker import (McpClient, Task, Worker, extract_waits_on, parse_catalog_cards,
                    parse_project_route, parse_projects, parse_task_line, parse_task_id)


class WorkerParsingTests(unittest.TestCase):
    def test_parse_task_line_and_id(self):
        self.assertEqual(parse_task_line("ops#42 [todo] Add dashboard {code, urgent}"), ("ops#42", "todo", "Add dashboard {code, urgent}"))
        self.assertEqual(parse_task_id("Claimed ops#42"), "ops#42")

    def test_project_repository_link(self):
        self.assertEqual(parse_projects("ops https://github.com/acme/repo.git")["ops"], "acme/repo")

    def test_dependencies(self):
        self.assertEqual(extract_waits_on("waits_on: ops#2, ops#3"), ("ops#2", "ops#3"))

    def test_project_route_defaults_and_overrides(self):
        route = parse_project_route("Project settings", "slug: hermes-harness-lab\nrepo: none\nmodel_lane: analysis\nread_only: true")
        self.assertEqual((route.provider, route.model, route.reason, route.profile), ("openai-api", "iq_3s", "high", "researcher"))
        self.assertTrue(route.read_only)
        route = parse_project_route("", "slug: x\nrepo: acme/repo\nmodel_lane: coding\nprovider: anthropic\nmodel: claude-test\nprofile: reviewer\nread_only: false")
        self.assertEqual((route.provider, route.model, route.profile, route.read_only), ("anthropic", "claude-test", "reviewer", False))

    def test_catalog_only_activates_doing_cards(self):
        text = """kanzo-project-catalog#1 [doing] Hermes harness
status: doing
slug: hermes-harness-lab
repo: none
model_lane: analysis
read_only: true

kanzo-project-catalog#2 [todo] Other project
status: todo
slug: k3s-ai-ops-copilot
repo: acme/ops
model_lane: coding
read_only: false
"""
        routes = parse_catalog_cards(text)
        self.assertEqual([route.slug for route in routes], ["hermes-harness-lab"])
        self.assertTrue(routes[0].read_only)

    def test_catalog_rejects_partial_or_duplicate_routes(self):
        with self.assertRaises(Exception):
            parse_project_route("", "slug: broken\nrepo: none\nmodel_lane: unknown\nread_only: true")
        duplicate = """kanzo-project-catalog#1 [doing] A
slug: x
repo: none
model_lane: analysis
read_only: true
kanzo-project-catalog#2 [doing] B
slug: x
repo: none
model_lane: analysis
read_only: true"""
        with self.assertRaises(Exception):
            parse_catalog_cards(duplicate)

    def test_catalog_worker_never_claims_catalog_or_all_projects(self):
        worker = Worker({"KANZO_ENABLED": "true", "MCPPROXY_KANZO_AGENT_TOKEN": "token", "KANZO_CATALOG_PROJECT": "kanzo-project-catalog"})
        calls = []

        def fake_call(tool, args):
            calls.append((tool, args))
            if tool == "list_tasks":
                return "kanzo-project-catalog#22 [doing] Project: Hermes Harness Lab {project-catalog}"
            if tool == "get_task":
                return "status: doing\nslug: hermes-harness-lab\nrepo: none\nmodel_lane: analysis\nread_only: true"
            return "No task available"

        worker._call = fake_call
        fake_client = type("MockClient", (), {"__init__": lambda self, *args: None, "connect": lambda self: None})
        with patch("worker.McpClient", fake_client):
            self.assertFalse(worker.process_one())
        self.assertEqual(calls[-1], ("next_task", {"project": "hermes-harness-lab", "filter": "-is:human -is:waiting"}))
        self.assertTrue(all(call[1].get("project") not in ("all", "kanzo-project-catalog") for call in calls if call[0] == "next_task"))

    def test_readonly_executor_uses_an_isolated_profile_home(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source" / "profiles" / "researcher"
            source.mkdir(parents=True)
            (source / "config.yaml").write_text("model: iq_3s", encoding="utf-8")
            (source / ".env").write_text("MCPPROXY_AGENT_READ_TOKEN=read-only", encoding="utf-8")
            (source / "auth.json").write_text("profile auth state", encoding="utf-8")
            (source / "state.db").write_text("shared database must not be copied", encoding="utf-8")
            target_root = root / "isolated"
            with patch.object(readonly_executor, "PROFILE_SOURCE_HOME", root / "source"), patch.object(readonly_executor, "ISOLATED_HOME_ROOT", target_root):
                target = readonly_executor.sync_profile("researcher")
            self.assertEqual((target / "config.yaml").read_text(encoding="utf-8"), "model: iq_3s")
            self.assertTrue((target / ".env").is_file())
            self.assertTrue((target / "auth.json").is_file())
            self.assertFalse((target / "state.db").exists())
            self.assertNotEqual(target.resolve(), source.resolve())

    def test_code_tasks_are_explicit(self):
        self.assertTrue(Task("ops#1", "ops", "todo", "Fix parser", ("code",), "").is_code)
        self.assertFalse(Task("ops#1", "ops", "todo", "Review plan", (), "").is_code)

    def test_mcp_tool_resolution_requires_unique_suffix(self):
        client = McpClient("http://localhost/mcp", "token")
        client.tools = {"kanzo_add_comment": {}, "kanzo_update_task": {}}
        self.assertEqual(client.tool_name("add_comment"), "kanzo_add_comment")
        client.tools["other_add_comment"] = {}
        with self.assertRaises(Exception):
            client.tool_name("add_comment")

    def test_disabled_worker_does_not_claim_tasks(self):
        worker = Worker({"KANZO_ENABLED": "false", "HERMES_HOME": str(Path(".").resolve())})
        worker._call = lambda *_: self.fail("disabled worker must not call MCP")
        self.assertFalse(worker.process_one())


if __name__ == "__main__":
    unittest.main()
