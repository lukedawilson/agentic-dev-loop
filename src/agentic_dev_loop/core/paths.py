"""Repo-relative path helpers shared by config validation and sync-back.

One place for the declared-path exclusion vocabulary (028): literal entries
exclude by component prefix (the dir and everything under it); wildcard
entries match by ``fnmatch.fnmatchcase`` on the full posix relpath. Both the
``caches:``/``sync_excludes:`` validators (core) and the export/sync-back
planners (engine) consume these, so the semantics cannot drift.
"""

from __future__ import annotations

import fnmatch


def relative_parts(path: str) -> tuple[str, ...]:
    """Normalise a repo-relative path into its non-empty, non-``.`` segments."""
    return tuple(seg for seg in path.replace("\\", "/").split("/")
                 if seg not in ("", "."))


def _has_wildcards(spec: str) -> bool:
    return any(ch in spec for ch in "*?[")


def path_excluded(rel: str, excludes: tuple[str, ...] | list[str]) -> bool:
    """True when a repo-relative posix path is excluded by a declared
    ``sync_excludes:`` spec (028).

    Literal entries exclude by component prefix; wildcard entries by
    ``fnmatch.fnmatchcase`` on the full posix relpath. ``*`` spans path
    separators (fnmatch string semantics), so ``**/bin/**`` matches a literal
    ``/bin/`` segment at any depth and ``*.dll`` matches at any depth.
    """
    parts = relative_parts(rel)
    for spec in excludes:
        if _has_wildcards(spec):
            if fnmatch.fnmatchcase(rel, spec):
                return True
        else:
            spec_parts = relative_parts(spec)
            if not spec_parts:
                # ``.`` (or a blank entry) excludes the whole tree.
                return True
            if parts[:len(spec_parts)] == spec_parts:
                return True
    return False


def dir_excluded(dir_rel: str, excludes: tuple[str, ...] | list[str]) -> bool:
    """True when a directory (as a posix relpath) must be pruned from a walk.

    Literal excludes prune their subtree. Wildcard excludes prune only when
    the directory itself is the matched target - checked with a trailing
    separator so ``**/bin/**`` prunes ``src/proj/bin`` but ``*.dll`` never
    prunes a directory.
    """
    parts = relative_parts(dir_rel)
    for spec in excludes:
        if _has_wildcards(spec):
            if fnmatch.fnmatchcase(dir_rel.rstrip("/") + "/", spec):
                return True
        else:
            spec_parts = relative_parts(spec)
            if not spec_parts:
                # ``.`` (or a blank entry) excludes the whole tree.
                return True
            if parts[:len(spec_parts)] == spec_parts:
                return True
    return False
