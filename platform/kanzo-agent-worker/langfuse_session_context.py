#!/usr/bin/env python3
"""Make the installed Hermes Langfuse plugin read Kanzo IDs per session.

The Langfuse plugin normally reads HERMES_KANBAN_* from the process environment.
That is shared by every session in the Gateway, so it cannot safely identify
concurrent Kanzo runs. Resolve the existing per-session KANZO_* values from the
Gateway ContextVar instead. This is deliberately an exact, guarded patch: if an
upstream plugin changes the expected helper, fail bootstrap rather than silently
losing run attribution.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path("/opt/data/home")
PROFILES = ("default", "researcher", "builder", "reviewer")
OLD = '''def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()
'''
NEW = '''def _env(name: str, default: str = "") -> str:
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
            # A missing session context must not inherit another run's IDs
            # from the shared Gateway process environment.
            return default.strip()
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
def patch_plugin_text(text: str) -> tuple[str, bool]:
    """Apply or upgrade the guarded per-session ID lookup in one plugin file."""
    if text.count(NEW) == 1:
        return text, False
    if text.count(PREVIOUS) == 1:
        return text.replace(PREVIOUS, NEW, 1), True
    if text.count(OLD) == 1:
        return text.replace(OLD, NEW, 1), True
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
        if profile != "default":
            config_path = ROOT / "profiles" / profile / "config.yaml"
            config = yaml.load(config_path.read_text(encoding="utf-8")) or {}
            settings = config.setdefault("plugins", {}).setdefault("entries", {}).setdefault("langfuse", {}).setdefault("settings", {})
            if not isinstance(settings, dict):
                raise SystemExit(f"invalid Langfuse settings in {config_path}")
            settings["end_user_attribution"] = False
            with config_path.open("w", encoding="utf-8") as stream:
                yaml.dump(config, stream)
    print("Langfuse per-session Kanzo metadata ready" + (f": {', '.join(patched)}" if patched else " (already current)"))


if __name__ == "__main__":
    main()
