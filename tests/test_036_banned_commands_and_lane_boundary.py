"""036 banned-command deny-list + engine-lane allowlist tests.

D1: the sandbox terminal refuses a denied argv before any shell spawn and
returns a structured ``[forbidden: <label>]`` observation. D2: sync-back
asserts every merged/synced path matches the active unit's
``engine-allowlist.yaml`` and fails without copying an out-of-lane write.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from agentic_dev_loop.core.paths import path_allowed
from agentic_dev_loop.engine.allowlist import load_engine_allowlist
from agentic_dev_loop.engine.forbidden import (match_forbidden_command,
                                                resolve_forbidden_rules)
from agentic_dev_loop.engine.sync_back import sync_back


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("orig-a")
    (ws / "tests").mkdir()
    (ws / "tests" / "t.py").write_text("orig-t")
    (ws / ".git").mkdir()
    (ws / ".git" / "HEAD").write_text("ref")
    return ws


def _export(tmp_path: Path, ws: Path) -> Path:
    exp = tmp_path / "exp"
    exp.mkdir()
    for rel in ("src/a.py", "tests/t.py"):
        src = ws / rel
        (exp / rel).parent.mkdir(parents=True, exist_ok=True)
        (exp / rel).write_text(src.read_text())
    return exp


class TestForbiddenCommandRules:
    def test_defaults_cover_deny_list(self):
        rules = resolve_forbidden_rules([], network=False)
        labels = [r.label for r in rules]
        for expected in ("git commit", "git push", "git rebase", "git checkout",
                         "git reset", "git tag", "npm install", "npm ci",
                         "pip install", "uv add", "pip-compile",
                         "dotnet add package", "npx playwright", "playwright",
                         "docker", "dotnet restore", "dotnet add"):
            assert expected in labels

    def test_network_on_drops_network_gated_dotnet(self):
        offline = resolve_forbidden_rules([], network=False)
        online = resolve_forbidden_rules([], network=True)
        assert "dotnet restore" in [r.label for r in offline]
        assert "dotnet add" in [r.label for r in offline]
        assert "dotnet restore" not in [r.label for r in online]
        assert "dotnet add" not in [r.label for r in online]

    def test_custom_rules_append_with_pattern_label(self):
        rules = resolve_forbidden_rules([r"rm\s+-rf"], network=False)
        assert rules[-1].label == r"rm\s+-rf"

    def test_shell_wrapped_argv_matches(self):
        argv = ["/bin/sh", "-c", "git push origin main"]
        assert match_forbidden_command(
            argv, resolve_forbidden_rules([], network=False)) == "git push"

    def test_empty_argv_never_matches(self):
        assert match_forbidden_command([], resolve_forbidden_rules([], False)) is None


class TestSandboxTerminalDenyList:
    def test_denied_command_returns_observation_without_spawn(self, fake_sandbox,
                                                              tmp_path):
        from openhands.tools.terminal import TerminalAction

        from agentic_dev_loop.engine.agents import SandboxTerminalExecutor
        ex = SandboxTerminalExecutor(
            fake_sandbox, workspace=str(tmp_path), env={},
            forbidden_commands=resolve_forbidden_rules([], network=False))
        obs = ex(TerminalAction(command="git commit -m x"), None)
        assert "[forbidden: git commit]" in obs.text
        assert obs.exit_code is None
        assert fake_sandbox.requests == []

    def test_custom_regex_returns_pattern_label(self, fake_sandbox, tmp_path):
        from openhands.tools.terminal import TerminalAction

        from agentic_dev_loop.engine.agents import SandboxTerminalExecutor
        ex = SandboxTerminalExecutor(
            fake_sandbox, workspace=str(tmp_path), env={},
            forbidden_commands=resolve_forbidden_rules([r"rm\s+-rf"], False))
        obs = ex(TerminalAction(command="rm -rf /tmp/x"), None)
        assert "[forbidden: rm\\s+-rf]" in obs.text
        assert fake_sandbox.requests == []

    def test_dotnet_restore_denied_offline(self, fake_sandbox, tmp_path):
        from openhands.tools.terminal import TerminalAction

        from agentic_dev_loop.engine.agents import SandboxTerminalExecutor
        ex = SandboxTerminalExecutor(
            fake_sandbox, workspace=str(tmp_path), env={},
            forbidden_commands=resolve_forbidden_rules([], network=False))
        obs = ex(TerminalAction(command="dotnet restore"), None)
        assert "[forbidden: dotnet restore]" in obs.text
        assert fake_sandbox.requests == []

    def test_dotnet_restore_allowed_online(self, fake_sandbox, tmp_path):
        from openhands.tools.terminal import TerminalAction

        from agentic_dev_loop.engine.agents import SandboxTerminalExecutor
        ex = SandboxTerminalExecutor(
            fake_sandbox, workspace=str(tmp_path), env={}, network=True,
            forbidden_commands=resolve_forbidden_rules([], network=True))
        fake_sandbox.script(lambda req: (0, "restored", False))
        obs = ex(TerminalAction(command="dotnet restore"), None)
        assert obs.exit_code == 0
        assert fake_sandbox.requests[0].argv == ["dotnet", "restore"]


class TestPathAllowed:
    def test_literal_prefix(self):
        assert path_allowed("src/a.py", ["src"])
        assert path_allowed("src/deep/a.py", ["src"])
        assert not path_allowed("tests/t.py", ["src"])

    def test_wildcard(self):
        assert path_allowed("src/a.py", ["src/**"])
        assert path_allowed("tests/test_x.py", ["**/test_*.py"])
        assert not path_allowed("docs/readme.md", ["src/**", "tests/**"])

    def test_dot_allows_whole_tree(self):
        assert path_allowed("anything/at/all", ["."])


class TestEngineAllowlistLoader:
    def test_plain_yaml_list(self, tmp_path):
        path = tmp_path / "engine-allowlist.yaml"
        path.write_text("- src/**\n- tests/**\n", encoding="utf-8")
        assert load_engine_allowlist(path) == ["src/**", "tests/**"]

    def test_empty_file_is_empty_allowlist(self, tmp_path):
        path = tmp_path / "engine-allowlist.yaml"
        path.write_text("", encoding="utf-8")
        assert load_engine_allowlist(path) == []

    def test_mapping_rejected(self, tmp_path):
        path = tmp_path / "engine-allowlist.yaml"
        path.write_text("allowlist: [src/**]\n", encoding="utf-8")
        with pytest.raises(ValueError, match="list"):
            load_engine_allowlist(path)

    def test_absolute_entry_rejected(self, tmp_path):
        path = tmp_path / "engine-allowlist.yaml"
        path.write_text("- /etc/passwd\n", encoding="utf-8")
        with pytest.raises(ValueError, match="repo-relative"):
            load_engine_allowlist(path)

    def test_traversal_rejected(self, tmp_path):
        path = tmp_path / "engine-allowlist.yaml"
        path.write_text("- ../outside\n", encoding="utf-8")
        with pytest.raises(ValueError, match="escapes"):
            load_engine_allowlist(path)


class TestSyncBackLaneBoundary:
    def test_absent_allowlist_is_unrestricted(self, tmp_path):
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (exp / "src" / "a.py").write_text("changed")
        (exp / "docs").mkdir()
        (exp / "docs" / "new.md").write_text("new")
        baseline = {"src/a.py": _digest(ws / "src" / "a.py")}
        report = sync_back(exp, ws, ["."], baseline=baseline, allowlist=None)
        assert report.lane_violations == ()
        assert set(report.synced_paths) == {"src/a.py", "docs/new.md"}

    def test_matching_paths_sync(self, tmp_path):
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (exp / "src" / "a.py").write_text("changed")
        baseline = {"src/a.py": _digest(ws / "src" / "a.py")}
        report = sync_back(exp, ws, ["."], baseline=baseline,
                           allowlist=["src/**"])
        assert report.lane_violations == ()
        assert report.synced_paths == ("src/a.py",)
        assert (ws / "src" / "a.py").read_text() == "changed"

    def test_out_of_lane_violation_fails_without_copying(self, tmp_path):
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (exp / "src" / "a.py").write_text("changed")
        (exp / "docs").mkdir()
        (exp / "docs" / "new.md").write_text("new")
        baseline = {"src/a.py": _digest(ws / "src" / "a.py")}
        report = sync_back(exp, ws, ["."], baseline=baseline,
                           allowlist=["src/**"])
        assert report.lane_violations == ("docs/new.md",)
        assert report.synced_paths == ()
        assert (ws / "src" / "a.py").read_text() == "orig-a"
        assert not (ws / "docs" / "new.md").exists()

    def test_merged_path_is_checked_too(self, tmp_path):
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (ws / "src" / "audit.md").write_text("base\nhost\n")
        (exp / "src" / "audit.md").write_text("base\nexport\n")
        baseline = {"src/audit.md": hashlib.sha256(b"base\n").hexdigest()}
        report = sync_back(exp, ws, ["src"], baseline=baseline,
                           merge_on_conflict=["src/audit.md"],
                           allowlist=["src/a.py"])
        assert report.lane_violations == ("src/audit.md",)
        assert report.merged_paths == ("src/audit.md",)
        assert report.synced_paths == ()
        assert (ws / "src" / "audit.md").read_text() == "base\nhost\n"

    def test_empty_allowlist_denies_every_synced_path(self, tmp_path):
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (exp / "src" / "a.py").write_text("changed")
        baseline = {"src/a.py": _digest(ws / "src" / "a.py")}
        report = sync_back(exp, ws, ["."], baseline=baseline, allowlist=[])
        assert report.lane_violations == ("src/a.py",)
        assert report.synced_paths == ()
        assert (ws / "src" / "a.py").read_text() == "orig-a"


class TestForbiddenCommandsConfig:
    def _load(self, tmp_path, data):
        path = tmp_path / "pipeline.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        from agentic_dev_loop.core.config import load_config
        return load_config(path)

    def _base(self):
        return {
            "name": "t",
            "llm": {"model": "m", "api_key_env": "K"},
            "agents_dir": "agents",
            "lockfiles": ["uv.lock"],
            "steps": [{"name": "dev", "agent": "dev", "on_pass": "success"}],
        }

    def test_forbidden_commands_default_empty(self, tmp_path):
        cfg = self._load(tmp_path, self._base())
        assert cfg.steps[0].forbidden_commands == []

    def test_forbidden_commands_accepts_list(self, tmp_path):
        data = self._base()
        data["steps"][0]["forbidden_commands"] = [r"rm\s+-rf", "curl"]
        cfg = self._load(tmp_path, data)
        assert cfg.steps[0].forbidden_commands == [r"rm\s+-rf", "curl"]

    def test_forbidden_commands_accepts_single_string(self, tmp_path):
        data = self._base()
        data["steps"][0]["forbidden_commands"] = "curl"
        cfg = self._load(tmp_path, data)
        assert cfg.steps[0].forbidden_commands == ["curl"]

    def test_forbidden_commands_rejects_invalid_regex(self, tmp_path):
        data = self._base()
        data["steps"][0]["forbidden_commands"] = ["[unclosed"]
        with pytest.raises(Exception, match="not a valid regex"):
            self._load(tmp_path, data)
