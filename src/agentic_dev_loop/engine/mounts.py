"""Mount-set resolution (unit-5): per-step mount granularity.

``resolve_mount_set`` turns a step's declared ``mounts:`` into a validated
``MountSet`` - strict-by-default (empty input = empty mount set, BR-1.1), mode
per entry (ro default, BR-1.2). Validation already happened at config load;
this module is the resolved, hashable model the container pool keys on (D-1).
"""

from __future__ import annotations

from dataclasses import dataclass

from agentic_dev_loop.core.config import MountSpec

RW = "rw"
RO = "ro"


@dataclass(frozen=True)
class Mount:
    """One resolved mount entry: a repo-relative path plus its mode."""

    path: str
    mode: str  # RW | RO


@dataclass(frozen=True)
class MountSet:
    """The resolved mount set (BR-1). Empty means mount nothing (strict
    default); equality/hash feed the profile-keyed container pool (D-1)."""

    mounts: tuple[Mount, ...] = ()

    def __iter__(self):
        return iter(self.mounts)

    def __len__(self) -> int:
        return len(self.mounts)

    def paths(self) -> tuple[str, ...]:
        return tuple(m.path for m in self.mounts)

    def rw_paths(self) -> tuple[str, ...]:
        """The declared-rw paths - the only paths that sync back (BR-4.2)."""
        return tuple(m.path for m in self.mounts if m.mode == RW)

    def to_specs(self) -> tuple[MountSpec, ...]:
        return tuple(MountSpec(path=m.path, mode=m.mode) for m in self.mounts)

    def to_dict(self) -> list[dict[str, str]]:
        return [{"path": m.path, "mode": m.mode} for m in self.mounts]


def resolve_mount_set(step_mounts: list[MountSpec] | None) -> MountSet:
    """Strict-by-default resolution (BR-1.1): empty/missing ``mounts`` yields
    an empty mount set - the step mounts nothing until paths are declared.

    Entries arrive already validated by ``MountSpec``; the resolved model is
    built deterministically in declaration order (PBT-03)."""
    if not step_mounts:
        return MountSet()
    return MountSet(
        mounts=tuple(Mount(path=m.path, mode=m.mode) for m in step_mounts))
