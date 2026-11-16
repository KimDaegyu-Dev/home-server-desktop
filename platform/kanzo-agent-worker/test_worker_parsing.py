"""Regression tests for parsing Kanzo claim responses."""

from __future__ import annotations

import unittest

from worker import parse_task_line


class ParseTaskLineTests(unittest.TestCase):
    def test_legacy_status_line_is_accepted(self) -> None:
        self.assertEqual(
            parse_task_line("hermes-harness-lab#97 [doing] Delegated child canary"),
            ("hermes-harness-lab#97", "doing", "Delegated child canary"),
        )

    def test_bracketed_title_is_not_mistaken_for_status(self) -> None:
        response = (
            "Moved to doing: hermes-harness-lab#97 [Canary] delegated child sandbox\n"
            "hermes-harness-lab#97 [Canary] delegated child sandbox\n"
            "status: doing, human: false"
        )
        self.assertEqual(
            parse_task_line(response),
            ("hermes-harness-lab#97", "doing", "[Canary] delegated child sandbox"),
        )

    def test_unknown_bracket_without_status_metadata_is_rejected(self) -> None:
        self.assertIsNone(parse_task_line("hermes-harness-lab#97 [Canary] delegated child sandbox"))


if __name__ == "__main__":
    unittest.main()
