"""GitHub device flow for Copilot and the Copilot model-list probe (058).

Copilot is used as an OpenAI-compatible endpoint on the existing OpenHands
runtime: the device flow produces an OAuth access token that becomes the
LLM's bearer credential. This module is stdlib-only and never runs during a
pipeline run - the ``copilot-login`` subcommand hosts the interactive flow.

The default client id belongs to OpenCode's OAuth app, not this project; it
is the zero-setup default and is overridable via
``AGENTIC_DEV_LOOP_COPILOT_CLIENT_ID`` for production use.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
COPILOT_BASE_URL = "https://api.githubcopilot.com"
TOKEN_PATH = Path("~/.agentic-dev-loop/auth/copilot.json")

DEFAULT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
DEFAULT_SCOPE = "copilot"
CLIENT_ID_ENV = "AGENTIC_DEV_LOOP_COPILOT_CLIENT_ID"
REQUEST_TIMEOUT = 30

COPILOT_HEADERS = {
    "Copilot-Integration-Id": "vscode-chat",
    "Editor-Version": "vscode/1.94.0",
    "Openai-Intent": "conversation-panel",
    "X-GitHub-Api-Version": "2022-11-28",
}


class CopilotAuthError(RuntimeError):
    """A loud fail-fast auth failure naming the operator's next step."""


def client_id() -> str:
    """The OAuth app id: the overridable env var, else the pinned default."""
    return os.environ.get(CLIENT_ID_ENV) or DEFAULT_CLIENT_ID


def _open_json(request: urllib.request.Request, opener=None) -> dict:
    opener = opener or urllib.request.urlopen
    try:
        with opener(request, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise CopilotAuthError(_http_error_message(e)) from e
    except urllib.error.URLError as e:
        raise CopilotAuthError(
            f"could not reach {request.full_url}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise CopilotAuthError(
            f"invalid JSON response from {request.full_url}") from e


def _http_error_message(error: urllib.error.HTTPError) -> str:
    if error.code in (401, 403):
        return (
            f"GitHub rejected the token (HTTP {error.code}). If this token "
            "was created before the 058 Copilot scope fix, delete "
            f"{TOKEN_PATH.expanduser()} and re-run "
            "`agentic-dev-loop copilot-login` (the old token has the wrong "
            "scope).")
    return f"GitHub request failed (HTTP {error.code}): {error.reason}"


def request_device_code(client_id_value: str | None = None,
                        scope: str = DEFAULT_SCOPE, opener=None) -> dict:
    data = urllib.parse.urlencode({
        "client_id": client_id_value or client_id(),
        "scope": scope,
    }).encode("utf-8")
    request = urllib.request.Request(
        DEVICE_CODE_URL, data=data,
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        method="POST")
    return _open_json(request, opener)


def poll_for_token(device_code: str, client_id_value: str | None = None,
                   interval: float = 5, expires_in: float = 900,
                   sleep=time.sleep, opener=None) -> dict:
    """Poll the token endpoint until GitHub issues a token or reports a
    terminal error. ``authorization_pending`` waits; ``slow_down`` grows the
    interval. Anything else (expired, denied) fails fast."""
    data = urllib.parse.urlencode({
        "client_id": client_id_value or client_id(),
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }).encode("utf-8")
    request = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        method="POST")
    wait = max(0.0, float(interval))
    deadline = time.monotonic() + max(0.0, float(expires_in))
    while True:
        payload = _open_json(request, opener)
        if payload.get("access_token"):
            return payload
        error = payload.get("error")
        if error == "authorization_pending":
            pass
        elif error == "slow_down":
            wait += 5
        else:
            raise CopilotAuthError(
                f"device flow failed: {error or 'unknown error'} - run "
                "`agentic-dev-loop copilot-login` again")
        if time.monotonic() >= deadline:
            raise CopilotAuthError(
                "the device code expired - run "
                "`agentic-dev-loop copilot-login` again")
        sleep(wait)


def write_token(token: dict, token_path: Path | str | None = None) -> Path:
    """Write the token JSON atomically-enough with mode 600 from creation."""
    path = Path(token_path).expanduser() if token_path else TOKEN_PATH.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(token, indent=2, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
    os.chmod(path, 0o600)
    return path


def login(token_path: Path | str | None = None, *,
          client_id_value: str | None = None, scope: str = DEFAULT_SCOPE,
          sleep=time.sleep, opener=None, printer=print) -> dict:
    """Run the device flow and persist the token. Prints the verification URL
    and user code; never called by build_llm or a pipeline run."""
    device = request_device_code(
        client_id_value=client_id_value, scope=scope, opener=opener)
    uri = device.get("verification_uri") or device.get(
        "verification_uri_complete")
    code = device.get("user_code", "")
    printer(f"Go to {uri} and enter code: {code}")
    token = poll_for_token(
        device["device_code"],
        client_id_value=client_id_value,
        interval=device.get("interval", 5),
        expires_in=device.get("expires_in", 900),
        sleep=sleep, opener=opener)
    write_token(token, token_path)
    return token


def _is_expired(expires_at) -> bool:
    if isinstance(expires_at, (int, float)):
        return time.time() >= expires_at
    if isinstance(expires_at, str):
        try:
            moment = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        return datetime.now(timezone.utc) >= moment
    return False


def read_token(token_path: Path | str | None = None) -> str:
    """Read the stored access token. Missing, unreadable, expired, or
    token-less files all fail fast with the copilot-login command."""
    path = Path(token_path).expanduser() if token_path else TOKEN_PATH.expanduser()
    if not path.is_file():
        raise CopilotAuthError(
            f"Copilot token not found at {path} - run "
            "`agentic-dev-loop copilot-login`")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise CopilotAuthError(
            f"Copilot token file {path} is unreadable: {e} - run "
            "`agentic-dev-loop copilot-login` again") from e
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise CopilotAuthError(
            f"Copilot token file {path} has no access_token - run "
            "`agentic-dev-loop copilot-login` again")
    if _is_expired(payload.get("expires_at")):
        raise CopilotAuthError(
            f"Copilot token at {path} has expired - run "
            "`agentic-dev-loop copilot-login` again")
    return token


def list_copilot_models(token: str, opener=None) -> list[str]:
    """GET /models for this token's tier (the probe behind copilot-models and
    the smoke pre-flight). Premium models are plan-gated; never assume one."""
    request = urllib.request.Request(
        f"{COPILOT_BASE_URL}/models",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/json",
                 **COPILOT_HEADERS},
        method="GET")
    payload = _open_json(request, opener)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise CopilotAuthError(
            "unexpected response from the Copilot /models endpoint")
    models = [item.get("id") for item in data
              if isinstance(item, dict) and isinstance(item.get("id"), str)]
    if not models:
        raise CopilotAuthError(
            "the Copilot /models endpoint listed no models for this token's "
            "tier - run `agentic-dev-loop copilot-login` and retry")
    return models
