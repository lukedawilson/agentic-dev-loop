"""Unit 12 - run-history reporting CLI.

The fixture is a sanitized slice of checkpoint databases produced by the real
engine (LangGraph ``SqliteSaver`` + engine graph nodes) with only the SDK
network boundary stubbed; see ``tests/fixtures/unit12/PROVENANCE.md`` and
``regen_fixture.py``. Synthetic rows appear only for the two documented edge
cases (corrupt blob, legacy prose).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentic_dev_loop.engine.cli import main
from agentic_dev_loop.reporting import (
    apply_filters,
    build_run_records,
    discover,
    pure_metrics,
    read_audit,
    read_dir,
    render_markdown,
)
from agentic_dev_loop.reporting.sources import (
    AuditRecord,
    RunRecord,
    _join_audit,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "unit12"
FIXTURE_RUNS = FIXTURES / "runs"
FIXTURE_STATE = FIXTURES / "state"
ALL_RUN_IDS = ["111111111111", "222222222222", "333333333333",
               "444444444444", "abcdef123456"]


def _records(run_ids=None, audit=None):
    ids = ALL_RUN_IDS if run_ids is None else run_ids
    dirs = [read_dir(FIXTURE_RUNS / name) for name in ids]
    return build_run_records(dirs, audit or [])


def _by_status(records, status):
    matches = [record for record in records if record.status == status]
    assert matches, f"no {status} record in {[r.status for r in records]}"
    return matches[0]


def test_fixture_is_a_real_engine_dump():
    provenance = (FIXTURES / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "real engine" in provenance
    assert "sanitized" in provenance
    assert (FIXTURES / "regen_fixture.py").is_file()
    for name in ALL_RUN_IDS:
        assert (FIXTURE_RUNS / name / "loop-checkpoints.sqlite").is_file()


# --- discovery / reading -------------------------------------------------

def test_discover_skips_dirs_without_sqlite(tmp_path):
    root = tmp_path / "runs"
    shutil.copytree(FIXTURE_RUNS, root)
    (root / "bbbbbbbbbbbb").mkdir()
    (root / "junk.txt").write_text("x", encoding="utf-8")
    assert [p.name for p in discover(root)] == ALL_RUN_IDS


def test_missing_runs_root_is_empty_not_error(tmp_path):
    assert discover(tmp_path / "nope") == []


def test_read_dir_is_read_only_and_never_needs_wal():
    run_dir = FIXTURE_RUNS / "abcdef123456"
    before = sorted(path.name for path in run_dir.iterdir())
    data = read_dir(run_dir)
    after = sorted(path.name for path in run_dir.iterdir())
    assert before == after
    assert not (run_dir / "loop-checkpoints.sqlite-wal").exists()
    assert not (run_dir / "loop-checkpoints.sqlite-shm").exists()
    assert len(data.threads) == 2


def test_unreadable_blob_marks_thread_only():
    records = _records(["abcdef123456", "222222222222"])
    unreadable = _by_status(records, "unreadable")
    assert unreadable.thread_id == "bad"
    assert unreadable.error
    assert any(record.status == "success" for record in records)


def test_audit_reader_keeps_only_command_rows_and_reads_rotation():
    rows = read_audit(FIXTURE_STATE)
    assert [row.step for row in rows] == ["test", "dev", "dev", "test"]
    assert all(row.exit_code is not None for row in rows)
    assert any(row.source == "sandbox-audit.jsonl.1" for row in rows)
    assert not any(row.step == "" for row in rows)


def test_missing_audit_files_yield_empty_pool(tmp_path):
    assert read_audit(tmp_path / "none") == []


# --- normalization -------------------------------------------------------

def test_attempts_segmented_by_verdict_reset():
    record = _by_status(_records(["abcdef123456"]), "success")
    assert [attempt.number for attempt in record.attempts] == [1, 2]
    assert [(v.step, v.verdict) for v in record.attempts[0].verdicts] == [
        ("brief", "pass"), ("dev", "pass"), ("test", "pass"),
        ("review", "fail")]
    assert [v.step for v in record.attempts[1].verdicts] == [
        "dev", "test", "review", "qa"]


def test_status_classification_success_failed_running_interrupted():
    records = _records(["abcdef123456", "333333333333", "111111111111"])
    assert _by_status(records, "success").run_id == "abcdef123456"
    assert _by_status(records, "failed").run_id == "333333333333"
    running = _by_status(records, "running")
    assert running.run_id == "111111111111"
    assert running.pending == ["qa"]
    interrupted = _by_status(records, "interrupted")
    assert interrupted.run_id == "abcdef123456"
    assert interrupted.pending == ["dev"]


def test_crashed_runs_carry_last_error_marker():
    records = _records(["111111111111", "333333333333"])
    assert all(record.prose.has_last_error for record in records)


def test_prose_facts_only_on_latest_thread():
    records = _records(["abcdef123456"])
    success = _by_status(records, "success")
    interrupted = _by_status(records, "interrupted")
    assert success.prose.review_findings_count == 2
    assert success.prose.review_verdict == "APPROVED"
    assert success.prose.qa_verdict == "PASS"
    assert interrupted.prose.review_findings_count is None


def test_audit_join_excludes_ambiguous_overlap():
    """Pure join rule: a row inside two thread ranges belongs to neither."""
    common = dict(run_id="x", dir=Path("."), subject="", attempts=[],
                  final={})
    first = RunRecord(thread_id="a", ts_start="2026-11-08T10:00:00+00:00",
                      ts_end="2026-11-08T10:10:00+00:00", status="success",
                      **common)
    second = RunRecord(thread_id="b", ts_start="2026-11-08T10:05:00+00:00",
                       ts_end="2026-11-08T10:15:00+00:00", status="success",
                       **common)
    _join_audit([first, second], [
        AuditRecord(timestamp="2026-11-08T10:07:00+00:00", step="dev",
                    exit_code=0)])
    assert first.audit_rows == [] and second.audit_rows == []
    assert first.audit_ambiguous == 1 and second.audit_ambiguous == 1


def test_audit_join_attributes_unambiguous_rows():
    records = _records(["abcdef123456"], audit=read_audit(FIXTURE_STATE))
    success = _by_status(records, "success")
    assert [row.step for row in success.audit_rows] == [
        "test", "dev", "dev", "test"]


# --- metrics -------------------------------------------------------------

def test_red_then_green_and_zero_test_verdicts():
    metrics = pure_metrics([_by_status(_records(["abcdef123456"]), "success")])
    tdd = metrics["tdd"]
    assert tdd["attempts_total"].value == 2
    assert tdd["attempts_red_then_green"].value == 0
    assert tdd["attempts_zero_test_verdicts"].value == 0
    assert "n=2" in tdd["pct_attempts_red_then_green"].basis


def test_dev_lane_unavailable_without_audit():
    record = _by_status(_records(["abcdef123456"]), "success")
    metric = pure_metrics([record])["tdd"]["dev_lane_red_then_green"]
    assert metric.availability != "available"
    assert "audit" in metric.availability.lower()


def test_dev_lane_counts_real_audit_test_commands():
    records = _records(["abcdef123456"], audit=read_audit(FIXTURE_STATE))
    record = _by_status(records, "success")
    metric = pure_metrics([record])["tdd"]["dev_lane_red_then_green"]
    assert metric.availability == "available"
    assert metric.value == 1


def test_qa_and_review_metrics():
    metrics = pure_metrics([_by_status(_records(["abcdef123456"]), "success")])
    assert metrics["review"]["defect_yield"].value == 2
    assert metrics["qa"]["qa_pass_rate"].value == 100.0
    assert metrics["loop_health"]["thread_count"].value == 1
    assert metrics["loop_health"]["success_rate"].value == 100.0


def test_filters_since_until_and_status():
    records = _records(["abcdef123456", "111111111111"])
    assert len(apply_filters(records)) == 3
    assert [record.run_id for record in
            apply_filters(records, statuses=["running"])] == ["111111111111"]
    ordered = sorted(records, key=lambda record: record.ts_start)
    assert {record.ts_start for record in
            apply_filters(records, since=ordered[1].ts_start)} == {
        ordered[1].ts_start, ordered[2].ts_start}
    assert {record.ts_start for record in
            apply_filters(records, until=ordered[1].ts_start)} == {
        ordered[0].ts_start}
    with pytest.raises(ValueError):
        apply_filters(records, since="not-a-date")


def test_every_metric_carries_value_provenance_basis_availability():
    metrics = pure_metrics(_records())
    for section, entries in metrics.items():
        groups = entries.values() if section == "threads" else [entries]
        for group in groups:
            for name, metric in group.items():
                assert metric.provenance in {
                    "mechanical", "parsed", "claimed"}, name
                assert metric.basis, name
                assert metric.availability, name


# --- render --------------------------------------------------------------

def test_render_ascii_fixed_sections_and_provenance():
    records = _records()
    text = render_markdown(records, pure_metrics(records))
    assert "\u2014" not in text
    assert text.isascii()
    for heading in ("## Loop health", "## TDD", "## Review", "## QA",
                    "## Threads"):
        assert heading in text
    assert "[mechanical]" in text
    assert "unavailable (" in text


def test_deep_dive_renders_attempt_tables():
    record = _by_status(_records(["abcdef123456"]), "success")
    text = render_markdown([record], pure_metrics([record]),
                           deep_dive=True, notice=None)
    assert "Attempt 1" in text and "Attempt 2" in text
    assert "abcdef123456" in text


# --- CLI -----------------------------------------------------------------

def test_cli_aggregate_and_deep_dive(capsys):
    assert main(["report", "--runs-root", str(FIXTURE_RUNS),
                 "--audit-dir", str(FIXTURE_STATE)]) == 0
    out = capsys.readouterr().out
    assert "# Run history report" in out
    assert "abcdef123456" in out

    assert main(["report", "abcdef123456", "--runs-root", str(FIXTURE_RUNS),
                 "--audit-dir", str(FIXTURE_STATE)]) == 0
    assert "Attempt 2" in capsys.readouterr().out


def test_cli_unknown_run_id_and_bad_datetime_are_clean_nonzero(tmp_path,
                                                               capsys):
    root = tmp_path / "runs"
    root.mkdir()
    assert main(["report", "ffffffffffff", "--runs-root", str(root)]) == 1
    assert "unknown run id" in capsys.readouterr().err
    assert main(["report", "--runs-root", str(root),
                 "--since", "nope"]) == 1
    assert "since" in capsys.readouterr().err.lower()


def test_cli_missing_root_still_succeeds(tmp_path, capsys):
    code = main(["report", "--runs-root", str(tmp_path / "absent")])
    assert code == 0
    assert "unavailable" in capsys.readouterr().out.lower()


def test_loop_report_wrapper_exists():
    path = (Path(__file__).resolve().parents[1]
            / ".opencode" / "commands" / "loop-report.md")
    text = path.read_text(encoding="utf-8")
    assert "agentic-dev-loop report" in text
    assert "$ARGUMENTS" in text


def test_legacy_prose_verdict_is_parsed_and_normalized():
    record = _by_status(_records(["444444444444"]), "success")
    verdicts = {verdict.step: verdict for verdict in record.attempts[0].verdicts}
    assert verdicts["review"].provenance == "parsed"
    assert verdicts["review"].verdict == "pass"
    assert verdicts["qa"].provenance == "parsed"
    assert verdicts["qa"].verdict == "pass"


def test_crash_rate_from_last_error(tmp_path):
    root = tmp_path / "runs"
    shutil.copytree(FIXTURE_RUNS / "333333333333", root / "333333333333")
    (root / "333333333333" / "last-error.md").write_text(
        "boom\n", encoding="utf-8")
    records = build_run_records([read_dir(root / "333333333333")], [])
    metrics = pure_metrics(records)
    assert metrics["loop_health"]["last_error_threads"].value == 1
    assert metrics["loop_health"]["crash_rate"].value == 100.0
