"""058 (D4/D5): the GitHub device flow and the Copilot /models probe.

HTTP is the external boundary, so it is the only thing mocked: a fake
urlopen returns scripted JSON payloads and records each request.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from agentic_dev_loop.engine import copilot_auth


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install(monkeypatch, responses):
    calls = []
    pending = list(responses)

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        payload = pending.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return FakeResponse(payload)

    monkeypatch.setattr(copilot_auth.urllib.request, "urlopen", fake_urlopen)
    return calls


DEVICE = {
    "device_code": "dc-1",
    "user_code": "ABCD-1234",
    "verification_uri": "https://github.com/login/device",
    "interval": 0,
    "expires_in": 900,
}


class TestClientIdAndScope:
    def test_scope_is_copilot(self):
        assert copilot_auth.DEFAULT_SCOPE == "copilot"

    def test_default_client_id_is_pinned(self):
        assert copilot_auth.DEFAULT_CLIENT_ID == "Iv1.b507a08c87ecfe98"

    def test_client_id_env_override(self, monkeypatch):
        monkeypatch.setenv("AGENTIC_DEV_LOOP_COPILOT_CLIENT_ID", "Iv1.mine")
        assert copilot_auth.client_id() == "Iv1.mine"


class TestDeviceFlow:
    def test_login_writes_token_with_mode_600(self, tmp_path, monkeypatch):
        calls = _install(monkeypatch, [
            DEVICE,
            {"access_token": "ghu_test", "scope": "copilot",
             "token_type": "bearer"},
        ])
        token_path = tmp_path / "auth" / "copilot.json"
        printed = []

        token = copilot_auth.login(
            token_path=token_path, sleep=lambda _s: None,
            printer=printed.append)

        assert token["access_token"] == "ghu_test"
        saved = json.loads(token_path.read_text(encoding="utf-8"))
        assert saved["access_token"] == "ghu_test"
        assert token_path.stat().st_mode & 0o777 == 0o600
        body = calls[0].data.decode("utf-8")
        assert "scope=copilot" in body
        assert copilot_auth.DEFAULT_CLIENT_ID in body
        assert any("ABCD-1234" in line for line in printed)

    def test_authorization_pending_is_polled(self, tmp_path, monkeypatch):
        _install(monkeypatch, [
            DEVICE,
            {"error": "authorization_pending"},
            {"access_token": "ghu_after_poll"},
        ])
        token = copilot_auth.login(
            token_path=tmp_path / "copilot.json", sleep=lambda _s: None,
            printer=lambda _s: None)
        assert token["access_token"] == "ghu_after_poll"

    def test_slow_down_increases_interval(self, tmp_path, monkeypatch):
        sleeps = []
        _install(monkeypatch, [
            DEVICE,
            {"error": "slow_down"},
            {"access_token": "ghu_slow"},
        ])
        token = copilot_auth.login(
            token_path=tmp_path / "copilot.json", sleep=sleeps.append,
            printer=lambda _s: None)
        assert token["access_token"] == "ghu_slow"
        assert sleeps[0] >= 5

    def test_expired_device_code_fails_with_login_hint(self, tmp_path, monkeypatch):
        _install(monkeypatch, [
            DEVICE,
            {"error": "expired_token"},
        ])
        with pytest.raises(copilot_auth.CopilotAuthError, match="copilot-login"):
            copilot_auth.login(
                token_path=tmp_path / "copilot.json", sleep=lambda _s: None,
                printer=lambda _s: None)

    def test_client_id_override_used_in_request(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTIC_DEV_LOOP_COPILOT_CLIENT_ID", "Iv1.mine")
        calls = _install(monkeypatch, [
            DEVICE,
            {"access_token": "ghu_x"},
        ])
        copilot_auth.login(
            token_path=tmp_path / "copilot.json", sleep=lambda _s: None,
            printer=lambda _s: None)
        assert "Iv1.mine" in calls[0].data.decode("utf-8")


class TestReadToken:
    def test_missing_file_names_login(self, tmp_path):
        with pytest.raises(copilot_auth.CopilotAuthError, match="copilot-login"):
            copilot_auth.read_token(tmp_path / "missing.json")

    def test_unreadable_json_names_login(self, tmp_path):
        path = tmp_path / "copilot.json"
        path.write_text("not json", encoding="utf-8")
        with pytest.raises(copilot_auth.CopilotAuthError, match="copilot-login"):
            copilot_auth.read_token(path)

    def test_missing_access_token_names_login(self, tmp_path):
        path = tmp_path / "copilot.json"
        path.write_text('{"scope": "copilot"}', encoding="utf-8")
        with pytest.raises(copilot_auth.CopilotAuthError, match="copilot-login"):
            copilot_auth.read_token(path)

    def test_reads_access_token(self, tmp_path):
        path = tmp_path / "copilot.json"
        path.write_text('{"access_token": "ghu_x"}', encoding="utf-8")
        assert copilot_auth.read_token(path) == "ghu_x"


class TestListCopilotModels:
    def test_returns_model_ids(self, monkeypatch):
        calls = _install(monkeypatch, [
            {"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]},
        ])
        models = copilot_auth.list_copilot_models("ghu_test")
        assert models == ["gpt-4o-mini", "gpt-4o"]
        request = calls[0]
        assert request.full_url.endswith("/models")
        assert request.get_header("Authorization") == "Bearer ghu_test"
        assert request.get_header("Copilot-integration-id") == "vscode-chat"

    def test_probe_omits_the_api_version_header(self, monkeypatch):
        """2026-11-06 live probe: the real endpoint rejects
        X-GitHub-Api-Version with 400 'invalid apiVersion' at /models and
        /chat/completions; the other three headers are accepted."""
        calls = _install(monkeypatch, [
            {"data": [{"id": "gpt-4o-mini"}]},
        ])
        copilot_auth.list_copilot_models("ghu_test")
        request = calls[0]
        assert request.get_header("X-github-api-version") is None
        assert request.get_header("Editor-version") == "vscode/1.94.0"
        assert request.get_header("Openai-intent") == "conversation-panel"

    def test_401_names_the_scope_fix(self, monkeypatch):
        error = urllib.error.HTTPError(
            "https://api.githubcopilot.com/models", 401, "Unauthorized", {}, None)
        _install(monkeypatch, [error])
        with pytest.raises(copilot_auth.CopilotAuthError) as excinfo:
            copilot_auth.list_copilot_models("ghu_bad")
        text = str(excinfo.value)
        assert "copilot-login" in text
        assert "delete" in text.lower()

    def test_unexpected_payload_fails(self, monkeypatch):
        _install(monkeypatch, [{"unexpected": True}])
        with pytest.raises(copilot_auth.CopilotAuthError, match="models"):
            copilot_auth.list_copilot_models("ghu_test")
