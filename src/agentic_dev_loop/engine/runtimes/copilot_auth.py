"""GitHub device flow for the copilot harness (unit 10).

Stdlib urllib only. The token is cached at
``~/.agentic-dev-loop/auth/copilot.json`` (mode 600, outside the workspace),
never printed and never accepted from pipeline yaml. A missing or expired
token re-runs the flow; an aborted flow fails loud.
"""

from __future__ import annotations

import json
import os
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
# The public GitHub Copilot device-flow client id (pinned constant; a test
# asserts this exact value so a silent change cannot ship).
COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
DEFAULT_SCOPE = "read:user"
AUTH_DIR = Path(".agentic-dev-loop") / "auth"
AUTH_FILE = AUTH_DIR / "copilot.json"
_EXPIRY_SKEW_SECONDS = 60


class CopilotAuthError(RuntimeError):
    """The device flow could not complete; the message names the remedy."""


@dataclass(frozen=True)
class CopilotToken:
    access_token: str
    token_type: str = "bearer"
    scope: str = ""
    expires_at: float | None = None

    def valid(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return True
        return (now if now is not None else time.time()) < (
            self.expires_at - _EXPIRY_SKEW_SECONDS)

    def redacted(self) -> str:
        return "***"


def auth_path() -> Path:
    return Path.home() / AUTH_FILE


def load_token(path: Path | None = None) -> CopilotToken | None:
    target = path or auth_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        return None
    return CopilotToken(
        access_token=token,
        token_type=str(data.get("token_type", "bearer")),
        scope=str(data.get("scope", "")),
        expires_at=data.get("expires_at"),
    )


def save_token(token: CopilotToken, path: Path | None = None) -> Path:
    target = path or auth_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": token.access_token,
        "token_type": token.token_type,
        "scope": token.scope,
        "expires_at": token.expires_at,
    }
    target.write_text(json.dumps(payload), encoding="utf-8")
    try:
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - non-POSIX filesystems
        pass
    return target


def _post_json(url: str, fields: dict[str, str],
               timeout: float = 30.0) -> dict:
    body = urllib.parse.urlencode(fields).encode("ascii")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise CopilotAuthError(f"GitHub device flow request failed: {exc}") from exc


def request_device_code(post=_post_json) -> dict:
    payload = post(DEVICE_CODE_URL,
                   {"client_id": COPILOT_CLIENT_ID, "scope": DEFAULT_SCOPE})
    if "device_code" not in payload or "user_code" not in payload:
        raise CopilotAuthError(
            "GitHub device flow refused: "
            f"{payload.get('error_description', payload.get('error', 'no code'))}")
    return payload


def poll_for_token(device_code: str, interval: int = 5,
                   expires_in: int = 900, post=_post_json,
                   sleep=time.sleep, clock=time.monotonic) -> CopilotToken:
    deadline = clock() + max(expires_in, 1)
    delay = max(interval, 1)
    while clock() < deadline:
        payload = post(TOKEN_URL, {
            "client_id": COPILOT_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        error = payload.get("error")
        if not error and payload.get("access_token"):
            return CopilotToken(
                access_token=payload["access_token"],
                token_type=str(payload.get("token_type", "bearer")),
                scope=str(payload.get("scope", "")))
        if error == "authorization_pending":
            sleep(delay)
            continue
        if error == "slow_down":
            delay += 5
            sleep(delay)
            continue
        raise CopilotAuthError(
            "GitHub device flow was refused: "
            f"{payload.get('error_description', error or 'unknown error')}")
    raise CopilotAuthError(
        "GitHub device flow timed out before authorisation completed; "
        "re-run to start a new flow")


def ensure_token(path: Path | None = None, *, print_fn=print,
                 post=_post_json, **poll_kwargs) -> CopilotToken:
    """Return a valid cached token, or run the device flow and store one."""
    cached = load_token(path)
    if cached is not None and cached.valid():
        return cached
    device = request_device_code(post)
    print_fn(
        "GitHub Copilot authorisation required: open "
        f"{device.get('verification_uri', 'https://github.com/login/device')} "
        f"and enter code {device['user_code']}")
    token = poll_for_token(device["device_code"],
                           interval=int(device.get("interval", 5)),
                           expires_in=int(device.get("expires_in", 900)),
                           post=post, **poll_kwargs)
    save_token(token, path)
    return token


def redactions_for(token: CopilotToken | None) -> tuple[str, ...]:
    return (token.access_token,) if token is not None else ()
