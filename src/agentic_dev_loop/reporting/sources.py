"""Read-only discovery and normalization of accumulated run history.

The module never writes: sqlite is opened ``mode=ro&immutable=1`` and prose
files are opened for reading only. ``exports/`` is never enumerated.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import msgpack

CHECKPOINT_DB = "loop-checkpoints.sqlite"
PROSE_FILES = (
    "attempt-brief.md",
    "implementation-summary.md",
    "review-findings.md",
    "qa-report.md",
    "last-error.md",
)
AUDIT_FILES = ("sandbox-audit.jsonl.1", "sandbox-audit.jsonl")
STATUS_VALUES = ("success", "failed", "interrupted", "running", "unreadable")
TEST_COMMAND_PATTERNS = (
    "pytest",
    "python -m pytest",
    "dotnet test",
    "npm test",
    "cargo test",
)
_VERDICT_RE = re.compile(r"VERDICT\s*:\s*([A-Za-z_ ]+)", re.IGNORECASE)
_NOTE_ATTEMPT_RE = re.compile(r"\(attempt\s+(\d+)\)", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+\S")
_TEST_CLAIM_RE = re.compile(r"\btest", re.IGNORECASE)
_PENDING_PREFIX = "~__pregel_pull, "
_LEGACY_VERDICTS = {
    "APPROVED": "pass",
    "PASS": "pass",
    "NEEDS CHANGES": "fail",
    "FAIL": "fail",
    "UNVERIFIABLE": "unverifiable",
    "ENV": "env",
}


def runs_root(root: Path | str | None = None) -> Path:
    if root is not None:
        return Path(root).expanduser()
    return Path(
        os.environ.get(
            "AGENTIC_DEV_LOOP_RUNS_ROOT",
            str(Path.home() / ".agentic-dev-loop" / "runs"),
        )
    ).expanduser()


def audit_dir(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    return Path(
        os.environ.get(
            "AGENTIC_DEV_LOOP_STATE_DIR",
            str(Path.home() / ".agentic-dev-loop" / "state"),
        )
    ).expanduser()


def discover(root: Path | str | None = None) -> list[Path]:
    """Run dirs under the runs root that contain a checkpoint database."""
    base = runs_root(root)
    if not base.is_dir():
        return []
    return sorted(
        p for p in base.iterdir()
        if p.is_dir() and (p / CHECKPOINT_DB).is_file()
    )


def find_run_dir(run_id: str, root: Path | str | None = None) -> Path | None:
    for path in discover(root):
        if path.name == run_id:
            return path
    return None


@dataclass
class AuditRecord:
    timestamp: str
    pipeline: str = ""
    step: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    duration_seconds: float | None = None
    container_id: str = ""
    request_digest: str = ""
    command: str = ""
    source: str = "sandbox-audit.jsonl"

    @property
    def ts(self) -> datetime:
        return parse_ts(self.timestamp)


@dataclass
class ProseFacts:
    texts: dict[str, str] = field(default_factory=dict)
    review_findings_count: int | None = None
    review_verdict: str | None = None
    qa_verdict: str | None = None
    claims_tests: bool | None = None
    has_last_error: bool = False
    parse_errors: list[str] = field(default_factory=list)

    def text(self, name: str) -> str | None:
        return self.texts.get(name)


@dataclass
class StepVerdict:
    step: str
    verdict: str
    provenance: str = "mechanical"
    ts: str | None = None


@dataclass
class Attempt:
    number: int
    verdicts: list[StepVerdict] = field(default_factory=list)
    retry_target: str | None = None
    terminal_step: str | None = None


@dataclass
class Checkpoint:
    ts: str
    checkpoint_ns: str
    checkpoint_id: str
    channel_values: dict
    rowid: int


@dataclass
class WriteRow:
    checkpoint_ns: str
    checkpoint_id: str
    task_path: str
    idx: int
    channel: str
    value: object
    rowid: int


@dataclass
class ThreadRaw:
    thread_id: str
    checkpoints: list[Checkpoint] = field(default_factory=list)
    writes: list[WriteRow] = field(default_factory=list)
    decode_error: str | None = None


@dataclass
class DirData:
    run_id: str
    path: Path
    threads: list[ThreadRaw] = field(default_factory=list)
    prose: ProseFacts = field(default_factory=ProseFacts)
    notice: str | None = None


@dataclass
class RunRecord:
    thread_id: str
    run_id: str
    dir: Path
    subject: str
    ts_start: str
    ts_end: str
    status: str
    attempts: list[Attempt]
    final: dict
    audit_rows: list[AuditRecord] = field(default_factory=list)
    audit_ambiguous: int = 0
    prose: ProseFacts = field(default_factory=ProseFacts)
    unreadable: bool = False
    error: str | None = None
    latest_in_dir: bool = False
    pending: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ts_start_dt(self) -> datetime | None:
        return parse_ts(self.ts_start) if self.ts_start else None

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    def attempt_verdicts(self, number: int) -> list[StepVerdict]:
        for attempt in self.attempts:
            if attempt.number == number:
                return attempt.verdicts
        return []


def parse_ts(value: str | None) -> datetime | None:
    """Parse an ISO timestamp into an aware UTC datetime; None if unusable."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decode(blob: bytes) -> object:
    return msgpack.unpackb(blob, raw=False)


def _row(row: sqlite3.Row, key: str, default=None):
    try:
        value = row[key]
    except (IndexError, KeyError):
        return default
    return default if value is None else value


def _read_prose(run_dir: Path) -> ProseFacts:
    facts = ProseFacts()
    for name in PROSE_FILES:
        path = run_dir / name
        if not path.is_file():
            continue
        try:
            facts.texts[name] = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            facts.parse_errors.append(f"{name}: {exc}")
    review = facts.texts.get("review-findings.md")
    if review is not None:
        facts.review_findings_count = sum(
            1 for line in review.splitlines() if _BULLET_RE.match(line))
        facts.review_verdict = _first_verdict(review, ("APPROVED", "NEEDS CHANGES"))
    qa = facts.texts.get("qa-report.md")
    if qa is not None:
        facts.qa_verdict = _first_verdict(
            qa, ("UNVERIFIABLE", "ENV", "PASS", "FAIL"))
    summary = facts.texts.get("implementation-summary.md")
    if summary is not None:
        facts.claims_tests = bool(_TEST_CLAIM_RE.search(summary))
    facts.has_last_error = "last-error.md" in facts.texts
    return facts


def _first_verdict(text: str, keywords: tuple[str, ...]) -> str | None:
    match = _VERDICT_RE.search(text)
    if match:
        raw = " ".join(match.group(1).split()).upper()
        for keyword in keywords:
            if raw.startswith(keyword):
                return keyword
    upper = text.upper()
    best: tuple[int, str] | None = None
    for keyword in keywords:
        idx = upper.find(keyword)
        if idx >= 0 and (best is None or idx < best[0]):
            best = (idx, keyword)
    return best[1] if best else None


def read_audit(path: Path | str | None = None) -> list[AuditRecord]:
    """Read the global audit files once; only rows carrying ``exit_code``."""
    base = audit_dir(path)
    rows: list[AuditRecord] = []
    for name in AUDIT_FILES:
        file = base / name
        if not file.is_file():
            continue
        try:
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "exit_code" not in raw:
                continue
            command = raw.get("command") or raw.get("argv") or ""
            if isinstance(command, list):
                command = " ".join(str(part) for part in command)
            rows.append(AuditRecord(
                timestamp=str(raw.get("timestamp", "")),
                pipeline=str(raw.get("pipeline", "")),
                step=str(raw.get("step", "")),
                exit_code=raw.get("exit_code"),
                timed_out=bool(raw.get("timed_out", False)),
                duration_seconds=raw.get("duration_seconds"),
                container_id=str(raw.get("container_id", "")),
                request_digest=str(raw.get("request_digest", "")),
                command=str(command),
                source=name,
            ))
    rows.sort(key=lambda row: row.timestamp)
    return rows


def read_dir(run_dir: Path | str, audit_pool=None) -> DirData:
    """Open one run dir read-only and decode its checkpoint store."""
    if audit_pool is None:
        audit_pool = ()
    path = Path(run_dir)
    data = DirData(run_id=path.name, path=path, prose=_read_prose(path))
    db_path = path / CHECKPOINT_DB
    if not db_path.is_file():
        data.notice = f"no {CHECKPOINT_DB}"
        return data
    uri = f"file:{db_path}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        data.notice = f"cannot open checkpoint database: {exc}"
        return data
    conn.row_factory = sqlite3.Row
    threads: dict[str, ThreadRaw] = {}
    try:
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "checkpoints" not in tables:
            data.notice = "checkpoint database has no checkpoints table"
            return data
        for row in conn.execute("SELECT rowid, * FROM checkpoints"):
            thread_id = str(_row(row, "thread_id", ""))
            thread = threads.setdefault(thread_id, ThreadRaw(thread_id))
            try:
                blob = _decode(_row(row, "checkpoint", b""))
            except Exception as exc:  # noqa: BLE001 - per-thread degradation
                thread.decode_error = f"checkpoint decode failed: {exc}"
                continue
            if not isinstance(blob, dict):
                thread.decode_error = "checkpoint blob is not a mapping"
                continue
            thread.checkpoints.append(Checkpoint(
                ts=str(blob.get("ts", "")),
                checkpoint_ns=str(_row(row, "checkpoint_ns", "") or ""),
                checkpoint_id=str(_row(row, "checkpoint_id", "")),
                channel_values=blob.get("channel_values", {}) or {},
                rowid=int(_row(row, "rowid", 0)),
            ))
        if "writes" in tables:
            for row in conn.execute("SELECT rowid, * FROM writes"):
                thread_id = str(_row(row, "thread_id", ""))
                thread = threads.setdefault(thread_id, ThreadRaw(thread_id))
                try:
                    value = _decode(_row(row, "value", b""))
                except Exception:
                    # A ``null``-typed write (routing markers such as
                    # ``branch:to:<node>``) stores an empty blob; keep the
                    # row so pending-node detection can see the channel.
                    value = None
                thread.writes.append(WriteRow(
                    checkpoint_ns=str(_row(row, "checkpoint_ns", "") or ""),
                    checkpoint_id=str(_row(row, "checkpoint_id", "")),
                    task_path=str(_row(row, "task_path", "") or ""),
                    idx=int(_row(row, "idx", 0) or 0),
                    channel=str(_row(row, "channel", "") or ""),
                    value=value,
                    rowid=int(_row(row, "rowid", 0)),
                ))
    finally:
        conn.close()
    data.threads = list(threads.values())
    return data


def _checkpoint_order(thread: ThreadRaw) -> dict[str, int]:
    ordered = sorted(
        thread.checkpoints,
        key=lambda cp: (cp.checkpoint_ns, cp.ts, cp.rowid),
    )
    return {cp.checkpoint_id: index for index, cp in enumerate(ordered)}


def _cv_by_checkpoint(thread: ThreadRaw) -> dict[str, dict]:
    return {cp.checkpoint_id: cp.channel_values for cp in thread.checkpoints}


def _is_reset(value: object) -> bool:
    """True for a verdict-reset write.

    Real dumps show two reset shapes: ``{}`` from the current engine's bump
    node and the initial checkpoint, and ``{'brief': None}`` from older
    engines (V7). Both start a new attempt segment.
    """
    if not isinstance(value, dict):
        return False
    if value == {}:
        return True
    return set(value) <= {"brief"} and value.get("brief") is None


def _attempts(thread: ThreadRaw, final: dict) -> list[Attempt]:
    order = _checkpoint_order(thread)
    cvs = _cv_by_checkpoint(thread)
    attempt_writes: dict[str, int] = {}
    for write in thread.writes:
        if write.channel != "attempt" or not isinstance(write.value, int):
            continue
        prior = attempt_writes.get(write.checkpoint_id)
        attempt_writes[write.checkpoint_id] = (
            write.value if prior is None else max(prior, write.value))
    writes = sorted(
        (w for w in thread.writes if w.channel == "step_verdicts"),
        key=lambda w: (order.get(w.checkpoint_id, 10**9),
                       w.checkpoint_ns, w.idx, w.rowid),
    )
    attempts: list[Attempt] = []
    seen: set[str] = set()
    current = Attempt(number=0)
    started = False

    def close(retry_target: str | None = None) -> None:
        nonlocal current, seen
        if current.verdicts:
            current.terminal_step = current.verdicts[-1].step
            current.retry_target = retry_target
            attempts.append(current)
        current = Attempt(number=current.number)
        seen = set()

    for write in writes:
        snapshot = write.value
        if not isinstance(snapshot, dict):
            continue
        cv = cvs.get(write.checkpoint_id, {})
        # Channel values lag their own writes by one superstep, so the
        # checkpoint's `attempt` can still be the previous number at a
        # bump; the `attempt` write for that checkpoint carries the new one.
        candidate = max(int(cv.get("attempt") or 0),
                        attempt_writes.get(write.checkpoint_id, 0))
        if _is_reset(snapshot):
            if started:
                close(str(cv.get("retry_target") or "") or None)
            number = candidate if candidate > current.number \
                else current.number + 1
            current = Attempt(number=number)
            started = True
            continue
        if current.number == 0:
            current.number = candidate or 1
        started = True
        ts = None
        for cp in thread.checkpoints:
            if cp.checkpoint_id == write.checkpoint_id:
                ts = cp.ts
                break
        for step, verdict in snapshot.items():
            if verdict is None or step in seen:
                continue
            seen.add(step)
            current.verdicts.append(
                StepVerdict(step=step, verdict=str(verdict), ts=ts))
    if current.verdicts:
        current.terminal_step = current.verdicts[-1].step
        if final.get("failed") and final.get("retry_target"):
            current.retry_target = str(final.get("retry_target"))
        attempts.append(current)
    if not attempts:
        final_verdicts = final.get("step_verdicts") or {}
        if isinstance(final_verdicts, dict) and final_verdicts:
            attempts.append(Attempt(
                number=int(final.get("attempt", 1) or 1),
                verdicts=[
                    StepVerdict(step=step, verdict=str(verdict))
                    for step, verdict in final_verdicts.items()
                    if verdict is not None
                ],
            ))
    return attempts


def _pending_nodes(thread: ThreadRaw, latest: Checkpoint) -> list[str]:
    """Scheduled-but-unrecorded work in the latest checkpoint.

    The current saver writes a task-start checkpoint (no writes of its own)
    whose predecessor carries a ``branch:to:<node>`` write; older stores
    recorded the pending task directly in ``task_path``.
    """
    pending: list[str] = []
    for write in thread.writes:
        if write.checkpoint_id != latest.checkpoint_id:
            continue
        if write.checkpoint_ns != latest.checkpoint_ns:
            continue
        if not write.task_path.startswith(_PENDING_PREFIX):
            continue
        node = write.task_path[len(_PENDING_PREFIX):]
        if not node or node.startswith("__"):
            continue
        pending.append(node)
    if pending:
        return sorted(set(pending))
    order = _checkpoint_order(thread)
    latest_index = order.get(latest.checkpoint_id)
    if latest_index is None:
        return []
    previous_id = next(
        (cp.checkpoint_id for cp in thread.checkpoints
         if order.get(cp.checkpoint_id) == latest_index - 1),
        None,
    )
    if previous_id is None:
        return []
    for write in thread.writes:
        if write.checkpoint_id != previous_id:
            continue
        if not write.channel.startswith("branch:to:"):
            continue
        node = write.channel[len("branch:to:"):]
        if not node or node.startswith("__") or node in ("success", "bump"):
            continue
        pending.append(node)
    return sorted(set(pending))


def _status(cv: dict, pending: list[str], latest_in_dir: bool) -> str:
    if cv.get("outcome") == "success":
        return "success"
    if cv.get("failed"):
        return "failed"
    if latest_in_dir and pending:
        return "running"
    return "interrupted"


def _legacy_verdicts(notes: list[str], attempts: list[Attempt]) -> None:
    """Attach prose-only verdicts to attempts that lack a structured one."""
    for note in notes:
        match = _VERDICT_RE.search(note)
        if not match:
            continue
        raw = " ".join(match.group(1).split()).upper()
        keyword = next((v for v in (
            "UNVERIFIABLE", "ENV", "APPROVED", "NEEDS CHANGES",
            "PASS", "FAIL") if raw.startswith(v)), None)
        if keyword is None:
            continue
        verdict = _LEGACY_VERDICTS[keyword]
        step = None
        lowered = note.lower()
        for candidate in ("qa", "review", "test", "dev", "brief"):
            if f"## {candidate} output" in lowered:
                step = candidate
                break
        if step is None:
            continue
        target = int(match_attempt.group(1)) if (
            match_attempt := _NOTE_ATTEMPT_RE.search(note)) else (
                attempts[-1].number if attempts else 1)
        for attempt in attempts:
            if attempt.number != target:
                continue
            if any(v.step == step for v in attempt.verdicts):
                continue
            attempt.verdicts.append(StepVerdict(
                step=step, verdict=verdict, provenance="parsed"))


def _attach_prose_verdicts(prose: ProseFacts, final: dict,
                           attempts: list[Attempt]) -> None:
    """Recover legacy agent verdicts from artifacts/prose (BR-11, U2)."""
    created = False
    if attempts:
        target = attempts[-1]
    else:
        target = Attempt(number=int(final.get("attempt", 1) or 1))
        created = True
    present = {verdict.step for verdict in target.verdicts}
    candidates: list[tuple[str, str, tuple[str, ...]]] = []
    artifacts = final.get("artifacts") if isinstance(final, dict) else None
    if isinstance(artifacts, dict):
        for step, text in artifacts.items():
            if isinstance(text, str) and text.strip():
                candidates.append((str(step), text,
                                   ("APPROVED", "NEEDS CHANGES", "PASS",
                                    "FAIL", "UNVERIFIABLE", "ENV")))
    for step, verdict, text in (
        ("review", prose.review_verdict,
         prose.text("review-findings.md")),
        ("qa", prose.qa_verdict, prose.text("qa-report.md")),
    ):
        if verdict and text:
            candidates.append((step, text, (verdict,)))
    for step, text, keywords in candidates:
        if step in present:
            continue
        keyword = _first_verdict(text, keywords)
        if keyword is None:
            continue
        verdict = _LEGACY_VERDICTS[keyword]
        target.verdicts.append(StepVerdict(
            step=step, verdict=verdict, provenance="parsed"))
        present.add(step)
    if created and target.verdicts:
        attempts.append(target)


def _as_dir_list(data) -> list[DirData]:
    if isinstance(data, DirData):
        return [data]
    if isinstance(data, (str, Path)):
        return [read_dir(data)]
    result: list[DirData] = []
    for item in data:
        if isinstance(item, DirData):
            result.append(item)
        elif isinstance(item, (str, Path)):
            result.append(read_dir(item))
        else:
            raise TypeError(f"unsupported run-dir input: {type(item)!r}")
    return result


def build_run_records(
    data: DirData | list[DirData] | tuple[DirData, ...],
    audit_pool: list[AuditRecord] | None = None,
) -> list[RunRecord]:
    """Normalize decoded dirs into one RunRecord per thread."""
    dirs = _as_dir_list(data)
    provisional: list[RunRecord] = []
    for dir_data in dirs:
        readable = [t for t in dir_data.threads if not t.decode_error]
        latest_id = None
        if readable:
            latest = max(readable, key=lambda t: (
                max((cp.ts for cp in t.checkpoints), default=""),))
            latest_id = latest.thread_id
        for thread in dir_data.threads:
            if thread.decode_error:
                provisional.append(RunRecord(
                    thread_id=thread.thread_id,
                    run_id=dir_data.run_id,
                    dir=dir_data.path,
                    subject="",
                    ts_start="",
                    ts_end="",
                    status="unreadable",
                    attempts=[],
                    final={},
                    prose=dir_data.prose if thread.thread_id == latest_id
                    else ProseFacts(),
                    unreadable=True,
                    error=thread.decode_error,
                    latest_in_dir=thread.thread_id == latest_id,
                ))
                continue
            checkpoints = sorted(
                thread.checkpoints,
                key=lambda cp: (cp.checkpoint_ns, cp.ts, cp.rowid))
            if not checkpoints:
                provisional.append(RunRecord(
                    thread_id=thread.thread_id,
                    run_id=dir_data.run_id,
                    dir=dir_data.path,
                    subject="",
                    ts_start="",
                    ts_end="",
                    status="interrupted",
                    attempts=[],
                    final={},
                    prose=ProseFacts(),
                    error="no checkpoints",
                ))
                continue
            latest = max(checkpoints, key=lambda cp: (cp.ts, cp.rowid))
            final = latest.channel_values
            pending = _pending_nodes(thread, latest)
            latest_in_dir = thread.thread_id == latest_id
            status = _status(final, pending, latest_in_dir)
            attempts = _attempts(thread, final)
            notes = [str(n) for n in (final.get("notes") or []) if n]
            _legacy_verdicts(notes, attempts)
            prose = dir_data.prose if latest_in_dir else ProseFacts()
            _attach_prose_verdicts(prose, final, attempts)
            provisional.append(RunRecord(
                thread_id=thread.thread_id,
                run_id=dir_data.run_id,
                dir=dir_data.path,
                subject=str(final.get("subject", "") or ""),
                ts_start=checkpoints[0].ts,
                ts_end=latest.ts,
                status=status,
                attempts=attempts,
                final=final,
                prose=prose,
                latest_in_dir=latest_in_dir,
                pending=pending,
                notes=notes,
            ))

    _join_audit(provisional, audit_pool or [])
    return provisional


def _as_audit(row: AuditRecord | dict) -> AuditRecord | None:
    if isinstance(row, AuditRecord):
        return row
    if not isinstance(row, dict):
        return None
    command = row.get("command") or row.get("argv") or ""
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    return AuditRecord(
        timestamp=str(row.get("timestamp", "")),
        pipeline=str(row.get("pipeline", "")),
        step=str(row.get("step", "")),
        exit_code=row.get("exit_code"),
        timed_out=bool(row.get("timed_out", False)),
        duration_seconds=row.get("duration_seconds"),
        container_id=str(row.get("container_id", "")),
        request_digest=str(row.get("request_digest", "")),
        command=str(command),
    )


def _join_audit(records: list[RunRecord], pool: list) -> None:
    ranges = []
    for record in records:
        start = parse_ts(record.ts_start)
        end = parse_ts(record.ts_end)
        if start is not None and end is not None and end < start:
            start, end = end, start
        ranges.append((record, start, end))
    for raw_row in pool:
        row = _as_audit(raw_row)
        if row is None:
            continue
        stamp = parse_ts(row.timestamp)
        if stamp is None:
            continue
        matches = [
            record for record, start, end in ranges
            if start is not None and end is not None and start <= stamp <= end
        ]
        if len(matches) == 1:
            matches[0].audit_rows.append(row)
        elif len(matches) > 1:
            for record in matches:
                record.audit_ambiguous += 1


def is_test_command(command: str) -> bool:
    lowered = command.lower()
    return any(pattern in lowered for pattern in TEST_COMMAND_PATTERNS)
