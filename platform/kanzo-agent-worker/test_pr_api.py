from __future__ import annotations

import unittest

from worker import WorkerError, _draft_pr_update_command


class DraftPrUpdateCommandTests(unittest.TestCase):
    def test_existing_pr_update_uses_github_rest_api(self) -> None:
        command = _draft_pr_update_command(
            "https://github.com/owner/repo/pull/5", 5, "title", "body",
        )
        self.assertEqual(command[:5], ["gh", "api", "repos/owner/repo/pulls/5", "--method", "PATCH"])
        self.assertIn("title=title", command)
        self.assertIn("body=body", command)

    def test_existing_pr_update_rejects_mismatched_or_external_urls(self) -> None:
        for url, number in (("https://github.com/owner/repo/pull/6", 5), ("https://example.com/owner/repo/pull/5", 5)):
            with self.subTest(url=url), self.assertRaises(WorkerError):
                _draft_pr_update_command(url, number, "title", "body")


if __name__ == "__main__":
    unittest.main()
