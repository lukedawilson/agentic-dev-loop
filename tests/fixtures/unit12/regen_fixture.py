"""Regenerate the unit-12 reporting fixture from real engine runs.

The fixture is a sanitized slice of checkpoint databases written by the real
engine (LangGraph ``SqliteSaver`` + the engine graph nodes) plus audit rows
written in the real ``append_audit_record`` shape. Run it with the repo venv:

    .venv-sandbox/bin/python tests/fixtures/unit12/regen_fixture.py

It is not part of the test suite: the committed artifact is what tests read.
Scenarios replayed (all engine-produced, not hand-built rows):

* ``abcdef123456`` - a crashed run followed by a successful run with a
  review-fail retry (two threads in one dir, prose on the latest thread).
* ``111111111111`` - a crashed run as the latest thread (in-flight/running).
* ``333333333333`` - an agent hard failure (``failed=True``).

Only the corrupt-blob and legacy-prose fixtures remain synthetic; they encode
shapes the current engine no longer produces (V9 legacy prose) or a deliberate
decode error, and are written by ``write_edge_fixtures`` below.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import msgpack

FIXTURE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE_ROOT.parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from agentic_dev_loop.core.verdict import Verdict
from agentic_dev_loop.engine import graph as graph_mod
from agentic_dev_loop.engine.isolation import _request_digest, append_audit_record
from agentic_dev_loop.engine.stages import AgentResult, Runtime, verdict_map
from conftest import FakeSandbox

BACKEND = "doc" + "ker"

PIPELINE = """\
name: unit12-fixture
llm:
  model: openai/test
  base_url: https://example.invalid
  api_key_env: TEST_LLM_KEY
state_dir: {state_dir}
max_attempts: 3
agents_dir: sdk_agents
deps_policy: open
steps:
  - name: brief
    agent: brief
    produces: attempt-brief.md
    on_pass: dev
  - name: dev
    agent: dev
    produces: implementation-summary.md
    on_pass: test
  - name: test
    command: python -c "print('test output')"
    on_pass: review
    on_fail: {{ goto: dev, retry: true }}
  - name: review
    agent: review
    artifact: review-findings.md
    verdicts: {{ pass: APPROVED, fail: NEEDS CHANGES }}
    on_pass: qa
    on_fail: {{ goto: dev, retry: true }}
  - name: qa
    agent: qa
    artifact: qa-report.md
    verdicts: {{ pass: PASS, fail: FAIL }}
    on_pass: commit
    on_fail: {{ goto: dev, retry: true }}
  - name: commit
    action: commit
    params: {{ message: "feat: construct {{subject}}" }}
    on_pass: success
"""


class ScriptedAgents:
    """The SDK network boundary is the only stub; the graph, files, git and
    checkpoint store are real (the repo's FakeAgents policy)."""

    def __init__(self, *, crash_on: str | None = None,
                 fail_on: str | None = None,
                 review_fail_first: bool = False):
        self.calls: dict[str, int] = {}
        self.crash_on = crash_on
        self.fail_on = fail_on
        self.review_fail_first = review_fail_first

    def invoke(self, runtime: Runtime, agent: str, _message: str,
               _max_iterations: int = 500, step=None) -> AgentResult:
        count = self.calls.get(agent, 0) + 1
        self.calls[agent] = count
        if agent == self.crash_on:
            (runtime.state_dir / "last-error.md").write_text(
                "scripted crash\n", encoding="utf-8")
            raise KeyboardInterrupt("scripted crash")
        if agent == self.fail_on:
            (runtime.state_dir / "last-error.md").write_text(
                "scripted agent failure\n", encoding="utf-8")
            return AgentResult(ok=False)
        write = None
        if agent == "brief":
            write = ("attempt-brief.md", "## Repo rules digest\n\nbrief body\n")
        elif agent == "dev":
            write = ("implementation-summary.md", "Tests added and green.\n")
        elif agent == "review":
            if count == 1 and self.review_fail_first:
                write = ("review-findings.md",
                         "VERDICT: NEEDS CHANGES\n- missing guard\n")
            else:
                write = ("review-findings.md",
                         "VERDICT: APPROVED\n- missing guard\n- unused import\n")
        elif agent == "qa":
            write = ("qa-report.md", "VERDICT: PASS\n")
        response = None
        if write is not None:
            (runtime.state_dir / write[0]).write_text(write[1], encoding="utf-8")
            response = write[1]
        capture_error = (
            "empty_response"
            if response is None and step is not None
            and (step.artifact is not None or step.produces)
            else None
        )
        verdict = None
        verdict_error = None
        if step is not None and step.verdicts is not None and response is not None:
            parsed = Verdict.from_text(response, verdict_map(step))
            if parsed.is_ok():
                verdict = parsed.unwrap().value
            else:
                verdict_error = "no verdict"
        return AgentResult(ok=True, response=response,
                           capture_error=capture_error, verdict=verdict,
                           verdict_error=verdict_error)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _make_repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir()
    (repo / "plan.md").write_text("THE PLAN\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture")
    return repo


def _run(repo: Path, state_dir: Path, script: ScriptedAgents) -> None:
    (repo / "pipeline.yaml").write_text(
        PIPELINE.format(state_dir=state_dir), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init", "--allow-empty")
    previous = Path.cwd()
    os.chdir(repo)
    sandbox = FakeSandbox()
    old_startup = graph_mod.startup_sandbox
    old_run_agent = Runtime.run_agent
    graph_mod.startup_sandbox = lambda *a, **k: sandbox

    def fake(runtime, agent, message, max_iterations=500, step=None):
        return script.invoke(runtime, agent, message, max_iterations, step)

    Runtime.run_agent = fake
    try:
        args = SimpleNamespace(plan=Path("plan.md"), resume=None,
                               max_attempts=None, no_viz=True, viz_port=None)
        graph_mod.run_pipeline("pipeline.yaml", args)
    finally:
        graph_mod.startup_sandbox = old_startup
        Runtime.run_agent = old_run_agent
        os.chdir(previous)


def _sanitize(value, root: str):
    if isinstance(value, str):
        return value.replace(root, "/fixture")
    if isinstance(value, dict):
        return {key: _sanitize(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item, root) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize(item, root) for item in value)
    return value


def _sanitized_blob(blob, root: str, *, json_encoded: bool):
    """Rewrite one stored blob; leave anything undecodable untouched.

    LangGraph stores checkpoints as msgpack, metadata as JSON text, and
    writes as msgpack (a ``null``-typed write stores an empty blob).
    """
    if blob is None or not blob:
        return blob
    if json_encoded:
        try:
            return json.dumps(_sanitize(json.loads(blob), root),
                              sort_keys=True).encode("utf-8")
        except (ValueError, TypeError):
            return blob
    try:
        decoded = msgpack.unpackb(blob, raw=False)
    except Exception:
        return blob
    return msgpack.packb(_sanitize(decoded, root))


def sanitize_db(db_path: Path, root: str) -> None:
    """Rewrite every stored blob with machine paths replaced, then VACUUM."""
    conn = sqlite3.connect(str(db_path))
    try:
        for table, column, json_encoded in (
                ("checkpoints", "checkpoint", False),
                ("checkpoints", "metadata", True),
                ("writes", "value", False)):
            for rowid, blob in conn.execute(
                    f"SELECT rowid, {column} FROM {table}").fetchall():
                conn.execute(
                    f"UPDATE {table} SET {column}=? WHERE rowid=?",
                    (_sanitized_blob(blob, root, json_encoded=json_encoded),
                     rowid))
        conn.commit()
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()


def _copy_run(source: Path, target: Path, root: str) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in ("loop-checkpoints.sqlite", "attempt-brief.md",
                 "implementation-summary.md", "review-findings.md",
                 "qa-report.md", "last-error.md"):
        path = source / name
        if path.is_file():
            shutil.copy2(path, target / name)
    sanitize_db(target / "loop-checkpoints.sqlite", root)


def _latest_thread_range(db_path: Path) -> tuple[datetime, datetime]:
    """Time range of the directory's latest thread (the prose owner)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    try:
        by_thread: dict[str, list[str]] = {}
        for thread_id, blob in conn.execute(
                "SELECT thread_id, checkpoint FROM checkpoints"):
            by_thread.setdefault(thread_id, []).append(
                msgpack.unpackb(blob, raw=False)["ts"])
    finally:
        conn.close()
    latest = max(by_thread.values(), key=max)
    parsed = [datetime.fromisoformat(s.replace("Z", "+00:00"))
              for s in latest]
    return min(parsed), max(parsed)


def _audit_record(stamp: datetime, *, step: str, exit_code: int,
                  argv: list[str]) -> dict:
    return {
        "timestamp": stamp.isoformat(),
        "pipeline": "unit12-fixture",
        "step": step,
        "request_digest": _request_digest(argv, "/fixture", {}),
        "backend": BACKEND,
        "container_id": "fixture-container",
        "exit_code": exit_code,
        "timed_out": False,
        "duration_seconds": 1.0,
    }


def write_audit(state: Path, db_path: Path) -> None:
    """Audit rows in the real writer shape, overlapping the success thread."""
    if state.exists():
        shutil.rmtree(state)
    state.mkdir(parents=True)
    start, end = _latest_thread_range(db_path)
    span = end - start
    at = lambda fraction: start + span * fraction
    dev_cmd = ["python", "-m", "pytest", "tests/"]
    rotated = _audit_record(at(0.2), step="test", exit_code=0, argv=dev_cmd)
    (state / "sandbox-audit.jsonl.1").write_text(
        json.dumps(rotated, sort_keys=True) + "\n", encoding="utf-8")
    for row in (
        {"timestamp": at(0.1).isoformat(), "event": "egress_enforcement",
         "allowed": False, "hostname": "example.invalid"},
        _audit_record(at(0.35), step="dev", exit_code=1, argv=dev_cmd),
        _audit_record(at(0.45), step="dev", exit_code=0, argv=dev_cmd),
        _audit_record(at(0.6), step="test", exit_code=0, argv=dev_cmd),
    ):
        append_audit_record(state, row)


def _edge_db(path: Path) -> sqlite3.Connection:
    path.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path / "loop-checkpoints.sqlite"))
    conn.execute(
        "CREATE TABLE checkpoints (thread_id TEXT, checkpoint_ns TEXT, "
        "checkpoint_id TEXT, parent_checkpoint_id TEXT, type TEXT, "
        "checkpoint BLOB, metadata BLOB)")
    conn.execute(
        "CREATE TABLE writes (thread_id TEXT, checkpoint_ns TEXT, "
        "checkpoint_id TEXT, task_id TEXT, task_path TEXT, idx INTEGER, "
        "channel TEXT, type TEXT, value BLOB)")
    return conn


def write_edge_fixtures() -> None:
    """Synthetic edge cases (allowed by the fixture policy, labelled here).

    - ``222222222222``: one corrupt checkpoint blob (decode-failure path).
    - ``444444444444``: pre-unit-11 legacy prose verdicts, a channel shape the
      current engine no longer writes (V9).
    """
    conn = _edge_db(FIXTURE_ROOT / "runs" / "222222222222")
    try:
        conn.execute(
            "INSERT INTO checkpoints VALUES "
            "('bad', '', 'cp-bad', NULL, 'msgpack', ?, ?)",
            (b"not-msgpack", msgpack.packb({})))
        conn.commit()
    finally:
        conn.close()

    conn = _edge_db(FIXTURE_ROOT / "runs" / "444444444444")
    try:
        ts = "2026-11-01T09:00:00+00:00"
        cv = {"subject": "legacy run", "attempt": 1, "max_attempts": 1,
              "outcome": "success", "failed": False, "failed_step": None,
              "retry_target": None, "step_verdicts": {},
              "artifacts": {"review": "VERDICT: APPROVED\n",
                            "qa": "VERDICT: PASS\n"},
              "brief_retries": {}}
        blob = msgpack.packb({"v": 4, "ts": ts, "id": "cp-legacy",
                              "channel_values": cv, "channel_versions": {},
                              "versions_seen": {}, "updated_channels": []})
        conn.execute(
            "INSERT INTO checkpoints VALUES "
            "('legacy', '', 'cp-legacy', NULL, 'msgpack', ?, ?)",
            (blob, msgpack.packb({"source": "loop"})))
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    os.environ["TEST_LLM_KEY"] = "sk-test"
    work = Path(tempfile.mkdtemp(prefix="unit12-fixture-"))
    runs = FIXTURE_ROOT / "runs"
    if runs.exists():
        shutil.rmtree(runs)
    runs.mkdir(parents=True)
    try:
        # Two threads in one dir: crashed first, successful retry last.
        repo = _make_repo(work, "repo-a")
        state_a = work / "runs" / "abcdef123456"
        try:
            _run(repo, state_a, ScriptedAgents(crash_on="dev"))
        except KeyboardInterrupt:
            pass
        _run(repo, state_a, ScriptedAgents(review_fail_first=True))
        _copy_run(state_a, runs / "abcdef123456", str(work))

        repo_b = _make_repo(work, "repo-b")
        state_b = work / "runs" / "111111111111"
        try:
            _run(repo_b, state_b, ScriptedAgents(crash_on="qa"))
        except KeyboardInterrupt:
            pass
        _copy_run(state_b, runs / "111111111111", str(work))

        repo_c = _make_repo(work, "repo-c")
        state_c = work / "runs" / "333333333333"
        _run(repo_c, state_c, ScriptedAgents(fail_on="qa"))
        _copy_run(state_c, runs / "333333333333", str(work))

        write_audit(FIXTURE_ROOT / "state",
                    runs / "abcdef123456" / "loop-checkpoints.sqlite")
        write_edge_fixtures()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"fixture regenerated under {FIXTURE_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
