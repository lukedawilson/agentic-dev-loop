"""`agentic-dev-loop fanout` - launch one independent pipeline run per
document source, each in its own git worktree and branch.

The planner is a pure function of the collected entries plus the git facts
(base commit, repo root, re-rooted pipeline path); the executor is two phases
so a worktree-creation failure never strands a running child: create every
worktree first, then spawn children for the entries that were created.

Children are stock single-run CLI invocations (`python -m agentic_dev_loop`)
started in their worktree. Completion is detected from the two stable
terminal CLI lines, not from exit codes, because a child with viz keeps
serving after its run finishes. Nothing here pushes, merges, rebases, or
lands: the operator merges each branch by hand.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from .registry import DocumentLoader, Registry


PASSED = "PASSED"
FAILED = "FAILED"
ABORTED = "ABORTED"
SKIPPED = "SKIPPED"

FINISHED_MARKER = "Pipeline finished: press ctrl+c to exit"
FAILED_MARKER = "agentic-dev-loop: pipeline failed - exiting with code 1"
_VIZ_URL = re.compile(r"Live graph viz: (http://\S+)")
_SLUG_INVALID = re.compile(r"[^A-Za-z0-9._-]+")

_SIGTERM_GRACE_SECONDS = 10.0
_CHILD_EOF_WAIT_SECONDS = 30


class FanoutUsageError(Exception):
    """A loud usage error - the caller maps it to exit code 2."""


@dataclass(frozen=True)
class FanoutEntry:
    """One document source: a git-safe slug, a table label, and the argv
    fragment that selects this source on a stock single-run invocation."""

    slug: str
    label: str
    invocation: tuple[str, ...]


@dataclass(frozen=True)
class FanoutPlanEntry:
    entry: FanoutEntry
    branch: str
    worktree: Path
    pipeline: Path
    argv: tuple[str, ...]


@dataclass(frozen=True)
class FanoutPlan:
    repo_root: Path
    base_ref: str
    base_commit: str
    worktree_root: Path
    entries: tuple[FanoutPlanEntry, ...]


@dataclass(frozen=True)
class FanoutStatus:
    slug: str
    branch: str
    worktree: Path
    viz_url: str | None
    outcome: str


def default_slug(value: str) -> str:
    """Sanitize any string into a git-safe worktree/branch name segment."""
    slug = _SLUG_INVALID.sub("-", str(value))
    while ".." in slug:
        slug = slug.replace("..", ".")
    slug = slug.strip(".-")
    if not slug:
        raise ValueError(
            f"cannot derive a git-safe slug from {value!r} - it has no "
            "usable characters")
    return slug


def build_fanout_parser(registry: Registry,
                        argv: Sequence[str]) -> argparse.ArgumentParser:
    """The full fanout parser: fanout flags plus each fanout-capable loader's
    flags registered with ``multi=True``. A loader that cannot register
    multi-value flags, or that lacks ``fanout_entries``, contributes nothing;
    if its flag is present the invocation is refused before any worktree
    exists."""
    parser = _fanout_parser()
    for loader in registry.document_loaders:
        try:
            loader.add_cli_args(parser, multi=True)
        except TypeError:
            _refuse_non_capable(registry, loader, argv)
            continue
        if not callable(getattr(loader, "fanout_entries", None)):
            _refuse_non_capable(registry, loader, argv)
    return parser


def collect_fanout_entries(registry: Registry,
                           args: argparse.Namespace) -> list[FanoutEntry]:
    """Entry order: loaders in registry order, values in argv order within a
    loader. Deterministic and semantically irrelevant - every entry launches
    independently."""
    entries: list[FanoutEntry] = []
    for loader in registry.document_loaders:
        factory = getattr(loader, "fanout_entries", None)
        if not callable(factory):
            continue
        found = factory(args)
        if found:
            entries.extend(found)
    return entries


def validate_entries(entries: Sequence[FanoutEntry]) -> None:
    if len(entries) < 2:
        if entries:
            argv = " ".join(shlex.quote(part) for part in entries[0].invocation)
            raise FanoutUsageError(
                "fanout needs at least two entries; for a single run use: "
                f"agentic-dev-loop PIPELINE {argv}")
        raise FanoutUsageError(
            "fanout needs at least two entries; pass repeated document-source "
            "flags (--plan, --aidlc-unit, or a custom loader flag)")
    seen: dict[str, str] = {}
    for entry in entries:
        other = seen.get(entry.slug)
        if other is not None:
            raise FanoutUsageError(
                f"duplicate fanout slug {entry.slug!r} from {other!r} and "
                f"{entry.label!r}")
        seen[entry.slug] = entry.label


def resolve_repo_root(cwd: Path) -> Path:
    """The main checkout root. A non-repo cwd is a loud usage error."""
    result = _git("rev-parse", "--show-toplevel", cwd=Path(cwd))
    top = result.stdout.strip()
    if result.returncode != 0 or not top:
        detail = result.stderr.strip() or result.stdout.strip()
        raise FanoutUsageError(
            f"{cwd} is not a git repository: {detail or 'git rev-parse failed'}")
    return Path(top)


def plan_fanout(entries: Sequence[FanoutEntry], *, pipeline: Path,
                repo_root: Path, base_ref: str, worktree_root: Path,
                shared_flags: Sequence[str]) -> FanoutPlan:
    """Resolve the base commit once, re-root the pipeline into each worktree,
    and compute every child argv. Git reads only - no mutation happens here."""
    repo_root = Path(repo_root).resolve()
    base_commit = _resolve_commit(repo_root, base_ref)
    pipeline_abs = Path(pipeline).resolve()
    try:
        pipeline_rel = pipeline_abs.relative_to(repo_root)
    except ValueError:
        raise FanoutUsageError(
            f"pipeline file {pipeline_abs} must be inside the git repository "
            f"{repo_root} so it can be re-rooted into each worktree")
    tracked = _git("cat-file", "-e", f"{base_commit}:{pipeline_rel.as_posix()}",
                   cwd=repo_root)
    if tracked.returncode != 0:
        raise FanoutUsageError(
            f"pipeline file {pipeline_rel} is not tracked in {base_ref} "
            f"({base_commit}); commit it before running fanout")
    root = Path(worktree_root).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root = root.resolve()
    planned: list[FanoutPlanEntry] = []
    for entry in entries:
        worktree = root / f"fanout-{entry.slug}"
        child_pipeline = worktree / pipeline_rel
        argv = (sys.executable, "-m", "agentic_dev_loop",
                str(child_pipeline), *entry.invocation, *shared_flags)
        planned.append(FanoutPlanEntry(
            entry=entry, branch=f"loop/fanout/{entry.slug}",
            worktree=worktree, pipeline=child_pipeline, argv=argv))
    return FanoutPlan(repo_root=repo_root, base_ref=base_ref,
                      base_commit=base_commit, worktree_root=root,
                      entries=tuple(planned))


def create_worktree(repo_root: Path, worktree: Path, branch: str,
                    base_commit: str) -> str | None:
    """`git worktree add` one entry. Returns an error message or None; never
    raises for an expected git refusal (existing branch, bad path)."""
    worktree = Path(worktree)
    try:
        worktree.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return str(exc)
    result = _git("worktree", "add", str(worktree), "-b", branch, base_commit,
                  cwd=Path(repo_root))
    if result.returncode == 0:
        return None
    return (result.stderr.strip() or result.stdout.strip()
            or f"git worktree add exited {result.returncode}")


def render_plan(plan: FanoutPlan) -> str:
    lines = [
        f"fanout plan: {len(plan.entries)} entries",
        f"repo: {plan.repo_root}",
        f"base-ref: {plan.base_ref} -> {plan.base_commit}",
        f"worktree-root: {plan.worktree_root}",
        "",
    ]
    for item in plan.entries:
        lines.extend([
            f"slug:     {item.entry.slug}",
            f"  label:    {item.entry.label}",
            f"  branch:   {item.branch}",
            f"  worktree: {_display_path(item.worktree, plan.repo_root)}",
            f"  pipeline: {_display_path(item.pipeline, plan.repo_root)}",
            "  argv:     " + " ".join(shlex.quote(part) for part in item.argv),
            "",
        ])
    return "\n".join(lines).rstrip("\n") + "\n"


def render_table(rows: Sequence[FanoutStatus], repo_root: Path) -> str:
    """The final status table - a pure function of the rows."""
    headers = ["slug", "branch", "worktree", "viz URL", "outcome"]
    cells = [
        [row.slug, row.branch, _display_path(row.worktree, repo_root),
         row.viz_url or "-", row.outcome]
        for row in rows
    ]
    widths = [len(header) for header in headers]
    for row in cells:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    header = "  ".join(value.ljust(width)
                       for value, width in zip(headers, widths))
    rule = "  ".join("-" * width for width in widths)
    body = ["  ".join(value.ljust(width)
                      for value, width in zip(row, widths)) for row in cells]
    return "\n".join([header, rule, *body]) + "\n"


def aggregate_exit_code(rows: Sequence[FanoutStatus]) -> int:
    if rows and all(row.outcome == PASSED for row in rows):
        return 0
    return 1


def extract_viz_url(line: str) -> str | None:
    match = _VIZ_URL.search(line)
    return match.group(1) if match else None


def is_finished_line(line: str) -> bool:
    return FINISHED_MARKER in line


def is_failed_line(line: str) -> bool:
    return FAILED_MARKER in line


def classify_outcome(*, finished: bool, failed: bool,
                     returncode: int | None) -> str:
    """Terminal-marker classification with the dead-process fallback for
    `--no-viz` children (which exit on their own without the marker)."""
    if failed:
        return FAILED
    if finished:
        return PASSED
    if returncode == 0:
        return PASSED
    return ABORTED


@dataclass
class StreamResult:
    viz_url: str | None = None
    finished: bool = False
    failed: bool = False
    eof: bool = False


def consume_stream(stream: Iterable[str], slug: str, out: TextIO) -> StreamResult:
    """Prefix each child line into the parent's stdout and capture the URL
    and terminal markers. A finished child stops the read: it is idle at its
    keep-alive, so waiting for EOF would block forever."""
    result = StreamResult()
    for raw in stream:
        line = raw.rstrip("\n")
        print(f"[{slug}] {line}", file=out, flush=True)
        url = extract_viz_url(line)
        if url is not None:
            result.viz_url = url
        if is_failed_line(line):
            result.failed = True
        if is_finished_line(line):
            result.finished = True
            return result
    result.eof = True
    return result


@dataclass
class _ChildState:
    proc: subprocess.Popen
    viz_url: str | None = None
    finished: bool = False
    failed: bool = False
    outcome: str | None = None
    done: threading.Event = field(default_factory=threading.Event)


def _pump(proc: subprocess.Popen, slug: str, out: TextIO,
          state: _ChildState) -> None:
    try:
        result = consume_stream(proc.stdout, slug, out)
        state.viz_url = result.viz_url
        state.finished = result.finished
        state.failed = result.failed
        if result.finished:
            returncode = None
        else:
            try:
                returncode = proc.wait(timeout=_CHILD_EOF_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                returncode = None
        state.outcome = classify_outcome(
            finished=result.finished, failed=result.failed,
            returncode=returncode)
    except (OSError, ValueError):
        state.outcome = ABORTED
    finally:
        state.done.set()


def _make_sigterm_handler(states: Sequence[_ChildState]):
    def handler(signum, frame):
        for state in states:
            if state.proc.poll() is None:
                try:
                    state.proc.terminate()
                except OSError:
                    pass
        deadline = time.monotonic() + _SIGTERM_GRACE_SECONDS
        while (time.monotonic() < deadline
               and any(state.proc.poll() is None for state in states)):
            time.sleep(0.05)
        for state in states:
            if state.proc.poll() is None:
                try:
                    state.proc.kill()
                except OSError:
                    pass
        raise SystemExit(143)
    return handler


def _execute(plan: FanoutPlan, *, no_viz: bool, out: TextIO | None = None,
             err: TextIO | None = None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    skipped: dict[str, FanoutStatus] = {}
    created: list[FanoutPlanEntry] = []
    for item in plan.entries:
        slug = item.entry.slug
        error: str | None
        try:
            error = create_worktree(plan.repo_root, item.worktree,
                                    item.branch, plan.base_commit)
        except OSError as exc:
            error = str(exc)
        if error is None and not item.pipeline.is_file():
            error = (f"pipeline file missing from the worktree: {item.pipeline}"
                     " (is it tracked in the base commit?)")
        if error is not None:
            print(f"fanout: {slug}: SKIPPED: {error}", file=err, flush=True)
            skipped[slug] = FanoutStatus(
                slug, item.branch, item.worktree, None, SKIPPED)
            continue
        print(f"fanout: {slug}: worktree {item.worktree} "
              f"(branch {item.branch})", file=out, flush=True)
        created.append(item)

    states: dict[str, _ChildState] = {}
    for item in created:
        slug = item.entry.slug
        try:
            proc = subprocess.Popen(
                list(item.argv), cwd=str(item.worktree),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", bufsize=1)
        except OSError as exc:
            print(f"fanout: {slug}: ABORTED: cannot spawn: {exc}", file=err,
                  flush=True)
            skipped[slug] = FanoutStatus(
                slug, item.branch, item.worktree, None, ABORTED)
            continue
        state = _ChildState(proc=proc)
        states[slug] = state
        threading.Thread(target=_pump, args=(proc, slug, out, state),
                         daemon=True).start()

    previous = None
    if states:
        previous = signal.signal(signal.SIGTERM,
                                 _make_sigterm_handler(list(states.values())))
    interrupted = False
    try:
        while not all(state.done.is_set() for state in states.values()):
            time.sleep(0.05)
    except KeyboardInterrupt:
        interrupted = True

    rows: list[FanoutStatus] = []
    for item in plan.entries:
        slug = item.entry.slug
        if slug in skipped:
            rows.append(skipped[slug])
            continue
        state = states.get(slug)
        rows.append(FanoutStatus(
            slug, item.branch, item.worktree,
            state.viz_url if state else None,
            (state.outcome if state and state.outcome else ABORTED)))
    if previous is not None:
        signal.signal(signal.SIGTERM, previous)

    print(render_table(rows, plan.repo_root), file=out, end="", flush=True)
    if states and not no_viz and not interrupted:
        print("Fanout finished: press ctrl+c to exit", file=out, flush=True)
        try:
            _wait_for_interrupt()
        except KeyboardInterrupt:
            pass
    return aggregate_exit_code(rows)


def run_fanout(argv: list[str]) -> int:
    """`agentic-dev-loop fanout ...` entry point. Usage errors exit 2."""
    try:
        return _run_fanout(list(argv))
    except FanoutUsageError as exc:
        print(f"agentic-dev-loop fanout: {exc}", file=sys.stderr)
        return 2


def _run_fanout(argv: list[str]) -> int:
    from .cli import _DEFAULT_PIPELINE, _load_env_file, _registry_for

    if argv and argv[0] in ("-h", "--help"):
        _fanout_parser().print_help()
        return 0
    explicit = Path(argv[0]) if argv and not argv[0].startswith("-") else None
    pipeline = explicit if explicit is not None else _DEFAULT_PIPELINE
    if not pipeline.is_file():
        if explicit is None:
            raise FanoutUsageError(f"no pipeline at {pipeline} and none given")
        raise FanoutUsageError(f"pipeline file not found: {pipeline}")

    registry, _cfg = _registry_for(pipeline)
    parser = build_fanout_parser(registry, argv)
    args = parser.parse_args(argv)
    if explicit is None and args.pipeline is not None:
        raise FanoutUsageError("PIPELINE must come before fanout options")
    args.pipeline = pipeline

    if args.env is not None:
        error = _load_env_file(Path(args.env))
        if error is not None:
            print(f"agentic-dev-loop fanout: {error}", file=sys.stderr)
            return 1

    entries = collect_fanout_entries(registry, args)
    validate_entries(entries)

    repo_root = resolve_repo_root(Path.cwd())
    worktree_root = args.worktree_root or repo_root.parent
    plan = plan_fanout(entries, pipeline=pipeline, repo_root=repo_root,
                       base_ref=args.base_ref, worktree_root=worktree_root,
                       shared_flags=_shared_flags(args))
    if args.dry_run:
        print(render_plan(plan))
        return 0
    return _execute(plan, no_viz=args.no_viz)


def _fanout_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-dev-loop fanout",
        description="Launch one run per document source in its own worktree.")
    parser.add_argument("pipeline", type=Path, nargs="?", default=None,
                        help="path to the pipeline YAML (defaults to "
                             "agentic-dev-loop/pipeline.yaml); it must come "
                             "before the document-source flags")
    parser.add_argument("--base-ref", default="HEAD",
                        help="git ref every worktree branches from "
                             "(default: HEAD)")
    parser.add_argument("--worktree-root", type=Path, default=None,
                        help="parent directory for the per-entry worktrees "
                             "(each named fanout-<slug>; default: the repo "
                             "root's parent directory)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the computed plan without touching git or "
                             "spawning any child")
    parser.add_argument("--max-attempts", type=int, default=None,
                        help="forwarded to every child run")
    parser.add_argument("--no-viz", action="store_true",
                        help="forwarded to every child run; children then "
                             "exit on their own")
    parser.add_argument("--env", metavar="FILE", default=None,
                        help="host env file forwarded to every child run")
    return parser


def _shared_flags(args: argparse.Namespace) -> tuple[str, ...]:
    flags: list[str] = []
    if getattr(args, "max_attempts", None) is not None:
        flags.extend(["--max-attempts", str(args.max_attempts)])
    if getattr(args, "env", None) is not None:
        flags.extend(["--env", str(Path(args.env).expanduser().resolve())])
    if getattr(args, "no_viz", False):
        flags.append("--no-viz")
    return tuple(flags)


def _refuse_non_capable(registry: Registry, loader: DocumentLoader,
                        argv: Sequence[str]) -> None:
    options = [option for option, owner in registry.cli_option_owners.items()
               if owner == loader.name]
    found = next(
        (option for token in argv for option in options
         if token == option or token.startswith(option + "=")), None)
    if found is not None:
        raise FanoutUsageError(
            f"document loader {loader.name!r} does not support fanout "
            f"(option {found})")


def _resolve_commit(repo_root: Path, base_ref: str) -> str:
    result = _git("rev-parse", "--verify", f"{base_ref}^{{commit}}",
                  cwd=repo_root)
    commit = result.stdout.strip()
    if result.returncode != 0 or not commit:
        detail = result.stderr.strip() or result.stdout.strip()
        raise FanoutUsageError(
            f"cannot resolve base ref {base_ref!r} in {repo_root}: "
            f"{detail or 'git rev-parse failed'}")
    return commit


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def _display_path(path: Path, root: Path) -> str:
    try:
        return os.path.relpath(Path(path), Path(root))
    except ValueError:
        return str(path)


def _wait_for_interrupt() -> None:
    idle = threading.Event()
    while not idle.wait(1.0):
        pass
