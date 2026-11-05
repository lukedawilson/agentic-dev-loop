"""Unit 10 copilot device flow: pinned client id, token store, redaction."""

import json
import os
import stat

import pytest

from agentic_dev_loop.engine.runtimes import copilot_auth
from agentic_dev_loop.engine.runtimes.copilot_auth import (COPILOT_CLIENT_ID,
                                                           CopilotAuthError,
                                                           CopilotToken,
                                                           ensure_token,
                                                           load_token,
                                                           save_token)


def test_client_id_is_pinned():
    assert COPILOT_CLIENT_ID == "Iv1.b507a08c87ecfe98"


def test_save_and_load_round_trip_with_mode_600(tmp_path):
    path = tmp_path / "auth/copilot.json"
    save_token(CopilotToken("tok", scope="read:user"), path)
    assert load_token(path).access_token == "tok"
    if os.name == "posix":
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600


def test_expired_token_runs_the_device_flow(tmp_path):
    path = tmp_path / "auth/copilot.json"
    save_token(CopilotToken("old", expires_at=1.0), path)
    responses = iter([
        {"device_code": "dev", "user_code": "ABCD",
         "verification_uri": "https://github.com/login/device",
         "interval": 1, "expires_in": 60},
        {"access_token": "fresh", "token_type": "bearer"},
    ])
    printed = []
    token = ensure_token(path, print_fn=printed.append,
                         post=lambda url, fields, **kw: next(responses),
                         sleep=lambda seconds: None)
    assert token.access_token == "fresh"
    assert load_token(path).access_token == "fresh"
    assert "ABCD" in printed[0] and "fresh" not in printed[0]


def test_valid_token_is_reused_without_network(tmp_path):
    path = tmp_path / "auth/copilot.json"
    save_token(CopilotToken("cached"), path)

    def post(*args, **kwargs):
        raise AssertionError("network must not be touched")

    assert ensure_token(path, post=post).access_token == "cached"


def test_refused_flow_fails_loud(tmp_path):
    responses = iter([
        {"device_code": "dev", "user_code": "ABCD", "interval": 1,
         "expires_in": 60},
        {"error": "access_denied", "error_description": "user said no"},
    ])
    with pytest.raises(CopilotAuthError, match="refused"):
        ensure_token(tmp_path / "copilot.json", print_fn=lambda _: None,
                     post=lambda url, fields, **kw: next(responses),
                     sleep=lambda seconds: None)


def test_token_redaction_helper():
    assert copilot_auth.redactions_for(CopilotToken("secret")) == ("secret",)
    assert copilot_auth.redactions_for(None) == ()
    assert CopilotToken("secret").redacted() == "***"
