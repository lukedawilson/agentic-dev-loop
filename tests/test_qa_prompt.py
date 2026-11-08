"""QA prompt contract (024): the three-way verdict vocabulary in the dogfood
copy of ``sdk_agents/qa.agent.md`` stays in sync with what the verdict parser
accepts."""

from pathlib import Path

QA_PROMPT = (Path(__file__).parent.parent / "agentic-dev-loop" / "sdk_agents"
             / "qa.agent.md")


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
