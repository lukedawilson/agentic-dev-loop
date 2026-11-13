"""066: the claude-login subcommand."""

from __future__ import annotations

from agentic_dev_loop.engine import cli
from agentic_dev_loop.engine.claude_auth import ClaudeAuthError


class TestClaudeLogin:
    def test_writes_through_to_login(self, tmp_path, monkeypatch):
        seen = {}

        def fake_login(token_path=None):
            seen["token_path"] = token_path
            return {"access_token": "sk-ant-oat01-x"}

        monkeypatch.setattr(cli, "claude_login", fake_login)
        target = tmp_path / "claude.json"
        assert cli.main(["claude-login", "--token-file", str(target)]) == 0
        assert seen["token_path"] == target

    def test_default_token_path_used(self, monkeypatch):
        seen = {}

        def fake_login(token_path=None):
            seen["token_path"] = token_path
            return {}

        monkeypatch.setattr(cli, "claude_login", fake_login)
        assert cli.main(["claude-login"]) == 0
        assert seen["token_path"].name == "claude.json"

    def test_auth_error_returns_one(self, monkeypatch, capsys):
        def boom(token_path=None):
            raise ClaudeAuthError("run `agentic-dev-loop claude-login`")

        monkeypatch.setattr(cli, "claude_login", boom)
        assert cli.main(["claude-login"]) == 1
        assert "claude-login" in capsys.readouterr().err

    def test_help_lists_the_subcommand(self, capsys):
        assert cli.main(["help"]) == 0
        assert "claude-login" in capsys.readouterr().out
