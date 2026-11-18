#!/usr/bin/env python3
"""Refresh secret-backed Hermes environment files without rerunning setup."""

from __future__ import annotations

import os
from pathlib import Path

from ruamel.yaml import YAML


ROOT = Path("/opt/data/home")
COMPRESSION_SETTINGS = {
    "enabled": True,
    "threshold_tokens": 36000,
    "abort_on_summary_failure": True,
    "hygiene_timeout_seconds": 300,
    "hygiene_total_ceiling_seconds": 600,
    "hygiene_max_turn_hold_seconds": 300,
    "context_timeout_seconds": 300,
    "context_total_ceiling_seconds": 600,
}
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
    yaml = YAML()
    for home in homes:
        config_path = home / "config.yaml"
        if config_path.is_file():
            config = yaml.load(config_path.read_text(encoding="utf-8")) or {}
            compression = config.setdefault("compression", {})
            if not isinstance(compression, dict):
                raise SystemExit(f"{config_path} has invalid compression settings")
            compression.update(COMPRESSION_SETTINGS)
            staged = config_path.with_name(".config.yaml.kanzo.tmp")
            with staged.open("w", encoding="utf-8") as stream:
                yaml.dump(config, stream)
            staged.chmod(0o600)
            staged.replace(config_path)
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
