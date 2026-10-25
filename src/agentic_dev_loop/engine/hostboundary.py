"""Host-side boundary checks (020): the host must execute nothing from the
workspace and read only git-clean control files.

Two fail-fast guards run before any sandbox/LLM work:

- ``check_orchestrator_install`` - refuses to run when the engine package
  itself resolves under the workspace (an in-workspace or editable install
  means the sandbox can rewrite the code the host is about to run).
- ``check_tooling_integrity`` - refuses when tooling paths (pipeline, agent
  defs, additional files, aidlc-docs, tools) are dirty relative to HEAD,
  closing the self-modifying loop and the self-gate (an agent-writable,
  network-on gate script).

``tooling_rel_paths`` is the single source of the tooling path set, shared by
the integrity check and the commit action's exclusion pathspec so the two can
never disagree.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from .proc import child_env

if TYPE_CHECKING:
    from agentic_dev_loop.core.config import PipelineConfig

# Tooling directories are workspace-root-relative fixed names (the same layout
# the dogfood pipeline and the consumer repo use); the other tooling paths are config- or
# pipeline-file-relative.
TOOLING_DIR_NAMES = ("aidlc-docs", "tools")


class HostBoundaryError(RuntimeError):
    """A loud fail-fast host-boundary failure; the message names the problem
    and points at the remedy."""


def check_orchestrator_install(workspace: str | Path,
                               engine_file: str | Path | None = None) -> None:
    """Refuse to run when the engine resolves under the workspace (020).

    The sandbox mount set is exactly {workspace}, so an in-workspace (or
    editable) install is agent-writable - the next run would execute
    agent-modified engine code in the host process. ``engine_file`` is an
    injectable seam for tests; production uses ``agentic_dev_loop.__file__``.
    """
    if engine_file is None:
        import agentic_dev_loop
        engine_file = agentic_dev_loop.__file__
    resolved = Path(engine_file).resolve()
    ws = Path(workspace).resolve()
    if resolved.is_relative_to(ws):
        raise HostBoundaryError(
            f"agentic_dev_loop resolves to {resolved}, inside the workspace "
            f"{ws} - the engine must be installed outside the workspace "
            "(e.g. `pipx install agentic-dev-loop`); refusing to run")


def tooling_rel_paths(cfg: "PipelineConfig", pipeline_path: str | Path,
                      base: str | Path, workspace: str | Path) -> list[str]:
    """Workspace-relative tooling paths for one pipeline, deduplicated and
    sorted. Only paths inside the workspace are included - anything outside
    the workspace is already outside the sandbox mount and therefore not
    agent-writable."""
    ws = Path(workspace).resolve()
    base = Path(base).resolve()
    candidates = [
        Path(pipeline_path).resolve(),
        (base / cfg.agents_dir).resolve(),
        *((base / rel).resolve() for rel in cfg.additional_files),
        *(ws / name for name in TOOLING_DIR_NAMES),
    ]
    rels = {p.relative_to(ws).as_posix() for p in candidates
            if p.is_relative_to(ws)}
    return sorted(rels)


def _git_status(paths: list[str], workspace: Path) -> tuple[int, str, str]:
    git = shutil.which("git")
    if git is None:
        raise HostBoundaryError(
            "git is not on PATH - the tooling integrity check cannot run")
    result = subprocess.run(
        [git, "status", "--porcelain", "--untracked-files=all", "--", *paths],
        cwd=workspace, capture_output=True, text=True, check=False,
        shell=False, env=child_env())
    return result.returncode, result.stdout, result.stderr


def _status_paths(output: str) -> list[str]:
    """The path column of ``git status --porcelain`` lines, with the two
    status characters stripped (``XY path`` or ``XY path -> path``)."""
    paths = []
    for line in output.splitlines():
        if not line:
            continue
        rest = line[3:] if len(line) > 3 else ""
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        if rest.strip():
            paths.append(rest)
    return paths


def check_tooling_integrity(cfg: "PipelineConfig", pipeline_path: str | Path,
                            base: str | Path, workspace: str | Path) -> None:
    """Refuse when any tooling path is dirty relative to HEAD (020).

    The run reads these paths host-side (pipeline, agent defs, shared
    additional files, the AI-DLC docs, and the ``tools/`` scripts). An agent
    that can rewrite them for the next run - or a gate script the agent can
    edit - must not be trusted. Dirty files are named; the commit action
    excludes the same paths so the loop never commits tooling changes.
    """
    rels = tooling_rel_paths(cfg, pipeline_path, base, workspace)
    if not rels:
        return
    code, output, err = _git_status(rels, Path(workspace))
    if code != 0:
        # No git repository → no HEAD baseline to be clean relative to; the
        # commit gate is already disabled for the same reason. Every other git
        # failure is loud.
        if "not a git repository" in err:
            return
        raise HostBoundaryError(
            "tooling integrity check failed - git could not inspect the "
            f"tooling paths: {err.strip() or output.strip()}")
    dirty = _status_paths(output)
    if dirty:
        raise HostBoundaryError(
            "tooling file(s) modified relative to HEAD: " + ", ".join(dirty)
            + " - tooling changes must be committed (or reverted) before the "
            "loop runs; the loop never commits them itself")
