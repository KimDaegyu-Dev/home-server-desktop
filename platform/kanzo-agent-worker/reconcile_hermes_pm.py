"""Reconcile the persistent Hermes PM environment before starting its Gateway."""

from __future__ import annotations

from collections.abc import Callable


VENV_DRIFT = "venv: out of sync with uv.lock"


def reconcile_environment(
    activate: Callable[[], list[str]],
    sync_venv: Callable[..., None],
) -> bool:
    """Repair only PM's known lock drift, then require a clean activation check.

    This runs from a Kubernetes init container after runtime ownership has
    drained. An explicit PM sync is a no-op when the persistent environment is
    already current, and rebuilds it when profile/plugin inputs changed.
    """
    problems = activate()
    if not problems:
        return False
    if problems != [VENV_DRIFT]:
        raise RuntimeError(f"Hermes PM preflight failed: {problems}")

    sync_venv(explicit=True)
    remaining = activate()
    if remaining:
        raise RuntimeError(f"Hermes PM remains unhealthy after sync: {remaining}")
    return True


def main() -> int:
    from pm.install import activate, sync_venv

    try:
        repaired = reconcile_environment(activate, sync_venv)
    except Exception as exc:
        raise SystemExit(str(exc)) from None
    print("Hermes PM environment synchronized" if repaired else "Hermes PM environment is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
