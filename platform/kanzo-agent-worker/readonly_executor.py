#!/usr/bin/env python3
"""Hermes executor isolated from project writes by a read-only workspace mount."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKSPACE = Path(os.environ.get("KANZO_WORKSPACE", "/workspace")).resolve()
PROFILE_SOURCE_HOME = Path(os.environ.get("HERMES_PROFILE_SOURCE_HOME", "/opt/data/home"))
ISOLATED_HOME_ROOT = Path(os.environ.get("HERMES_HOME", "/opt/data/readonly-hermes"))
ACTIVE_LOCK = threading.Lock()
ACTIVE_RUNS: dict[tuple[str, str], dict[str, object]] = {}
PENDING_CANCELS: dict[tuple[str, str], float] = {}
SESSION_LINE_RE = re.compile(r"^\s*(?:Session|session_id):\s*([A-Za-z0-9._:-]{8,128})\s*$", re.IGNORECASE)
LOG = logging.getLogger("kanzo-readonly-executor")


def terminate_process_group(process: subprocess.Popen[str]) -> None:
    """Give Hermes and its tools a grace period, then force-stop the group."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        process.poll()  # Reap Hermes itself while checking its remaining tool group.
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def interrupt_gateway_session(record: dict[str, object], identity: tuple[str, str]) -> bool:
    """Cancel the exact isolated Hermes session; never treat viewer termination as cancellation."""
    session_id = record.get("session_id")
    profile_home = record.get("profile_home")
    process = record.get("process")
    if not isinstance(session_id, str) or not session_id or not isinstance(profile_home, Path):
        return False
    if record.get("interrupt_accepted") is True:
        return True
    control_env = dict(os.environ)
    for name in (
        "MCPPROXY_KANZO_AGENT_TOKEN", "MCPPROXY_AGENT_READ_TOKEN", "GITHUB_TOKEN", "GH_TOKEN",
        "LITELLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    ):
        control_env.pop(name, None)
    control_env["HERMES_HOME"] = str(profile_home)
    control_env["HOME"] = str(profile_home)
    control_env["PYTHONPATH"] = os.pathsep.join(
        part for part in ("/opt/hermes", "/opt/kanzo-agent", control_env.get("PYTHONPATH", "")) if part
    )
    command = [
        "/opt/hermes/.venv/bin/python", "/opt/kanzo-agent/gateway_control.py", "interrupt",
        "--profile-home", str(profile_home), "--session-id", session_id,
    ]
    try:
        completed = subprocess.run(
            command, cwd="/opt/hermes", env=control_env, capture_output=True,
            text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    response = None
    for output_line in reversed(completed.stdout.splitlines()):
        try:
            candidate = json.loads(output_line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            response = candidate
            break
    if (
        completed.returncode != 0
        or not response
        or response.get("accepted") is not True
        or response.get("session_id") != session_id
    ):
        return False
    record["interrupt_accepted"] = True
    generation = response.get("execution_generation")
    if isinstance(generation, int) and not isinstance(generation, bool):
        record["execution_generation"] = generation
    if isinstance(process, subprocess.Popen) and process.poll() is None:
        terminate_process_group(process)
    return True


def sync_profile(profile: str) -> Path:
    """Copy user-managed profile settings into a separate gateway home.

    Hermes chat is a gateway client; without this separate home it attaches to
    the shared writable gateway, whose tools do not inherit this container's
    read-only workspace mount.
    """
    if profile not in {"researcher", "builder", "reviewer"}:
        raise ValueError("unsupported Hermes profile")
    source = PROFILE_SOURCE_HOME / "profiles" / profile
    destination = ISOLATED_HOME_ROOT / "profiles" / profile
    if not (source / "config.yaml").is_file():
        raise ValueError(f"Hermes profile {profile!r} is not initialized")
    destination.mkdir(parents=True, exist_ok=True)
    for name in (
        "config.yaml", ".env", "SOUL.md", "USER.md",
        "system_prompt.md", "AGENTS.md", "CLAUDE.md", ".cursorrules", "auth.json",
    ):
        src, dst = source / name, destination / name
        if src.is_file():
            shutil.copy2(src, dst)
    # Hermes resolves plugin credentials through the active profile's secret
    # scope. In a multiplexed gateway, ordinary process environment variables
    # are deliberately not used as a fallback for a routed profile. Carry the
    # VSO-injected Langfuse values into this isolated profile's private dotenv
    # file so its Gateway can build the profile-scoped trace client.
    dotenv = destination / ".env"
    injected = {
        name: os.environ[name]
        for name in (
            "HERMES_LANGFUSE_PUBLIC_KEY",
            "HERMES_LANGFUSE_SECRET_KEY",
            "HERMES_LANGFUSE_BASE_URL",
        )
        if os.environ.get(name)
    }
    if injected:
        existing = dotenv.read_text(encoding="utf-8").splitlines() if dotenv.exists() else []
        keys = set(injected)
        kept = [line for line in existing if line.partition("=")[0].strip() not in keys]
        dotenv.write_text(
            "\n".join([*kept, *(f"{name}={value}" for name, value in injected.items())]) + "\n",
            encoding="utf-8",
        )
        dotenv.chmod(0o600)
    _sync_profile_state(source, destination)
    return destination


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sync_profile_state(source: Path, destination: Path) -> None:
    """Three-way-sync mutable Hermes profile files without erasing local learning.

    The source profile remains authoritative for ordinary settings files. For
    skills, plugins, scripts, .omh data, and MEMORY.md, this records the last
    source hash. Source updates flow into an unchanged destination; a local
    destination edit wins on conflict and is logged for later review.
    """
    state_path = destination / ".kanzo-profile-sync.json"
    if state_path.is_symlink():
        LOG.error("Refusing to follow ReadOnly profile sync manifest symlink at %s", state_path)
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        LOG.warning("Ignoring invalid ReadOnly profile sync manifest at %s", state_path)
        state = {}
    previous = state.get("source_hashes", {}) if isinstance(state, dict) else {}
    if not isinstance(previous, dict):
        previous = {}

    sources: dict[str, Path] = {}
    for tree_name in ("skills", "plugins", "scripts", ".omh"):
        source_root = source / tree_name
        if not source_root.is_dir():
            continue
        for src in sorted(source_root.rglob("*")):
            if src.is_symlink() or not src.is_file():
                continue
            relative = src.relative_to(source).as_posix()
            sources[relative] = src
    memory_source = source / "MEMORY.md"
    if memory_source.is_file() and not memory_source.is_symlink():
        sources["MEMORY.md"] = memory_source

    current: dict[str, str] = {}
    destination_root = destination.resolve()

    def destination_path(relative: str) -> Path | None:
        if relative != "MEMORY.md" and not relative.startswith(("skills/", "plugins/", "scripts/", ".omh/")):
            return None
        candidate = destination / relative
        try:
            candidate.resolve(strict=False).relative_to(destination_root)
        except (OSError, ValueError):
            LOG.warning("Refusing ReadOnly profile path outside its home: %s", relative)
            return None
        return candidate

    for relative, src in sources.items():
        source_hash = _file_sha256(src)
        dst = destination_path(relative)
        if dst is None:
            continue
        old_source_hash = previous.get(relative)
        if dst.is_symlink():
            LOG.warning("Preserving symlink in ReadOnly profile state: %s", dst)
            current[relative] = source_hash
            continue
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        elif not dst.is_file():
            LOG.warning("Preserving non-file ReadOnly profile state at %s", dst)
        else:
            destination_hash = _file_sha256(dst)
            if old_source_hash is None:
                # First sync after upgrading from the old destructive copier:
                # keep the already-persistent destination and establish a base.
                if destination_hash != source_hash:
                    LOG.info("Preserving existing ReadOnly profile file on initial sync: %s", relative)
            elif destination_hash == old_source_hash:
                if source_hash != old_source_hash:
                    shutil.copy2(src, dst)
            elif source_hash != old_source_hash and destination_hash != source_hash:
                LOG.warning("ReadOnly profile sync conflict; keeping local file: %s", relative)
        current[relative] = source_hash

    for relative, old_source_hash in previous.items():
        if relative in sources or not isinstance(relative, str) or not isinstance(old_source_hash, str):
            continue
        dst = destination_path(relative)
        if dst is None:
            continue
        if dst.is_symlink() or not dst.is_file():
            continue
        if _file_sha256(dst) == old_source_hash:
            dst.unlink()
        else:
            LOG.warning("Preserving locally changed file removed from source profile: %s", relative)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".json.tmp")
    if temporary.is_symlink():
        LOG.error("Refusing to follow ReadOnly profile sync temporary symlink at %s", temporary)
        return
    temporary.write_text(
        json.dumps({"version": 1, "source_hashes": current}, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(state_path)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/cancel":
            try:
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                identity = (payload["task_id"], payload["run_id"])
                if not re.fullmatch(r"[A-Za-z0-9_.-]+#\d+", identity[0]) or not re.fullmatch(r"[a-f0-9]{12}", identity[1]):
                    raise ValueError("invalid task/run identity")
            except Exception as exc:
                self.send_error(400, str(exc))
                return
            with ACTIVE_LOCK:
                record = ACTIVE_RUNS.get(identity)
                if record is not None:
                    record["cancel_requested"] = True
            if record is None:
                # The cancellation may beat the /run request by a few ms. Keep
                # this exact random run identity until its process registers.
                with ACTIVE_LOCK:
                    cutoff = time.monotonic() - 300
                    for key, created in list(PENDING_CANCELS.items()):
                        if created < cutoff:
                            PENDING_CANCELS.pop(key, None)
                    PENDING_CANCELS[identity] = time.monotonic()
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b'{"accepted":true,"pending":true}')))
                self.end_headers()
                self.wfile.write(b'{"accepted":true,"pending":true}')
                return
            accepted = interrupt_gateway_session(record, identity)
            self.send_response(202 if accepted else 409)
            body = json.dumps({
                "accepted": accepted,
                "session_id": record.get("session_id"),
                "execution_generation": record.get("execution_generation"),
            }, separators=(",", ":")).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/run":
            self.send_error(404)
            return
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            command = payload["command"]
            cwd = Path(payload["cwd"]).resolve()
            identity = (payload["task_id"], payload["run_id"])
            if not re.fullmatch(r"[A-Za-z0-9_.-]+#\d+", identity[0]) or not re.fullmatch(r"[a-f0-9]{12}", identity[1]):
                raise ValueError("invalid task/run identity")
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
        profile = command[2]
        try:
            hermes_home = sync_profile(profile)
            record: dict[str, object] = {
                "process": None,
                "profile_home": hermes_home,
                "session_id": None,
                "execution_generation": None,
                "cancel_requested": False,
                "interrupt_accepted": False,
            }
            with ACTIVE_LOCK:
                cutoff = time.monotonic() - 300
                for key, created in list(PENDING_CANCELS.items()):
                    if created < cutoff:
                        PENDING_CANCELS.pop(key, None)
                pending_since = PENDING_CANCELS.pop(identity, None)
                cancel_was_pending = pending_since is not None and pending_since >= cutoff
                if identity in ACTIVE_RUNS:
                    raise ValueError("task/run identity is already active")
                if not cancel_was_pending:
                    ACTIVE_RUNS[identity] = record
                else:
                    record["cancel_requested"] = True
            if cancel_was_pending:
                self.wfile.write((json.dumps({"type": "executor_exit", "code": 130}) + "\n").encode())
                self.wfile.flush()
                return
        except Exception as exc:
            self.wfile.write((json.dumps({"type": "executor_exit", "code": 1, "error": str(exc)}) + "\n").encode())
            self.wfile.flush()
            return
        env = dict(os.environ)
        env["HOME"] = str(hermes_home)
        env["HERMES_HOME"] = str(hermes_home)
        # Keep Langfuse traces from the read-only executor linked to the same
        # Kanzo card and run identity as writable worker executions.
        env["HERMES_KANBAN_TASK"] = identity[0]
        env["HERMES_KANBAN_RUN_ID"] = identity[1]
        if env.get("LITELLM_API_KEY"):
            env["OPENAI_API_KEY"] = env["LITELLM_API_KEY"]
        if env.get("LITELLM_BASE_URL"):
            env["OPENAI_BASE_URL"] = env["LITELLM_BASE_URL"]
        env.pop("MCPPROXY_KANZO_AGENT_TOKEN", None)
        env.pop("KANZO_BROKER_WORKER_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        env.pop("GH_TOKEN", None)
        try:
            # Drop --profile because HERMES_HOME now names the isolated
            # profile directory itself. This starts/attaches only to the
            # gateway whose tool processes share this container's RO mount.
            isolated_command = [command[0], *command[3:]]
            process = subprocess.Popen(
                isolated_command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True,
            )
            record["process"] = process
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
                    terminate_process_group(process)
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
                session_match = SESSION_LINE_RE.match(line.strip())
                if session_match:
                    record["session_id"] = session_match.group(1)
                self.wfile.write(line.encode("utf-8", errors="replace"))
                self.wfile.flush()
        except Exception:
            code = 1
        finally:
            with ACTIVE_LOCK:
                if "identity" in locals() and ACTIVE_RUNS.get(identity) is locals().get("record"):
                    ACTIVE_RUNS.pop(identity, None)
        try:
            self.wfile.write((json.dumps({"type": "executor_exit", "code": code}) + "\n").encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
