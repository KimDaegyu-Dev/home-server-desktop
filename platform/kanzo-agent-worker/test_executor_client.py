from __future__ import annotations

import unittest
from unittest.mock import patch

from executor_client import ExecutorClient


class ExecutorSessionBudgetTests(unittest.TestCase):
    def test_session_request_carries_server_enforced_turn_cap(self) -> None:
        client = ExecutorClient("http://executor.mcp.svc.cluster.local:8766", "x" * 32)
        calls = []
        with patch.object(client, "_post_json", side_effect=lambda path, payload, _timeout: calls.append((path, payload)) or {
            "session_id": "session-1", "execution_generation": 0,
        }):
            result = client.create_session(
                task_id="demo#1", run_id="a" * 12, profile="builder",
                provider="openai-api", model="iq_3s", reasoning="high", max_turns=8,
            )
        self.assertEqual(result["session_id"], "session-1")
        self.assertEqual(calls[0][0], "/v1/sessions")
        self.assertEqual(calls[0][1]["max_turns"], 8)

    def test_session_turn_cap_rejects_values_outside_global_80(self) -> None:
        client = ExecutorClient("http://executor.mcp.svc.cluster.local:8766", "x" * 32)
        for value in (0, 81, True, "8"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.create_session(
                    task_id="demo#1", run_id="a" * 12, profile="builder",
                    provider="openai-api", model="iq_3s", reasoning="high", max_turns=value,
                )


if __name__ == "__main__":
    unittest.main()
