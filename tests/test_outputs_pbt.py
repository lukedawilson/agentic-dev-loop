"""Property-based tests for the capture/parse channel (PBT-01 properties)."""

import re
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from agentic_dev_loop.engine.outputs import CapturedOutput
from agentic_dev_loop.core.verdict import Verdict, VerdictError

VERDICTS = {"PASS": "pass", "FAIL": "fail"}

# ── Generators (PBT-07: realistic, varied) ──────────────────────────────

_decor_chars = st.text(alphabet="#*_`>- ", max_size=6)
_prose = st.text(
    alphabet=st.characters(blacklist_categories=("Cc", "Cs"), blacklist_characters="\n"),
    max_size=40,
)
_keyword = st.sampled_from(sorted(VERDICTS))

# After the keyword: nothing, or a caveat-punctuated prose (a decision line).
_tail = st.one_of(
    st.just(""),
    st.builds(
        lambda c, p: f" {c}{p}",
        st.sampled_from("\u2014\u2013,;:."),
        _prose,
    ),
)

verdict_line = st.builds(
    lambda deco, kw, tail: f"{deco}VERDICT: {kw}{tail}".strip(),
    _decor_chars,
    _keyword,
    _tail,
)


@st.composite
def text_with_single_decision(draw):
    """A multi-line text whose only decision line is the given one."""
    decision = draw(verdict_line)
    filler = draw(
        st.lists(
            st.text(alphabet=st.characters(blacklist_characters="\nV"), max_size=50),
            min_size=0,
            max_size=5,
        )
    )
    lines = filler + [decision]
    return "\n".join(lines)


@st.composite
def text_with_conflicting_decisions(draw):
    first = draw(verdict_line.filter(
        lambda v: "PASS" in v.split(":", 1)[1]
        and "FAIL" not in v.split(":", 1)[1]))
    second = draw(verdict_line.filter(
        lambda v: "FAIL" in v.split(":", 1)[1]
        and "PASS" not in v.split(":", 1)[1]))
    return f"{first}\n{second}"


# ── Properties ─────────────────────────────────────────────────────────

@given(text_with_single_decision())
@settings(max_examples=200)
def test_decoration_invariance(text: str):
    """Wrapping the one decision line in markdown decoration never changes the verdict."""
    expected = Verdict.from_text(text, VERDICTS)
    assert expected.is_ok()
    value = expected.unwrap().value
    decorated = text.replace("VERDICT:", "**VERDICT:**")
    again = Verdict.from_text(decorated, VERDICTS)
    assert again.is_ok()
    assert again.unwrap().value == value


@given(st.text(alphabet=st.characters(blacklist_characters="\n"), max_size=100))
@settings(max_examples=200)
def test_no_verdict_keyword_is_no_verdict(text: str):
    """Any text without the VERDICT keyword yields NO_VERDICT, never a verdict."""
    text = re.sub(r"VERDICT", "", text, flags=re.IGNORECASE)
    result = Verdict.from_text(text, VERDICTS)
    if result.is_err():
        assert result.error == VerdictError.NO_VERDICT


@given(text_with_conflicting_decisions())
@settings(max_examples=100)
def test_conflicting_decisions_are_ambiguous(text: str):
    """Two genuinely conflicting decision lines are never silently resolved."""
    result = Verdict.from_text(text, VERDICTS)
    assert result.is_err()
    assert result.error == VerdictError.AMBIGUOUS


@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"), min_size=1, max_size=500))
def test_capture_write_round_trip(text: str):
    """CapturedOutput.write → read is the identity on raw_text."""
    with tempfile.TemporaryDirectory() as td:
        state_dir = Path(td) / ".pr"
        captured = CapturedOutput(raw_text=text, artifact_path=state_dir / "out.md")
        assert captured.write().is_ok()
        # Product artifacts are UTF-8 by contract (BR-6) - reads must never
        # fall back to the locale encoding (cp1252 on Windows CI).
        assert (state_dir / "out.md").read_text(encoding="utf-8") == text
