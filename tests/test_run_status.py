"""run-status subcommand: checkpoint rendering + OpenCode-command install."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import msgpack
import pytest

from agentic_dev_loop.core.config import load_config
from agentic_dev_loop.engine.cli import main
from agentic_dev_loop.engine.run_status import (
    NoRunError,
    RunStatus,
    collect,
    collect_latest,
    command_text,
    format_output,
    install_command,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

TWO_STEP_YAML = """\
name: tiny
llm:
  model: openai/deepseek-v4-pro
  base_url: https://api.deepseek.com
  api_key_env: TEST_LLM_KEY
state_dir: __STATE_DIR__
agents_dir: sdk_agents
deps_policy: open
steps:
  - name: dev
    agent: dev
    produces: implementation-summary.md
    on_pass: review
  - name: review
    agent: review
    artifact: review-findings.md
    verdicts: { pass: APPROVED, fail: NEEDS CHANGES }
    on_pass: success
"""


def _pipeline(tmp_path: Path, state_dir: Path) -> Path:
    p = tmp_path / "pipeline.yaml"
    p.write_text(TWO_STEP_YAML.replace("__STATE_DIR__", str(state_dir)))
    return p


def _checkpoint(*, ts: str, outcome=None, failed=False, failed_step=None,
                step_verdicts=None, notes=None, subject="unit test"):
    return {
        "v": 4,
        "ts": ts,
        "id": "cp-" + ts,
        "channel_values": {
            "subject": subject,
            "attempt": 1,
            "max_attempts": 3,
            "outcome": outcome,
            "failed": failed,
            "failed_step": failed_step,
            "failure_class": None,
            "step_verdicts": step_verdicts or {},
            "notes": notes or [],
        },
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }


def _write_db(state_dir: Path, checkpoints: list[tuple[str, str, dict]],
              writes: list[tuple[str, str, str]] | None = None) -> Path:
    """A checkpoint DB with the columns ``collect`` reads (the real schema is a
    superset; these queries are column-name based)."""
    db_path = state_dir / "loop-checkpoints.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path))
    db.execute("CREATE TABLE checkpoints "
               "(thread_id TEXT, checkpoint_id TEXT, checkpoint BLOB)")
    db.execute("CREATE TABLE writes "
               "(thread_id TEXT, checkpoint_id TEXT, task_path TEXT)")
    for tid, cid, cp in checkpoints:
        db.execute("INSERT INTO checkpoints "
                   "(thread_id, checkpoint_id, checkpoint) VALUES (?, ?, ?)",
                   (tid, cid, msgpack.packb(cp)))
    for tid, cid, tp in (writes or []):
        db.execute("INSERT INTO writes "
                   "(thread_id, checkpoint_id, task_path) VALUES (?, ?, ?)",
                   (tid, cid, tp))
    db.commit()
    db.close()
    return db_path


def _rs(**overrides) -> RunStatus:
    base = dict(
        mermaid="graph TD;\n\tdev --> review\n\treview --> success\n",
        node_ids=["dev", "review", "success"],
        step_names=["dev", "review"],
        status="RUNNING", status_text="in progress", current="review",
        subject="unit test", thread_id="t1", attempt=1, max_attempts=3,
        last_checkpoint="2026-11-08T00:00:00Z",
        verdicts={"dev": "pass"}, failed=False, failed_step=None,
        failure_class=None, notes=[],
    )
    base.update(overrides)
    return RunStatus(**base)


# --- collect ------------------------------------------------------------

def test_collect_no_run_raises(tmp_path):
    state_dir = tmp_path / "state"
    cfg = load_config(_pipeline(tmp_path, state_dir))
    with pytest.raises(NoRunError):
        collect(cfg)


def test_collect_success_run(tmp_path):
    state_dir = tmp_path / "state"
    _write_db(state_dir, [
        ("t1", "c1", _checkpoint(ts="2026-11-08T00:00:00Z", outcome="success",
                                 step_verdicts={"dev": "pass",
                                                "review": "APPROVED"})),
    ])
    cfg = load_config(_pipeline(tmp_path, state_dir))
    rs = collect(cfg)
    assert rs.status == "DONE"
    assert rs.current == "success"
    assert rs.status_text == "success"
    assert rs.thread_id == "t1"


def test_collect_running_run_picks_latest_and_next_node(tmp_path):
    state_dir = tmp_path / "state"
    _write_db(state_dir, [
        ("t1", "c1", _checkpoint(ts="2026-11-08T00:00:00Z",
                                 step_verdicts={"dev": "pass"})),
        ("t1", "c2", _checkpoint(ts="2026-11-08T00:01:00Z",
                                 step_verdicts={"dev": "pass"})),
    ], writes=[("t1", "c2", "~__pregel_pull, review")])
    cfg = load_config(_pipeline(tmp_path, state_dir))
    rs = collect(cfg)
    assert rs.status == "RUNNING"
    assert rs.current == "review"
    assert rs.last_checkpoint == "2026-11-08T00:01:00Z"


# --- format_output ------------------------------------------------------

def test_format_output_success_marks_done_and_highlights_current():
    rs = _rs(status="DONE", status_text="success", current="success",
             verdicts={"dev": "pass", "review": "APPROVED"})
    out = format_output(rs)
    assert "===MERMAID===" in out and "===SUMMARY===" in out
    assert "classDef current fill:#ffd43b" in out
    assert "classDef done fill:#2f9e44" in out
    assert "class success current;" in out
    assert "class dev,review done;" in out
    assert "- Steps: dev=pass, review=APPROVED" in out


def test_format_output_running_shows_verdicts_and_pending():
    rs = _rs(status="RUNNING", current="review", verdicts={"dev": "pass"})
    out = format_output(rs)
    assert "class review current;" in out
    assert "class dev done;" in out
    assert "- Steps: dev=pass, review=in progress" in out


# --- collect_latest (workspace discovery) -------------------------------

def test_collect_latest_finds_latest_across_state_dirs(tmp_path):
    runs_root = tmp_path / "runs"
    _write_db(runs_root / "aaaa", [
        ("t1", "c1", _checkpoint(ts="2026-11-08T00:00:00Z", outcome="success",
                                 step_verdicts={"dev": "pass",
                                                "review": "APPROVED"})),
    ])
    _write_db(runs_root / "bbbb", [
        ("t2", "c1", _checkpoint(ts="2026-11-08T01:00:00Z",
                                 subject="smoke-viz-provider",
                                 step_verdicts={"dev": "pass",
                                                "review": "APPROVED"})),
    ])
    rs = collect_latest(runs_root)
    assert rs.thread_id == "t2"
    assert rs.last_checkpoint == "2026-11-08T01:00:00Z"
    assert rs.subject == "smoke-viz-provider"


def test_collect_latest_no_runs_raises(tmp_path):
    with pytest.raises(NoRunError):
        collect_latest(tmp_path / "nonexistent")


def test_collect_latest_skips_dirs_without_checkpoints(tmp_path):
    runs_root = tmp_path / "runs"
    (runs_root / "empty").mkdir(parents=True)
    _write_db(runs_root / "real", [
        ("t1", "c1", _checkpoint(ts="2026-11-08T00:00:00Z", outcome="success",
                                 step_verdicts={"dev": "pass"})),
    ])
    rs = collect_latest(runs_root)
    assert rs.thread_id == "t1"


def test_collect_latest_builds_linear_mermaid(tmp_path):
    runs_root = tmp_path / "runs"
    _write_db(runs_root / "aaaa", [
        ("t1", "c1", _checkpoint(ts="2026-11-08T00:00:00Z", outcome="success",
                                 step_verdicts={"dev": "pass",
                                                "review": "APPROVED"})),
    ])
    rs = collect_latest(runs_root)
    assert rs.status == "DONE"
    assert "dev --> review" in rs.mermaid
    assert "review --> success" in rs.mermaid
    out = format_output(rs)
    assert "class success current;" in out
    assert "class dev,review done;" in out


# --- command_text / install --------------------------------------------

def test_command_text_matches_repo_command_file():
    repo_file = REPO_ROOT / ".opencode" / "commands" / "run-status.md"
    assert command_text() == repo_file.read_text(encoding="utf-8")


def test_install_command_writes_thin_wrapper(tmp_path):
    target = install_command(tmp_path / "commands")
    assert target.name == "run-status.md"
    assert target.read_text(encoding="utf-8") == command_text()


# --- CLI dispatch -------------------------------------------------------

def test_cli_run_status_prints_no_run(tmp_path, monkeypatch, capsys):
    state_dir = tmp_path / "state"
    pipeline = _pipeline(tmp_path, state_dir)
    code = main(["run-status", str(pipeline)])
    assert code == 0
    assert "NO_RUN" in capsys.readouterr().out


def test_cli_run_status_install_uses_xdg_config_home(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    code = main(["run-status", "--install"])
    out = capsys.readouterr().out
    assert code == 0
    installed = (tmp_path / "config" / "opencode" / "commands" / "run-status.md")
    assert installed.exists()
    assert installed.read_text(encoding="utf-8") == command_text()
    assert str(installed) in out


def test_cli_run_status_no_arg_discovers_latest(tmp_path, monkeypatch, capsys):
    runs_root = tmp_path / "runs"
    _write_db(runs_root / "aaaa", [
        ("t1", "c1", _checkpoint(ts="2026-11-08T00:00:00Z", outcome="success",
                                 step_verdicts={"dev": "pass",
                                                "review": "APPROVED"})),
    ])
    monkeypatch.setenv("AGENTIC_DEV_LOOP_RUNS_ROOT", str(runs_root))
    code = main(["run-status"])
    out = capsys.readouterr().out
    assert code == 0
    assert "===SUMMARY===" in out
    assert "- Status: success" in out


def test_default_commands_dir_respects_env(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    from agentic_dev_loop.engine.run_status import default_commands_dir
    assert default_commands_dir() == (Path.home() / ".config" / "opencode"
                                      / "commands")
