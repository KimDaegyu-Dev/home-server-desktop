"""Durable identity and single-writer leases for shared task workspaces."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class WorkspaceRegistryError(RuntimeError):
    """Base error for workspace registry operations."""


class WorkspaceBusyError(WorkspaceRegistryError):
    """A workspace already has a writer lease."""


class WorkspaceSourceMismatchError(WorkspaceRegistryError):
    """A workspace ID was reused for a different source identity."""


class WorkspaceDeletedError(WorkspaceRegistryError):
    """A tombstoned workspace ID cannot be reused."""


_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_STATES = frozenset({"leased", "ready", "paused", "waiting", "review", "merged", "deleted", "conflict"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def validate_workspace_id(value: str) -> str:
    if not isinstance(value, str) or not _WORKSPACE_ID.fullmatch(value):
        raise WorkspaceRegistryError("workspace_id must be 1-64 letters, digits, dots, underscores, or hyphens")
    return value


class WorkspaceRegistry:
    """Small SQLite registry stored beside the durable execution ledger.

    The registry intentionally keeps metadata only. Workspace files live in the
    separately mounted workspace volume and are deleted only after an explicit,
    verified merge cleanup operation.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), timeout=30, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout = 30000")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = FULL")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS workspaces (
                workspace_id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                source_repo TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                workspace_path TEXT NOT NULL,
                branch TEXT NOT NULL,
                state TEXT NOT NULL,
                linked_tasks_json TEXT NOT NULL,
                lease_run_id TEXT,
                lease_task_id TEXT,
                lease_acquired_at TEXT,
                last_run_id TEXT,
                last_task_id TEXT,
                head_sha TEXT,
                pull_request TEXT,
                dirty INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            )"""
        )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        value["linked_tasks"] = json.loads(value.pop("linked_tasks_json"))
        value["dirty"] = bool(value["dirty"])
        return value

    @staticmethod
    def _add_task(linked_tasks: list[str], task_id: str) -> list[str]:
        if task_id not in linked_tasks:
            linked_tasks.append(task_id)
        return linked_tasks

    def acquire(
        self,
        *,
        workspace_id: str,
        project: str,
        task_id: str,
        run_id: str,
        source_repo: str,
        source_ref: str,
        workspace_path: str,
        branch: str,
    ) -> dict[str, Any]:
        """Create or resume a workspace and atomically acquire its writer lease."""
        validate_workspace_id(workspace_id)
        now = _now()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)).fetchone()
                if row is None:
                    self._db.execute(
                        """INSERT INTO workspaces (
                            workspace_id, project, source_repo, source_ref, workspace_path,
                            branch, state, linked_tasks_json, lease_run_id, lease_task_id,
                            lease_acquired_at, last_run_id, last_task_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'leased', ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (workspace_id, project, source_repo, source_ref, workspace_path, branch,
                         json.dumps([task_id]), run_id, task_id, now, run_id, task_id, now, now),
                    )
                else:
                    existing = self._decode(row)
                    if existing["state"] == "deleted":
                        raise WorkspaceDeletedError(f"Workspace {workspace_id!r} was deleted and cannot be reused")
                    identity = (existing["project"], existing["source_repo"], existing["source_ref"])
                    requested = (project, source_repo, source_ref)
                    if identity != requested:
                        raise WorkspaceSourceMismatchError(
                            f"Workspace {workspace_id!r} belongs to {identity!r}, not {requested!r}"
                        )
                    owner = existing.get("lease_run_id")
                    if owner and owner != run_id:
                        raise WorkspaceBusyError(
                            f"Workspace {workspace_id!r} is leased by run {owner!r}; refusing concurrent write"
                        )
                    linked = self._add_task(existing["linked_tasks"], task_id)
                    self._db.execute(
                        """UPDATE workspaces SET state='leased', linked_tasks_json=?, lease_run_id=?,
                           lease_task_id=?, lease_acquired_at=?, last_run_id=?, last_task_id=?,
                           workspace_path=?, branch=?, updated_at=? WHERE workspace_id=?""",
                        (json.dumps(linked), run_id, task_id, now, run_id, task_id,
                         workspace_path, branch, now, workspace_id),
                    )
                self._db.execute("COMMIT")
            except Exception:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise
        return self.get(workspace_id)  # type: ignore[return-value]

    def release(
        self,
        workspace_id: str,
        run_id: str,
        *,
        state: str,
        head_sha: str | None = None,
        pull_request: str | None = None,
        dirty: bool = False,
    ) -> dict[str, Any]:
        if state not in _STATES - {"leased", "merged", "deleted"}:
            raise WorkspaceRegistryError(f"invalid workspace release state: {state!r}")
        now = _now()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)).fetchone()
                existing = self._decode(row)
                if existing is None:
                    raise WorkspaceRegistryError(f"Workspace {workspace_id!r} does not exist")
                if existing.get("lease_run_id") != run_id:
                    raise WorkspaceBusyError(f"Run {run_id!r} does not own workspace {workspace_id!r}")
                self._db.execute(
                    """UPDATE workspaces SET state=?, lease_run_id=NULL, lease_task_id=NULL,
                       lease_acquired_at=NULL, head_sha=coalesce(?, head_sha),
                       pull_request=coalesce(?, pull_request), dirty=?, updated_at=? WHERE workspace_id=?""",
                    (state, head_sha, pull_request, int(dirty), now, workspace_id),
                )
                self._db.execute("COMMIT")
            except Exception:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise
        return self.get(workspace_id)  # type: ignore[return-value]

    def mark_deleted(self, workspace_id: str, *, head_sha: str | None = None) -> dict[str, Any]:
        now = _now()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                existing = self._decode(self._db.execute(
                    "SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)
                ).fetchone())
                if existing is None:
                    raise WorkspaceRegistryError(f"Workspace {workspace_id!r} does not exist")
                if existing.get("lease_run_id"):
                    raise WorkspaceBusyError(f"Workspace {workspace_id!r} still has an active lease")
                if existing.get("state") != "merged":
                    raise WorkspaceRegistryError("Workspace must be marked merged before it can be deleted")
                self._db.execute(
                    "UPDATE workspaces SET state='deleted', dirty=0, head_sha=coalesce(?, head_sha), deleted_at=?, updated_at=? WHERE workspace_id=?",
                    (head_sha, now, now, workspace_id),
                )
                self._db.execute("COMMIT")
            except Exception:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise
        return self.get(workspace_id)  # type: ignore[return-value]

    def mark_merged(self, workspace_id: str, *, head_sha: str) -> dict[str, Any]:
        if not isinstance(head_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", head_sha):
            raise WorkspaceRegistryError("Merged head SHA must be a 40-character lowercase Git object ID")
        now = _now()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                existing = self._decode(self._db.execute(
                    "SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)
                ).fetchone())
                if existing is None:
                    raise WorkspaceRegistryError(f"Workspace {workspace_id!r} does not exist")
                if existing.get("lease_run_id"):
                    raise WorkspaceBusyError(f"Workspace {workspace_id!r} still has an active lease")
                self._db.execute(
                    "UPDATE workspaces SET state='merged', dirty=0, head_sha=?, updated_at=? WHERE workspace_id=?",
                    (head_sha, now, workspace_id),
                )
                self._db.execute("COMMIT")
            except Exception:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise
        return self.get(workspace_id)  # type: ignore[return-value]

    def get(self, workspace_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)).fetchone()
        return self._decode(row)

    def list(self, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM workspaces"
        if not include_deleted:
            query += " WHERE state != 'deleted'"
        query += " ORDER BY created_at, workspace_id"
        with self._lock:
            rows = self._db.execute(query).fetchall()
        return [self._decode(row) for row in rows if row is not None]

    def metrics_snapshot(self) -> dict[str, Any]:
        """Return bounded aggregate workspace health without exposing IDs as labels."""
        with self._lock:
            states = {
                str(row[0]): int(row[1])
                for row in self._db.execute(
                    "SELECT state, count(*) FROM workspaces GROUP BY state"
                ).fetchall()
            }
            row = self._db.execute(
                """SELECT count(*),
                          coalesce(sum(CASE WHEN lease_run_id IS NOT NULL THEN 1 ELSE 0 END), 0),
                          coalesce(sum(CASE WHEN dirty != 0 AND state != 'deleted' THEN 1 ELSE 0 END), 0)
                   FROM workspaces"""
            ).fetchone()
        return {
            "states": states,
            "total": int(row[0]) if row else 0,
            "active_leases": int(row[1]) if row else 0,
            "dirty": int(row[2]) if row else 0,
        }
