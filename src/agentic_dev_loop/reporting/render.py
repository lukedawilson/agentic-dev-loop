"""ASCII markdown rendering for reports. Returns text; never prints."""

from __future__ import annotations

from .metrics import Metric, pure_metrics
from .sources import RunRecord


def _plain(value: object) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        text = f"{value:g}"
    elif isinstance(value, dict):
        text = (", ".join(f"{key}={_plain(val)}"
                          for key, val in value.items()) if value else "none")
    elif isinstance(value, (list, tuple)):
        text = ", ".join(_plain(item) for item in value) if value else "none"
    else:
        text = str(value)
    return text


def _metric(metric: Metric) -> str:
    if metric.availability != "available":
        return (f"- {metric.name}: unavailable ({metric.availability}) "
                f"[{metric.provenance}]")
    return (f"- {metric.name}: {_plain(metric.value)} "
            f"[{metric.provenance}] ({metric.basis})")


def _section(title: str, metrics: dict[str, Metric]) -> list[str]:
    lines = [f"## {title}", ""]
    if not metrics:
        lines.append("- unavailable (no metrics)")
    else:
        lines.extend(_metric(metric) for metric in metrics.values())
    lines.append("")
    return lines


def _filter_lines(filters: dict | None) -> list[str]:
    lines = ["## Filter summary", ""]
    if not filters:
        lines.append("- none (all runs)")
    else:
        for key in ("since", "until", "status"):
            value = filters.get(key)
            if value:
                lines.append(f"- {key}: {value}")
        if not any(filters.get(key) for key in ("since", "until", "status")):
            lines.append("- none (all runs)")
    lines.append("")
    return lines


def _thread_row(record: RunRecord) -> str:
    subject = (record.subject or "-").replace("|", "\\|")
    start = record.ts_start or "-"
    return (f"| {record.run_id} | {record.thread_id} | {subject} | {start} "
            f"| {record.status} | {len(record.attempts)} |")


def _aggregate(records: list[RunRecord], metrics: dict,
               filters: dict | None, notice: str | None) -> list[str]:
    lines = ["# Run history report", ""]
    if notice:
        lines.extend([f"> {notice}", ""])
    lines.extend(_filter_lines(filters))
    lines.extend(_section("Loop health", metrics["loop_health"]))
    lines.extend(_section("TDD", metrics["tdd"]))
    lines.extend(_section("Review", metrics["review"]))
    lines.extend(_section("QA", metrics["qa"]))
    lines.extend([
        "## Threads",
        "",
        "| Run | Thread | Subject | Start | Status | Attempts |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    if records:
        lines.extend(_thread_row(record) for record in records)
    else:
        lines.append("| - | - | - | - | - | - |")
    for record in records:
        if record.unreadable and record.error:
            lines.append(f"- unreadable thread {record.thread_id}: {record.error}")
    lines.append("")
    return lines


def _excerpt(text: str | None, limit: int = 1200) -> str:
    if not text:
        return "-"
    clipped = text[:limit]
    if len(text) > limit:
        clipped += "\n... (truncated)"
    return clipped


def _deep(records: list[RunRecord], metrics: dict,
          filters: dict | None, notice: str | None) -> list[str]:
    run_id = records[0].run_id if records else "(unknown)"
    lines = [f"# Run {run_id}", ""]
    if notice:
        lines.extend([f"> {notice}", ""])
    if filters and any(filters.get(key) for key in ("since", "until", "status")):
        lines.extend(_filter_lines(filters))
    for record in records:
        lines.extend([
            f"## Thread {record.thread_id}",
            "",
            f"- subject: {record.subject or '-'}",
            f"- start: {record.ts_start or '-'}",
            f"- end: {record.ts_end or '-'}",
            f"- status: {record.status}",
            f"- error: {record.error}" if record.unreadable else
            f"- attempts: {len(record.attempts)}",
            "",
        ])
        if record.unreadable:
            lines.extend(["no attempt data (thread unreadable)", ""])
        for attempt in record.attempts:
            lines.extend([
                f"### Attempt {attempt.number}",
                "",
                "| Step | Verdict | Provenance |",
                "| --- | --- | --- |",
            ])
            for verdict in attempt.verdicts:
                lines.append(
                    f"| {verdict.step} | {verdict.verdict} | "
                    f"[{verdict.provenance}] |")
            lines.append("")
    thread_metrics = metrics.get("threads", {})
    if thread_metrics:
        lines.extend(["## Thread metrics", ""])
        for thread_id, entries in thread_metrics.items():
            lines.extend([f"### {thread_id}", ""])
            lines.extend(_metric(metric) for metric in entries.values())
            lines.append("")
    lines.extend(_section("Loop health", metrics["loop_health"]))
    lines.extend(_section("TDD", metrics["tdd"]))
    lines.extend(_section("Review", metrics["review"]))
    lines.extend(_section("QA", metrics["qa"]))
    artifacts = []
    for record in records:
        for name in ("review-findings.md", "qa-report.md",
                     "implementation-summary.md", "attempt-brief.md",
                     "last-error.md"):
            text = record.prose.text(name)
            if text is None:
                continue
            artifacts.extend([
                f"### {record.thread_id}: {name}", "", "```text",
                _excerpt(text), "```", ""])
    lines.extend(["## Artifacts", ""])
    lines.extend(artifacts or ["- none", ""])
    return lines


def render_markdown(
    run_records: list[RunRecord],
    metrics: dict | None = None,
    *,
    deep_dive: bool = False,
    filters: dict | None = None,
    notice: str | None = None,
) -> str:
    """Render the aggregate report or a deep dive; ASCII only, no em dash."""
    records = list(run_records)
    computed = metrics if metrics is not None else pure_metrics(records)
    lines = (_deep(records, computed, filters, notice) if deep_dive
             else _aggregate(records, computed, filters, notice))
    text = "\n".join(lines).replace("\u2014", "-")
    return text.encode("ascii", "replace").decode("ascii")
