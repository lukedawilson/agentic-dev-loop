"""QA prompt contract (024): the no-substitution rule and the three-way
verdict rules live in the dogfood copy of sdk_agents/qa.agent.md."""

from pathlib import Path

QA_PROMPT = Path(__file__).parent.parent / "agentic-dev-loop" / "sdk_agents" / "qa.agent.md"


def _text() -> str:
    return QA_PROMPT.read_text(encoding="utf-8")


def test_no_substitution_rule_present():
    text = _text()
    assert "Never substitute unrelated generic probes" in text
    assert "UNVERIFIABLE" in text
    assert "blocking reason" in text


def test_unverifiable_verdict_rules_present():
    text = _text()
    assert "VERDICT: PASS" in text
    assert "VERDICT: UNVERIFIABLE" in text
    assert "VERDICT: FAIL" in text
    assert "## UNVERIFIABLE" in text


def test_inconclusive_budget_rule_retained():
    assert "INCONCLUSIVE" in _text()


def test_probe_plan_rules_present():
    text = _text()
    assert "**Variant matrix.**" in text
    assert "**Outcome probes.**" in text
    assert "**Concurrency probes.**" in text
    assert "**Error messages are assertions.**" in text


def test_env_verdict_rules_present():
    text = _text()
    assert "VERDICT: ENV" in text
    assert "## ENV" in text
    assert "environment" in text.lower()
    assert "harness" in text
    assert "2 attempts" in text


def test_env_unverifiable_distinction_present():
    text = _text()
    assert "UNVERIFIABLE" in text
    assert "verification environment" in text
    assert "product code" in text
