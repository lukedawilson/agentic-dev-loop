"""Unit-5 config guard (code-gen step 1): ``mounts`` + ``egress`` on StepConfig,
with loud validation at load (BR-1/BR-6/BR-8)."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agentic_dev_loop.core.config import MountSpec, load_config


def _base() -> dict:
    return {
        "name": "t",
        "llm": {"model": "m", "api_key_env": "K"},
        "agents_dir": "agents",
        "lockfiles": ["uv.lock"],
        "steps": [{"name": "dev", "agent": "dev", "on_pass": "success"}],
    }


def _load(tmp_path, data):
    p = tmp_path / "pipeline.yaml"
    p.write_text(yaml.safe_dump(data))
    return load_config(p)


class TestMountsAccepted:
    def test_mounts_round_trip(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "dev", "agent": "dev", "on_pass": "success",
                          "mounts": [{"path": "src", "mode": "ro"},
                                     {"path": ".venv-sandbox", "mode": "rw"}]}]
        cfg = _load(tmp_path, data)
        assert cfg.steps[0].mounts == [
            MountSpec(path="src", mode="ro"),
            MountSpec(path=".venv-sandbox", mode="rw"),
        ]

    def test_mount_mode_defaults_ro(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "dev", "agent": "dev", "on_pass": "success",
                          "mounts": [{"path": "src"}]}]
        assert _load(tmp_path, data).steps[0].mounts[0].mode == "ro"

    def test_empty_mounts_is_default(self, tmp_path):
        cfg = _load(tmp_path, _base())
        assert cfg.steps[0].mounts == []


class TestMountPathRejected:
    @pytest.mark.parametrize("path", [".git", ".git/config", "src/.git/x"])
    def test_git_rejected(self, tmp_path, path):
        data = _base()
        data["steps"] = [{"name": "dev", "agent": "dev",
                          "mounts": [{"path": path}]}]
        with pytest.raises(ValidationError, match="\\.git"):
            _load(tmp_path, data)

    @pytest.mark.parametrize("path", ["/etc/passwd", "\\\\windows", "C:\\\\repo"])
    def test_absolute_rejected(self, tmp_path, path):
        data = _base()
        data["steps"] = [{"name": "dev", "agent": "dev",
                          "mounts": [{"path": path}]}]
        with pytest.raises(ValidationError, match="repo-relative"):
            _load(tmp_path, data)

    @pytest.mark.parametrize("path", ["../escape", "src/../../etc", "a/../b"])
    def test_traversal_rejected(self, tmp_path, path):
        data = _base()
        data["steps"] = [{"name": "dev", "agent": "dev",
                          "mounts": [{"path": path}]}]
        with pytest.raises(ValidationError, match="traversal"):
            _load(tmp_path, data)

    def test_host_only_dir_rejected(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "dev", "agent": "dev",
                          "mounts": [{"path": ".venv"}]}]
        with pytest.raises(ValidationError, match="host-only"):
            _load(tmp_path, data)

    def test_state_dir_rejected(self, tmp_path):
        data = _base()
        data["state_dir"] = ".pr"
        data["steps"] = [{"name": "dev", "agent": "dev",
                          "mounts": [{"path": ".pr"}]}]
        with pytest.raises(ValidationError, match="state_dir"):
            _load(tmp_path, data)

    def test_unknown_mode_rejected(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "dev", "agent": "dev",
                          "mounts": [{"path": "src", "mode": "rwx"}]}]
        with pytest.raises(ValidationError, match="rw"):
            _load(tmp_path, data)


class TestEgressAccepted:
    def test_egress_browser_class(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "qa", "agent": "qa", "on_pass": "success",
                          "egress": {"class": "browser"}}]
        assert _load(tmp_path, data).steps[0].egress.class_ == "browser"

    def test_egress_preset_and_targets(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "restore", "command": "true",
                          "network": True,
                          "egress": {"presets": ["package-registries"],
                                     "targets": ["example.com:443"]}}]
        eg = _load(tmp_path, data).steps[0].egress
        assert eg.presets == ["package-registries"]
        assert eg.targets == ["example.com:443"]

    def test_scheme_prefixed_target_normalised(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "restore", "command": "true",
                          "network": True,
                          "egress": {"targets": ["https://pypi.org:443"]}}]
        assert _load(tmp_path, data).steps[0].egress.targets == ["pypi.org:443"]


class TestEgressRejected:
    def test_unknown_preset_rejected(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "restore", "command": "true",
                          "egress": {"presets": ["nope"]}}]
        with pytest.raises(ValidationError, match="unknown egress preset"):
            _load(tmp_path, data)

    def test_unknown_class_rejected(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "qa", "agent": "qa",
                          "egress": {"class": "everything"}}]
        with pytest.raises(ValidationError, match="egress class"):
            _load(tmp_path, data)

    @pytest.mark.parametrize("target", [
        "foo/bar",          # path form can never match a hostname
        "pypi.org:99999",   # port out of range
        "pypi.org:",        # empty port
        "pypi.org:abc",     # non-numeric port
        "https://x/y/z",    # scheme + path
        "[::1",             # bad IPv6 literal
    ])
    def test_malformed_target_rejected_at_load(self, tmp_path, target):
        data = _base()
        data["steps"] = [{"name": "restore", "command": "true",
                          "egress": {"targets": [target]}}]
        with pytest.raises(ValidationError, match="malformed egress target"):
            _load(tmp_path, data)


class TestActionStepAcceptance:
    """The schema cannot tell a host-side built-in from a sandbox action, so
    mounts/egress on an action step are accepted here and refused host-side
    once the registry is known (mirrors ``network``, 020)."""

    def test_mounts_on_action_accepted_at_config_level(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "commit", "action": "commit",
                          "on_pass": "success",
                          "mounts": [{"path": "src"}]}]
        assert _load(tmp_path, data).steps[0].mounts[0].path == "src"

    def test_egress_on_action_accepted_at_config_level(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "commit", "action": "commit",
                          "on_pass": "success",
                          "egress": {"class": "browser"}}]
        assert _load(tmp_path, data).steps[0].egress.class_ == "browser"
