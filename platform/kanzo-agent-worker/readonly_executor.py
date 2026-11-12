#!/usr/bin/env python3
"""Hermes executor isolated from project writes by a read-only workspace mount."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKSPACE = Path(os.environ.get("KANZO_WORKSPACE", "/workspace")).resolve()
HOME = Path(os.environ.get("HERMES_HOME", "/opt/data/home"))


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/run":
            self.send_error(404)
            return
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            command = payload["command"]
            cwd = Path(payload["cwd"]).resolve()
            if not isinstance(command, list) or len(command) < 4 or command[:2] != ["hermes", "--profile"] or command[2] not in {"researcher", "builder", "reviewer"}:
                raise ValueError("invalid Hermes command")
            if not (cwd == WORKSPACE or WORKSPACE in cwd.parents):
                raise ValueError("cwd is outside the mounted workspace")
            if not cwd.is_dir():
                raise ValueError("cwd does not exist")
        except Exception as exc:
            self.send_error(400, str(exc))
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        env = dict(os.environ)
        env["HOME"] = str(HOME)
        env["HERMES_HOME"] = str(HOME)
        if env.get("LITELLM_API_KEY"):
            env["OPENAI_API_KEY"] = env["LITELLM_API_KEY"]
        if env.get("LITELLM_BASE_URL"):
            env["OPENAI_BASE_URL"] = env["LITELLM_BASE_URL"]
        env.pop("MCPPROXY_KANZO_AGENT_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        env.pop("GH_TOKEN", None)
        try:
            process = subprocess.Popen(
                command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True,
            )
            assert process.stdout is not None
            events: queue.Queue[str | None] = queue.Queue()

            def read_stdout() -> None:
                try:
                    for output in process.stdout:
                        events.put(output)
                finally:
                    events.put(None)

            threading.Thread(target=read_stdout, daemon=True).start()
            deadline = time.monotonic() + 2700
            while True:
                now = time.monotonic()
                if now >= deadline:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                    code = 124
                    break
                try:
                    line = events.get(timeout=min(30, deadline - now))
                except queue.Empty:
                    self.wfile.write(b'{"type":"executor_heartbeat"}\n')
                    self.wfile.flush()
                    continue
                if line is None:
                    code = process.wait()
                    break
                self.wfile.write(line.encode("utf-8", errors="replace"))
                self.wfile.flush()
        except Exception:
            code = 1
        try:
            self.wfile.write((json.dumps({"type": "executor_exit", "code": code}) + "\n").encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
