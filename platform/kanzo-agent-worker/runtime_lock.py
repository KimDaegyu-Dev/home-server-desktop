"""Serialize access to the persistent Hermes home and executor ledger."""

from __future__ import annotations

import argparse
import os
import stat
import sys
import time
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _open_lock(path: Path) -> int:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError("runtime_lock_file_unsafe")
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        return fd
    except BaseException:
        os.close(fd)
        raise


def acquire_lock(path: Path) -> int:
    """Hold an exclusive process lock until the returned fd is closed."""
    fd = _open_lock(path)
    try:
        if os.name == "nt":
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX)
    except BaseException:
        os.close(fd)
        raise
    return fd


def release_lock(fd: int) -> None:
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _try_lock(path: Path) -> int | None:
    fd = _open_lock(path)
    try:
        if os.name == "nt":
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as exc:
        if os.name != "nt" and not isinstance(exc, BlockingIOError):
            os.close(fd)
            raise
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return fd


def wait_until_unowned(paths: list[Path], poll_seconds: float = 1.0) -> None:
    """Wait for all previous owners, holding both locks during each availability check."""
    announce_at = time.monotonic() + 30
    while True:
        held: list[int] = []
        available = True
        try:
            for path in sorted(paths, key=lambda value: str(value)):
                fd = _try_lock(path)
                if fd is None:
                    available = False
                    break
                held.append(fd)
        finally:
            for fd in reversed(held):
                release_lock(fd)
                os.close(fd)
        if available:
            return
        if time.monotonic() >= announce_at:
            print("waiting_for_previous_hermes_runtime_owner", flush=True)
            announce_at = time.monotonic() + 30
        time.sleep(max(0.1, poll_seconds))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("wait",))
    args = parser.parse_args()
    home = Path(os.environ.get("HERMES_HOME", "/opt/data/home"))
    state_dir = Path(os.environ.get(
        "KANZO_EXECUTOR_DATA_DIR", str(home / "kanzo-worker" / "executor-state")
    ))
    wait_until_unowned([home / "gateway.lock", state_dir / "executor-api.lock"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
