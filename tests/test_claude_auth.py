"""066: the Claude subscription OAuth PKCE flow.

HTTP is the external boundary. The exchange uses a real ``curl`` subprocess
against a local ``http.server`` token endpoint (real curl, real HTTP, no
network); the login flow's authorize URL and callback are exercised against
a local loopback ``http.server`` with an injected browser opener.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pytest

from agentic_dev_loop.engine import claude_auth

CURL = shutil.which("curl")


def _query(url: str) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)


def _completed(argv, body: str, status: int = 200):
    return subprocess.CompletedProcess(argv, 0, f"{body}\n{status}", "")


def _runner(body: str, status: int = 200):
    seen = {}

    def run(argv):
        seen["argv"] = argv
        return _completed(argv, body, status)

    run.seen = seen
    return run


TOKEN_BODY = json.dumps({
    "access_token": "sk-ant-oat01-minted",
    "refresh_token": "sk-ant-ort01-minted",
    "expires_in": 28800,
    "scope": "user:inference",
    "token_type": "Bearer",
})


class TestClientId:
    def test_default_client_id_is_the_claude_code_uuid(self):
        assert claude_auth.CLAUDE_CLIENT_ID == \
            "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AGENTIC_DEV_LOOP_CLAUDE_CLIENT_ID",
                           "00000000-0000-0000-0000-000000000000")
        assert claude_auth.client_id() == \
            "00000000-0000-0000-0000-000000000000"


class TestAuthorizeUrl:
    def test_pinned_parameters(self):
        url = claude_auth.authorize_url(
            "http://localhost:54321/callback", "challenge-value",
            "state-value")
        assert url.startswith(claude_auth.AUTHORIZE_URL + "?code=true&")
        assert url.split("&")[1].startswith("client_id=")
        params = _query(url)
        assert params["code"] == ["true"]
        assert params["client_id"] == [claude_auth.CLAUDE_CLIENT_ID]
        assert params["response_type"] == ["code"]
        assert params["redirect_uri"] == \
            ["http://localhost:54321/callback"]
        assert params["scope"] == [claude_auth.SCOPES]
        assert params["code_challenge"] == ["challenge-value"]
        assert params["code_challenge_method"] == ["S256"]
        assert params["state"] == ["state-value"]

    def test_seven_scopes_in_captured_order(self):
        assert claude_auth.SCOPES == (
            "org:create_api_key user:profile user:inference "
            "user:sessions:claude_code user:mcp_servers user:file_upload "
            "user:plugins")

    def test_scope_and_redirect_uri_are_not_percent_mangled(self):
        url = claude_auth.authorize_url(
            "http://localhost:1234/callback", "challenge", "state")
        assert ("scope=org:create_api_key+user:profile+user:inference+"
                "user:sessions:claude_code+user:mcp_servers+user:file_upload+"
                "user:plugins") in url
        assert "redirect_uri=http://localhost:1234/callback" in url

    def test_client_id_env_override_reaches_the_url(self, monkeypatch):
        monkeypatch.setenv("AGENTIC_DEV_LOOP_CLAUDE_CLIENT_ID",
                           "11111111-1111-1111-1111-111111111111")
        url = claude_auth.authorize_url(
            "http://localhost:1/callback", "c", "s")
        assert _query(url)["client_id"] == \
            ["11111111-1111-1111-1111-111111111111"]


class TestPkce:
    def test_pair_is_s256_of_a_32_byte_verifier(self):
        verifier, challenge = claude_auth.pkce_pair(
            rng=lambda size: b"a" * size)
        padded = verifier + "=" * (-len(verifier) % 4)
        assert len(base64.urlsafe_b64decode(padded)) == 32
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        assert challenge == expected


class TestExchange:
    def test_body_and_curl_argv(self):
        runner = _runner(TOKEN_BODY)
        token = claude_auth.exchange_token(
            "the-code", "http://localhost:7777/callback", "the-verifier",
            "the-state", curl_runner=runner)
        assert token["access_token"] == "sk-ant-oat01-minted"
        argv = runner.seen["argv"]
        assert isinstance(argv, list)
        assert argv[0] == "curl"
        assert argv[argv.index("-X") + 1] == "POST"
        assert claude_auth.TOKEN_ENDPOINT in argv
        assert "Content-Type: application/json" in argv
        assert "Accept: application/json" in argv
        assert "%{http_code}" in argv[argv.index("-w") + 1]
        body = json.loads(argv[argv.index("--data-binary") + 1])
        assert body == {
            "grant_type": "authorization_code",
            "code": "the-code",
            "redirect_uri": "http://localhost:7777/callback",
            "client_id": claude_auth.CLAUDE_CLIENT_ID,
            "code_verifier": "the-verifier",
            "state": "the-state",
        }
        assert "scope" not in body
        assert "client_secret" not in body
        assert "code_challenge_method" not in body

    def test_429_reports_rate_limit_and_retry_guidance(self):
        runner = _runner(
            '{"error":"rate_limit_error","message":"slow down"}', 429)
        with pytest.raises(claude_auth.ClaudeAuthError) as excinfo:
            claude_auth.exchange_token("c", "http://localhost/cb", "v", "s",
                                       curl_runner=runner)
        text = str(excinfo.value)
        assert "rate limited" in text
        assert "retry in a few minutes" in text
        assert "rate_limit_error" in text

    def test_other_non_200_reports_the_response_message(self):
        runner = _runner(
            '{"error":"invalid_grant","message":"code expired"}', 400)
        with pytest.raises(claude_auth.ClaudeAuthError) as excinfo:
            claude_auth.exchange_token("c", "http://localhost/cb", "v", "s",
                                       curl_runner=runner)
        text = str(excinfo.value)
        assert "400" in text
        assert "invalid_grant" in text
        assert "code expired" in text

    def test_non_json_200_is_a_loud_error(self):
        runner = _runner("not json at all")
        with pytest.raises(claude_auth.ClaudeAuthError, match="JSON"):
            claude_auth.exchange_token("c", "http://localhost/cb", "v", "s",
                                       curl_runner=runner)

    def test_missing_curl_names_curl(self):
        def runner(_argv):
            raise FileNotFoundError("curl")

        with pytest.raises(claude_auth.ClaudeAuthError, match="curl"):
            claude_auth.exchange_token("c", "http://localhost/cb", "v", "s",
                                       curl_runner=runner)

    def test_missing_required_field_is_a_loud_error(self):
        runner = _runner('{"access_token":"sk-ant-oat01-x"}')
        with pytest.raises(claude_auth.ClaudeAuthError, match="refresh_token"):
            claude_auth.exchange_token("c", "http://localhost/cb", "v", "s",
                                       curl_runner=runner)

    @pytest.mark.skipif(CURL is None, reason="curl is not installed")
    def test_real_curl_against_a_local_token_endpoint(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                if self.path.endswith("/rate"):
                    self._send(429, '{"error":"rate_limit_error"}')
                else:
                    self._send(200, TOKEN_BODY)

            def _send(self, status, body):
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            token = claude_auth.exchange_token(
                "real-code", "http://localhost:1234/callback", "real-verifier",
                "real-state", token_endpoint=f"{base}/token")
            assert token["access_token"] == "sk-ant-oat01-minted"
            with pytest.raises(claude_auth.ClaudeAuthError,
                               match="rate limited"):
                claude_auth.exchange_token(
                    "real-code", "http://localhost:1234/callback",
                    "real-verifier", "real-state",
                    token_endpoint=f"{base}/rate")
        finally:
            server.shutdown()
            server.server_close()


class TestWriteToken:
    def test_schema_and_mode_600(self, tmp_path):
        path = claude_auth.write_token({
            "access_token": "sk-ant-oat01-a",
            "refresh_token": "sk-ant-ort01-r",
            "expires_in": 3600,
            "scope": "user:inference",
            "token_type": "Bearer",
        }, tmp_path / "auth" / "claude.json")
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert set(saved) == {"access_token", "refresh_token", "expires_at"}
        assert saved["access_token"] == "sk-ant-oat01-a"
        assert saved["refresh_token"] == "sk-ant-ort01-r"
        assert (datetime.fromisoformat(saved["expires_at"])
                > datetime.now(timezone.utc))
        assert path.stat().st_mode & 0o777 == 0o600

    def test_missing_refresh_token_is_a_loud_error(self, tmp_path):
        with pytest.raises(claude_auth.ClaudeAuthError, match="refresh_token"):
            claude_auth.write_token({"access_token": "a", "expires_in": 1},
                                    tmp_path / "claude.json")


class _LoopbackBrowser:
    """Stand-in for webbrowser.open: follows the captured authorize URL's
    redirect_uri back to the login flow's loopback callback."""

    def __init__(self, state_override: str | None = None):
        self.state_override = state_override
        self.status: int | None = None
        self.page = ""
        self.authorize_url = ""

    def __call__(self, url: str) -> None:
        self.authorize_url = url
        params = _query(url)
        redirect = params["redirect_uri"][0]
        state = self.state_override or params["state"][0]
        target = f"{redirect}?code=the-code&state={state}"
        try:
            with urllib.request.urlopen(target) as response:
                self.status = response.status
                self.page = response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            self.status = e.code
            self.page = e.read().decode("utf-8")


class TestLogin:
    def test_loopback_callback_writes_the_token(self, tmp_path):
        runner = _runner(TOKEN_BODY)
        browser = _LoopbackBrowser()
        printed = []
        path = tmp_path / "auth" / "claude.json"

        token = claude_auth.login(
            token_path=path, opener=browser, curl_runner=runner,
            printer=printed.append)

        assert token["access_token"] == "sk-ant-oat01-minted"
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["access_token"] == "sk-ant-oat01-minted"
        assert path.stat().st_mode & 0o777 == 0o600
        assert browser.status == 200
        assert "complete" in browser.page.lower()
        assert printed and "Open this URL" in printed[0]
        url = printed[0].split("Open this URL to authorize Claude: ", 1)[1]
        state = _query(url)["state"][0]
        padded = state + "=" * (-len(state) % 4)
        assert len(base64.urlsafe_b64decode(padded)) == 32
        argv = runner.seen["argv"]
        body = json.loads(argv[argv.index("--data-binary") + 1])
        assert body["state"] == state
        assert body["code"] == "the-code"
        assert body["redirect_uri"] == _query(url)["redirect_uri"][0]

    def test_state_mismatch_is_rejected_without_exchanging(self, tmp_path):
        def no_exchange(_argv):
            raise AssertionError("the exchange must not run on a bad state")

        browser = _LoopbackBrowser(state_override="wrong-state")
        with pytest.raises(claude_auth.ClaudeAuthError, match="state"):
            claude_auth.login(token_path=tmp_path / "claude.json",
                              opener=browser, curl_runner=no_exchange,
                              printer=lambda _line: None)
        assert browser.status == 400

    def test_error_callback_fails_loudly(self, tmp_path):
        browser = _LoopbackBrowser()

        def opener(url):
            params = _query(url)
            redirect = params["redirect_uri"][0]
            state = params["state"][0]
            try:
                urllib.request.urlopen(
                    f"{redirect}?error=access_denied&state={state}")
            except urllib.error.HTTPError as e:
                browser.status = e.code

        with pytest.raises(claude_auth.ClaudeAuthError, match="access_denied"):
            claude_auth.login(token_path=tmp_path / "claude.json",
                              opener=opener, printer=lambda _line: None)
