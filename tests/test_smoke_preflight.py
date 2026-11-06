"""058 (D10): the run_smoke llm pre-flight - credential checks before the
worktree is created, using fixture yaml strings only (no network)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SMOKE = Path(__file__).resolve().parent / "smoke" / "run_smoke.py"
_spec = importlib.util.spec_from_file_location("run_smoke_under_test", _SMOKE)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)

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


def _write(tmp_path: Path, text: str) -> Path:
    (tmp_path / "pipeline.yaml").write_text(text, encoding="utf-8")
    return tmp_path


class TestCredentialPreflight:
    def test_missing_env_var_names_the_export(self, tmp_path):
        config = _write(tmp_path, ENV_YAML)
        with pytest.raises(smoke.SmokePreflightError) as excinfo:
            smoke.preflight_llm(config, environ={})
        assert "export SMOKE_TEST_KEY" in str(excinfo.value)

    def test_present_env_var_passes(self, tmp_path):
        config = _write(tmp_path, ENV_YAML)
        smoke.preflight_llm(config, environ={"SMOKE_TEST_KEY": "sk-x"})

    def test_missing_token_file_names_copilot_login(self, tmp_path):
        token = tmp_path / "auth" / "copilot.json"
        config = _write(tmp_path, _secret_yaml(token))
        with pytest.raises(smoke.SmokePreflightError) as excinfo:
            smoke.preflight_llm(config, environ={})
        assert "copilot-login" in str(excinfo.value)

    def test_present_token_file_passes(self, tmp_path):
        token = tmp_path / "auth" / "copilot.json"
        token.parent.mkdir()
        token.write_text('{"access_token": "ghu_x"}', encoding="utf-8")
        config = _write(tmp_path, _secret_yaml(token))
        smoke.preflight_llm(config, environ={})

    def test_env_provider_is_not_treated_as_a_file(self, tmp_path):
        yaml = """\
name: smoke
llm:
  model: m
  api_key_env: SMOKE_TEST_KEY
agents_dir: agents
steps:
  - name: dev
    agent: dev
    on_pass: success
"""
        config = _write(tmp_path, yaml)
        smoke.preflight_llm(config, environ={"SMOKE_TEST_KEY": "sk-x"})


class TestModelPreflight:
    def test_pinned_model_must_be_in_the_tier_list(self, tmp_path):
        token = tmp_path / "auth" / "copilot.json"
        token.parent.mkdir()
        token.write_text('{"access_token": "ghu_x"}', encoding="utf-8")
        config = _write(tmp_path, _secret_yaml(token))
        with pytest.raises(smoke.SmokePreflightError, match="gpt-4o-mini"):
            smoke.preflight_copilot_model(
                config,
                token_reader=lambda _p: "ghu_x",
                models_lister=lambda _t: ["gpt-4o"])

    def test_pinned_model_present_passes(self, tmp_path):
        token = tmp_path / "auth" / "copilot.json"
        token.parent.mkdir()
        token.write_text('{"access_token": "ghu_x"}', encoding="utf-8")
        config = _write(tmp_path, _secret_yaml(token))
        smoke.preflight_copilot_model(
            config,
            token_reader=lambda _p: "ghu_x",
            models_lister=lambda _t: ["gpt-4o-mini", "gpt-4o"])

    def test_env_only_config_skips_the_probe(self, tmp_path):
        config = _write(tmp_path, ENV_YAML)
        smoke.preflight_copilot_model(
            config,
            token_reader=lambda _p: pytest.fail("probe must not run"),
            models_lister=lambda _t: pytest.fail("probe must not run"))
