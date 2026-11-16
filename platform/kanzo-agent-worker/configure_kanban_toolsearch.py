"""Defer Hermes Kanban schemas for worker profiles, keeping them searchable."""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Iterable
from pathlib import Path


def add_deferred_tools(
    config: dict,
    *,
    default_deferred: Iterable[str],
    kanban_tools: Iterable[str],
) -> tuple[dict, bool]:
    """Merge Kanban into a profile's deferral set without dropping defaults.

    An explicit ``enabled: off`` is respected. Existing custom deferrals and
    unrelated tool-search settings are preserved.
    """
    updated = deepcopy(config)
    tools = updated.setdefault("tools", {})
    if not isinstance(tools, dict):
        raise ValueError("tools config must be a mapping")

    raw = tools.get("tool_search")
    enabled = raw.get("enabled") if isinstance(raw, dict) else raw
    if enabled is False or str(enabled).strip().lower() in {"off", "false", "0", "no"}:
        return updated, False
    if raw is True:
        search = {"enabled": "on"}
    elif raw is None:
        search = {}
    elif isinstance(raw, dict):
        search = dict(raw)
    else:
        raise ValueError("tools.tool_search must be a mapping or boolean")

    existing = search.get("defer")
    if existing is None:
        existing = list(default_deferred)
    if not isinstance(existing, list) or any(not isinstance(item, str) for item in existing):
        raise ValueError("tools.tool_search.defer must be a list of tool names")
    merged = list(dict.fromkeys([*existing, *(str(name) for name in kanban_tools)]))
    if merged == search.get("defer") and isinstance(raw, dict):
        return updated, False
    search["defer"] = merged
    tools["tool_search"] = search
    return updated, True


def main() -> int:
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    import model_tools  # Registers the canonical toolset map without constructing tool schemas.
    from ruamel.yaml import YAML
    from tools.registry import registry

    root = Path("/opt/data/home")
    profiles = ("researcher", "builder", "reviewer")
    defaults = DEFAULT_CONFIG["tools"]["tool_search"]["defer"]
    kanban_tools = sorted(
        name for name, toolset in registry.get_tool_to_toolset_map().items()
        if toolset == "kanban"
    )
    if not kanban_tools:
        raise SystemExit("Hermes Kanban toolset is unavailable; refusing profile config update")

    yaml = YAML()
    changed = []
    for profile in profiles:
        path = root / "profiles" / profile / "config.yaml"
        if not path.is_file():
            raise SystemExit(f"Hermes profile config is missing: {profile}")
        config = yaml.load(path.read_text(encoding="utf-8")) or {}
        updated, was_changed = add_deferred_tools(
            config, default_deferred=defaults, kanban_tools=kanban_tools
        )
        if was_changed:
            with path.open("w", encoding="utf-8") as stream:
                yaml.dump(updated, stream)
            changed.append(profile)
    print(
        "Kanban schemas deferred for worker profiles: "
        + (", ".join(changed) if changed else "already configured")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
