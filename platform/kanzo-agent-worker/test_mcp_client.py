import json
import unittest
from unittest.mock import Mock

from worker import McpClient, Worker


class McpClientTests(unittest.TestCase):
    def test_upstream_tool_routes_are_cached_after_discovery(self):
        client = McpClient("https://mcp.invalid/mcp", "token")
        read_name = "mcp__home_server__call_tool_read"
        write_name = "mcp__home_server__call_tool_write"
        retrieve_name = "mcp__home_server__retrieve_tools"
        client.tools = {name: {} for name in (read_name, write_name, retrieve_name)}
        discoveries = []
        calls = []

        def invoke(name, args):
            calls.append((name, args))
            if name == retrieve_name:
                discoveries.append(args)
                return json.dumps({"tools": [
                    {"id": "kanzo:list_tasks", "call_with": "call_tool_read"},
                    {"id": "kanzo:add_comment", "call_with": "call_tool_write"},
                ]})
            return "ok"

        client._invoke = invoke
        self.assertEqual(client.call("list_tasks", {"project": "demo"}), "ok")
        self.assertEqual(client.call("add_comment", {"id": "demo#1", "body": "note"}), "ok")

        self.assertEqual(len(discoveries), 1)
        self.assertEqual(calls[1][0], read_name)
        self.assertEqual(calls[1][1]["name"], "kanzo:list_tasks")
        self.assertEqual(calls[2][0], write_name)
        self.assertEqual(calls[2][1]["name"], "kanzo:add_comment")

    def test_invoke_prefers_structured_content(self):
        client = McpClient("https://mcp.invalid/mcp", "token")
        client.tools = {"mcp__home_server__retrieve_tools": {}}
        payload = {"tools": [{"id": "kanzo:list_tasks", "call_with": "call_tool_read"}]}
        client._post = lambda _message: {"result": {"structuredContent": payload, "content": []}}

        self.assertEqual(client._invoke("mcp__home_server__retrieve_tools", {}), json.dumps(payload, separators=(",", ":")))

    def test_progress_comment_failure_does_not_escape(self):
        worker = object.__new__(Worker)
        worker._comment = Mock(side_effect=RuntimeError("temporary MCP failure"))

        worker._comment_progress("demo#1", "Still working")

        worker._comment.assert_called_once_with("demo#1", "Still working")


if __name__ == "__main__":
    unittest.main()
