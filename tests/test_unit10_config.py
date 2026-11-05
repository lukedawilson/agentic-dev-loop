"""Unit 10 config: provider selection and strict dead-config rejection."""

import pytest
import yaml
from pydantic import ValidationError

from agentic_dev_loop.core.config import PipelineConfig, load_config

def _base(**over):
    data = {
        "name": "t",
        "llm": {"model": "m", "api_key_env": "K"},
        "agents_dir": "agents",
        "lockfiles": ["uv.lock"],
        "steps": [{"name": "dev", "agent": "dev", "on_pass": "success"}],
    }
    data.update(over)
    return data


def _load(tmp_path, data):
    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_config(path)


def test_absent_provider_defaults_to_openhands(tmp_path):
    cfg = _load(tmp_path, _base())
    assert cfg.provider == "openhands"
    assert cfg.claude is None and cfg.copilot is None


def test_unknown_provider_names_valid_values():
    with pytest.raises(ValidationError) as err:
        PipelineConfig.model_validate(_base(provider="gemini"))
    message = str(err.value)
    assert "openhands" in message and "claude" in message and "copilot" in message


def test_openhands_requires_llm():
    data = _base()
    data.pop("llm")
    with pytest.raises(ValidationError, match="llm: is required"):
        PipelineConfig.model_validate(data)


def test_claude_block_with_openhands_provider_is_dead_config():
    data = _base(claude={"login": "subscription"})
    with pytest.raises(ValidationError, match="claude:"):
        PipelineConfig.model_validate(data)


def test_claude_provider_accepts_block_without_llm(tmp_path):
    data = _base(provider="claude", claude={"login": "subscription",
                                            "model": "sonnet",
                                            "max_turns": 12})
    data.pop("llm")
    cfg = _load(tmp_path, data)
    assert cfg.llm is None
    assert cfg.claude.login == "subscription"
    assert cfg.claude.model == "sonnet"
    assert cfg.claude.max_turns == 12


def test_claude_provider_requires_block():
    data = _base(provider="claude")
    data.pop("llm")
    with pytest.raises(ValidationError, match="requires a 'claude:' block"):
        PipelineConfig.model_validate(data)


def test_claude_login_is_required():
    data = _base(provider="claude", claude={})
    data.pop("llm")
    with pytest.raises(ValidationError):
        PipelineConfig.model_validate(data)


def test_llm_family_forbidden_for_claude():
    data = _base(provider="claude", claude={"login": "subscription"},
                 llms={"review": {"model": "m", "api_key_env": "K"}})
    data.pop("llm")
    with pytest.raises(ValidationError, match="llms: is openhands-only"):
        PipelineConfig.model_validate(data)


def test_step_llm_and_reasoning_forbidden_for_copilot():
    data = _base(provider="copilot", copilot={"auth": "device"})
    data.pop("llm")
    data["steps"] = [{"name": "dev", "agent": "dev", "llm": "review",
                      "on_pass": "success"}]
    with pytest.raises(ValidationError, match="llm: is openhands-only"):
        PipelineConfig.model_validate(data)


def test_step_reasoning_effort_forbidden_for_copilot():
    data = _base(provider="copilot", copilot={"auth": "device"})
    data.pop("llm")
    data["steps"] = [{"name": "dev", "agent": "dev",
                      "reasoning_effort": "high", "on_pass": "success"}]
    with pytest.raises(ValidationError, match="reasoning_effort is openhands-only"):
        PipelineConfig.model_validate(data)


def test_copilot_block_mismatch_fails():
    data = _base(provider="claude", claude={"login": "subscription"},
                 copilot={"auth": "device"})
    data.pop("llm")
    with pytest.raises(ValidationError, match="copilot:"):
        PipelineConfig.model_validate(data)


def test_provider_blocks_forbid_unknown_keys():
    data = _base(provider="claude", claude={"login": "subscription",
                                            "token": "nope"})
    data.pop("llm")
    with pytest.raises(ValidationError):
        PipelineConfig.model_validate(data)


def test_max_turns_must_be_positive():
    data = _base(provider="claude", claude={"login": "subscription",
                                            "max_turns": 0})
    data.pop("llm")
    with pytest.raises(ValidationError):
        PipelineConfig.model_validate(data)


def test_existing_config_corpus_still_loads(tmp_path):
    cfg = _load(tmp_path, _base())
    assert cfg.provider == "openhands"
    assert cfg.llm is not None
