"""run_smoke provider injection and llm pre-flight: the provider argument and
the config directory are independent (the provider's llm block is injected
into a provider-free template), and credential checks run before the worktree
is created. Fixture yaml strings only, no network."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SMOKE = Path(__file__).resolve().parent / "smoke" / "run_smoke.py"
_spec = importlib.util.spec_from_file_location("run_smoke_under_test", _SMOKE)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)

PLAN_YAML = """\
name: smoke
agents_dir: agents
steps:
  - name: dev
    agent: dev
    on_pass: success
"""

ENV_YAML = """\
name: smoke
llm:
  model: openai/deepseek-v4-pro
  api_key_env: SMOKE_TEST_KEY
agents_dir: agents
steps:
  - name: dev
    agent: dev
    on_pass: success
"""


def _secret_yaml(token_path: Path) -> str:
    return f"""\
name: smoke
llm:
  model: gpt-4o-mini
  base_url: https://api.githubcopilot.com
  api_key_secret:
    from: file
    key: {token_path}
    json_key: access_token
agents_dir: agents
steps:
  - name: dev
    agent: dev
    on_pass: success
"""


class TestProviderInjection:
    def test_registry_has_exactly_openhands_and_copilot(self):
        assert set(smoke.PROVIDER_BLOCKS) == {"openhands", "copilot"}

    def test_block_is_injected_after_the_name_line(self):
        text = smoke.inject_provider_block(PLAN_YAML, "copilot")
        assert text.startswith("name: smoke\nllm:\n")
        assert "base_url: https://api.githubcopilot.com" in text
        assert "api_key_secret:" in text
        assert "agents_dir: agents" in text

    def test_block_is_prepended_when_the_template_has_no_name(self):
        template = """\
agents_dir: agents
steps:
  - name: dev
    agent: dev
    on_pass: success
"""
        text = smoke.inject_provider_block(template, "openhands")
        assert text.startswith("llm:\n  model: openai/deepseek-v4-pro\n")

    def test_a_template_that_carries_an_llm_block_is_rejected(self):
        with pytest.raises(smoke.SmokePreflightError, match="llm"):
            smoke.inject_provider_block(ENV_YAML, "openhands")

    def test_openhands_block_preflights_the_env_var(self):
        text = smoke.inject_provider_block(PLAN_YAML, "openhands")
        with pytest.raises(smoke.SmokePreflightError) as excinfo:
            smoke.preflight_llm(text, environ={})
        assert "export OPENAI_API_KEY" in str(excinfo.value)

    def test_openhands_block_passes_when_the_env_var_is_set(self):
        text = smoke.inject_provider_block(PLAN_YAML, "openhands")
        smoke.preflight_llm(text, environ={"OPENAI_API_KEY": "sk-x"})

    def test_copilot_block_probes_the_pinned_model(self):
        text = smoke.inject_provider_block(PLAN_YAML, "copilot")
        with pytest.raises(smoke.SmokePreflightError, match="gpt-4o-mini"):
            smoke.preflight_copilot_model(
                text,
                token_reader=lambda _p: "ghu_x",
                models_lister=lambda _t: ["gpt-4o"])

    def test_copilot_block_passes_when_the_model_is_in_the_tier_list(self):
        text = smoke.inject_provider_block(PLAN_YAML, "copilot")
        smoke.preflight_copilot_model(
            text,
            token_reader=lambda _p: "ghu_x",
            models_lister=lambda _t: ["gpt-4o-mini", "gpt-4o"])

    def test_openhands_block_skips_the_model_probe(self):
        text = smoke.inject_provider_block(PLAN_YAML, "openhands")
        smoke.preflight_copilot_model(
            text,
            token_reader=lambda _p: pytest.fail("probe must not run"),
            models_lister=lambda _t: pytest.fail("probe must not run"))


class TestCredentialPreflight:
    def test_missing_env_var_names_the_export(self):
        with pytest.raises(smoke.SmokePreflightError) as excinfo:
            smoke.preflight_llm(ENV_YAML, environ={})
        assert "export SMOKE_TEST_KEY" in str(excinfo.value)

    def test_present_env_var_passes(self):
        smoke.preflight_llm(ENV_YAML, environ={"SMOKE_TEST_KEY": "sk-x"})

    def test_missing_token_file_names_copilot_login(self, tmp_path):
        token = tmp_path / "auth" / "copilot.json"
        with pytest.raises(smoke.SmokePreflightError) as excinfo:
            smoke.preflight_llm(_secret_yaml(token), environ={})
        assert "copilot-login" in str(excinfo.value)

    def test_present_token_file_passes(self, tmp_path):
        token = tmp_path / "auth" / "copilot.json"
        token.parent.mkdir()
        token.write_text('{"access_token": "ghu_x"}', encoding="utf-8")
        smoke.preflight_llm(_secret_yaml(token), environ={})

    def test_env_provider_is_not_treated_as_a_file(self):
        smoke.preflight_llm(ENV_YAML, environ={"SMOKE_TEST_KEY": "sk-x"})


class TestModelPreflight:
    def test_pinned_model_must_be_in_the_tier_list(self, tmp_path):
        token = tmp_path / "auth" / "copilot.json"
        token.parent.mkdir()
        token.write_text('{"access_token": "ghu_x"}', encoding="utf-8")
        with pytest.raises(smoke.SmokePreflightError, match="gpt-4o-mini"):
            smoke.preflight_copilot_model(
                _secret_yaml(token),
                token_reader=lambda _p: "ghu_x",
                models_lister=lambda _t: ["gpt-4o"])

    def test_pinned_model_present_passes(self, tmp_path):
        token = tmp_path / "auth" / "copilot.json"
        token.parent.mkdir()
        token.write_text('{"access_token": "ghu_x"}', encoding="utf-8")
        smoke.preflight_copilot_model(
            _secret_yaml(token),
            token_reader=lambda _p: "ghu_x",
            models_lister=lambda _t: ["gpt-4o-mini", "gpt-4o"])

    def test_env_only_config_skips_the_probe(self):
        smoke.preflight_copilot_model(
            ENV_YAML,
            token_reader=lambda _p: pytest.fail("probe must not run"),
            models_lister=lambda _t: pytest.fail("probe must not run"))
