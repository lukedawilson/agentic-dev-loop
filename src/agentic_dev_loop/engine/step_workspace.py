"""Step workspace materialisation (unit-5): the .git-free export the sandbox
mounts (BR-2).

Copy-based (ID-4): everything except ``.git`` and host-only dirs (``.venv``)
is copied into ``state_dir/exports/<run-id>/``; declared cache paths are
bind-mounted rw from the real workspace at the same relative paths (BR-3).
The export is owned and mutated by the engine, never by the sandbox.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .sync_back import SyncBackReport, _snapshot, sync_back

# Host-only dirs never copied into the export (022). ``.git`` is excluded by
# construction (BR-2) and is guarded separately so a nested ``.git`` can never
# leak through either.
_EXCLUDED_DIRS = frozenset({".git", ".venv"})


@dataclass(frozen=True)
class CacheMount:
    """One declared cache bind-mount (F2): the real workspace source path and
    its repo-relative posix path (== the mount target inside the export)."""

    source: str  # absolute host path in the real workspace
    relative: str  # repo-relative posix path


@dataclass(frozen=True)
class StepWorkspacePlan:
    """The pure plan (PBT-03): which relative paths copy, which cache paths
    bind-mount, and the export destination."""

    export_root: str
    copy_paths: tuple[str, ...]  # repo-relative files to copy (posix, sorted)
    cache_mounts: tuple[CacheMount, ...]


def _relative_parts(path: str) -> tuple[str, ...]:
    return tuple(seg for seg in path.replace("\\", "/").split("/")
                 if seg not in ("", "."))


def plan_step_workspace(repo: str | Path, cache_paths: list[str],
                        platform: str, export_root: str | Path,
                        state_dir: str | None = None) -> StepWorkspacePlan:
    """Plan the export (BR-2/BR-3): copy every workspace file except
    ``.git``/host-only dirs/cache paths/``state_dir``; bind-mount each
    declared cache path rw. A missing declared cache path is a loud failure
    (BR-3.2, pillar 5). Pure over platform facts - deterministic given the
    repo state."""
    repo = Path(repo)
    export_root = Path(export_root)
    cache_rels = [_relative_parts(c) for c in cache_paths]
    state_dir_rel = _relative_parts(state_dir) if state_dir else None

    for cache in cache_paths:
        if not (repo / cache).exists():
            raise ValueError(
                f"declared cache path {cache!r} does not exist in the workspace "
                f"{repo} - declare it or create it before the run (BR-3.2)")

    copy_paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        rel_dir = Path(dirpath).relative_to(repo)
        rel_dir_parts = _relative_parts(rel_dir.as_posix())
        dirnames[:] = [
            d for d in dirnames
            if d not in _EXCLUDED_DIRS
            and not _excluded(rel_dir_parts + (d,), cache_rels, state_dir_rel)
        ]
        for name in filenames:
            parts = rel_dir_parts + (name,)
            if _excluded(parts, cache_rels, state_dir_rel):
                continue
            copy_paths.append("/".join(parts))

    cache_mounts = tuple(
        CacheMount(source=str(repo / c), relative=c) for c in cache_paths)
    return StepWorkspacePlan(
        export_root=str(export_root),
        copy_paths=tuple(sorted(copy_paths)),
        cache_mounts=cache_mounts,
    )


def _excluded(parts: tuple[str, ...], cache_rels: list[tuple[str, ...]],
              state_dir_rel: tuple[str, ...] | None) -> bool:
    """True when a relative path (as parts) must be excluded from the export:
    ``.git``/host-only components, any declared cache, or the state dir."""
    if any(part in _EXCLUDED_DIRS for part in parts):
        return True
    for cache in cache_rels:
        if _is_within(parts, cache):
            return True
    if state_dir_rel and _is_within(parts, state_dir_rel):
        return True
    return False


def _is_within(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return bool(prefix) and parts[:len(prefix)] == prefix


class StepWorkspace:
    """The engine-owned .git-free derivative the sandbox mounts (BR-2)."""

    def __init__(self, plan: StepWorkspacePlan, real_repo: str | Path):
        self._plan = plan
        self._export_root = Path(plan.export_root)
        self._real_repo = Path(real_repo)

    @property
    def export_root(self) -> Path:
        return self._export_root

    @classmethod
    def materialise(cls, repo: str | Path, cache_paths: list[str],
                    platform: str, export_root: str | Path,
                    state_dir: str | None = None) -> "StepWorkspace":
        """Copy the workspace into the export, excluding ``.git``/host-only
        dirs/caches/``state_dir``; pre-create the cache mount targets so the
        rw bind-mounts have uid-owned destinations (the AE-SBX-009 class)."""
        plan = plan_step_workspace(repo, cache_paths, platform, export_root,
                                   state_dir=state_dir)
        ws = cls(plan, real_repo=repo)
        ws._copy(repo)
        ws._baseline = _snapshot(
            ws._export_root, cache_paths=[c.relative for c in plan.cache_mounts])
        return ws

    def _copy(self, repo: Path) -> None:
        self._export_root.mkdir(parents=True, exist_ok=True)
        for rel in self._plan.copy_paths:
            src = repo / rel
            dst = self._export_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        for cache in self._plan.cache_mounts:
            (self._export_root / cache.relative).mkdir(parents=True,
                                                       exist_ok=True)

    def mount_points(self) -> list[tuple[str, str, str]]:
        """The Docker mount list: export root rw + cache bind-mounts rw at the
        same relative paths (BR-3.1). Returns (host, container, mode)."""
        root = str(self._export_root)
        points = [(root, root, "rw")]
        for cache in self._plan.cache_mounts:
            points.append((cache.source, f"{root}/{cache.relative}", "rw"))
        return points

    def sync_back(self, declared_rw: list[str]) -> SyncBackReport:
        """Git-aware diff-copy of declared-rw changes back to the real repo
        (BR-4). Delegates to ``sync_back``; conflicts are loud."""
        return sync_back(
            self._export_root, self._real_repo, declared_rw,
            baseline=getattr(self, "_baseline", None),
            cache_paths=[c.relative for c in self._plan.cache_mounts])
