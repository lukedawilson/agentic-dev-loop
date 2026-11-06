"""058 (D4/D5): the copilot-login and copilot-models subcommands."""

from __future__ import annotations

import json

from agentic_dev_loop.engine import cli
from agentic_dev_loop.engine.copilot_auth import CopilotAuthError


class TestCopilotLogin:
    def test_writes_through_to_login(self, tmp_path, monkeypatch):
        seen = {}

        def fake_login(token_path=None):
            seen["token_path"] = token_path
            return {"access_token": "ghu_x"}

        monkeypatch.setattr(cli, "login", fake_login)
        target = tmp_path / "copilot.json"
        assert cli.main(["copilot-login", "--token-file", str(target)]) == 0
        assert seen["token_path"] == target

    def test_default_token_path_used(self, monkeypatch):
        seen = {}

        def fake_login(token_path=None):
            seen["token_path"] = token_path
            return {}

        monkeypatch.setattr(cli, "login", fake_login)
        assert cli.main(["copilot-login"]) == 0
        assert seen["token_path"].name == "copilot.json"

    def test_auth_error_returns_one(self, monkeypatch, capsys):
        def boom(token_path=None):
            raise CopilotAuthError("run `agentic-dev-loop copilot-login`")

        monkeypatch.setattr(cli, "login", boom)
        assert cli.main(["copilot-login"]) == 1
        assert "copilot-login" in capsys.readouterr().err


class TestCopilotModels:
    def test_lists_models(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "read_token", lambda _p=None: "ghu_x")
        monkeypatch.setattr(
            cli, "list_copilot_models", lambda _t: ["gpt-4o-mini", "gpt-4o"])
        assert cli.main(["copilot-models"]) == 0
        out = capsys.readouterr().out
        assert "gpt-4o-mini" in out
        assert "gpt-4o" in out

    def test_json_output(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "read_token", lambda _p=None: "ghu_x")
        monkeypatch.setattr(
            cli, "list_copilot_models", lambda _t: ["gpt-4o-mini"])
        assert cli.main(["copilot-models", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload == {"models": ["gpt-4o-mini"]}

    def test_token_file_override(self, tmp_path, monkeypatch):
        seen = {}

        def fake_read(path=None):
            seen["path"] = path
            return "ghu_x"

        monkeypatch.setattr(cli, "read_token", fake_read)
        monkeypatch.setattr(cli, "list_copilot_models", lambda _t: ["m"])
        target = tmp_path / "copilot.json"
        assert cli.main(["copilot-models", "--token-file", str(target)]) == 0
        assert seen["path"] == target

    def test_auth_error_returns_one(self, monkeypatch, capsys):
        def boom(_p=None):
            raise CopilotAuthError("run `agentic-dev-loop copilot-login`")

        monkeypatch.setattr(cli, "read_token", boom)
        assert cli.main(["copilot-models"]) == 1
        assert "copilot-login" in capsys.readouterr().err

    def test_probe_error_returns_one(self, monkeypatch, capsys):
        def boom(_token):
            raise CopilotAuthError("delete the old token file")

        monkeypatch.setattr(cli, "read_token", lambda _p=None: "ghu_x")
        monkeypatch.setattr(cli, "list_copilot_models", boom)
        assert cli.main(["copilot-models"]) == 1
        assert "delete the old token file" in capsys.readouterr().err
