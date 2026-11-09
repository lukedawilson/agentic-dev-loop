"""Read-only view model for the historical runs pages (unit 13).

Everything here is derived from ``reporting.sources`` and
``reporting.metrics``; no metric is computed in the viz layer. Run records
and metrics are serialized explicitly - ``Metric.as_dict()`` for metrics and
a purpose-built summary for records - so the JSON shape is stable and free
of raw dataclass dumps.
"""

from __future__ import annotations

from pathlib import Path

from agentic_dev_loop.reporting import (
    apply_filters,
    build_run_records,
    discover,
    find_run_dir,
    parse_ts,
    pure_metrics,
    read_audit,
    read_dir,
)
from agentic_dev_loop.reporting.metrics import Metric
from agentic_dev_loop.reporting.sources import (
    STATUS_VALUES,
    DirData,
    ProseFacts,
    RunRecord,
)

ARTIFACT_FILES = {
    "brief": "attempt-brief.md",
    "summary": "implementation-summary.md",
    "findings": "review-findings.md",
    "qa": "qa-report.md",
}


def parse_filters(
    since: str | None,
    until: str | None,
    status: str | list[str] | None,
) -> tuple[dict | None, str | None]:
    """Validate query filters (BR-5); return ``(filters, error_message)``.

    ``since`` is inclusive and ``until`` exclusive, mirroring unit-12
    ``apply_filters``. Empty values are treated as absent.
    """
    since = since or None
    until = until or None
    if since and parse_ts(since) is None:
        return None, f"invalid since datetime: {since}"
    if until and parse_ts(until) is None:
        return None, f"invalid until datetime: {until}"

    statuses: list[str] = []
    if status is not None:
        raw = [status] if isinstance(status, str) else list(status)
        for item in raw:
            for part in str(item).split(","):
                part = part.strip()
                if part:
                    statuses.append(part)
    unknown = sorted(set(statuses) - set(STATUS_VALUES))
    if unknown:
        return None, f"invalid status value: {', '.join(unknown)}"
    return {"since": since, "until": until, "statuses": statuses}, None


def _metric_map(metrics: dict) -> dict:
    return {
        name: metric.as_dict() if isinstance(metric, Metric) else metric
        for name, metric in metrics.items()
    }


def _serialize_metrics(metrics: dict) -> dict:
    return {
        "loop_health": _metric_map(metrics.get("loop_health", {})),
        "tdd": _metric_map(metrics.get("tdd", {})),
        "review": _metric_map(metrics.get("review", {})),
        "qa": _metric_map(metrics.get("qa", {})),
        "threads": {
            thread_id: _metric_map(thread_metrics)
            for thread_id, thread_metrics
            in metrics.get("threads", {}).items()
        },
    }


def _verdict_summary(record: RunRecord) -> dict:
    summary: dict[str, str] = {}
    for attempt in record.attempts:
        for verdict in attempt.verdicts:
            summary[verdict.step] = verdict.verdict
    return summary


def record_summary(record: RunRecord) -> dict:
    return {
        "thread_id": record.thread_id,
        "run_dir": str(record.dir),
        "subject": record.subject,
        "ts_start": record.ts_start,
        "ts_end": record.ts_end,
        "status": record.status,
        "attempt_count": record.attempt_count,
        "verdict_summary": _verdict_summary(record),
    }


def _sort_key(record: RunRecord) -> tuple[str, str]:
    return (record.ts_start or "", record.thread_id)


def _read_record_dirs(runs_root: str | None = None) -> tuple[list[DirData], list[str]]:
    dirs: list[DirData] = []
    notices: list[str] = []
    for path in discover(runs_root):
        data = read_dir(path)
        dirs.append(data)
        if data.notice:
            notices.append(f"{data.run_id}: {data.notice}")
    return dirs, notices


def list_payload(
    since: str | None = None,
    until: str | None = None,
    statuses: list[str] | None = None,
    runs_root: str | None = None,
    audit_dir: str | None = None,
) -> dict:
    """Recompute the runs list for the current filters (BR-3, BR-7)."""
    dirs, notices = _read_record_dirs(runs_root)
    audit = read_audit(audit_dir)
    records = build_run_records(dirs, audit)
    filtered = apply_filters(records, since, until, statuses or [])
    filtered.sort(key=_sort_key, reverse=True)
    metrics = pure_metrics(filtered, audit)
    return {
        "filters": {
            "since": since,
            "until": until,
            "statuses": list(statuses or []),
        },
        "records": [record_summary(record) for record in filtered],
        "metrics": _serialize_metrics(metrics),
        "notice": "; ".join(notices) if notices else None,
    }


def artifact_facts(prose: ProseFacts) -> dict:
    artifacts: dict = {}
    for key, filename in ARTIFACT_FILES.items():
        text = prose.text(filename)
        artifacts[key] = {
            "file": filename,
            "present": text is not None,
            "text": text,
        }
    artifacts["facts"] = {
        "review_findings_count": prose.review_findings_count,
        "review_verdict": prose.review_verdict,
        "qa_verdict": prose.qa_verdict,
        "claims_tests": prose.claims_tests,
        "parse_errors": list(prose.parse_errors),
    }
    return artifacts


def _attempts_payload(record: RunRecord) -> list[dict]:
    return [
        {
            "number": attempt.number,
            "verdicts": [
                {
                    "step": verdict.step,
                    "verdict": verdict.verdict,
                    "provenance": verdict.provenance,
                }
                for verdict in attempt.verdicts
            ],
        }
        for attempt in record.attempts
    ]


def detail_payload(run_id: str,
                   runs_root: str | None = None,
                   audit_dir: str | None = None) -> dict | None:
    """Per-run detail, or ``None`` when the run dir is unknown (BR-6)."""
    path: Path | None = find_run_dir(run_id, runs_root)
    if path is None:
        return None
    data = read_dir(path)
    audit = read_audit(audit_dir)
    records = build_run_records(data, audit)
    metrics = pure_metrics(records, audit)
    threads = []
    for record in records:
        thread_metrics = metrics.get("threads", {}).get(record.thread_id, {})
        threads.append({
            "thread_id": record.thread_id,
            "subject": record.subject,
            "ts_start": record.ts_start,
            "ts_end": record.ts_end,
            "status": record.status,
            "attempts": _attempts_payload(record),
            "metrics": _metric_map(thread_metrics),
        })
    return {
        "run_id": run_id,
        "threads": threads,
        "artifacts": artifact_facts(data.prose),
        "notice": data.notice,
    }
