"""Run-status reporting and OpenCode-command installation.

``agentic-dev-loop run-status <pipeline>`` renders the latest run for a pipeline
as a highlighted mermaid topology plus a bullet summary, reading the run-state
checkpoint database the engine already writes. The ``--install`` flag copies the
thin OpenCode command wrapper (which shells out to this subcommand) into the
consumer's global OpenCode commands directory.

The OpenCode command wrapper itself is shipped as a packaged resource
(``run_status_command.md``, auto-included by the engine's ``*.md``
package-data), mirroring how ``brief_prompt.md`` is shipped.
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
from agentic_dev_loop.core.config import PipelineConfig, load_config

CHECKPOINT_DB = "loop-checkpoints.sqlite"
COMMAND_FILE = "run-status.md"
COMMAND_RESOURCE = "run_status_command.md"

CURRENT_FILL = "#ffd43b"
DONE_FILL = "#2f9e44"


class NoRunError(Exception):
    """No checkpoint database exists for the pipeline - no run recorded yet."""


@dataclass
class RunStatus:
    """The decoded state of the latest run, ready to render."""

    mermaid: str
    node_ids: list[str]
    step_names: list[str]
    status: str            # "DONE" | "FAILED" | "RUNNING"
    status_text: str       # "success" | "failed" | "in progress"
    current: str
    subject: str | None
    thread_id: str
    attempt: object
    max_attempts: object
    last_checkpoint: str
    verdicts: dict
    failed: bool
    failed_step: str | None
    failure_class: str | None
    notes: list


def _quote_node(name: str) -> str:
    return name if name.replace("_", "").isalnum() else f'"{name}"'


def _pending_nodes(writes: list[tuple[str]]) -> list[str]:
    """Node names the latest checkpoint schedules next (its pending writes)."""
    out = []
    for (tp,) in writes:
        last = tp.rsplit(", ", 1)[-1] if ", " in tp else tp
        if not last.startswith("~") and last != "__start__":
            out.append(last)
    return sorted(set(out))


def collect(cfg: PipelineConfig) -> RunStatus:
    """Read the latest checkpoint and return a :class:`RunStatus`.

    Raises :class:`NoRunError` when no checkpoint database exists for the
    pipeline's ``state_dir`` (or it holds no checkpoints).
    """
    mermaid, node_ids = viz.topology_mermaid(cfg)
    step_names = [s.name for s in cfg.steps]
    db_path = Path(cfg.state_dir).expanduser().resolve() / CHECKPOINT_DB
    if not db_path.exists():
        raise NoRunError(db_path)

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    try:
        best = None
        for r in db.execute(
                "SELECT thread_id, checkpoint_id, checkpoint FROM checkpoints"):
            cp = msgpack.unpackb(r["checkpoint"], raw=False)
            ts = cp.get("ts", "")
            if best is None or ts > best[0]:
                best = (ts, r["thread_id"], r["checkpoint_id"], cp)
        if best is None:
            raise NoRunError(db_path)
        ts, tid, cid, cp = best
        cv = cp.get("channel_values", {}) or {}

        writes = db.execute(
            "SELECT task_path FROM writes WHERE thread_id=? AND checkpoint_id=?",
            (tid, cid)).fetchall()
    finally:
        db.close()

    pending = _pending_nodes([tuple(w) for w in writes])
    outcome = cv.get("outcome")
    failed = bool(cv.get("failed"))
    failed_step = cv.get("failed_step")
    failure_class = cv.get("failure_class")
    verdicts = cv.get("step_verdicts") or {}

    if outcome == "success":
        status, current, status_text = "DONE", "success", "success"
    elif failed:
        status = "FAILED"
        current = failed_step or "unknown"
        status_text = "failed"
    elif pending:
        status, current, status_text = "RUNNING", pending[0], "in progress"
    else:
        status, current, status_text = "RUNNING", step_names[0], "in progress"

    return RunStatus(
        mermaid=mermaid, node_ids=node_ids, step_names=step_names,
        status=status, status_text=status_text, current=current,
        subject=cv.get("subject"), thread_id=tid,
        attempt=cv.get("attempt"), max_attempts=cv.get("max_attempts"),
        last_checkpoint=ts, verdicts=verdicts, failed=failed,
        failed_step=failed_step, failure_class=failure_class,
        notes=list(cv.get("notes") or []),
    )


def format_output(rs: RunStatus) -> str:
    """Render the highlighted mermaid + bullet summary as one string."""
    done = [s for s in rs.step_names if s in rs.verdicts]
    if rs.status == "DONE":
        done += [s for s in rs.step_names if s not in rs.verdicts]

    lines = [
        f"classDef current fill:{CURRENT_FILL},stroke:#333,stroke-width:2px;",
        f"classDef done fill:{DONE_FILL},stroke:{DONE_FILL},color:#fff;",
    ]
    if rs.current in rs.node_ids:
        lines.append(f"class {_quote_node(rs.current)} current;")
    done_nodes = [_quote_node(n) for n in done
                  if n in rs.node_ids and n != rs.current]
    if done_nodes:
        lines.append("class " + ",".join(done_nodes) + " done;")
    mermaid = rs.mermaid.rstrip("\n") + "\n\t" + "\n\t".join(lines) + "\n"

    def step_label(s: str) -> str:
        if s in rs.verdicts:
            return str(rs.verdicts[s])
        if s == rs.current:
            return "in progress"
        return "done" if rs.status == "DONE" else "pending"

    body = [
        f"- Status: {rs.status_text}",
        f"- Current step: {rs.current}",
        f"- Subject: {rs.subject}",
        f"- Thread: {rs.thread_id}",
        f"- Attempt: {rs.attempt} / {rs.max_attempts}",
        f"- Last checkpoint: {rs.last_checkpoint}",
        "- Steps: " + ", ".join(f"{s}={step_label(s)}" for s in rs.step_names),
    ]
    if rs.failed:
        body.append(f"- Failed at: {rs.failed_step}")
    if rs.failure_class:
        body.append(f"- Failure class: {rs.failure_class}")
    for n in rs.notes[-3:]:
        body.append(f"- Note: {n}")

    return "===MERMAID===\n" + mermaid + "===SUMMARY===\n" + "\n".join(body) + "\n"


def command_text() -> str:
    """The shipped OpenCode command wrapper (thin: shells out to this CLI)."""
    return (files("agentic_dev_loop.engine")
            .joinpath(COMMAND_RESOURCE)
            .read_text(encoding="utf-8"))


def default_commands_dir() -> Path:
    """The consumer's global OpenCode commands directory."""
    root = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(root).expanduser() / "opencode" / "commands"


def install_command(dest_dir: Path | None = None) -> Path:
    """Copy the wrapper into ``dest_dir`` (default: OpenCode global commands).

    Returns the written path. The destination directory is created if missing.
    """
    dest = dest_dir if dest_dir is not None else default_commands_dir()
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / COMMAND_FILE
    target.write_text(command_text(), encoding="utf-8")
    return target


def _run_status(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agentic-dev-loop run-status",
        description="Render the latest pipeline run as a highlighted mermaid "
                    "graph with a bullet summary, or install the /run-status "
                    "OpenCode command.")
    parser.add_argument(
        "pipeline", type=Path, nargs="?",
        default=Path("agentic-dev-loop/pipeline.yaml"),
        help="pipeline file (default: agentic-dev-loop/pipeline.yaml)")
    parser.add_argument(
        "--install", action="store_true",
        help="copy the /run-status OpenCode command into the consumer's "
             "OpenCode commands directory, then exit")
    args = parser.parse_args(argv)

    if args.install:
        target = install_command()
        print(f"installed {target}")
        return 0

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
