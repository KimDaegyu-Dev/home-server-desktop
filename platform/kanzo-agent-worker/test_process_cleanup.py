"""Verify executor cancellation stops helper descendants, not just the leader."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from executor_api import ApiError, _run_helper
from readonly_executor import terminate_process_group


@unittest.skipUnless(os.name == "posix", "process-group cleanup uses POSIX signals")
class ProcessGroupCleanupTests(unittest.TestCase):
    @staticmethod
    def start_parent(*, child_ignores_term: bool) -> tuple[subprocess.Popen[str], int]:
        handler = "signal.signal(signal.SIGTERM, signal.SIG_IGN); " if child_ignores_term else ""
        child = (
            "import signal,time; " + handler + "time.sleep(60)"
        )
        parent = (
            "import subprocess,sys; "
            f"child=subprocess.Popen([sys.executable, '-c', {child!r}], stdout=subprocess.DEVNULL); "
            "print(child.pid, flush=True); import time; time.sleep(60)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", parent],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        assert process.stdout is not None
        line = process.stdout.readline().strip()
        process.stdout.close()
        if not line.isdecimal():
            process.kill()
            process.wait(timeout=2)
            raise AssertionError(f"helper child did not report a pid: {line!r}")
        return process, int(line)

    @staticmethod
    def assert_process_stopped(test: unittest.TestCase, pid: int) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                state = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()[2]
            except FileNotFoundError:
                return
            if state == "Z":
                return
            time.sleep(0.05)
        test.fail(f"helper descendant {pid} is still running")

    def test_executor_api_timeout_kills_sigterm_ignoring_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pid_file = Path(temp) / "child.pid"
            child = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
            parent = (
                "import pathlib,subprocess,sys,time; "
                f"child=subprocess.Popen([sys.executable, '-c', {child!r}], stdout=subprocess.DEVNULL); "
                f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid)); time.sleep(60)"
            )
            with self.assertRaises(ApiError) as caught:
                _run_helper([sys.executable, "-c", parent], timeout=0.2, cwd=temp)
            self.assertEqual(caught.exception.status, 504)
            self.assertTrue(pid_file.is_file())
            self.assert_process_stopped(self, int(pid_file.read_text(encoding="ascii")))

    def test_readonly_executor_terminates_parent_and_graceful_descendant(self) -> None:
        process, child_pid = self.start_parent(child_ignores_term=False)
        try:
            terminate_process_group(process)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
        self.assert_process_stopped(self, child_pid)


if __name__ == "__main__":
    unittest.main()
