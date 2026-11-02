"""Step workspace materialisation (unit-5): the .git-free export the sandbox
mounts (BR-2).

Copy-based (ID-4): everything except ``.git``, gitignored paths, declared
caches, the run state dir, and sync-excludes is copied into
``state_dir/exports/<run-id>/``; declared caches are bind-mounted rw from the
host-side cache dir (``{isolation.state_dir}/caches/<name>``, 052) at the same
relative paths (BR-3). The export is owned and mutated by the engine, never by
the sandbox.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from agentic_dev_loop.core.paths import dir_excluded, path_excluded

from .sync_back import SyncBackReport, _is_ignored, _snapshot, sync_back

# ``.git`` is excluded by construction (BR-2) and is guarded separately so a
# nested ``.git`` can never leak through either. Toolchain dirs (``.venv``,
# ``node_modules``, etc.) are gitignored, not engine-hardcoded (052).
_EXCLUDED_DIRS = frozenset({".git"})


@dataclass(frozen=True)
class CacheMount:
    """One declared cache bind-mount (F2/052): the host-side cache source path
    and its repo-relative posix path (== the mount target inside the export)."""

    source: str  # absolute host path (host-side cache dir)
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
                        state_dir: str | None = None,
                        excludes: list[str] | None = None,
                        ignored: frozenset[str] | set[str] = frozenset(),
                        cache_host_root: str | Path | None = None,
                        ) -> StepWorkspacePlan:
    """Plan the export (BR-2/BR-3/052): copy every workspace file except
    ``.git``/gitignored paths/cache paths/``state_dir``/sync-excludes; bind-mount
    each declared cache rw from the host-side cache dir. The ignored-path set is
    computed host-side (git check-ignore) and passed in as input data - the pure
    planner never touches git. Pure over platform facts - deterministic given
    the repo state."""
    repo = Path(repo)
    export_root = Path(export_root)
    cache_rels = [_relative_parts(c) for c in cache_paths]
    state_dir_rel = _relative_parts(state_dir) if state_dir else None
    excludes = list(excludes or ())
    cache_src = Path(cache_host_root) if cache_host_root is not None else repo

    copy_paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        rel_dir = Path(dirpath).relative_to(repo)
        rel_dir_parts = _relative_parts(rel_dir.as_posix())
        dirnames[:] = [
            d for d in dirnames
            if d not in _EXCLUDED_DIRS
            and not _excluded(rel_dir_parts + (d,), cache_rels, state_dir_rel)
            and not dir_excluded((rel_dir / d).as_posix(), excludes)
            and not _is_ignored((rel_dir / d).as_posix(), ignored)
        ]
        for name in filenames:
            parts = rel_dir_parts + (name,)
            if _excluded(parts, cache_rels, state_dir_rel):
                continue
            rel = "/".join(parts)
            if path_excluded(rel, excludes):
                continue
            if _is_ignored(rel, ignored):
                continue
            copy_paths.append(rel)

    cache_mounts = tuple(
        CacheMount(source=str(cache_src / c), relative=c) for c in cache_paths)
    return StepWorkspacePlan(
        export_root=str(export_root),
        copy_paths=tuple(sorted(copy_paths)),
        cache_mounts=cache_mounts,
    )


def _excluded(parts: tuple[str, ...], cache_rels: list[tuple[str, ...]],
              state_dir_rel: tuple[str, ...] | None) -> bool:
    """True when a relative path (as parts) must be excluded from the export:
    ``.git`` components, any declared cache, or the state dir."""
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
                    state_dir: str | None = None,
                    excludes: list[str] | None = None,
                    ignored: frozenset[str] | set[str] = frozenset(),
                    cache_host_root: str | Path | None = None,
                    ) -> "StepWorkspace":
        """Copy the workspace into the export, excluding ``.git``/gitignored
        paths/caches/``state_dir``/sync-excludes; pre-create the cache mount
        targets so the rw bind-mounts have uid-owned destinations (the
        AE-SBX-009 class)."""
        plan = plan_step_workspace(repo, cache_paths, platform, export_root,
                                   state_dir=state_dir, excludes=excludes,
                                   ignored=ignored,
                                   cache_host_root=cache_host_root)
        ws = cls(plan, real_repo=repo)
        ws._copy(repo)
        ws._baseline = _snapshot(
            ws._export_root,
            cache_paths=[c.relative for c in plan.cache_mounts],
            excludes=list(excludes or ()), ignored=ignored)
        return ws

    def _copy(self, repo: Path) -> None:
        self._export_root.mkdir(parents=True, exist_ok=True)
        for rel in self._plan.copy_paths:
            src = repo / rel
            dst = self._export_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst, follow_symlinks=False)
        for cache in self._plan.cache_mounts:
            (self._export_root / cache.relative).mkdir(parents=True,
                                                       exist_ok=True)

    def mount_points(self) -> list[tuple[str, str, str]]:
        """The engine mount list: export root rw + cache bind-mounts rw at the
        same relative paths (BR-3.1). Returns (host, container, mode)."""
        root = str(self._export_root)
        points = [(root, root, "rw")]
        for cache in self._plan.cache_mounts:
            points.append((cache.source, f"{root}/{cache.relative}", "rw"))
        return points

    def sync_back(self, declared_rw: list[str],
                  excludes: list[str] | None = None,
                  ignored: frozenset[str] | set[str] = frozenset(),
                  ) -> SyncBackReport:
        """Git-aware diff-copy of declared-rw changes back to the real repo
        (BR-4/052). Delegates to ``sync_back``; conflicts are loud."""
        return sync_back(
            self._export_root, self._real_repo, declared_rw,
            baseline=getattr(self, "_baseline", None),
            cache_paths=[c.relative for c in self._plan.cache_mounts],
            excludes=list(excludes or ()), ignored=ignored)
