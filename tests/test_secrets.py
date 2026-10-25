"""Unit tests for the host-side secret store (019): schema/validation, provider
resolution, injection assembly, the ``.env*`` ban, and output scrubbing.

No Docker daemon and no network: resolution is driven with an explicit
``environ`` mapping, and the ban/scrubber are pure filesystem/string tests.
"""
from __future__ import annotations

import yaml
import pytest
from pydantic import ValidationError

from agentic_dev_loop.core.config import (KNOWN_SECRET_PROVIDERS, PipelineConfig,
                                          SecretSpec, load_config)
from agentic_dev_loop.engine.isolation import resolve_sandbox_env
from agentic_dev_loop.engine.secrets import (
    REDACTED, EnvProvider, SecretResolutionError, redact_values,
    resolve_secrets, scan_env_files, step_secret_env)


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

    def test_known_providers_pinned(self):
        assert KNOWN_SECRET_PROVIDERS == frozenset({"env"})

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

    def test_no_secrets_param_changes_nothing(self):
        env = resolve_sandbox_env({"MY_VAR": "declared"}, host_env={})
        assert env == {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp",
                       "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TERM": "xterm",
                       "MY_VAR": "declared"}


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
