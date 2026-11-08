"""Verdict parsing - the shared decision contract (BR-6).

`Verdict.from_text` parses the captured response for routing; expected
conditions are typed `Result` errors, never exceptions. `parse_verdict`
collapses that `Result` to a route string for routing consumers: a clear
decision maps to its canonical value, anything unclear maps to ``retry`` -
totality for arbitrary input (no crash, no silent routing).
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from typing import Iterable

from .failure import FailureClass
from .results import Result

# The structured verdict channel's tool name. The tool is registered by the
# engine (engine/agents.py); the entity owns the argument contract.
RECORD_VERDICT_TOOL = "record_verdict"

# Leading markdown/list/quote decoration tolerated on a verdict line.
_LEAD_DECOR = "#*_`>- "
# Decoration tolerated between VERDICT and ':' and after the keyword.
_DECOR = "*_`> "
# Caveat delimiters: after one of these, arbitrary prose may follow the
# keyword without the line losing its decision status. '.' doubles as the
# bare sentence terminator.
_CAVEAT = "\u2014\u2013,;:."
# Negators recognized immediately before the keyword.
_NEGATORS = ("NOT", "DOES NOT", "DID NOT")

RETRY = "retry"


class VerdictError(enum.Enum):
    NO_VERDICT = "no_verdict"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class VerdictChannelError:
    """A structured verdict channel failure: the class plus the evidence a
    report must name (the offending value, the configured set, the conflict)."""

    code: FailureClass
    detail: str


@dataclass(frozen=True)
class Verdict:
    """A parsed decision: `value` is the canonical outcome (e.g. 'pass').

    Parsing policy (BR-2): the last decoration-tolerant `VERDICT: X` line
    that carries a *decision* wins; a keyword followed directly by prose is
    discussion, not a decision. Conflicting decisions are never silently
    resolved - they surface as AMBIGUOUS and retry.
    """

    value: str

    @staticmethod
    def from_text(text: str, verdicts: dict[str, str]) -> "Result[Verdict, VerdictError]":
        decisions: set[str] = set()
        saw_verdict_line = False
        for line in text.splitlines():
            is_line, decision = _parse_line(line, verdicts)
            if is_line:
                saw_verdict_line = True
            if decision is not None:
                decisions.add(decision)
        if len(decisions) == 1:
            return Result.ok(Verdict(value=next(iter(decisions))))
        if len(decisions) > 1:
            return Result.err(VerdictError.AMBIGUOUS)
        if saw_verdict_line:
            return Result.err(VerdictError.AMBIGUOUS)
        return Result.err(VerdictError.NO_VERDICT)


    @staticmethod
    def from_events(
        events: Iterable[object], verdicts: dict[str, str]
    ) -> "Result[Verdict, VerdictChannelError]":
        """Decode the verdict from record_verdict tool calls (FR-11.2).

        The event stream is the only source: prose is never parsed here. Zero
        calls is NO_VERDICT_TOOL; a value outside the configured set is
        INVALID_VERDICT_VALUE; two different decisions are CONFLICTING_VERDICT
        (fail fast, FD-Q1). Matching is case-insensitive because models vary
        the casing of a configured keyword."""
        values: list[str] = []
        calls = 0
        for event in events:
            if getattr(event, "tool_name", None) != RECORD_VERDICT_TOOL:
                continue
            calls += 1
            value = _verdict_argument(event)
            if value is not None:
                values.append(value)
        if calls == 0:
            return Result.err(VerdictChannelError(
                FailureClass.NO_VERDICT_TOOL,
                f"no {RECORD_VERDICT_TOOL} tool call in the event stream"))
        lookup = {keyword.casefold(): value
                  for keyword, value in verdicts.items()}
        decided: dict[str, str] = {}
        for value in values:
            canonical = lookup.get(value.casefold())
            if canonical is None:
                configured = ", ".join(sorted(verdicts))
                return Result.err(VerdictChannelError(
                    FailureClass.INVALID_VERDICT_VALUE,
                    f"{RECORD_VERDICT_TOOL} value {value!r} is not in the "
                    f"configured set [{configured}]"))
            decided[canonical] = value
        if not decided:
            return Result.err(VerdictChannelError(
                FailureClass.INVALID_VERDICT_VALUE,
                f"{RECORD_VERDICT_TOOL} call carried no readable verdict "
                "argument"))
        if len(decided) > 1:
            return Result.err(VerdictChannelError(
                FailureClass.CONFLICTING_VERDICT,
                "conflicting verdicts: "
                + ", ".join(sorted(repr(v) for v in decided.values()))))
        return Result.ok(Verdict(value=next(iter(decided))))


def _verdict_argument(event: object) -> str | None:
    """The verdict argument carried by one RecordVerdictAction event.

    Prefers the parsed action (the SDK's normal path); falls back to the raw
    tool-call arguments JSON when the action was not parsed."""
    action = getattr(event, "action", None)
    value = getattr(action, "verdict", None)
    if isinstance(value, str):
        return value
    tool_call = getattr(event, "tool_call", None)
    arguments = getattr(tool_call, "arguments", None)
    if not isinstance(arguments, str):
        return None
    try:
        payload = json.loads(arguments)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("verdict"), str):
        return payload["verdict"]
    return None


def parse_verdict(text: str, verdicts: dict[str, str]) -> str:
    """Route for a verdict artifact: the canonical decision value on a single
    clear decision, else ``retry``. Never raises - unclear verdicts retry."""
    result = Verdict.from_text(text, verdicts)
    if result.is_ok():
        return result.value.value
    return RETRY


def _parse_line(line: str, verdicts: dict[str, str]) -> tuple[bool, str | None]:
    """Parse one line -> (is_verdict_line, decision_or_None).

    Ported verbatim from the pre-unit-1 `verdicts.py` parser; the original
    returned None for both non-verdict lines and verdict lines without a
    decision - the flag now separates those cases for BR-2.5/BR-2.6.
    """
    stripped = line.strip().lstrip(_LEAD_DECOR)
    if not stripped.upper().startswith("VERDICT"):
        return False, None
    rest = stripped[len("VERDICT"):].lstrip(_DECOR)
    if not rest.startswith(":"):
        return True, None
    body = rest[1:].strip()
    negated = False
    for negator in _NEGATORS:
        if body.upper().startswith(negator + " "):
            negated = True
            body = body[len(negator):].strip()
            break
    body = body.lstrip(_DECOR)
    for keyword in sorted(verdicts, key=len, reverse=True):
        if not body.upper().startswith(keyword.upper()):
            continue
        after = body[len(keyword):].strip().lstrip(_DECOR).strip()
        if after.startswith("?"):
            return True, None
        if after.startswith("!"):
            if after[1:].strip():
                return True, None  # bare '!' is emphasis; '!' plus prose is not
        elif after and after[0] not in _CAVEAT and not after.startswith("-"):
            return True, None  # bare prose - discussion, not a decision
        if negated:
            if len(verdicts) == 2:
                other = next(k for k in verdicts if k != keyword)
                return True, verdicts[other]
            return True, None  # negation on a larger set is ambiguous
        return True, verdicts[keyword]
    return True, None
