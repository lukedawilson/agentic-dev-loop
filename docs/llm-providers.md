# LLM providers

Every step, profile, and gate works identically on either provider style:
API-key endpoints speak the OpenAI-compatible wire format, and the Claude
subscription runs on litellm's native Anthropic path.

| Provider | Setup |
| --- | --- |
| **Any OpenAI-compatible endpoint** | API key via `api_key_env` + `base_url` in the `llm:` block: OpenAI, DeepSeek, or any OpenAI-compatible API. |
| **GitHub Copilot subscription** | `agentic-dev-loop copilot-login` once, then point the `llm:` block at `https://api.githubcopilot.com` with the file-backed token. |
| **Claude subscription** | `agentic-dev-loop claude-login` once, then point the `llm:` block at `https://api.anthropic.com` with the file-backed token. |

## OpenAI-compatible endpoints

```yaml
# OpenAI-compatible endpoint (any provider)
llm:
  model: openai/deepseek-v4-pro
  base_url: https://api.deepseek.com
  api_key_env: OPENAI_API_KEY
```

- `api_key_env` names the host env var the engine reads at run start; a
  missing key fails fast, naming it.
- `base_url` can point at any OpenAI-compatible API. The `llm:` block takes
  exactly one of `api_key_env` or `api_key_secret`; see
  [secrets](pipeline.md#secrets) for the file-backed shape.
- `reasoning_effort` is opt-in and forwarded per
  [the reference](pipeline.md#reasoning-effort-opt-in). A thinking model
  without an effort level is invalid config and fails fast.

## GitHub Copilot subscription

Copilot runs as an OpenAI-compatible endpoint on the existing runtime - no
new driver, no MCP, no subprocess. Log in once, then point an `llm:` block at
the endpoint:

```bash
agentic-dev-loop copilot-login    # device flow; writes the token (mode 600)
agentic-dev-loop copilot-models   # the models this token's tier can use
```

```yaml
llm:
  model: openai/claude-opus-5.5    # pro default
  base_url: https://api.githubcopilot.com
  api_key_secret:
    from: file
    key: ~/.agentic-dev-loop/auth/copilot.json
    json_key: access_token
  headers:
    Copilot-Integration-Id: vscode-chat
    Editor-Version: vscode/1.94.0
    Openai-Intent: conversation-panel
llms:
  flash:
    model: openai/claude-sonnet-5.5  # flash profile; no step effort
    base_url: https://api.githubcopilot.com
    api_key_secret:
      from: file
      key: ~/.agentic-dev-loop/auth/copilot.json
      json_key: access_token
    headers:
      Copilot-Integration-Id: vscode-chat
      Editor-Version: vscode/1.94.0
      Openai-Intent: conversation-panel
```

Working models and what they accept:

- `claude-opus-5.5` (pro) and `claude-sonnet-5.5` (flash) are the current
  working set. The gateway splits an Anthropic model's non-streamed chat
  response into multiple choices; the engine merges them before the SDK
  reads them, so these pins work. Effort: opus-5.5 accepts `medium`/`xhigh`;
  sonnet-5.5 accepts none or `medium`/`high`.
- Also usable: `gpt-5.6-luna` (routes through the Responses API, which the
  SDK switches to automatically; accepts `xhigh`) and `gpt-4.1` (chat path,
  clean tool calls, but hard-400s on ANY `reasoning_effort`).
- Avoid: `gpt-4o-mini` (Copilot caps its chat context at 12288 tokens),
  `claude-opus-4.8` (not currently supported), and `kimi-k3` (the Copilot
  gateway silently drops the tool results the SDK sends for it, leaving the
  agent blind).

Copilot specifics:

- The `openai/` prefix on the model names is required. litellm routes a
  bare `claude-*` name through its Anthropic provider path, which speaks
  Anthropic's wire format and dies at the gateway with "missing required
  Authorization header"; a bare `kimi-k3` makes litellm refuse outright.
  The prefix forces the OpenAI-compatible route: Bearer auth,
  chat/completions, bare model name in the body.
- The three headers above are accepted at `/models` and
  `/chat/completions`. Do not add `X-GitHub-Api-Version: 2022-11-28`; the
  endpoint rejects it with 400 `invalid apiVersion` at both endpoints.
- The token is the Copilot subscription credential; the pinned OAuth client
  id belongs to OpenCode's app and is overridable with
  `AGENTIC_DEV_LOOP_COPILOT_CLIENT_ID`.
- A token with the old `read:user` scope is refused (401/403): delete it and
  re-run `agentic-dev-loop copilot-login`. The fail-fast names this fix.
- `model:` stays required. Confirm the models with `copilot-models` before a
  run.

## Claude subscription

A Claude Pro or Max subscription runs on Anthropic's own endpoint through
litellm's native Anthropic path - no new driver, no MCP, no subprocess. The
credential is the Claude Code OAuth token: subscription metering applies to
it, while an API key bills at pay-as-you-go rates.
`agentic-dev-loop claude-login` runs the same OAuth flow and writes the
token file the engine reads (mode 600):

```bash
agentic-dev-loop claude-login
```

It opens the browser for the authorize step, serves a loopback callback,
exchanges the authorization code, and writes
`~/.agentic-dev-loop/auth/claude.json` with the access token, the refresh
token, and the expiry. Re-run it when the token expires.

```yaml
llm:
  model: claude-opus-5-5       # pro default
  base_url: https://api.anthropic.com
  api_key_secret:
    from: file
    key: ~/.agentic-dev-loop/auth/claude.json
    json_key: access_token
llms:
  flash:
    model: claude-sonnet-5-5   # flash profile; no step effort
    base_url: https://api.anthropic.com
    api_key_secret:
      from: file
      key: ~/.agentic-dev-loop/auth/claude.json
      json_key: access_token
```

Working models and what they accept:

- `claude-opus-5-5` (pro default) and `claude-sonnet-5-5` (flash) are the
  current working set. Effort: both accept `low`/`medium`/`high`/`xhigh`/
  `max`.

Claude specifics:

- No `headers:` block: litellm detects the OAuth token prefix and sends
  Bearer auth plus the required API version and beta headers automatically.
- The `base_url` has no `/v1` suffix: litellm appends the `/v1/messages`
  path itself, and a `/v1` value would 404 on a doubled path.
- Bare model names are required (`claude-opus-5-5`, not
  `openai/claude-opus-5-5`). The `openai/` prefix is a Copilot-gateway
  routing hint; here the native Anthropic path is the point, and a bare
  name is what routes it there.
- The engine sends Anthropic's billing header automatically as the first
  system block of every request. Nothing to configure.
- The OAuth client id is Claude Code's own first-party app id, overridable
  with `AGENTIC_DEV_LOOP_CLAUDE_CLIENT_ID` if Anthropic rotates it.
- The token endpoint can rate limit the exchange; `claude-login` reports
  it and asks you to retry in a few minutes.
- The token expires. When the engine's fail-fast names the credential file,
  re-run `agentic-dev-loop claude-login`.
- `model:` stays required.
