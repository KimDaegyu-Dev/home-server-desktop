"""Guard the low-overhead human-agent-report integration."""

from __future__ import annotations

import unittest

from worker import (
    ProjectRoute,
    Task,
    format_worker_comment,
    parse_worker_outcome,
    strip_expected_readonly_mutation_footer,
    task_prompt,
)


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

        self.assertIn('skill_view(name="productivity/human-agent-report")', prompt)
        self.assertIn("follow its Compose and evidence-contract guidance", prompt)
        self.assertIn("exactly once immediately before finalizing", prompt)
        self.assertIn("If it was already loaded in this run, reuse it.", prompt)
        self.assertIn("ordinarily keep the comment under 1,200 characters", prompt)
        self.assertIn("Use MCPProxy discovery narrowly", prompt)
        self.assertIn("Do not repeat discovery", prompt)
        self.assertIn("verify the action is not already satisfied", prompt)
        self.assertIn("Do not create report artifacts", prompt)
        self.assertIn("Do not create report artifacts or make a second model call", prompt)
        self.assertNotIn("/human-agent-report compose", prompt)
        self.assertNotIn("~/.hermes/skills", prompt)

    def test_comment_formatter_removes_duplicate_separator_artifacts(self) -> None:
        self.assertEqual(
            format_worker_comment("Summary.\n\n--\n-\n\n# Evidence\n- verified"),
            "Summary.\n\n# Evidence\n- verified",
        )

    def test_expected_readonly_verifier_footer_is_removed_from_comment(self) -> None:
        comment = (
            "Read-only checks passed; workspace writes were correctly rejected.\n\n"
            "⚠️ File-mutation verifier: 1 file edit(s) FAILED this turn\n"
            "• `/workspace/.kanzo-ro-file-marker` — [write_file] Read-only file system"
        )

        self.assertEqual(
            strip_expected_readonly_mutation_footer(comment, read_only=True),
            "Read-only checks passed; workspace writes were correctly rejected.",
        )

    def test_readonly_footer_filter_preserves_real_or_unrelated_failures(self) -> None:
        external_path = (
            "Result.\n\nFile-mutation verifier: 1 file edit(s) FAILED this turn\n"
            "• `/tmp/output.md` — [write_file] Read-only file system"
        )
        non_readonly = (
            "Result.\n\nFile-mutation verifier: 1 file edit(s) FAILED this turn\n"
            "• `/workspace/output.md` — [write_file] Read-only file system"
        )
        actual_failure = (
            "Result.\n\nFile-mutation verifier: 1 file edit(s) FAILED this turn\n"
            "• `/workspace/output.md` — [write_file] Permission denied by policy"
        )

        self.assertEqual(strip_expected_readonly_mutation_footer(external_path, read_only=True), external_path)
        self.assertEqual(strip_expected_readonly_mutation_footer(non_readonly, read_only=False), non_readonly)
        self.assertEqual(strip_expected_readonly_mutation_footer(actual_failure, read_only=True), actual_failure)

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
