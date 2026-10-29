"""Sync-back (unit-5): copy declared-rw export changes back to the real repo.

In-process shutil diff-copy (ID-5), git-aware: only declared-rw paths sync,
``.git`` is never touched, and a conflict (the real repo changed underneath)
is a loud failure, never an overwrite (BR-4).

A declared-rw entry of ``.`` (or a blank entry) names the whole tree - but the
bind-mounted caches and declared sync-excludes are excluded: caches are already
rw windows into the real workspace (BR-3.3/F2) and their contents never appear
in the export copy; sync-excludes are scratch (never copied, never synced -
028), so neither may ever classify as additions/deletions/conflicts here.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from agentic_dev_loop.core.paths import dir_excluded, path_allowed, path_excluded

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
    merged_paths: tuple[str, ...] = ()
    lane_violations: tuple[str, ...] = ()
    host_added_paths: tuple[str, ...] = ()
    step_id: str | None = None
    timestamp: str = ""


def merge_append_only(host_text: str, export_text: str) -> str:
    """Union-merge two append-style logs (029): the host file wins for every
    line it already contains (host order is preserved) and each export-only
    line is appended in export order. Nothing is ever deleted, so both sides
    converge on the line-level union; identical sides return the host text
    unchanged.

    Export-only is a multiset difference against the host, not a naive
    ``difflib`` opcode filter: the common both-appended-tail case is a
    ``replace`` opcode there, and a union must keep both tails."""
    host_lines = host_text.splitlines()
    export_lines = export_text.splitlines()
    remaining: Counter[str] = Counter(host_lines)
    extra: list[str] = []
    for line in export_lines:
        if remaining[line]:
            remaining[line] -= 1
        else:
            extra.append(line)
    if not extra:
        return host_text
    merged = "\n".join(host_lines + extra)
    if host_text.endswith("\n") or export_text.endswith("\n"):
        merged += "\n"
    return merged


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
              cache_paths: list[str] | tuple[str, ...] = (),
              excludes: list[str] | tuple[str, ...] = ()) -> bool:
    parts = _parts(rel)
    if _forbidden(parts):
        return False
    cache = [_parts(c) for c in cache_paths]
    if _within_cache(parts, cache):
        return False
    if path_excluded(rel, excludes):
        return False
    for d in declared_rw:
        d_parts = _parts(d)
        if not d_parts:
            # ``.`` (or a blank entry) declares the whole tree, minus the
            # forbidden, cache, and excluded components removed above.
            return True
        if parts[:len(d_parts)] == d_parts:
            return True
    return False


def plan_sync_back(export_changes: Mapping[str, str],
                   declared_rw: list[str] | tuple[str, ...],
                   cache_paths: list[str] | tuple[str, ...] = (),
                   excludes: list[str] | tuple[str, ...] = ()) -> SyncPlan:
    """Pure plan (PBT-03): keep only declared-rw changes; split into copy,
    delete, and conflict buckets. Non-declared, ``.git``/host-only paths,
    declared caches, and sync-excludes never sync (BR-4.2/028)."""
    to_copy: list[str] = []
    to_delete: list[str] = []
    conflicts: list[str] = []
    for rel, kind in export_changes.items():
        if not _declared(rel, declared_rw, cache_paths, excludes):
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
              cache_paths: list[str] | tuple[str, ...] = (),
              excludes: list[str] | tuple[str, ...] = ()) -> dict[str, str]:
    """rel-path -> sha256 for every file under root, excluding ``.git``,
    host-only dirs, declared caches (BR-3: caches are bind-mounted, not
    copied, so their contents are never part of the export), and declared
    sync-excludes (028: scratch paths are never part of either snapshot).
    Symlinks are tracked by their link target (never resolved against the
    host), so dangling links snapshot fine."""
    cache = [_parts(c) for c in cache_paths]
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        rel_parts = _parts(rel_dir.as_posix())
        dirnames[:] = [
            d for d in dirnames
            if d not in _FORBIDDEN
            and not _within_cache(rel_parts + (d,), cache)
            and not dir_excluded((rel_dir / d).as_posix(), excludes)
        ]
        for name in filenames:
            parts = rel_parts + (name,)
            if _forbidden(parts) or _within_cache(parts, cache):
                continue
            rel = "/".join(parts)
            if path_excluded(rel, excludes):
                continue
            out[rel] = _digest(Path(dirpath) / name)
    return out


def sync_back(export_root: str | Path, real_repo: str | Path,
              declared_rw: list[str] | tuple[str, ...],
              baseline: Mapping[str, str] | None = None,
              step_id: str | None = None,
              cache_paths: list[str] | tuple[str, ...] = (),
              excludes: list[str] | tuple[str, ...] = (),
              merge_on_conflict: list[str] | tuple[str, ...] = (),
              allowlist: list[str] | tuple[str, ...] | None = None) -> SyncBackReport:
    """Diff-copy declared-rw export changes into the real repo (ID-5).

    A conflict - a declared-rw path changed in the export AND in the real repo
    since materialisation - is a loud failure (BR-4.4): the report carries the
    conflicts and nothing is copied. A ``merge_on_conflict:`` path (029) is
    partitioned across every change kind: a both-sides-present conflict is
    reported in ``merged_paths`` for the caller to union-merge, while a
    one-side deletion (``added``/``deleted``) stays a hard conflict so the
    append-only log is never silently resurrected or erased. Non-conflicting
    adds/modifies still copy and deletions still remove host-side when no
    hard conflict remains. ``.git``, the bind-mounted caches, and declared
    sync-excludes are never touched."""
    export_root = Path(export_root)
    real_repo = Path(real_repo)
    baseline = dict(baseline or {})
    export_files = _snapshot(export_root, cache_paths, excludes)
    real_files = _snapshot(real_repo, cache_paths, excludes)

    changes: dict[str, str] = {}
    all_rels = set(export_files) | set(real_files)
    for rel in sorted(all_rels):
        if not _declared(rel, declared_rw, cache_paths, excludes):
            continue
        exp = export_files.get(rel)
        real = real_files.get(rel)
        base = baseline.get(rel)
        if exp is None and real is not None:
            # A file missing from the export AND from the baseline was added
            # host-side after materialisation - never an agent deletion, so it
            # must not be unlinked (037).
            if base is None:
                changes[rel] = "host-added"
            else:
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

    merge_specs = list(merge_on_conflict or ())
    merged_paths: list[str] = []
    hard_conflicts: list[str] = []
    plan_changes: dict[str, str] = {}
    for rel, kind in changes.items():
        if path_excluded(rel, merge_specs):
            # Declared merge path (029). Union-merge only handles both sides
            # present; a one-side deletion stays a hard conflict (raise +
            # absorb, host wins) so a concurrent host/export delete can never
            # silently resurrect or erase the append-only log.
            if kind == "conflict":
                merged_paths.append(rel)
            elif kind in ("added", "deleted"):
                hard_conflicts.append(rel)
            else:
                plan_changes[rel] = kind
        elif kind == "conflict":
            hard_conflicts.append(rel)
        else:
            plan_changes[rel] = kind

    plan = plan_sync_back(plan_changes, declared_rw, cache_paths, excludes)
    host_added_paths = tuple(sorted(
        rel for rel, kind in changes.items() if kind == "host-added"))
    merged_paths = tuple(sorted(merged_paths))
    hard_conflicts = tuple(sorted(hard_conflicts))

    # 036 lane boundary (v1): every path that would be merged or synced must
    # match the unit allowlist. A violation fails before any copy/delete/merge
    # is applied - the out-of-lane write is detection, not promotion into the
    # real repo. None = unrestricted (no allowlist file).
    if allowlist is not None:
        checked = tuple(sorted(set(merged_paths) | set(plan.to_copy)
                               | set(plan.to_delete)))
        lane_violations = tuple(p for p in checked
                                if not path_allowed(p, allowlist))
    else:
        lane_violations = ()

    if lane_violations:
        return SyncBackReport(
            synced_paths=(), conflicts=hard_conflicts,
            merged_paths=merged_paths, lane_violations=lane_violations,
            host_added_paths=host_added_paths,
            step_id=step_id, timestamp=datetime.now(timezone.utc).isoformat())

    # A hard (non-merge) conflict keeps the BR-4.4 loud failure and the
    # copy/delete stays atomic: nothing non-merge copies or deletes when one
    # remains. Merge paths are still reported so the caller union-merges them
    # first (029 step order: merge before the hard-conflict raise).
    if hard_conflicts:
        return SyncBackReport(
            synced_paths=(), conflicts=hard_conflicts,
            merged_paths=merged_paths, host_added_paths=host_added_paths,
            step_id=step_id, timestamp=datetime.now(timezone.utc).isoformat())

    for rel in plan.to_copy:
        src = export_root / rel
        dst = real_repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst, follow_symlinks=False)
    for rel in plan.to_delete:
        (real_repo / rel).unlink(missing_ok=True)

    return SyncBackReport(
        synced_paths=plan.to_copy + plan.to_delete,
        conflicts=(), merged_paths=merged_paths, lane_violations=(),
        host_added_paths=host_added_paths,
        step_id=step_id, timestamp=datetime.now(timezone.utc).isoformat())
