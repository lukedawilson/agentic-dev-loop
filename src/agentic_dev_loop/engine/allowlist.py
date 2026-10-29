"""Engine-lane allowlist (036 D2 v1).

The active AI-DLC unit directory may carry ``engine-allowlist.yaml`` - a YAML
list of repo-relative allowed paths/globs. Absent = unrestricted (backward
compatible). Present = sync-back asserts every merged/synced path matches an
entry and fails the step otherwise. v2 (read-only export root with rw
bind-mounts for the allowlisted paths) is deferred.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agentic_dev_loop.core.config import _validate_declared_path
from .loaders import select_loader

ALLOWLIST_FILENAME = "engine-allowlist.yaml"


def load_engine_allowlist(path: str | Path) -> list[str]:
    """Parse and validate an ``engine-allowlist.yaml`` file.

    The file is a plain YAML list of repo-relative path/glob strings (the
    same declared-path vocabulary as ``sync_excludes:``). An empty file is a
    valid, deliberately empty allowlist (nothing matches, so every synced
    path violates the lane). Missing files are handled by the caller as the
    unrestricted case, not here.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"engine allowlist file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError(
            f"{ALLOWLIST_FILENAME} must be a YAML list of paths/globs, "
            f"got {type(data).__name__}")
    return [_validate_declared_path(entry, allow_wildcards=True)
            for entry in data]


def resolve_engine_allowlist(registry, args) -> list[str] | None:
    """Resolve the active unit's allowlist, or None when unrestricted.

    Only AI-DLC unit loaders expose a unit directory; other loaders (plan,
    none) have no lane boundary. The file lives at
    ``<unit_dir>/engine-allowlist.yaml``.
    """
    loader = select_loader(registry, args)
    if loader is None:
        return None
    unit_dir = getattr(loader, "unit_dir", None)
    if not callable(unit_dir):
        return None
    try:
        path = Path(unit_dir(args)) / ALLOWLIST_FILENAME
    except (FileNotFoundError, RuntimeError):
        # No active AI-DLC unit can be determined (e.g. a resumed run with no
        # aidlc-state file) -> no lane boundary, exactly like an absent file.
        return None
    if not path.is_file():
        return None
    return load_engine_allowlist(path)
