"""Guard the low-overhead human-agent-report integration."""

from __future__ import annotations

import unittest

from worker import ProjectRoute, Task, task_prompt


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

        self.assertIn("/human-agent-report compose", prompt)
        self.assertIn("invoke the installed skill once", prompt)
        self.assertIn("Do not access it through a literal filesystem path", prompt)
        self.assertIn("Do not create `report.md` or `facts.json`", prompt)
        self.assertIn("without a second model call", prompt)
        self.assertNotIn("~/.hermes/skills", prompt)


if __name__ == "__main__":
    unittest.main()
