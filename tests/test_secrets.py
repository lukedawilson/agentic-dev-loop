"""Unit tests for the host-side secret store (019): schema/validation, provider
resolution, injection assembly, the ``.env*`` ban, and output scrubbing.

No Docker daemon and no network: resolution is driven with an explicit
``environ`` mapping, and the ban/scrubber are pure filesystem/string tests.
"""
from __future__ import annotations

import yaml
import pytest
from pydantic import ValidationError

from agentic_dev_loop.core.config import (PipelineConfig, SecretSpec,
                                          load_config)
from agentic_dev_loop.engine.isolation import resolve_sandbox_env
from agentic_dev_loop.engine.secrets import (
    REDACTED, EnvProvider, FileProvider, SecretResolutionError,
    redact_values, resolve_secrets, scan_env_files, step_secret_env)


def _base() -> dict:
    return {
        "name": "test-pipeline",
        "llm": {"model": "m", "api_key_env": "K"},
        "agents_dir": "sdk_agents",
        "lockfiles": ["uv.lock"],
        "steps": [{"name": "dev", "agent": "dev", "on_pass": "success"}],
    }


def _load(tmp_path, data):
    p = tmp_path / "pipeline.yaml"
    p.write_text(yaml.safe_dump(data))
    return load_config(p)


def _cfg_with_secrets(tmp_path) -> PipelineConfig:
    data = _base()
    data["secrets"] = {
        "kinde-client-secret": {"from": "env", "key": "KINDIE_CLIENT_SECRET"},
        "r2-keys": {"from": "env", "key": "R2_KEYS"},
    }
    data["steps"] = [
        {"name": "qa", "agent": "qa", "secrets": ["kinde-client-secret"],
         "on_pass": "success"},
    ]
    return _load(tmp_path, data)


class TestSecretSchema:
    def test_secrets_block_round_trips(self, tmp_path):
        cfg = _cfg_with_secrets(tmp_path)
        assert set(cfg.secrets) == {"kinde-client-secret", "r2-keys"}
        spec = cfg.secrets["kinde-client-secret"]
        assert spec.from_ == "env" and spec.key == "KINDIE_CLIENT_SECRET"
        assert cfg.steps[0].secrets == ["kinde-client-secret"]

    def test_defaults_are_empty(self, tmp_path):
        cfg = _load(tmp_path, _base())
        assert cfg.secrets == {}
        assert cfg.steps[0].secrets == []

    def test_secret_spec_alias_round_trip(self):
        spec = SecretSpec.model_validate({"from": "env", "key": "K"})
        assert spec.model_dump(by_alias=True) == {"from": "env", "key": "K"}
        assert spec.model_dump() == {"from_": "env", "key": "K"}

    def test_value_shaped_ref_rejected(self, tmp_path):
        data = _base()
        data["secrets"] = {"kinde-client-secret": "actual-secret-value"}
        with pytest.raises(ValidationError, match="not a literal value"):
            _load(tmp_path, data)

    def test_secret_spec_extra_key_rejected(self, tmp_path):
        data = _base()
        data["secrets"] = {"r": {"from": "env", "key": "K", "value": "x"}}
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            _load(tmp_path, data)

    def test_unknown_provider_rejected(self, tmp_path):
        data = _base()
        data["secrets"] = {"r": {"from": "op", "key": "K"}}
        with pytest.raises(ValidationError, match="unknown secret provider"):
            _load(tmp_path, data)

    def test_empty_key_rejected(self, tmp_path):
        data = _base()
        data["secrets"] = {"r": {"from": "env", "key": "  "}}
        with pytest.raises(ValidationError, match="non-empty string"):
            _load(tmp_path, data)

    def test_step_undeclared_ref_rejected(self, tmp_path):
        data = _base()
        data["steps"][0]["secrets"] = ["nope"]
        with pytest.raises(ValidationError, match="not declared in the top-level 'secrets:'"):
            _load(tmp_path, data)

    def test_action_step_rejects_secrets(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "commit", "action": "commit",
                          "secrets": ["r"], "on_pass": "success"}]
        data["secrets"] = {"r": {"from": "env", "key": "K"}}
        with pytest.raises(ValidationError, match="'secrets' is only allowed"):
            _load(tmp_path, data)

    def test_command_step_accepts_secrets(self, tmp_path):
        data = _base()
        data["secrets"] = {"r": {"from": "env", "key": "K"}}
        data["steps"] = [{"name": "probe", "command": "printenv K",
                          "secrets": ["r"], "on_pass": "success"}]
        assert _load(tmp_path, data).steps[0].secrets == ["r"]


class TestProviderResolution:
    def test_env_provider_resolves(self):
        provider = EnvProvider({"K": "v1", "OTHER": "v2"})
        assert provider.resolve(["K", "OTHER"]) == {"K": "v1", "OTHER": "v2"}

    def test_env_provider_missing_is_loud(self):
        provider = EnvProvider({"K": "v1"})
        with pytest.raises(SecretResolutionError, match="OTHER"):
            provider.resolve(["K", "OTHER"])

    def test_resolve_secrets_returns_ref_keyed_values(self, tmp_path):
        cfg = _cfg_with_secrets(tmp_path)
        resolved = resolve_secrets(cfg, {"KINDIE_CLIENT_SECRET": "kinde-secret",
                                         "R2_KEYS": "r2-keys"})
        assert resolved == {"kinde-client-secret": "kinde-secret",
                            "r2-keys": "r2-keys"}

    def test_resolve_secrets_missing_names_ref_step_and_key(self, tmp_path):
        cfg = _cfg_with_secrets(tmp_path)
        with pytest.raises(SecretResolutionError) as e:
            resolve_secrets(cfg, {"KINDIE_CLIENT_SECRET": "kinde-secret"})
        msg = str(e.value)
        assert "r2-keys" in msg
        assert "R2_KEYS" in msg
        assert "env" in msg
        assert "['<none>']" in msg  # r2-keys is declared but unused by any step

    def test_resolve_secrets_missing_used_ref_names_step(self, tmp_path):
        cfg = _cfg_with_secrets(tmp_path)
        with pytest.raises(SecretResolutionError) as e:
            resolve_secrets(cfg, {"R2_KEYS": "r2-keys"})
        assert "kinde-client-secret" in str(e.value)
        assert "['qa']" in str(e.value)

    def test_step_secret_env_only_declared_refs(self, tmp_path):
        cfg = _cfg_with_secrets(tmp_path)
        resolved = {"kinde-client-secret": "kinde-secret", "r2-keys": "r2"}
        env = step_secret_env(cfg, cfg.steps[0], resolved)
        assert env == {"KINDIE_CLIENT_SECRET": "kinde-secret"}


class TestInjectionAssembly:
    def test_secrets_injected_into_env(self):
        env = resolve_sandbox_env({"MY_VAR": "declared"}, host_env={},
                                  secrets={"K": "secret"})
        assert env["MY_VAR"] == "declared"
        assert env["K"] == "secret"
        assert env["PATH"].startswith("/usr/local/bin")

    def test_secret_wins_over_same_named_step_env(self):
        env = resolve_sandbox_env({"K": "literal"}, host_env={},
                                  secrets={"K": "secret"})
        assert env["K"] == "secret"


class TestEnvFileBan:
    def test_env_file_trips(self, tmp_path):
        (tmp_path / ".env").write_text("OPENAI_API_KEY=x")
        with pytest.raises(SecretResolutionError) as e:
            scan_env_files(tmp_path)
        assert ".env" in str(e.value)
        assert "secrets:" in str(e.value)

    def test_any_env_star_trips(self, tmp_path):
        (tmp_path / ".env.local").write_text("x")
        with pytest.raises(SecretResolutionError, match=".env.local"):
            scan_env_files(tmp_path)

    @pytest.mark.parametrize("name", [".env.example", ".env.template", ".env.sample"])
    def test_convention_fixtures_whitelisted(self, tmp_path, name):
        (tmp_path / name).write_text("EXAMPLE=placeholder")
        scan_env_files(tmp_path)  # must not raise

    def test_clean_workspace_passes(self, tmp_path):
        (tmp_path / "README.md").write_text("no secrets")
        scan_env_files(tmp_path)

    def test_names_all_hits(self, tmp_path):
        (tmp_path / ".env").write_text("a")
        (tmp_path / ".env.production").write_text("b")
        with pytest.raises(SecretResolutionError) as e:
            scan_env_files(tmp_path)
        assert ".env.production" in str(e.value) and ".env" in str(e.value)


class TestRedactValues:
    def test_replaces_every_value(self):
        assert redact_values("a secret b", ["secret"]) == "a [REDACTED] b"

    def test_longest_first_avoids_partial(self):
        assert redact_values("abc", ["ab", "abc"]) == REDACTED

    def test_empty_values_ignored(self):
        assert redact_values("x", ["", ""]) == "x"

    def test_empty_text(self):
        assert redact_values("", ["x"]) == ""

    def test_deduplicates_and_sorts(self):
        assert redact_values("a-x-b", ["x", "x"]) == "a-[REDACTED]-b"


class TestFileProvider:
    """058 (D3): the file provider reads a locator path, with an optional
    JSON key selection; every failure names the fix."""

    def _json(self, tmp_path, payload):
        path = tmp_path / "copilot.json"
        path.write_text(payload, encoding="utf-8")
        return path

    def test_reads_whole_file_without_json_key(self, tmp_path):
        path = tmp_path / "token.txt"
        path.write_text("  token-value\n", encoding="utf-8")
        assert FileProvider().resolve([str(path)])[str(path)] == "token-value"

    def test_reads_json_key(self, tmp_path):
        path = self._json(
            tmp_path,
            '{"access_token": "ghu_x", "scope": "copilot"}')
        resolved = FileProvider().resolve(
            [str(path)], {str(path): "access_token"})
        assert resolved[str(path)] == "ghu_x"

    def test_missing_file_names_login_command(self, tmp_path):
        with pytest.raises(SecretResolutionError, match="copilot-login"):
            FileProvider().resolve([str(tmp_path / "missing.json")])

    def test_missing_claude_token_names_claude_login(self, tmp_path):
        with pytest.raises(SecretResolutionError, match="claude-login"):
            FileProvider().resolve([str(tmp_path / "claude.json")])

    def test_invalid_json_fails_loudly(self, tmp_path):
        path = self._json(tmp_path, "not json")
        with pytest.raises(SecretResolutionError, match="not valid JSON"):
            FileProvider().resolve([str(path)], {str(path): "access_token"})

    def test_missing_json_key_names_the_key(self, tmp_path):
        path = self._json(tmp_path, '{"other": "x"}')
        with pytest.raises(SecretResolutionError, match="access_token"):
            FileProvider().resolve([str(path)], {str(path): "access_token"})

    def test_non_string_json_value_rejected(self, tmp_path):
        path = self._json(tmp_path, '{"access_token": 123}')
        with pytest.raises(SecretResolutionError, match="access_token"):
            FileProvider().resolve([str(path)], {str(path): "access_token"})


class TestFileSecretResolution:
    def test_resolve_secrets_routes_json_key_selection(self, tmp_path):
        token = tmp_path / "copilot.json"
        token.write_text('{"access_token": "ghu_x"}', encoding="utf-8")
        data = _base()
        data["secrets"] = {
            "copilot-token": {
                "from": "file", "key": str(token),
                "json_key": "access_token"}}
        data["steps"] = [
            {"name": "dev", "agent": "dev", "secrets": ["copilot-token"],
             "on_pass": "success"},
        ]
        cfg = _load(tmp_path, data)
        assert resolve_secrets(cfg) == {"copilot-token": "ghu_x"}
        assert resolve_secrets(cfg, {}) == {"copilot-token": "ghu_x"}


class TestLlmSecretRedactionSet:
    """058 (D3): a file-backed llm key joins the run redaction set."""

    def test_llm_file_key_is_resolved_for_redaction(self, tmp_path):
        token = tmp_path / "copilot.json"
        token.write_text('{"access_token": "ghu_redact_me"}',
                         encoding="utf-8")
        pipeline = tmp_path / "pipeline.yaml"
        pipeline.write_text(f"""\
name: redaction
llm:
  model: gpt-4o-mini
  api_key_secret:
    from: file
    key: {token}
    json_key: access_token
agents_dir: sdk_agents
deps_policy: open
steps:
  - name: dev
    agent: dev
    on_pass: success
""", encoding="utf-8")
        cfg = load_config(pipeline)
        resolved = resolve_secrets(cfg)
        assert resolved["llm-api-key:llm"] == "ghu_redact_me"

    def test_unreferenced_profile_key_is_not_resolved(self, tmp_path):
        pipeline = tmp_path / "pipeline.yaml"
        pipeline.write_text("""\
name: redaction
llm:
  model: m
  api_key_env: K
llms:
  review:
    model: m
    api_key_secret:
      from: file
      key: /does/not/exist.json
      json_key: access_token
agents_dir: sdk_agents
deps_policy: open
steps:
  - name: dev
    agent: dev
    on_pass: success
""", encoding="utf-8")
        cfg = load_config(pipeline)
        assert resolve_secrets(cfg, {"K": "v"}) == {}
