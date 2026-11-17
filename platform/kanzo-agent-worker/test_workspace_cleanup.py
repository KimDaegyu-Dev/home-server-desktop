from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from worker import delete_remote_branch_with_lease, exact_branch_ref_sha


class ExactBranchRefShaTests(unittest.TestCase):
    def test_returns_sha_for_exact_ref(self) -> None:
        sha = "a" * 40
        refs = [
            {"ref": "refs/heads/kanzo/workspaces/demo-extended", "object": {"sha": "b" * 40}},
            {"ref": "refs/heads/kanzo/workspaces/demo", "object": {"sha": sha}},
        ]
        self.assertEqual(exact_branch_ref_sha(refs, "refs/heads/kanzo/workspaces/demo"), sha)

    def test_prefix_match_is_not_treated_as_the_exact_branch(self) -> None:
        refs = [{"ref": "refs/heads/kanzo/workspaces/demo-extended", "object": {"sha": "a" * 40}}]
        self.assertIsNone(exact_branch_ref_sha(refs, "refs/heads/kanzo/workspaces/demo"))

    def test_missing_or_malformed_sha_fails_closed(self) -> None:
        self.assertEqual(exact_branch_ref_sha([{"ref": "refs/heads/demo", "object": {}}], "refs/heads/demo"), "")
        self.assertEqual(exact_branch_ref_sha({}, "refs/heads/demo"), "")


class ConditionalBranchDeleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.origin = Path(self.temp.name) / "origin.git"
        subprocess.run(["git", "init", "--bare", str(self.origin)], check=True, capture_output=True)
        self.source = Path(self.temp.name) / "source"
        self.source.mkdir()
        subprocess.run(["git", "init", str(self.source)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.source), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.source), "config", "user.email", "test@example.invalid"], check=True)
        (self.source / "file.txt").write_text("content" + chr(10))
        subprocess.run(["git", "-C", str(self.source), "add", "file.txt"], check=True)
        subprocess.run(["git", "-C", str(self.source), "commit", "-m", "initial"], check=True, capture_output=True)
        self.sha = subprocess.run(["git", "-C", str(self.source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "-C", str(self.source), "push", str(self.origin), "HEAD:refs/heads/kanzo/workspaces/demo"], check=True, capture_output=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_expected_sha_deletes_branch(self) -> None:
        result = delete_remote_branch_with_lease(str(self.origin), "kanzo/workspaces/demo", self.sha, {})
        self.assertEqual(result.returncode, 0, result.stderr)
        refs = subprocess.run(["git", "--git-dir", str(self.origin), "show-ref", "--verify", "refs/heads/kanzo/workspaces/demo"], capture_output=True)
        self.assertNotEqual(refs.returncode, 0)

    def test_stale_expected_sha_preserves_branch(self) -> None:
        result = delete_remote_branch_with_lease(str(self.origin), "kanzo/workspaces/demo", "f" * 40, {})
        self.assertNotEqual(result.returncode, 0)
        refs = subprocess.run(["git", "--git-dir", str(self.origin), "rev-parse", "refs/heads/kanzo/workspaces/demo"], check=True, capture_output=True, text=True)
        self.assertEqual(refs.stdout.strip(), self.sha)


if __name__ == "__main__":
    unittest.main()
