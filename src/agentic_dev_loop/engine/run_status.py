"""`run-status` subcommand: report the latest agentic-dev-loop run.

Two modes:

- ``agentic-dev-loop run-status`` (no argument) discovers the latest run
  across the whole run-state directory (``~/.agentic-dev-loop/runs/`` or
  ``$AGENTIC_DEV_LOOP_RUNS_ROOT``), so a run started from any pipeline path
  (including a smoke run's disposable temp pipeline) is found.
- ``agentic-dev-loop run-status <pipeline>`` reports the latest run for that
  one pipeline (its ``state_dir`` derived from the pipeline path).

Both print two sections: ``===MERMAID===`` (a mermaid graph of the pipeline
topology with the current step highlighted) and ``===SUMMARY===`` (bullets).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import msgpack

from agentic_dev_loop import viz
from agentic_dev_loop.core.config import load_config

CHECKPOINT_DB = "loop-checkpoints.sqlite"
COMMAND_FILE = "run-status.md"
COMMAND_RESOURCE = "run_status_command.md"
CURRENT_FILL = "#ffd43b"
DONE_FILL = "#2f9e44"


class NoRunError(Exception):
    """Raised when no run (checkpoint database) exists yet."""


@dataclass
class RunStatus:
    mermaid: str
    node_ids: list[str]
    step_names: list[str]
    status: str  # DONE | FAILED | RUNNING
    status_text: str
    current: str
    subject: str
    thread_id: str
    attempt: int
    max_attempts: int
    last_checkpoint: str
    verdicts: dict[str, str]
    failed: bool
    failed_step: str | None
    failure_class: str | None
    notes: list[str]


def _quote_node(name: str) -> str:
    if name.replace("_", "").isalnum():
        return name
    return f'"{name}"'


def _pending_nodes(writes: list[str]) -> list[str]:
    """The next scheduled nodes from a checkpoint's writes (task_path rows)."""
    pending = []
    for task_path in writes:
        if task_path.startswith("~__pregel_pull, "):
            node = task_path[len("~__pregel_pull, "):]
        elif task_path.startswith("~"):
            continue
        else:
            continue
        if node in ("__start__",) or node.startswith("__"):
            continue
        pending.append(node)
    return sorted(set(pending))


def _classify(cv: dict, pending: list[str], step_names: list[str]):
    """Decode a checkpoint's channel_values into (status, current, status_text)."""
    outcome = cv.get("outcome")
    if outcome == "success":
        return "DONE", "success", "success"
    failed = cv.get("failed")
    failed_step = cv.get("failed_step")
    if failed:
        return "FAILED", failed_step or "?", "failed"
    if pending:
        return "RUNNING", pending[0], "in progress"
    return "RUNNING", step_names[0] if step_names else "?", "in progress"


def _read_latest(db_path: Path):
    """Read the latest checkpoint from one checkpoint database.

    Returns ``(ts, thread_id, checkpoint_id, cv, pending)`` or ``None`` when
    the database does not exist or holds no checkpoints.
    """
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'"
        ).fetchone()
        if has_table is None:
            # Empty/initialized-but-unused database: no checkpoints table yet.
            return None
        rows = conn.execute(
            "SELECT thread_id, checkpoint_id, checkpoint FROM checkpoints"
        ).fetchall()
        latest = None  # (ts, thread_id, checkpoint_id, cv)
        for row in rows:
            blob = msgpack.unpackb(row["checkpoint"], raw=False)
            ts = blob.get("ts", "")
            cv = blob.get("channel_values", {}) or {}
            if latest is None or ts > latest[0]:
                latest = (ts, row["thread_id"], row["checkpoint_id"], cv)
        if latest is None:
            return None
        ts, thread_id, checkpoint_id, cv = latest
        writes = [
            r["task_path"]
            for r in conn.execute(
                "SELECT task_path FROM writes WHERE thread_id=? AND checkpoint_id=?",
                (thread_id, checkpoint_id),
            ).fetchall()
        ]
        return ts, thread_id, checkpoint_id, cv, writes
    finally:
        conn.close()


def _discovered_topology(step_names: list[str], current: str) -> str:
    """A linear mermaid chain for a discovered run (no pipeline config)."""
    nodes = list(step_names) + ["success"]
    lines = ["graph TD;"]
    for i in range(len(nodes) - 1):
        lines.append(f"\t{_quote_node(nodes[i])} --> {_quote_node(nodes[i + 1])}")
    lines.append("")
    return "\n".join(lines)


def collect(cfg) -> RunStatus:
    """Report the latest run for a loaded ``PipelineConfig``."""
    mermaid, node_ids = viz.topology_mermaid(cfg)
    step_names = [s.name for s in cfg.steps]
    db_path = Path(cfg.state_dir).expanduser().resolve() / CHECKPOINT_DB
    latest = _read_latest(db_path)
    if latest is None:
        raise NoRunError(f"no run recorded in {db_path}")
    ts, thread_id, _checkpoint_id, cv, writes = latest
    pending = _pending_nodes(writes)
    status, current, status_text = _classify(cv, pending, step_names)
    subject = cv.get("subject", "")
    verdicts = cv.get("step_verdicts", {}) or {}
    failed = bool(cv.get("failed"))
    failed_step = cv.get("failed_step")
    failure_class = cv.get("failure_class")
    notes = cv.get("notes", []) or []
    return RunStatus(
        mermaid=mermaid,
        node_ids=list(node_ids),
        step_names=step_names,
        status=status,
        status_text=status_text,
        current=current,
        subject=subject,
        thread_id=thread_id,
        attempt=int(cv.get("attempt", 1)),
        max_attempts=int(cv.get("max_attempts", 1)),
        last_checkpoint=ts,
        verdicts=verdicts,
        failed=failed,
        failed_step=failed_step,
        failure_class=failure_class,
        notes=list(notes),
    )


def _runs_root() -> Path:
    return Path(
        os.environ.get(
            "AGENTIC_DEV_LOOP_RUNS_ROOT",
            str(Path.home() / ".agentic-dev-loop" / "runs"),
        )
    ).expanduser()


def collect_latest(runs_root: Path | None = None) -> RunStatus:
    """Discover the globally latest run across all state directories.

    Scans ``<runs_root>/*/loop-checkpoints.sqlite`` and reports the run whose
    latest checkpoint has the greatest timestamp. The pipeline topology is
    unavailable without the pipeline config, so the mermaid is a linear chain
    over the recorded step verdicts plus the terminal ``success`` node.
    """
    root = Path(runs_root).expanduser() if runs_root else _runs_root()
    if not root.is_dir():
        raise NoRunError(f"no run directory at {root}")
    best = None  # (ts, thread_id, checkpoint_id, cv, writes)
    for db in sorted(root.glob(f"*/{CHECKPOINT_DB}")):
        latest = _read_latest(db)
        if latest is None:
            continue
        if best is None or latest[0] > best[0]:
            best = latest
    if best is None:
        raise NoRunError(f"no run recorded under {root}")
    ts, thread_id, _checkpoint_id, cv, writes = best
    verdicts = cv.get("step_verdicts", {}) or {}
    done_steps = list(verdicts.keys())
    pending = _pending_nodes(writes)
    status, current, status_text = _classify(cv, pending, done_steps)
    if status == "DONE":
        step_names = done_steps
        node_ids = done_steps + ["success"]
    else:
        step_names = done_steps + (
            [current] if current not in done_steps and current != "success" else []
        )
        node_ids = step_names
    mermaid = _discovered_topology(step_names, current)
    subject = cv.get("subject", "")
    failed = bool(cv.get("failed"))
    failed_step = cv.get("failed_step")
    failure_class = cv.get("failure_class")
    notes = cv.get("notes", []) or []
    return RunStatus(
        mermaid=mermaid,
        node_ids=node_ids,
        step_names=step_names,
        status=status,
        status_text=status_text,
        current=current,
        subject=subject,
        thread_id=thread_id,
        attempt=int(cv.get("attempt", 1)),
        max_attempts=int(cv.get("max_attempts", 1)),
        last_checkpoint=ts,
        verdicts=verdicts,
        failed=failed,
        failed_step=failed_step,
        failure_class=failure_class,
        notes=list(notes),
    )


def format_output(rs: RunStatus) -> str:
    """Render the mermaid + summary sections for a RunStatus."""
    done = [s for s in rs.step_names if s in rs.verdicts]
    if rs.status == "DONE":
        for s in rs.step_names:
            if s not in done:
                done.append(s)
    mermaid = rs.mermaid.rstrip("\n")
    mermaid += "\n"
    mermaid += f"\tclassDef current fill:{CURRENT_FILL},stroke:#1b1b1b,stroke-width:2px;\n"
    mermaid += f"\tclassDef done fill:{DONE_FILL},stroke:#1b1b1b,color:#fff;\n"
    mermaid += f"\tclass {_quote_node(rs.current)} current;\n"
    if done:
        mermaid += "\tclass " + ",".join(_quote_node(s) for s in done) + " done;\n"

    lines = [
        f"- Status: {rs.status_text}",
        f"- Current step: {rs.current}",
        f"- Subject: {rs.subject or '(none)'}",
        f"- Thread: {rs.thread_id}",
        f"- Attempt: {rs.attempt}/{rs.max_attempts}",
        f"- Last checkpoint: {rs.last_checkpoint}",
    ]
    step_parts = []
    for s in rs.step_names:
        if s in rs.verdicts:
            step_parts.append(f"{s}={rs.verdicts[s]}")
        elif s == rs.current:
            step_parts.append(f"{s}=in progress")
    lines.append(f"- Steps: {', '.join(step_parts)}" if step_parts
                 else "- Steps: (none)")
    if rs.failed:
        lines.append(f"- Failed at: {rs.failed_step}")
    if rs.failure_class:
        lines.append(f"- Failure class: {rs.failure_class}")
    for note in rs.notes[-3:]:
        lines.append(f"- Note: {note}")
    return "===MERMAID===\n" + mermaid + "===SUMMARY===\n" + "\n".join(lines) + "\n"


def command_text() -> str:
    return files("agentic_dev_loop.engine").joinpath(COMMAND_RESOURCE).read_text(
        encoding="utf-8"
    )


def default_commands_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / "opencode" / "commands"


def install_command(dest_dir: Path | None = None) -> Path:
    target_dir = Path(dest_dir) if dest_dir else default_commands_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / COMMAND_FILE
    target.write_text(command_text(), encoding="utf-8")
    return target


def _run_status_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-dev-loop run-status",
        description="Report the latest agentic-dev-loop run.",
    )
    parser.add_argument(
        "pipeline",
        type=Path,
        nargs="?",
        default=None,
        help="pipeline file to report (default: discover the latest run "
        "across the run-state directory)",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="install the /run-status OpenCode command into the consumer "
        "commands directory and exit",
    )
    return parser


def _run_status(argv: list[str]) -> int:
    args = _run_status_parser().parse_args(argv)

    if args.install:
        target = install_command()
        print(f"installed {target}")
        return 0

    if args.pipeline is None:
        try:
            rs = collect_latest()
        except NoRunError:
            print("NO_RUN")
            return 0
    else:
        try:
            cfg = load_config(args.pipeline)
        except FileNotFoundError:
            print(f"run-status: pipeline not found: {args.pipeline}",
                  file=sys.stderr)
            return 1
        try:
            rs = collect(cfg)
        except NoRunError:
            print("NO_RUN")
            return 0

    sys.stdout.write(format_output(rs))
    return 0
