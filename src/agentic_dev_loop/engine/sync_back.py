"""Sync-back (unit-5): copy declared-rw export changes back to the real repo.

In-process shutil diff-copy (ID-5), git-aware: only declared-rw paths sync,
``.git`` is never touched, and a conflict (the real repo changed underneath)
is a loud failure, never an overwrite (BR-4).

A declared-rw entry of ``.`` (or a blank entry) names the whole tree - but the
bind-mounted caches are excluded: they are already rw windows into the real
workspace (BR-3.3/F2) and their contents never appear in the export copy, so
they must never be classified as additions/deletions here.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

# Never sync these components, whatever the declaration says (BR-2/BR-4).
_FORBIDDEN = frozenset({".git", ".venv"})


@dataclass(frozen=True)
class SyncPlan:
    """The pure sync plan (BR-4.2): declared-rw changes split into copies,
    deletions, and conflicts."""

    to_copy: tuple[str, ...]
    to_delete: tuple[str, ...]
    conflicts: tuple[str, ...]


@dataclass(frozen=True)
class SyncBackReport:
    """The audit record for one sync-back (BR-4/BR-11/SECURITY-03)."""

    synced_paths: tuple[str, ...]
    conflicts: tuple[str, ...]
    step_id: str | None = None
    timestamp: str = ""


def _parts(rel: str) -> tuple[str, ...]:
    """Normalise a repo-relative path into its non-empty, non-``.`` segments."""
    return tuple(seg for seg in rel.replace("\\", "/").split("/")
                 if seg not in ("", "."))


def _forbidden(parts: tuple[str, ...]) -> bool:
    return any(part in _FORBIDDEN for part in parts)


def _within_cache(parts: tuple[str, ...],
                  cache_parts: list[tuple[str, ...]]) -> bool:
    """True when ``parts`` is under (or equal to) any declared cache dir."""
    return any(c and parts[:len(c)] == c for c in cache_parts)


def _declared(rel: str, declared_rw: list[str] | tuple[str, ...],
              cache_paths: list[str] | tuple[str, ...] = ()) -> bool:
    parts = _parts(rel)
    if _forbidden(parts):
        return False
    cache = [_parts(c) for c in cache_paths]
    if _within_cache(parts, cache):
        return False
    for d in declared_rw:
        d_parts = _parts(d)
        if not d_parts:
            # ``.`` (or a blank entry) declares the whole tree, minus the
            # forbidden and cache components excluded above.
            return True
        if parts[:len(d_parts)] == d_parts:
            return True
    return False


def plan_sync_back(export_changes: Mapping[str, str],
                   declared_rw: list[str] | tuple[str, ...],
                   cache_paths: list[str] | tuple[str, ...] = ()) -> SyncPlan:
    """Pure plan (PBT-03): keep only declared-rw changes; split into copy,
    delete, and conflict buckets. Non-declared, ``.git``/host-only paths, and
    declared caches never sync (BR-4.2)."""
    to_copy: list[str] = []
    to_delete: list[str] = []
    conflicts: list[str] = []
    for rel, kind in export_changes.items():
        if not _declared(rel, declared_rw, cache_paths):
            continue
        if kind == "conflict":
            conflicts.append(rel)
        elif kind in ("added", "modified"):
            to_copy.append(rel)
        elif kind == "deleted":
            to_delete.append(rel)
    return SyncPlan(
        to_copy=tuple(sorted(to_copy)),
        to_delete=tuple(sorted(to_delete)),
        conflicts=tuple(sorted(conflicts)),
    )


def _digest(path: Path) -> str:
    if path.is_symlink():
        # Track the link target, never the resolved content: the export can
        # legitimately hold dangling symlinks (host-darwin venv entries,
        # scratch links) and resolving them against the host would crash.
        return "link:" + hashlib.sha256(
            os.readlink(path).encode("utf-8")).hexdigest()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(root: Path,
              cache_paths: list[str] | tuple[str, ...] = ()) -> dict[str, str]:
    """rel-path -> sha256 for every file under root, excluding ``.git``,
    host-only dirs, and declared caches (BR-3: caches are bind-mounted, not
    copied, so their contents are never part of the export). Symlinks are
    tracked by their link target (never resolved against the host), so
    dangling links snapshot fine."""
    cache = [_parts(c) for c in cache_paths]
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        rel_parts = _parts(rel_dir.as_posix())
        dirnames[:] = [
            d for d in dirnames
            if d not in _FORBIDDEN
            and not _within_cache(rel_parts + (d,), cache)
        ]
        for name in filenames:
            parts = rel_parts + (name,)
            if _forbidden(parts) or _within_cache(parts, cache):
                continue
            rel = "/".join(parts)
            out[rel] = _digest(Path(dirpath) / name)
    return out


def sync_back(export_root: str | Path, real_repo: str | Path,
              declared_rw: list[str] | tuple[str, ...],
              baseline: Mapping[str, str] | None = None,
              step_id: str | None = None,
              cache_paths: list[str] | tuple[str, ...] = ()) -> SyncBackReport:
    """Diff-copy declared-rw export changes into the real repo (ID-5).

    A conflict - a declared-rw path changed in the export AND in the real repo
    since materialisation - is a loud failure (BR-4.4): the report carries the
    conflicts and nothing is copied. Otherwise declared-rw adds/modifies copy
    host-side and declared-rw deletions remove host-side; ``.git`` and the
    bind-mounted caches are never touched."""
    export_root = Path(export_root)
    real_repo = Path(real_repo)
    baseline = dict(baseline or {})
    export_files = _snapshot(export_root, cache_paths)
    real_files = _snapshot(real_repo, cache_paths)

    changes: dict[str, str] = {}
    all_rels = set(export_files) | set(real_files)
    for rel in sorted(all_rels):
        if not _declared(rel, declared_rw, cache_paths):
            continue
        exp = export_files.get(rel)
        real = real_files.get(rel)
        base = baseline.get(rel)
        if exp is None and real is not None:
            changes[rel] = "deleted"
        elif real is None and exp is not None:
            changes[rel] = "added"
        elif exp != real:
            # The real repo moved from what we materialised AND the export
            # also moved -> conflict, never an overwrite.
            if base is not None and real != base:
                changes[rel] = "conflict"
            else:
                changes[rel] = "modified"

    plan = plan_sync_back(changes, declared_rw, cache_paths)
    if plan.conflicts:
        return SyncBackReport(
            synced_paths=(), conflicts=plan.conflicts, step_id=step_id,
            timestamp=datetime.now(timezone.utc).isoformat())

    for rel in plan.to_copy:
        src = export_root / rel
        dst = real_repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst, follow_symlinks=False)
    for rel in plan.to_delete:
        (real_repo / rel).unlink(missing_ok=True)

    return SyncBackReport(
        synced_paths=plan.to_copy + plan.to_delete,
        conflicts=(), step_id=step_id,
        timestamp=datetime.now(timezone.utc).isoformat())
