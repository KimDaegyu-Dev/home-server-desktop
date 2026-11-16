#!/usr/bin/env python3
"""Make the installed Hermes Langfuse plugin read Kanzo IDs per session.

The Langfuse plugin normally reads HERMES_KANBAN_* from the process environment.
That is shared by every session in the Gateway, so it cannot safely identify
concurrent Kanzo runs. Resolve the KANZO_* values from the bound session client
environment instead. The Gateway allowlist is configured so these non-secret
IDs are session-scoped. Patches are guarded:
if upstream changes the expected helpers, fail bootstrap rather than silently
losing run attribution.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path("/opt/data/home")
PROFILES = ("default", "researcher", "builder", "reviewer")
KANZO_CLIENT_ENV = ("KANZO_TASK_ID", "KANZO_RUN_ID")
OLD = '''def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()
'''
NEW = '''def _env(name: str, default: str = "") -> str:
    # Kanzo run identity must come from the active Gateway session. Process
    # environment is shared across sessions and can retain another run's IDs.
    session_name = {"HERMES_KANBAN_TASK": "KANZO_TASK_ID",
                    "HERMES_KANBAN_RUN_ID": "KANZO_RUN_ID"}.get(name)
    if session_name:
        try:
            from agent.session_client_env import bound_client_env
            session_env = bound_client_env()
            value = (session_env.get(session_name) or session_env.get(name)
                     if isinstance(session_env, dict) else None)
            if value:
                return str(value).strip()
        except Exception:
            pass
        # A missing or unavailable session context is unattributed. Never
        # fall back to process-wide IDs, even when they appear to name a run.
        return default.strip()
    return os.environ.get(name, default).strip()
'''
PREVIOUS = '''def _env(name: str, default: str = "") -> str:
    # Kanzo IDs are bound to the active Gateway session, never to the shared
    # Gateway process environment (which may serve multiple sessions at once).
    session_name = {"HERMES_KANBAN_TASK": "KANZO_TASK_ID",
                    "HERMES_KANBAN_RUN_ID": "KANZO_RUN_ID"}.get(name)
    if session_name:
        try:
            from gateway.session_context import get_session_env
            value = get_session_env(session_name, "") or get_session_env(name, "")
            if value:
                return str(value).strip()
        except Exception:
            pass
    return os.environ.get(name, default).strip()
'''

def ensure_kanzo_client_env(config: dict) -> tuple[dict, bool]:
    """Allow only the two non-secret run identifiers in this session's client env."""
    updated = dict(config)
    terminal = updated.setdefault("terminal", {})
    if not isinstance(terminal, dict):
        raise SystemExit("invalid Hermes terminal settings; refusing to set Kanzo client env")
    current = terminal.get("client_env_passthrough", [])
    if current is None:
        current = []
    if not isinstance(current, list) or any(not isinstance(name, str) for name in current):
        raise SystemExit("invalid terminal.client_env_passthrough; refusing to overwrite it")
    names = list(dict.fromkeys([*current, *KANZO_CLIENT_ENV]))
    changed = names != current
    terminal["client_env_passthrough"] = names
    return updated, changed
def patch_plugin_text(text: str) -> tuple[str, bool]:
    """Apply the guarded per-session lookup across known Hermes plugin revisions."""
    if text.count(NEW) == 1:
        return text, False
    matcher = re.compile(r'(?ms)^def _env\([^\n]*\n.*?(?=^def |\Z)')
    matches = list(matcher.finditer(text))
    if len(matches) == 1:
        current = matches[0].group(0)
        if any(marker in current for marker in (
            "os.environ.get(name, default)",
            "get_session_env(",
            "bound_client_env(",
        )):
            return text[:matches[0].start()] + NEW + text[matches[0].end():], True
    raise SystemExit("unexpected Langfuse _env helper; refusing an unsafe patch")


def main() -> None:
    from ruamel.yaml import YAML

    patched = []
    yaml = YAML()
    for profile in PROFILES:
        source = ROOT / "profiles" / profile / "plugins" / "langfuse" / "__init__.py"
        if not source.is_file():
            if profile == "default":
                source = ROOT / "plugins" / "langfuse" / "__init__.py"
            if not source.is_file():
                raise SystemExit(f"Langfuse plugin source missing for profile {profile}")
        text = source.read_text(encoding="utf-8")
        updated, changed = patch_plugin_text(text)
        if changed:
            source.write_text(updated, encoding="utf-8")
            patched.append(profile)
        config_path = ROOT / "config.yaml" if profile == "default" else ROOT / "profiles" / profile / "config.yaml"
        config = yaml.load(config_path.read_text(encoding="utf-8")) or {}
        config, client_env_changed = ensure_kanzo_client_env(config)
        if profile != "default":
            settings = config.setdefault("plugins", {}).setdefault("entries", {}).setdefault("langfuse", {}).setdefault("settings", {})
            if not isinstance(settings, dict):
                raise SystemExit(f"invalid Langfuse settings in {config_path}")
            settings["end_user_attribution"] = False
        if changed or client_env_changed or profile != "default":
            with config_path.open("w", encoding="utf-8") as stream:
                yaml.dump(config, stream)
    print("Langfuse per-session Kanzo metadata ready" + (f": {', '.join(patched)}" if patched else " (already current)"))


if __name__ == "__main__":
    main()
