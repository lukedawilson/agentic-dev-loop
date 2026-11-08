"""QA prompt contract (024): the three-way verdict vocabulary in the staged
``sdk_agents/qa.agent.md.new`` stays in sync with what the verdict parser
accepts. The active prompt is restored to the pre-unit-11 prose contract for
the installed engine; the record_verdict contract ships as ``.new`` until the
operator flips it (see the unit-11 review, prompt staging)."""

from pathlib import Path

QA_PROMPT = (Path(__file__).parent.parent / "agentic-dev-loop" / "sdk_agents"
             / "qa.agent.md.new")


def _text() -> str:
    return QA_PROMPT.read_text(encoding="utf-8")


def test_unverifiable_verdict_rules_present():
    text = _text()
    assert "record_verdict" in text
    assert "`PASS`" in text
    assert "`UNVERIFIABLE`" in text
    assert "`FAIL`" in text
    assert "## UNVERIFIABLE" in text


def test_inconclusive_budget_rule_retained():
    assert "INCONCLUSIVE" in _text()


def test_env_verdict_rules_present():
    text = _text()
    assert 'record_verdict(verdict="ENV")' in text
    assert "## ENV" in text
    assert "2 attempts" in text
