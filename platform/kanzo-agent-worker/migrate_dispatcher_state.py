#!/usr/bin/env python3
"""Copy dispatcher state for a quiescent cutover; stop the worker first.

Roots contain run-ledger.sqlite3 and artifacts/. Hermes HOME, executor state,
and heartbeat files are deliberately outside this migration's scope.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile

from run_ledger import STATES, UNFINISHED_STATES


class MigrationError(Exception):
    """A migration safety check failed; messages must contain no stored data."""


MIGRATION_MARKER = ".kanzo-dispatcher-state-migrated-v1"
SUPPORTED_LEDGER_SCHEMAS = {1: {"runs"}, 2: {"runs", "claim_intents"}}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MigrationError(message)


def real_entry(path: Path, directory: bool) -> None:
    info = path.lstat()
    require(
        not path.is_symlink()
        and not getattr(path, "is_junction", lambda: False)()
        and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)),
        "State contains a link or an unsupported filesystem entry.",
    )


def inventory(root: Path) -> dict[str, tuple[str, int, str]]:
    """Include empty directories and file digests, without reading JSON values."""
    real_entry(root, True)
    entries = {}
    for directory, directories, files in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in directories:
            path = base / name
            real_entry(path, True)
            entries[path.relative_to(root).as_posix()] = ("directory", 0, "")
        for name in files:
            path = base / name
            real_entry(path, False)
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            entries[path.relative_to(root).as_posix()] = (
                "file", path.stat().st_size, digest.hexdigest()
            )
    return entries


def verify_schema(connection: sqlite3.Connection) -> int:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}
    require(
        version in SUPPORTED_LEDGER_SCHEMAS and tables == SUPPORTED_LEDGER_SCHEMAS[version],
        "Unsupported ledger schema version.",
    )
    return version


def verify_database(connection: sqlite3.Connection) -> dict[str, int]:
    require(
        connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
        "Ledger integrity check failed.",
    )
    verify_schema(connection)
    counts = dict(connection.execute("SELECT state, COUNT(*) FROM runs GROUP BY state"))
    require(set(counts) <= STATES, "Ledger contains unknown run states.")
    require(not any(counts.get(state, 0) for state in UNFINISHED_STATES),
            "Source ledger has unfinished runs; resolve them before cutover.")
    return counts


def migrate(source_arg: str, destination_arg: str) -> tuple[int, int, bool]:
    source = Path(source_arg).expanduser().resolve(strict=True)
    destination = Path(destination_arg).expanduser().resolve()
    require(source != destination and source not in destination.parents
            and destination not in source.parents,
            "Source and destination must be disjoint paths.")
    real_entry(source, True)
    ledger = source / "run-ledger.sqlite3"
    artifacts = source / "artifacts"
    real_entry(ledger, False)
    real_entry(artifacts, True)
    marker = destination / MIGRATION_MARKER
    if destination.exists() and marker.exists():
        real_entry(destination, True)
        real_entry(marker, False)
        require(marker.read_bytes() == b"verified\n", "Migration marker is invalid.")
        existing_ledger = destination / ledger.name
        existing_artifacts = destination / "artifacts"
        real_entry(existing_ledger, False)
        real_entry(existing_artifacts, True)
        with sqlite3.connect(existing_ledger.as_uri() + "?mode=ro", uri=True) as check:
            require(
                check.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
                "Previously migrated ledger integrity check failed.",
            )
            verify_schema(check)
        return 0, 0, True
    if destination.exists():
        real_entry(destination, True)
        require(not any(destination.iterdir()), "Destination must be empty.")
    else:
        destination.mkdir(parents=True, mode=0o700)

    # Reserve the destination against concurrent invocations of this utility.
    reservation = destination / ".dispatcher-migration.lock"
    descriptor = os.open(reservation, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    staging = None
    published = []
    guard = reader = None
    try:
        require(set(destination.iterdir()) == {reservation}, "Destination is no longer empty.")
        # BEGIN IMMEDIATE blocks ledger writers during backup and artifact copying.
        # A separate reader is needed: backing up the write connection can hang.
        guard = sqlite3.connect(ledger.as_uri() + "?mode=rw", uri=True, timeout=0)
        guard.execute("BEGIN IMMEDIATE")
        reader = sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True, timeout=0)
        reader.execute("BEGIN")
        counts = verify_database(reader)
        before = inventory(artifacts)
        staging = Path(tempfile.mkdtemp(prefix=".dispatcher-migration-", dir=destination))
        copied_db = staging / ledger.name
        with sqlite3.connect(copied_db) as copied:
            reader.backup(copied)
            require(verify_database(copied) == counts, "Copied ledger row counts differ.")
            # Publish a self-contained database, without a required WAL sidecar.
            copied.execute("PRAGMA journal_mode=DELETE")
        copied_db.chmod(0o600)
        shutil.copytree(artifacts, staging / "artifacts")
        for directory, _, files in os.walk(staging / "artifacts"):
            Path(directory).chmod(0o700)
            for name in files:
                (Path(directory) / name).chmod(0o600)
        require(inventory(staging / "artifacts") == before,
                "Copied artifact counts or contents differ.")
        require(inventory(artifacts) == before, "Source artifacts changed during migration.")
        # Reopen independently, rather than trusting only the backup connection.
        with sqlite3.connect(copied_db.as_uri() + "?mode=ro", uri=True) as check:
            require(verify_database(check) == counts, "Copied database verification failed.")
        require(set(destination.iterdir()) == {reservation, staging},
                "Destination changed during migration.")
        for name in (ledger.name, "artifacts"):
            target = destination / name
            require(not target.exists(), "Destination entry already exists.")
            (staging / name).rename(target)
            published.append(target)
        staging.rmdir()
        staging = None
        marker_fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        published.append(marker)
        try:
            os.write(marker_fd, b"verified\n")
            os.fsync(marker_fd)
        finally:
            os.close(marker_fd)
        return sum(counts.values()), sum(value[0] == "file" for value in before.values()), False
    except BaseException:
        # Remove only entries created by this invocation; preserve source state.
        for path in reversed(published):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        raise
    finally:
        if reader is not None:
            reader.close()
        if guard is not None:
            guard.close()
        if staging is not None:
            shutil.rmtree(staging)
        reservation.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Stopped worker's existing state root")
    parser.add_argument("--destination", required=True, help="New, empty dispatcher state root")
    args = parser.parse_args()
    try:
        rows, files, already_migrated = migrate(args.source, args.destination)
    except MigrationError as exc:
        print(f"Migration refused: {exc}", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error):
        # Filesystem/SQLite exception strings may include sensitive paths or data.
        print("Migration failed: filesystem or SQLite operation failed; keep worker stopped and inspect destination.",
              file=sys.stderr)
        return 1
    if already_migrated:
        print("Migration already completed; destination was left unchanged.")
    else:
        print(f"Migration verified: {rows} ledger rows, {files} artifact files. Source retained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
