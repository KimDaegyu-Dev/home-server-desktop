"""Durable, single-flight execution ledger for the Kanzo worker."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


class RunLedgerError(RuntimeError):
    """Base exception for ledger operations."""


class LedgerCorruptionError(RunLedgerError):
    """Raised when the database cannot be trusted or understood."""


class InvalidTransitionError(RunLedgerError):
    """Raised when a run state transition is not allowed."""


class ActiveRunError(RunLedgerError):
    """Raised when a second unfinished run is started."""


class DuplicateRunError(RunLedgerError):
    """Raised when a run ID already exists."""


class RunNotFoundError(RunLedgerError):
    """Raised when an operation references an unknown run ID."""


SCHEMA_VERSION = 1
STATES = frozenset(
    {
        "claimed",
        "preparing",
        "running",
        "finalizing",
        "succeeded",
        "review",
        "waiting",
        "cancelled",
        "failed",
        "orphaned",
    }
)
ACTIVE_STATES = ("claimed", "preparing", "running", "finalizing")
UNFINISHED_STATES = (*ACTIVE_STATES, "orphaned")
TRANSITIONS = {
    "claimed": frozenset({"preparing", "cancelled", "failed", "orphaned"}),
    "preparing": frozenset(
        {"running", "review", "waiting", "cancelled", "failed", "orphaned"}
    ),
    "running": frozenset(
        {"finalizing", "review", "waiting", "cancelled", "failed", "orphaned"}
    ),
    "finalizing": frozenset(
        {"succeeded", "review", "waiting", "cancelled", "failed", "orphaned"}
    ),
    "succeeded": frozenset(),
    "review": frozenset(),
    "waiting": frozenset(),
    "cancelled": frozenset(),
    "failed": frozenset(),
    # An operator must explicitly adjudicate a run whose outcome is unknown.
    "orphaned": frozenset({"succeeded", "review", "cancelled", "failed"}),
}

_SECRET_KEY = re.compile(
    r"(?:secret|token|password|credential|authorization|api[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|github_pat_[A-Za-z0-9_]{12,}|mcp_agt_[A-Za-z0-9]{12,})\b"),
)
_PUBLICATION_STEP = re.compile(r"^[a-zA-Z0-9_.:-]{1,128}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sanitize(value: Any, key: str | None = None) -> Any:
    if key is not None and _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(child_key): _sanitize(child_value, str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        result = value
        for pattern in _SECRET_TEXT_PATTERNS:
            result = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + "[REDACTED]", result)
        return result
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise TypeError(f"Unsupported JSON value type: {type(value).__name__}")


def _json_dump(value: Any) -> str:
    return json.dumps(_sanitize(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _json_load(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LedgerCorruptionError("Ledger contains invalid JSON") from exc


def _require_mapping(value: Mapping[str, Any] | None, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return dict(value)


class RunLedger:
    """SQLite execution ledger using durable transactions and single-flight claims.

    A run is active while claimed, preparing, running, or finalizing. An orphaned
    run is also unfinished and blocks a new start until explicitly adjudicated.
    Review, waiting, and other outcome states finish the worker execution and do
    not trigger a retry.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        self._lock = threading.RLock()
        try:
            self._connection = sqlite3.connect(
                str(self.path), timeout=30, isolation_level=None, check_same_thread=False
            )
            self.path.chmod(0o600)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA busy_timeout = 30000")
            self._initialize()
        except (sqlite3.DatabaseError, OSError) as exc:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            raise LedgerCorruptionError("Unable to initialize execution ledger") from exc

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _initialize(self) -> None:
        with self._lock:
            integrity = self._connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise LedgerCorruptionError("Execution ledger integrity check failed")

            version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
            tables = {
                row[0]
                for row in self._connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if version == 0 and not tables:
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA synchronous = FULL")
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    self._connection.execute(
                        """CREATE TABLE runs (
                            run_id TEXT PRIMARY KEY,
                            task_id TEXT NOT NULL,
                            state TEXT NOT NULL CHECK (state IN (
                                'claimed','preparing','running','finalizing','succeeded',
                                'review','waiting','cancelled','failed','orphaned'
                            )),
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            metadata_json TEXT NOT NULL,
                            fields_json TEXT NOT NULL,
                            result_json TEXT,
                            publication_json TEXT NOT NULL
                        )"""
                    )
                    self._connection.execute(
                        "CREATE INDEX runs_state_created ON runs(state, created_at)"
                    )
                    self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                    self._connection.execute("COMMIT")
                except Exception:
                    self._connection.execute("ROLLBACK")
                    raise
            elif version != SCHEMA_VERSION or tables != {"runs"}:
                raise LedgerCorruptionError("Execution ledger schema version is unsupported")

            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._validate_schema()

    def _validate_schema(self) -> None:
        columns = {
            row["name"]: row["type"].upper()
            for row in self._connection.execute("PRAGMA table_info(runs)")
        }
        expected = {
            "run_id": "TEXT",
            "task_id": "TEXT",
            "state": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
            "metadata_json": "TEXT",
            "fields_json": "TEXT",
            "result_json": "TEXT",
            "publication_json": "TEXT",
        }
        if columns != expected:
            raise LedgerCorruptionError("Execution ledger schema does not match its version")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield self._connection
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _check_state(state: str) -> None:
        if state not in STATES:
            raise ValueError(f"Unknown run state: {state!r}")

    @staticmethod
    def _record(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "task_id": row["task_id"],
            "state": row["state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": _json_load(row["metadata_json"]),
            "fields": _json_load(row["fields_json"]),
            "result": _json_load(row["result_json"]) if row["result_json"] is not None else None,
            "publication": _json_load(row["publication_json"]),
        }

    def start(
        self, task_id: str, run_id: str, metadata: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        metadata_json = _json_dump(_require_mapping(metadata, "metadata"))
        now = _now()
        with self._transaction() as connection:
            active = connection.execute(
                f"SELECT run_id FROM runs WHERE state IN ({','.join('?' for _ in UNFINISHED_STATES)}) LIMIT 1",
                UNFINISHED_STATES,
            ).fetchone()
            if active is not None:
                raise ActiveRunError("An unfinished run already exists")
            if connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone():
                raise DuplicateRunError("run_id already exists")
            connection.execute(
                """INSERT INTO runs (
                    run_id, task_id, state, created_at, updated_at,
                    metadata_json, fields_json, result_json, publication_json
                ) VALUES (?, ?, 'claimed', ?, ?, ?, '{}', NULL, '{}')""",
                (run_id, task_id, now, now, metadata_json),
            )
            return self._record(connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())

    def transition(
        self, run_id: str, state: str, fields: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        self._check_state(state)
        field_updates = _require_mapping(fields, "fields")
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise RunNotFoundError("run_id does not exist")
            current = row["state"]
            if state not in TRANSITIONS[current]:
                raise InvalidTransitionError(f"Transition {current!r} -> {state!r} is not allowed")
            stored_fields = _json_load(row["fields_json"])
            stored_fields.update(field_updates)
            connection.execute(
                "UPDATE runs SET state = ?, updated_at = ?, fields_json = ? WHERE run_id = ?",
                (state, _now(), _json_dump(stored_fields), run_id),
            )
            return self._record(connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())

    def record_fields(self, run_id: str, fields: Mapping[str, Any]) -> dict[str, Any]:
        """Durably merge runtime identifiers without changing the run state."""
        field_updates = _require_mapping(fields, "fields")
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise RunNotFoundError("run_id does not exist")
            stored_fields = _json_load(row["fields_json"])
            stored_fields.update(field_updates)
            connection.execute(
                "UPDATE runs SET updated_at = ?, fields_json = ? WHERE run_id = ?",
                (_now(), _json_dump(stored_fields), run_id),
            )
            return self._record(connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())

    def save_result(
        self,
        run_id: str,
        outcome: str,
        summary: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(outcome, str) or not outcome.strip():
            raise ValueError("outcome must be a non-empty string")
        if not isinstance(summary, str):
            raise TypeError("summary must be a string")
        result_json = _json_dump(
            {
                "outcome": outcome,
                "summary": summary,
                "metadata": _require_mapping(metadata, "metadata"),
                "saved_at": _now(),
            }
        )
        with self._transaction() as connection:
            if connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is None:
                raise RunNotFoundError("run_id does not exist")
            connection.execute(
                "UPDATE runs SET result_json = ?, updated_at = ? WHERE run_id = ?",
                (result_json, _now(), run_id),
            )
            return self._record(connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())

    def mark_publication(self, run_id: str, step: str) -> dict[str, Any]:
        if not isinstance(step, str) or not _PUBLICATION_STEP.fullmatch(step):
            raise ValueError("step must be 1-128 safe identifier characters")
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise RunNotFoundError("run_id does not exist")
            publication = _json_load(row["publication_json"])
            publication.setdefault(step, _now())
            connection.execute(
                "UPDATE runs SET publication_json = ?, updated_at = ? WHERE run_id = ?",
                (_json_dump(publication), _now(), run_id),
            )
            return self._record(connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                row = self._connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
                return self._record(row)
            except sqlite3.DatabaseError as exc:
                raise LedgerCorruptionError("Unable to read execution ledger") from exc

    def get_active(self) -> dict[str, Any] | None:
        with self._lock:
            try:
                row = self._connection.execute(
                    f"SELECT * FROM runs WHERE state IN ({','.join('?' for _ in ACTIVE_STATES)}) ORDER BY created_at LIMIT 1",
                    ACTIVE_STATES,
                ).fetchone()
                return self._record(row)
            except sqlite3.DatabaseError as exc:
                raise LedgerCorruptionError("Unable to read execution ledger") from exc

    def list_unfinished(self) -> list[dict[str, Any]]:
        with self._lock:
            try:
                rows = self._connection.execute(
                    f"SELECT * FROM runs WHERE state IN ({','.join('?' for _ in UNFINISHED_STATES)}) ORDER BY created_at",
                    UNFINISHED_STATES,
                ).fetchall()
                return [self._record(row) for row in rows]
            except sqlite3.DatabaseError as exc:
                raise LedgerCorruptionError("Unable to read execution ledger") from exc
