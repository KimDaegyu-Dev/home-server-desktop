"""Read-only Langfuse trace reconciliation for Kanzo run ledger records."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any


MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _timestamp(value: str | None, fallback: datetime) -> datetime:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return fallback.astimezone(timezone.utc)


def reconcile_session(session_id: str | None, started_at: str | None) -> dict[str, Any]:
    """Inspect one bounded Langfuse v2 observation query; never create or update traces."""
    checked_at = datetime.now(timezone.utc)
    result: dict[str, Any] = {"checked_at": checked_at.isoformat(timespec="seconds").replace("+00:00", "Z")}
    public_key = os.getenv("HERMES_LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("HERMES_LANGFUSE_SECRET_KEY", "").strip()
    base_url = os.getenv("HERMES_LANGFUSE_BASE_URL", "").strip().rstrip("/")
    if not public_key or not secret_key or not base_url:
        result["state"] = "unavailable"
        result["reason"] = "credentials_or_base_url_missing"
        return result
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 256:
        result["state"] = "unavailable"
        result["reason"] = "session_id_missing_or_invalid"
        return result

    start = _timestamp(started_at, checked_at) - timedelta(minutes=2)
    params = urllib.parse.urlencode({
        "sessionId": session_id,
        "fromStartTime": start.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "toStartTime": (checked_at + timedelta(minutes=1)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "fields": "core,basic,trace_context",
        "limit": "1000",
    })
    url = f"{base_url}/api/public/v2/observations?{params}"
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 200:
                result.update({"state": "api_error", "http_status": response.status})
                return result
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            result["state"] = "api_error"
            result["reason"] = "response_too_large"
            return result
        payload = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        result.update({"state": "api_error", "http_status": int(exc.code)})
        return result
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TimeoutError):
        result["state"] = "api_error"
        result["reason"] = "request_or_response_invalid"
        return result

    observations = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(observations, list):
        result["state"] = "api_error"
        result["reason"] = "observations_response_invalid"
        return result
    matches = [
        row for row in observations
        if isinstance(row, dict) and row.get("sessionId") == session_id
    ]
    trace_ids = sorted({row.get("traceId") for row in matches if isinstance(row.get("traceId"), str) and row["traceId"]})
    result.update({
        "observation_count": len(matches),
        "trace_count": len(trace_ids),
        "has_more": bool(isinstance(payload.get("meta"), dict) and payload["meta"].get("cursor")),
    })
    if len(trace_ids) == 1:
        result["trace_id"] = trace_ids[0]
    if not matches:
        result["state"] = "not_found"
        return result
    if len(trace_ids) != 1 or result["has_more"]:
        result["state"] = "ambiguous"
        return result
    roots = [row for row in matches if row.get("isRootObservation") is True]
    if len(roots) != 1:
        result["state"] = "root_missing" if not roots else "ambiguous"
        return result
    root = roots[0]
    result["state"] = "ended" if root.get("endTime") else "incomplete"
    result["root_level"] = root.get("level") if isinstance(root.get("level"), str) else None
    result["root_ended"] = bool(root.get("endTime"))
    result["root_end_time"] = root.get("endTime") if isinstance(root.get("endTime"), str) else None
    return result
