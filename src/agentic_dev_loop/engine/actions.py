"""Built-in pipeline actions. Node-shaped: (state, params) -> state update.

`commit` policy (ported unchanged from the consumer): agents never commit mid-run;
the pipeline commits exactly once, and only when the worktree was clean at
run start - a dirty-at-start run is committed by the human, so the
auto-commit can never swallow pre-existing unrelated changes (run 004: the
dev agent's b4acb62a swept up an unrelated .gitignore edit).
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .proc import child_env, is_pid_token


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Best-effort git invocation - callers inspect returncode/stdout rather
    than raising, so a git problem never crashes a verified run. ``git`` is
    resolved via ``shutil.which`` and run argv-style with a scrubbed child
    environment (BR-5)."""
    git = shutil.which("git")
    if git is None:
        return subprocess.CompletedProcess(["git", *args], 127, "", "")
    return subprocess.run([git, *args], cwd=cwd, capture_output=True,
                          text=True, check=False, shell=False,
                          env=child_env())


def _trusted_hooks_dir() -> str:
    """The agentic-dev-loop-managed hooks dir (Q3): repo hooks in ``.git/hooks``
    never execute during the trusted commit action. Created empty; git runs
    with ``-c core.hooksPath=<this dir>`` so there is no hook to run."""
    d = Path("~/.agentic-dev-loop/state/hooks").expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def git_worktree_clean(path: Path) -> bool:
    """True only when `path` is a git repo with no uncommitted changes.
    Outside a repo there is no clean baseline - False (fail-safe, keeps the
    commit node disabled)."""
    result = _git(["status", "--porcelain"], cwd=path)
    return result.returncode == 0 and not result.stdout.strip()


def _parse_cwd_output(out: str) -> str | None:
    """The cwd path from `lsof -a -p PID -d cwd -Fn` output, or None when the
    payload doesn't carry one. Two formats occur in the wild: macOS prints
    `p<PID>` / `fcwd` / `n<path>` (the path is the `n` line immediately after
    `fcwd`); Linux omits the fd line and prints `p<PID>` / `n<path>` (the
    path is the first `n` line)."""
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line == "fcwd" and i + 1 < len(lines):
            nxt = lines[i + 1]
            if nxt.startswith("n"):
                return nxt[1:]
        if line.startswith("n"):
            return line[1:]
    return None


def _pid_cwd(pid: str) -> str | None:
    """The working directory of `pid`, or None on any failure (lsof missing,
    the process gone, or an unreadable cwd). Positive-only attribution: a pid
    whose cwd can't be determined is spared by the caller, never killed.
    Only a validated pid token reaches ``lsof`` (BR-5.2)."""
    if not is_pid_token(pid):
        return None
    lsof = shutil.which("lsof")
    if lsof is None:
        return None
    try:
        out = subprocess.run(
            [lsof, "-a", "-p", pid, "-d", "cwd", "-Fn"],
            capture_output=True, text=True, check=False, shell=False,
            env=child_env()).stdout
    except FileNotFoundError:
        return None
    return _parse_cwd_output(out)


def _listening_pids(port: int, match: str | None = None,
                    cwd: Path | str | None = None) -> list[str]:
    """PIDs of processes listening on `port`, optionally only those whose
    command line contains `match` and whose working directory resolves to
    `cwd`. The current process is always excluded - a pipeline must never
    sweep its own listener (e.g. the live viz server), only orphaned
    listeners left behind by earlier runs or agent subprocesses.

    `cwd` is positive-only: a pid whose cwd can't be determined is spared,
    so a multi-instance setup (two agentic-dev-loop runs from different
    workspaces) never kills the other run's server.

    Every token from ``lsof`` output is validated numeric before it can reach
    ``ps``/``kill`` (BR-5.2); binaries resolve via ``shutil.which`` and run
    argv-style with a scrubbed environment (BR-5.1, BR-5.3, BR-5.4)."""
    lsof = shutil.which("lsof")
    if lsof is None:
        return []
    out = subprocess.run(
        [lsof, "-nP", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
        capture_output=True, text=True, check=False, shell=False,
        env=child_env()).stdout
    self_pid = str(os.getpid())
    ps = shutil.which("ps")
    pids = []
    for token in out.split():
        if not is_pid_token(token) or token == self_pid:
            continue
        if match is not None:
            if ps is None:
                continue
            cmd = subprocess.run(
                [ps, "-p", token, "-o", "command="],
                capture_output=True, text=True, check=False, shell=False,
                env=child_env()).stdout
            if match not in cmd:
                continue
        if cwd is not None:
            pid_cwd = _pid_cwd(token)
            if pid_cwd is None or Path(pid_cwd).resolve() != Path(cwd).resolve():
                continue
        pids.append(token)
    return pids


def kill_listeners_on_port(port: int, match: str | None = None,
                           term_timeout: float = 3.0,
                           cwd: Path | str | None = None) -> None:
    """Kill processes listening on `port`, optionally only those whose command
    line contains `match` and whose working directory resolves to `cwd`. A
    stage that was cut short can orphan its server; the next attempt's server
    would fail to bind and probes would hit the stale process serving a
    previous attempt's build.

    SIGTERM first, then SIGKILL whatever still holds the port after
    `term_timeout` - a server that ignores TERM must not hold the port
    indefinitely. Port freedom is judged by re-running lsof, so zombies
    (dead, unreaped children) never look like survivors."""
    if shutil.which("lsof") is None:
        print(f"WARNING: lsof not found - cannot sweep orphaned listeners on "
              f"port {port}.", file=sys.stderr, flush=True)
        return
    kill = shutil.which("kill")
    pids = _listening_pids(port, match, cwd)
    for pid in pids:
        if kill is not None:
            subprocess.run([kill, pid], capture_output=True, check=False,
                           shell=False, env=child_env())
    survivors = pids
    deadline = time.monotonic() + term_timeout
    while survivors and time.monotonic() < deadline:
        survivors = _listening_pids(port, match, cwd)
        if survivors:
            time.sleep(0.05)
    for pid in survivors:
        if kill is not None:
            subprocess.run([kill, "-9", pid], capture_output=True, check=False,
                           shell=False, env=child_env())


def checkpoint_exclude_pathspec(state_dir: str | None, cwd: Path) -> str | None:
    """git pathspec excluding the live checkpoint DB from ``git add -A``.

    The checkpointer keeps ``state_dir/loop-checkpoints.sqlite`` open for the
    whole run; on Windows an open SQLite file blocks other readers, so
    ``git add`` fails to hash it (CI-red gate failure). When the state dir
    sits inside the repo, exclude its sqlite files (db + wal/shm) from the
    auto-commit - checkpoint state is runtime machinery, not constructed
    work. Returns None when there is nothing in-repo to exclude (no state
    dir known, or it lives outside the repo).
    """
    if not state_dir:
        return None
    rel = os.path.relpath(state_dir, cwd)
    if rel == ".." or rel.startswith(".." + os.sep):
        return None
    rel = rel.replace(os.sep, "/")
    if rel == ".":
        return ":(exclude)*.sqlite*"
    return f":(exclude){rel}/*.sqlite*"


def tooling_exclude_pathspec(tooling_paths: list[str] | None) -> list[str]:
    """git pathspecs excluding tooling paths from ``git add -A`` (020).

    The loop must never commit its own tooling (pipeline, agent defs,
    additional files, aidlc-docs, tools): the run-start integrity check
    requires those paths to be clean relative to HEAD, so the auto-commit
    must sweep everything *except* them. Empty when there is nothing to
    exclude."""
    specs = []
    for rel in (tooling_paths or []):
        rel = rel.replace(os.sep, "/")
        if rel in ("", ".", ".."):
            continue
        specs.append(f":(exclude){rel}")
    return specs


def commit(state: dict, params: dict) -> dict:
    """The pipeline's only commit point. Skipped loudly when the worktree was
    dirty at run start, so the auto-commit can never swallow pre-existing
    unrelated changes; a dirty-at-start run is committed by the human.

    `params["message"]` is required - a template rendered with `{subject}`.
    Runs in the process's cwd (the consumer repo the pipeline was launched
    from)."""
    subject = state["subject"]
    if not state.get("commit_allowed", False):
        print(f"[{subject}] Skipping auto-commit - worktree was dirty at run "
              f"start; commit the constructed work manually.", flush=True)
        return {}
    if "message" not in params:
        raise ValueError("commit action requires params.message "
                         "(a template rendered with {subject})")
    cwd = Path.cwd()
    hooks = _trusted_hooks_dir()
    add_argv = ["-c", f"core.hooksPath={hooks}", "add", "-A"]
    excludes = []
    checkpoint_exclude = checkpoint_exclude_pathspec(state.get("state_dir"), cwd)
    if checkpoint_exclude:
        excludes.append(checkpoint_exclude)
    excludes.extend(tooling_exclude_pathspec(state.get("tooling_paths")))
    if excludes:
        add_argv += ["--", *excludes]
    added = _git(add_argv, cwd=cwd)
    if added.returncode != 0:
        # The add is the step that touches the most files (a locked DB, a
        # permission problem) - its error is the diagnostic that matters.
        print(f"WARNING: [{subject}] auto-commit failed - git add: "
              f"{added.stderr.strip()}", file=sys.stderr, flush=True)
        return {}
    if not _git(["status", "--porcelain"], cwd=cwd).stdout.strip():
        print(f"[{subject}] Nothing to commit.", flush=True)
        return {}
    message = params["message"].format(subject=subject)
    result = _git(["-c", f"core.hooksPath={hooks}", "commit", "-m", message],
                  cwd=cwd)
    if result.returncode != 0:
        # The work is built and verified - a git error is a loud warning,
        # not a pipeline failure.
        print(f"WARNING: [{subject}] auto-commit failed - commit manually.\n"
              f"{result.stderr.strip()}", file=sys.stderr, flush=True)
        return {}
    short = _git(["rev-parse", "--short", "HEAD"], cwd=cwd).stdout.strip()
    print(f"[{subject}] Committed constructed work as {short}.", flush=True)
    return {}
