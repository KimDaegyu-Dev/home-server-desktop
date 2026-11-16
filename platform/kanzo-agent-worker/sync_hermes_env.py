#!/usr/bin/env python3
"""Refresh secret-backed Hermes environment files without rerunning setup."""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path("/opt/data/home")
VALUES = {
    "MCPPROXY_URL": os.environ.get("MCPPROXY_URL", ""),
    "MCPPROXY_AGENT_READ_TOKEN": os.environ.get("MCPPROXY_AGENT_READ_TOKEN", ""),
    "SEARXNG_URL": os.environ.get("SEARXNG_URL", ""),
    "LITELLM_API_KEY": os.environ.get("LITELLM_API_KEY", ""),
    "LITELLM_BASE_URL": os.environ.get("LITELLM_BASE_URL", ""),
    "OPENAI_API_KEY": os.environ.get("LITELLM_API_KEY", ""),
    "OPENAI_BASE_URL": os.environ.get("LITELLM_BASE_URL", ""),
    "KANZO_ANALYSIS_MODEL": "iq_3s",
    "HERMES_LANGFUSE_PUBLIC_KEY": os.environ.get("HERMES_LANGFUSE_PUBLIC_KEY", ""),
    "HERMES_LANGFUSE_SECRET_KEY": os.environ.get("HERMES_LANGFUSE_SECRET_KEY", ""),
    "HERMES_LANGFUSE_BASE_URL": os.environ.get("HERMES_LANGFUSE_BASE_URL", ""),
}


def main() -> None:
    homes = [ROOT, *(ROOT / "profiles" / name for name in ("researcher", "builder", "reviewer"))]
    for home in homes:
        path = home / ".env"
        existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        kept = [line for line in existing if line.partition("=")[0] not in VALUES]
        path.write_text(
            "\n".join([*kept, *(f"{key}={value}" for key, value in VALUES.items())]) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
    print("Hermes environment credentials refreshed")


if __name__ == "__main__":
    main()
