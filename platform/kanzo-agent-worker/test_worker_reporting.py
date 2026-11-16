"""Guard the low-overhead human-agent-report integration."""

from __future__ import annotations

import unittest

from worker import ProjectRoute, Task, format_worker_comment, parse_worker_outcome, task_prompt


class WorkerReportingPromptTests(unittest.TestCase):
    def test_final_kanzo_report_uses_skill_loader_without_artifact_churn(self) -> None:
        route = ProjectRoute(
            slug="test-1",
            repo=None,
            model_lane="analysis",
            provider="openai-api",
            model="iq_3s",
            reason="high",
            profile="researcher",
            read_only=True,
        )
        task = Task(
            task_id="test-1#1",
            project="test-1",
            status="doing",
            title="Canary report",
            tags=("smoke-test",),
            details="Verify a concise Kanzo report.",
            route=route,
        )

        prompt = task_prompt(task, code=False)

        self.assertIn("Apply those rules directly from this instruction", prompt)
        self.assertIn("target at most 1,200 characters", prompt)
        self.assertIn("Do not create `report.md` or `facts.json`", prompt)
        self.assertIn("Do not make a second model call", prompt)
        self.assertNotIn("/human-agent-report compose", prompt)
        self.assertNotIn("~/.hermes/skills", prompt)

    def test_comment_formatter_removes_duplicate_separator_artifacts(self) -> None:
        self.assertEqual(
            format_worker_comment("Summary.\n\n--\n-\n\n# Evidence\n- verified"),
            "Summary.\n\n# Evidence\n- verified",
        )

    def test_tool_free_exact_response_keeps_protocol_markers_out_of_comment(self) -> None:
        task = Task(
            task_id="test-1#2",
            project="test-1",
            status="doing",
            title="Exact response smoke",
            tags=("tool-free",),
            details="Reply exactly EVENT_WAKE_OK.",
        )
        prompt = task_prompt(task, code=False)
        result = (
            "EVENT_WAKE_OK\n"
            "KANZO_WORKER_OUTCOME: done\n"
            "KANZO_WORKER_SUMMARY: exact response returned"
        )

        outcome, comment = parse_worker_outcome(result)

        self.assertIn("keep that exact response as the answer body", prompt)
        self.assertIn("protocol markers are not part of the human-facing response", prompt)
        self.assertEqual(outcome, "done")
        self.assertEqual(comment, "EVENT_WAKE_OK")


if __name__ == "__main__":
    unittest.main()
