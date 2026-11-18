from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from worker import Task, Worker, WorkerError


class GitPushPreflightTests(unittest.TestCase):
    def make_worker(self, root: str, token: str = "secret-token") -> Worker:
        worker = object.__new__(Worker)
        worker.env = {"GITHUB_TOKEN": token}
        worker.workspace = Path(root)
        worker._hermes_env = lambda: {}
        worker._redact_sensitive_output = lambda value: value.replace(token, "[REDACTED]")
        worker._run_active_command = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        return worker

    def test_success_uses_remote_push_dry_run_without_leaving_askpass_file(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            worker = self.make_worker(root)
            with patch("worker.shutil.which", return_value="/usr/bin/gh"):
                worker._preflight_git_push(
                    Task("demo#1", "demo", "doing", "title", (), ""),
                    Path(root),
                    {"branch": "kanzo/workspaces/demo"},
                )
            args, kwargs = worker._run_active_command.call_args
            self.assertIn("--dry-run", args[0])
            self.assertIn("HEAD:refs/heads/kanzo/workspaces/demo", args[0])
            self.assertEqual(kwargs["env"]["KANZO_GIT_TOKEN"], "secret-token")
            self.assertFalse(list(Path(root).glob("git-askpass-preflight-*.sh")))

    def test_denied_preflight_fails_before_hermes_can_start(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            worker = self.make_worker(root)
            worker._run_active_command.return_value = subprocess.CompletedProcess([], 128, "", "403 denied")
            with patch("worker.shutil.which", return_value="/usr/bin/gh"):
                with self.assertRaisesRegex(WorkerError, "Hermes was not started"):
                    worker._preflight_git_push(
                        Task("demo#1", "demo", "doing", "title", (), ""),
                        Path(root),
                        {"branch": "kanzo/workspaces/demo"},
                    )
            self.assertEqual(worker._run_active_command.call_count, 1)
            self.assertFalse(list(Path(root).glob("git-askpass-preflight-*.sh")))

    def test_missing_gh_fails_before_git_or_hermes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            worker = self.make_worker(root)
            with patch("worker.shutil.which", return_value=None):
                with self.assertRaisesRegex(WorkerError, "GitHub CLI gh.*Hermes was not started"):
                    worker._preflight_git_push(
                        Task("demo#1", "demo", "doing", "title", (), ""),
                        Path(root),
                        {"branch": "kanzo/workspaces/demo"},
                    )
            worker._run_active_command.assert_not_called()
            self.assertFalse(list(Path(root).glob("git-askpass-preflight-*.sh")))

    def test_missing_token_fails_without_git_call(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            worker = self.make_worker(root, token="")
            with self.assertRaises(WorkerError):
                worker._preflight_git_push(Task("demo#1", "demo", "doing", "title", (), ""), Path(root), {"branch": "kanzo/workspaces/demo"})
            worker._run_active_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
