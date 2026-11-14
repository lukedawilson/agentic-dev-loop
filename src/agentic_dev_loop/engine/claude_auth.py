"""Claude subscription OAuth PKCE flow (066).

The Claude subscription endpoint consumes a Claude Code OAuth access token.
``claude-login`` runs the same first-party PKCE flow and writes the token
file the engine's ``api_key_secret`` FileProvider reads; it never runs
during a pipeline run. Login only: refresh-on-read is a follow-up plan, so
the refresh token is persisted now and the file schema stays put.

The token exchange uses a system ``curl`` subprocess, not urllib: the
Cloudflare edge in front of platform.claude.com answers urllib with 403
1010 "Access denied based on your browser's signature", while system curl
reaches the API. A missing curl is a loud error, never a urllib fallback.

The default client id is Claude Code's own first-party OAuth app id, the
same borrow pattern the Copilot path uses with OpenCode's app, and is
overridable via ``AGENTIC_DEV_LOOP_CLAUDE_CLIENT_ID``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

AUTHORIZE_URL = "https://claude.com/cai/oauth/authorize"
TOKEN_ENDPOINT = "https://platform.claude.com/v1/oauth/token"
TOKEN_PATH = Path("~/.agentic-dev-loop/auth/claude.json")

CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLIENT_ID_ENV = "AGENTIC_DEV_LOOP_CLAUDE_CLIENT_ID"
SCOPES = (
    "org:create_api_key user:profile user:inference "
    "user:sessions:claude_code user:mcp_servers user:file_upload "
    "user:plugins")

CURL_TIMEOUT = 60
# The token endpoint soft-blocks curl's default user agent (HTTP 429
# rate_limit_error) and accepts this one, the same UA the Claude Code CLI
# sends. Probed live 2026-11-14.
EXCHANGE_USER_AGENT = "axios/1.15.2"
CALLBACK_TIMEOUT = 300
REQUIRED_TOKEN_FIELDS = ("access_token", "refresh_token", "expires_in")


class ClaudeAuthError(RuntimeError):
    """A loud fail-fast auth failure naming the operator's next step."""


def client_id() -> str:
    """The OAuth app id: the overridable env var, else the pinned default."""
    return os.environ.get(CLIENT_ID_ENV) or CLAUDE_CLIENT_ID


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def pkce_pair(rng=os.urandom) -> tuple[str, str]:
    """A 32-byte PKCE verifier and its S256 challenge, base64url unpadded."""
    verifier = _b64url(rng(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def authorize_url(redirect_uri: str, code_challenge: str, state: str,
                  client_id_value: str | None = None) -> str:
    """The captured authorize URL shape, parameter order included."""
    params = [
        ("code", "true"),
        ("client_id", client_id_value or client_id()),
        ("response_type", "code"),
        ("redirect_uri", redirect_uri),
        ("scope", SCOPES),
        ("code_challenge", code_challenge),
        ("code_challenge_method", "S256"),
        ("state", state),
    ]
    query = urllib.parse.urlencode(params, safe=":/")
    return f"{AUTHORIZE_URL}?{query}"


def _run_curl(argv: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=CURL_TIMEOUT, check=False)
    except subprocess.TimeoutExpired as e:
        raise ClaudeAuthError(
            "the Claude token endpoint did not respond within "
            f"{CURL_TIMEOUT}s - retry `agentic-dev-loop claude-login`") from e


def _missing_curl() -> ClaudeAuthError:
    return ClaudeAuthError(
        "curl is required for the Claude token exchange but was not found "
        "on PATH - install curl and run `agentic-dev-loop claude-login` again")


def _parse_response(output: str, returncode: int, stderr: str,
                    token_endpoint: str) -> tuple[str, int]:
    if "\n" not in output:
        detail = output.strip() or stderr.strip() or f"curl exit {returncode}"
        raise ClaudeAuthError(
            f"curl did not return an HTTP status from {token_endpoint}: "
            f"{detail}")
    body, _, status_text = output.rpartition("\n")
    try:
        return body, int(status_text.strip())
    except ValueError as e:
        raise ClaudeAuthError(
            f"could not read the HTTP status from curl output for "
            f"{token_endpoint}: {status_text.strip() or 'empty'}") from e


def exchange_token(code: str, redirect_uri: str, code_verifier: str,
                   state: str, *, token_endpoint: str = TOKEN_ENDPOINT,
                   curl_runner=None) -> dict:
    """Exchange the authorization code for tokens via system curl."""
    body = json.dumps({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id(),
        "code_verifier": code_verifier,
        "state": state,
    }, separators=(",", ":"))
    argv = [
        "curl", "-sS",
        "-X", "POST", token_endpoint,
        "-H", "Content-Type: application/json",
        "-H", "Accept: application/json",
        "-A", EXCHANGE_USER_AGENT,
        "--data-binary", body,
        "-w", "\n%{http_code}",
    ]
    runner = curl_runner or _run_curl
    try:
        completed = runner(argv)
    except FileNotFoundError as e:
        raise _missing_curl() from e
    body_text, status = _parse_response(
        completed.stdout or "", completed.returncode,
        completed.stderr or "", token_endpoint)
    detail = body_text.strip()[:500]
    if status == 429:
        raise ClaudeAuthError(
            "the token endpoint is rate limited (HTTP 429): "
            f"{detail or 'rate_limit_error'}; retry in a few minutes by "
            "running `agentic-dev-loop claude-login` again")
    if status != 200:
        raise ClaudeAuthError(
            f"the token exchange failed (HTTP {status}): "
            f"{detail or 'no response body'}")
    try:
        payload = json.loads(body_text)
    except json.JSONDecodeError as e:
        raise ClaudeAuthError(
            "the token endpoint returned a non-JSON response (HTTP 200): "
            f"{detail}") from e
    if not isinstance(payload, dict):
        raise ClaudeAuthError(
            "the token endpoint returned a JSON value that is not an object")
    missing = [name for name in REQUIRED_TOKEN_FIELDS
               if not _present(payload.get(name))]
    if missing:
        raise ClaudeAuthError(
            "the token endpoint response is missing required field(s): "
            + ", ".join(missing))
    return payload


def _present(value) -> bool:
    if isinstance(value, str):
        return bool(value)
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _expires_in_seconds(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise ClaudeAuthError(
            f"the token endpoint returned a non-numeric expires_in: {value!r}"
        ) from e


def write_token(token: dict, token_path: Path | str | None = None) -> Path:
    """Write access token, refresh token, and absolute expiry with mode 600."""
    access = token.get("access_token")
    refresh = token.get("refresh_token")
    if not isinstance(access, str) or not access:
        raise ClaudeAuthError("the token payload has no access_token")
    if not isinstance(refresh, str) or not refresh:
        raise ClaudeAuthError("the token payload has no refresh_token")
    expires_at = (datetime.now(timezone.utc)
                  + timedelta(seconds=_expires_in_seconds(
                      token.get("expires_in"))))
    payload = {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": expires_at.isoformat(),
    }
    path = (Path(token_path).expanduser() if token_path
            else TOKEN_PATH.expanduser())
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)
    return path


class _CallbackServer(HTTPServer):
    expected_state: str
    result: dict[str, str]


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        server = self.server
        params = urllib.parse.parse_qs(
            urllib.parse.urlsplit(self.path).query)
        state = (params.get("state") or [""])[0]
        if state != server.expected_state:
            server.result["error"] = "state mismatch in the OAuth callback"
            self._respond(400, "Claude login failed: state mismatch. "
                               "Close this window and run "
                               "`agentic-dev-loop claude-login` again.")
            return
        error = (params.get("error") or [""])[0]
        if error:
            server.result["error"] = error
            self._respond(400, f"Claude login failed: {error}. Close this "
                               "window and run `agentic-dev-loop claude-login` "
                               "again.")
            return
        code = (params.get("code") or [""])[0]
        if not code:
            server.result["error"] = "the OAuth callback carried no code"
            self._respond(400, "Claude login failed: no authorization code.")
            return
        server.result["code"] = code
        self._respond(200, "Claude login complete. You can close this window.")

    def _respond(self, status: int, message: str) -> None:
        body = (message + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def login(token_path: Path | str | None = None, *, opener=None,
          token_endpoint: str = TOKEN_ENDPOINT, curl_runner=None,
          printer=print,
          callback_timeout: float = CALLBACK_TIMEOUT) -> dict:
    """Run the PKCE flow and persist the token; never called by a pipeline."""
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(32)
    result: dict[str, str] = {}
    server = _CallbackServer(("127.0.0.1", 0), _CallbackHandler)
    server.expected_state = state
    server.result = result
    port = server.server_address[1]
    redirect_uri = f"http://localhost:{port}/callback"
    url = authorize_url(redirect_uri, challenge, state)
    open_browser = opener or webbrowser.open
    printer(f"Open this URL to authorize Claude: {url}")
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        open_browser(url)
        thread.join(callback_timeout)
    finally:
        server.server_close()
    if thread.is_alive():
        raise ClaudeAuthError(
            "timed out waiting for the browser callback - run "
            "`agentic-dev-loop claude-login` again")
    if result.get("error"):
        raise ClaudeAuthError(
            f"authorization failed: {result['error']} - run "
            "`agentic-dev-loop claude-login` again")
    code = result.get("code")
    if not code:
        raise ClaudeAuthError(
            "the OAuth callback carried no authorization code - run "
            "`agentic-dev-loop claude-login` again")
    token = exchange_token(code, redirect_uri, verifier, state,
                           token_endpoint=token_endpoint,
                           curl_runner=curl_runner)
    write_token(token, token_path)
    return token
