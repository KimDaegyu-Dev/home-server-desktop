import unittest
from pathlib import Path

from worker import McpClient, Task, Worker, extract_waits_on, parse_projects, parse_task_line, parse_task_id


class WorkerParsingTests(unittest.TestCase):
    def test_parse_task_line_and_id(self):
        self.assertEqual(parse_task_line("ops#42 [todo] Add dashboard {code, urgent}"), ("ops#42", "todo", "Add dashboard {code, urgent}"))
        self.assertEqual(parse_task_id("Claimed ops#42"), "ops#42")

    def test_project_repository_link(self):
        self.assertEqual(parse_projects("ops https://github.com/acme/repo.git")["ops"], "acme/repo")

    def test_dependencies(self):
        self.assertEqual(extract_waits_on("waits_on: ops#2, ops#3"), ("ops#2", "ops#3"))

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
