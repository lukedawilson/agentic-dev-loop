"""Pure metric computation over normalized run records.

No I/O and no printing: unit 13 renders these metrics unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .sources import (
    Attempt,
    AuditRecord,
    RunRecord,
    StepVerdict,
    is_test_command,
    parse_ts,
    STATUS_VALUES,
)


@dataclass(frozen=True)
class Metric:
    name: str
    value: object
    provenance: str
    basis: str
    availability: str = "available"
    numerator: float | int | None = None
    denominator: float | int | None = None
    detail: dict = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.availability == "available"

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "provenance": self.provenance,
            "basis": self.basis,
            "availability": self.availability,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "detail": self.detail,
        }


def _rate(name: str, numerator: int, denominator: int, provenance: str,
          basis: str, empty_reason: str) -> Metric:
    if denominator == 0:
        return Metric(name, None, provenance, f"{basis} n={denominator}",
                      empty_reason, numerator, denominator)
    return Metric(name, round(numerator * 100.0 / denominator, 1),
                  provenance, f"{basis} n={denominator}", "available",
                  numerator, denominator)


def _count(name: str, value: int, provenance: str, basis: str) -> Metric:
    return Metric(name, value, provenance, basis,
                  "available", value, None)


def _red_then_green(verdicts: list[StepVerdict]) -> bool:
    saw_fail = False
    for verdict in verdicts:
        if verdict.step != "test":
            continue
        if verdict.verdict == "fail":
            saw_fail = True
        elif saw_fail and verdict.verdict == "pass":
            return True
    return False


def _test_verdicts(verdicts: list[StepVerdict]) -> list[StepVerdict]:
    return [verdict for verdict in verdicts if verdict.step == "test"]


def _attempts(record: RunRecord) -> list[Attempt]:
    return [] if record.unreadable else record.attempts


def _step_verdicts(record: RunRecord, step: str) -> list[StepVerdict]:
    return [v for attempt in _attempts(record) for v in attempt.verdicts
            if v.step == step]


def _qa_verdicts(record: RunRecord) -> list[StepVerdict]:
    return _step_verdicts(record, "qa")


def _review_pass_then_qa_fail(attempt: Attempt) -> bool:
    review_pass = False
    for verdict in attempt.verdicts:
        if verdict.step == "review" and verdict.verdict == "pass":
            review_pass = True
        elif review_pass and verdict.step == "qa" and verdict.verdict == "fail":
            return True
    return False


def _attempts_reaching_qa(record: RunRecord) -> list[Attempt]:
    return [a for a in _attempts(record)
            if any(v.step == "qa" for v in a.verdicts)]


def _dev_lane(record: RunRecord) -> Metric:
    name = "dev_lane_red_then_green"
    if record.unreadable:
        return Metric(name, None, "mechanical", "no readable thread",
                      "unreadable thread")
    if record.audit_ambiguous:
        return Metric(name, None, "mechanical",
                      "audit rows by timestamp join", "ambiguous overlap")
    if not record.audit_rows:
        return Metric(name, None, "mechanical", "audit rows by timestamp join",
                      "audit rotated")
    # Real audit rows carry a request digest but no command text (V12), so
    # a dev row without text is a candidate rather than discarded.
    rows = [
        row for row in record.audit_rows
        if row.step == "dev"
        and (not row.command or is_test_command(row.command))
    ]
    if not rows:
        return Metric(name, None, "mechanical",
                      "dev-step rows matching test-command pattern",
                      "no dev test commands in audit")
    rows.sort(key=lambda row: row.timestamp)
    saw_red = False
    green = False
    for row in rows:
        if row.exit_code not in (0, None):
            saw_red = True
        elif saw_red and row.exit_code == 0:
            green = True
            break
    return Metric(name, 1 if green else 0, "mechanical",
                  f"n={len(rows)} dev audit rows", "available",
                  1 if green else 0, len(rows))


def _thread_metrics(record: RunRecord) -> dict[str, Metric]:
    attempts = _attempts(record)
    r2g = sum(1 for attempt in attempts if _red_then_green(attempt.verdicts))
    zero_test = sum(1 for attempt in attempts
                    if not _test_verdicts(attempt.verdicts))
    qa = _qa_verdicts(record)
    qa_pass = sum(1 for v in qa if v.verdict == "pass")
    qa_provenance = ("parsed" if any(v.provenance == "parsed" for v in qa)
                     else "mechanical")
    review_pass = sum(
        1 for attempt in attempts
        if any(v.step == "review" and v.verdict == "pass"
               for v in attempt.verdicts))
    findings = record.prose.review_findings_count
    if findings is None:
        defect = Metric("defect_yield", None, "parsed",
                        "review-findings.md findings per review-pass attempt",
                        "no review artifact")
    elif review_pass == 0:
        defect = Metric("defect_yield", None, "parsed",
                        "review-findings.md findings per review-pass attempt",
                        "no review-pass attempts")
    else:
        defect = Metric("defect_yield", round(findings / review_pass, 2),
                        "parsed",
                        f"[parsed] findings / [mechanical] review-pass "
                        f"attempts n={review_pass}", "available",
                        findings, review_pass)
    claims = record.prose.claims_tests
    mechanical_tests = any(_test_verdicts(a.verdicts) for a in attempts)
    if claims is None:
        claim_metric = Metric("claim_agreement", None, "claimed",
                              "implementation-summary.md test mention vs "
                              "mechanical test verdicts",
                              "no implementation summary")
    else:
        agree = int(claims == mechanical_tests)
        claim_metric = Metric(
            "claim_agreement", agree, "claimed",
            f"[claimed] self-report vs [mechanical] test verdicts n=1",
            "available", agree, 1)
    return {
        "status": Metric("status", record.status, "mechanical",
                         f"thread {record.thread_id}"),
        "attempt_count": _count("attempt_count", len(attempts), "mechanical",
                                f"thread {record.thread_id}"),
        "attempts_red_then_green": _count(
            "attempts_red_then_green", r2g, "mechanical",
            f"test verdict sequences n={len(attempts)}"),
        "pct_attempts_red_then_green": _rate(
            "pct_attempts_red_then_green", r2g, len(attempts), "mechanical",
            "test verdict sequences", "no attempts"),
        "attempts_zero_test_verdicts": _count(
            "attempts_zero_test_verdicts", zero_test, "mechanical",
            f"test verdict sequences n={len(attempts)}"),
        "dev_lane_red_then_green": _dev_lane(record),
        "claim_agreement": claim_metric,
        "defect_yield": defect,
        "qa_pass_rate": _rate("qa_pass_rate", qa_pass, len(qa),
                              qa_provenance, "qa verdicts", "no qa verdicts"),
        "environmental_rate": _rate(
            "environmental_rate",
            sum(1 for v in qa if v.verdict == "ENV"), len(qa),
            qa_provenance, "qa verdicts", "no qa verdicts"),
        "unverifiable_rate": _rate(
            "unverifiable_rate",
            sum(1 for v in qa if v.verdict == "UNVERIFIABLE"), len(qa),
            qa_provenance, "qa verdicts", "no qa verdicts"),
        "catch_rate": _rate(
            "catch_rate",
            sum(1 for a in _attempts_reaching_qa(record)
                if _review_pass_then_qa_fail(a)),
            len(_attempts_reaching_qa(record)), "mechanical",
            "attempts reaching qa", "no qa verdicts"),
    }


def _aggregate_tdd(records: list[RunRecord]) -> dict[str, Metric]:
    readable = [r for r in records if not r.unreadable]
    all_attempts = [a for r in readable for a in r.attempts]
    r2g = sum(1 for a in all_attempts if _red_then_green(a.verdicts))
    zero_test = sum(1 for a in all_attempts
                    if not _test_verdicts(a.verdicts))
    threads_r2g = sum(
        1 for r in readable
        if any(_red_then_green(a.verdicts) for a in r.attempts))
    dev_metrics = [_dev_lane(r) for r in readable]
    dev_available = [m for m in dev_metrics if m.available]
    if dev_available:
        dev = Metric(
            "dev_lane_red_then_green",
            sum(int(m.value) for m in dev_available),
            "mechanical",
            f"threads with dev-lane red-then-green n={len(dev_available)}",
            "available",
            sum(int(m.value) for m in dev_available), len(dev_available))
    else:
        if any(r.audit_ambiguous for r in readable):
            reason = "ambiguous overlap"
        elif not any(r.audit_rows for r in readable):
            reason = "no audit rows (rotation)"
        else:
            reason = "no dev test commands in audit"
        dev = Metric("dev_lane_red_then_green", None, "mechanical",
                     "audit rows joined by timestamp", reason)

    claim_rows = [m for m in
                  (_thread_metrics(r)["claim_agreement"] for r in readable)
                  if m.available]
    claims = Metric(
        "claim_agreement_rate",
        (round(sum(int(m.value) for m in claim_rows) * 100.0 / len(claim_rows), 1)
         if claim_rows else None),
        "claimed",
        "[claimed] implementation-summary test mention vs [mechanical] test "
        f"verdicts n={len(claim_rows)}",
        "available" if claim_rows else "no implementation summaries",
        sum(int(m.value) for m in claim_rows) if claim_rows else None,
        len(claim_rows) if claim_rows else None)
    claims_mention = Metric(
        "claims_mention_tests",
        sum(1 for r in readable if r.prose.claims_tests),
        "claimed", "implementation-summary.md test mention", "available")
    return {
        "attempts_total": _count("attempts_total", len(all_attempts),
                                 "mechanical", "readable attempts"),
        "attempts_red_then_green": _count(
            "attempts_red_then_green", r2g, "mechanical",
            f"test verdict sequences n={len(all_attempts)}"),
        "pct_attempts_red_then_green": _rate(
            "pct_attempts_red_then_green", r2g, len(all_attempts),
            "mechanical", "test verdict sequences", "no attempts"),
        "threads_red_then_green": _count(
            "threads_red_then_green", threads_r2g, "mechanical",
            f"threads with at least one attempt n={len(readable)}"),
        "pct_threads_red_then_green": _rate(
            "pct_threads_red_then_green", threads_r2g, len(readable),
            "mechanical", "readable threads", "no threads"),
        "attempts_zero_test_verdicts": _count(
            "attempts_zero_test_verdicts", zero_test, "mechanical",
            f"test verdict sequences n={len(all_attempts)}"),
        "pct_attempts_zero_test_verdicts": _rate(
            "pct_attempts_zero_test_verdicts", zero_test, len(all_attempts),
            "mechanical", "test verdict sequences", "no attempts"),
        "dev_lane_red_then_green": dev,
        "claims_mention_tests": claims_mention,
        "claim_agreement_rate": claims,
    }


def _aggregate_review(records: list[RunRecord]) -> dict[str, Metric]:
    readable = [r for r in records if not r.unreadable]
    attempts = [a for r in readable for a in r.attempts]
    review_pass = sum(
        1 for a in attempts
        if any(v.step == "review" and v.verdict == "pass" for v in a.verdicts))
    missed = sum(1 for a in attempts if _review_pass_then_qa_fail(a))
    reaching_qa = sum(
        1 for a in attempts if any(v.step == "qa" for v in a.verdicts))
    fixed = 0
    for record in readable:
        sequence = [verdict
                    for attempt in record.attempts
                    for verdict in attempt.verdicts]
        seen_review_fail = False
        seen_dev_pass = False
        for verdict in sequence:
            if verdict.step == "review" and verdict.verdict == "fail":
                seen_review_fail = True
                seen_dev_pass = False
            elif seen_review_fail and verdict.step == "dev" and \
                    verdict.verdict == "pass":
                seen_dev_pass = True
            elif seen_dev_pass and verdict.step == "review":
                fixed += 1
                break

    parsed = [r for r in readable if r.prose.review_findings_count is not None]
    findings = sum(int(r.prose.review_findings_count or 0) for r in parsed)
    if not parsed:
        defect = Metric("defect_yield", None, "parsed",
                        "review-findings.md findings per review-pass attempt",
                        "no review artifacts")
    elif review_pass == 0:
        defect = Metric("defect_yield", None, "parsed",
                        "review-findings.md findings per review-pass attempt",
                        "no review-pass attempts")
    else:
        defect = Metric("defect_yield", round(findings / review_pass, 2),
                        "parsed",
                        f"[parsed] findings / [mechanical] review-pass "
                        f"attempts n={review_pass}", "available",
                        findings, review_pass)

    rubber = 0
    rubber_available = any(
        r.prose.text("review-findings.md") is not None for r in readable)
    if rubber_available:
        for record in readable:
            text = record.prose.text("review-findings.md")
            if text is None or text.strip():
                continue
            rubber += sum(
                1 for a in record.attempts
                if any(v.step == "review" and v.verdict == "pass"
                       for v in a.verdicts))
    return {
        "review_pass_attempts": _count(
            "review_pass_attempts", review_pass, "mechanical",
            "attempts with review pass"),
        "defect_yield": defect,
        "review_missed": _count(
            "review_missed", missed, "mechanical",
            "attempts: review pass then qa fail"),
        "review_missed_rate": _rate(
            "review_missed_rate", missed, reaching_qa, "mechanical",
            "attempts reaching qa", "no qa verdicts"),
        "findings_fixed": _count(
            "findings_fixed", fixed, "mechanical",
            "review fail, dev pass, re-review across a retry"),
        "rubber_stamp": Metric(
            "rubber_stamp", rubber if rubber_available else None, "mechanical",
            f"review-pass attempts with an empty review artifact "
            f"n={review_pass}",
            "available" if rubber_available else "no review artifact",
            rubber if rubber_available else None, review_pass),
    }


def _aggregate_qa(records: list[RunRecord]) -> dict[str, Metric]:
    readable = [r for r in records if not r.unreadable]
    qa = [v for r in readable for v in _qa_verdicts(r)]
    attempts = [a for r in readable for a in r.attempts]
    reaching_qa = [a for a in attempts
                   if any(v.step == "qa" for v in a.verdicts)]
    caught = sum(1 for a in reaching_qa if _review_pass_then_qa_fail(a))
    provenance = ("parsed" if any(v.provenance == "parsed" for v in qa)
                  else "mechanical")
    return {
        "qa_verdicts": _count("qa_verdicts", len(qa), provenance,
                              "qa step verdicts"),
        "catch_rate": _rate("catch_rate", caught, len(reaching_qa),
                            "mechanical", "attempts reaching qa",
                            "no qa verdicts"),
        "environmental_rate": _rate(
            "environmental_rate",
            sum(1 for v in qa if v.verdict == "ENV"), len(qa),
            provenance, "qa verdicts", "no qa verdicts"),
        "unverifiable_rate": _rate(
            "unverifiable_rate",
            sum(1 for v in qa if v.verdict == "UNVERIFIABLE"), len(qa),
            provenance, "qa verdicts", "no qa verdicts"),
        "qa_pass_rate": _rate(
            "qa_pass_rate", sum(1 for v in qa if v.verdict == "pass"),
            len(qa), provenance, "qa verdicts", "no qa verdicts"),
    }


def _step_durations(records: list[RunRecord]) -> Metric:
    buckets: dict[str, list[float]] = {}
    for record in records:
        for attempt in _attempts(record):
            previous = None
            for verdict in attempt.verdicts:
                current = parse_ts(verdict.ts)
                if current is not None and previous is not None:
                    delta = (current - previous).total_seconds()
                    if delta >= 0:
                        buckets.setdefault(verdict.step, []).append(delta)
                if current is not None:
                    previous = current
    if not buckets:
        return Metric("per_step_durations", None, "mechanical",
                      "checkpoint ts deltas between consecutive steps",
                      "no timed step checkpoints")
    means = {step: round(sum(values) / len(values), 2)
             for step, values in sorted(buckets.items())}
    counts = {step: len(values) for step, values in sorted(buckets.items())}
    return Metric("per_step_durations", means, "mechanical",
                  "mean ts delta seconds per step", "available",
                  detail={"counts": counts})


def _audit_durations(records: list[RunRecord]) -> Metric:
    buckets: dict[str, list[float]] = {}
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        for row in record.audit_rows:
            key = (row.timestamp, row.step, row.container_id)
            if key in seen:
                continue
            seen.add(key)
            if row.duration_seconds is None:
                continue
            buckets.setdefault(row.step, []).append(float(row.duration_seconds))
    if not buckets:
        return Metric("audit_step_durations", None, "mechanical",
                      "audit duration_seconds by step", "audit rotated")
    means = {step: round(sum(values) / len(values), 2)
             for step, values in sorted(buckets.items())}
    counts = {step: len(values) for step, values in sorted(buckets.items())}
    return Metric("audit_step_durations", means, "mechanical",
                  "mean audit duration_seconds by step", "available",
                  detail={"counts": counts})


def _aggregate_loop(records: list[RunRecord]) -> dict[str, Metric]:
    total = len(records)
    counts = {status: sum(1 for r in records if r.status == status)
              for status in STATUS_VALUES}
    attempt_dist: dict[str, int] = {}
    retry_dist: dict[str, int] = {}
    brief_retries = 0
    for record in records:
        key = str(len(_attempts(record)))
        attempt_dist[key] = attempt_dist.get(key, 0) + 1
        target = record.final.get("retry_target") if record.final else None
        if target:
            retry_dist[str(target)] = retry_dist.get(str(target), 0) + 1
        retries = record.final.get("brief_retries") if record.final else None
        if isinstance(retries, dict):
            brief_retries += sum(int(v) for v in retries.values()
                                 if isinstance(v, (int, float)))
        elif isinstance(retries, (int, float)):
            brief_retries += int(retries)
    verdict_dist: dict[str, dict[str, int]] = {}
    for record in records:
        for attempt in _attempts(record):
            for verdict in attempt.verdicts:
                bucket = verdict_dist.setdefault(verdict.step, {})
                bucket[verdict.verdict] = bucket.get(verdict.verdict, 0) + 1
    metrics = {
        "thread_count": _count("thread_count", total, "mechanical",
                               "filtered run records"),
        "attempt_count_distribution": Metric(
            "attempt_count_distribution", dict(sorted(attempt_dist.items())),
            "mechanical", f"threads n={total}", "available"),
        "retry_target_frequency": Metric(
            "retry_target_frequency", dict(sorted(retry_dist.items())),
            "mechanical", f"threads with a recorded retry target n={total}",
            "available"),
        "brief_retries": _count("brief_retries", brief_retries, "mechanical",
                                f"summed brief_retries n={total}"),
        "verdict_distribution": Metric(
            "verdict_distribution", verdict_dist, "mechanical",
            "all step verdicts", "available"),
        "per_step_durations": _step_durations(records),
        "audit_step_durations": _audit_durations(records),
        "last_error_threads": _count(
            "last_error_threads",
            sum(1 for r in records if r.prose.has_last_error),
            "mechanical", f"threads with last-error.md n={total}"),
    }
    metrics["crash_rate"] = _rate(
        "crash_rate", metrics["last_error_threads"].value, total,
        "mechanical", "threads with last-error.md", "no threads")
    metrics["test_runs_per_dev_pass"] = _test_runs_per_dev_pass(records)
    for status in STATUS_VALUES:
        label = "running" if status == "running" else status
        metrics[f"{label}_rate"] = _rate(
            f"{label}_rate", counts[status], total, "mechanical",
            "filtered threads", "no threads")
        metrics[f"{label}_count"] = _count(
            f"{label}_count", counts[status], "mechanical",
            f"filtered threads n={total}")
    return metrics


def _test_runs_per_dev_pass(records: list[RunRecord]) -> Metric:
    rows = 0
    dev_passes = 0
    for record in records:
        if record.unreadable:
            continue
        dev_passes += sum(
            1 for attempt in record.attempts
            if any(v.step == "dev" and v.verdict == "pass"
                   for v in attempt.verdicts))
        rows += sum(
            1 for row in record.audit_rows
            if row.step == "dev"
            and (not row.command or is_test_command(row.command)))
    if dev_passes == 0:
        return Metric("test_runs_per_dev_pass", None, "mechanical",
                      "audit dev test rows per dev pass",
                      "no dev-pass attempts")
    if rows == 0:
        return Metric("test_runs_per_dev_pass", None, "mechanical",
                      "audit dev test rows per dev pass", "audit rotated")
    return Metric("test_runs_per_dev_pass", round(rows / dev_passes, 2),
                  "mechanical",
                  f"dev test rows / dev-pass attempts n={dev_passes}",
                  "available", rows, dev_passes)


def pure_metrics(
    run_records: list[RunRecord],
    audit_pool: list[AuditRecord] | None = None,
) -> dict:
    """Compute per-thread and aggregate metrics; pure (no I/O, no prints)."""
    records = list(run_records)
    return {
        "threads": {record.thread_id: _thread_metrics(record)
                    for record in records},
        "loop_health": _aggregate_loop(records),
        "tdd": _aggregate_tdd(records),
        "review": _aggregate_review(records),
        "qa": _aggregate_qa(records),
    }


def apply_filters(
    run_records: list[RunRecord],
    since: str | None = None,
    until: str | None = None,
    statuses: list[str] | str | None = None,
) -> list[RunRecord]:
    """Apply --since/--until (inclusive start, exclusive end) and --status."""
    since_dt = parse_ts(since) if since else None
    until_dt = parse_ts(until) if until else None
    if since and since_dt is None:
        raise ValueError(f"invalid --since datetime: {since}")
    if until and until_dt is None:
        raise ValueError(f"invalid --until datetime: {until}")
    selected: set[str] | None = None
    if statuses:
        raw = [statuses] if isinstance(statuses, str) else list(statuses)
        selected = set()
        for item in raw:
            for part in str(item).split(","):
                part = part.strip()
                if part:
                    selected.add(part)
        unknown = selected - set(STATUS_VALUES)
        if unknown:
            raise ValueError(
                f"invalid --status value: {', '.join(sorted(unknown))}")
    result: list[RunRecord] = []
    for record in run_records:
        if selected is not None and record.status not in selected:
            continue
        stamp = record.ts_start_dt
        if since_dt is not None:
            if stamp is None or stamp < since_dt:
                continue
        if until_dt is not None:
            if stamp is None or stamp >= until_dt:
                continue
        result.append(record)
    return result
