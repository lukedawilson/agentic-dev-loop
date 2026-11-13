# 066 - Claude subscription login subcommand

> **Status: COMPLETED**

## Background

The Claude subscription provider is shipped (05e688d, 842c65d): an
`llm:` block pointing at https://api.anthropic.com with the token in
`~/.agentic-dev-loop/auth/claude.json`, the billing-header system prefix
injected by the engine, and litellm OAuth auto-detect handling the bearer
auth. Probe gates P1-P3 and P5 are closed (auth, headers, models, effort,
thinking passback).

The credential bootstrap is manual today: the operator runs `claude login`
once (Claude Code's own CLI), extracts the OAuth access token out of the
macOS Keychain, and writes the JSON file by hand. This plan replaces that
with an owned `claude-login` subcommand that runs the same OAuth PKCE flow
Claude Code itself uses, and writes the file.

Scope decision (operator, 2026-11-13): **login only, no refresh-on-read.**
An expired token fails fast naming `claude-login`, the same policy the
Copilot path already has. Refresh is a follow-up plan; the refresh token is
persisted today so the file schema does not change when it lands.

This does not revive the unit-4/unit-10 driver surface (still WONTSHIP per
058 D8); it is a login flow for an endpoint profile, same as copilot-login.

## Blocking bug found on plan load

Found 2026-11-13 running this plan through the loop: config load dies with
`ValueError: step 'dev': 'custom_prompt' file not found: <the dev prompt
text>` before any step runs. Root cause: `_custom_prompt_looks_like_path`
(core/config.py, 040/D2) treats a value as path-shaped when it has a slash,
a backslash, or a non-empty `Path(value).suffix`. pathlib is version
dependent here: on Python 3.12 `Path("prose (AUDIT GATE).").suffix` is
`""`, but on Python 3.14 it is `"."` (the trailing sentence period counts
as the extension). The dev step's AUDIT GATE prompt ends in a period, so on
3.14 it is misclassified as a filename and fails hard. The repo venv and CI
are 3.12, which is why no test caught it; the operator's pipx install is
3.14.

The fix must be version independent: strip trailing dots before the suffix
test, so a lone trailing period never counts as an extension while real
extensions (`.md`, `.py`) keep the hard-error contract.

**Task 0 (TDD):** RED test in `tests/test_config.py`: a prose custom_prompt
ending in `.` resolves as literal text, and the path-shaped cases
(`qa-plan.md`, `tests/qa-plan`) still hard-error. Note: the red is only
observable on Python 3.14; on 3.12 the new test passes pre-fix (expected,
documented in the test comment). GREEN: change the suffix test in
`_custom_prompt_looks_like_path` to use `Path(value.rstrip(".")).suffix`.
Operator workaround until the fix lands: remove the trailing period from
the dev custom_prompt in `agentic-dev-loop/pipeline.yaml` (line 107,
`pattern (AUDIT GATE).` -> `pattern (AUDIT GATE)`), which lets config load
on 3.14 today.

## Verified facts

All live-probed 2026-11-13 against the real endpoints unless noted.

| Fact | Evidence |
|---|---|
| Authorize URL shape | Captured from Claude Code 2.1.323 via an open-browser shim during a real login: `https://claude.com/cai/oauth/authorize?code=true&client_id=<UUID>&response_type=code&redirect_uri=http://localhost:<port>/callback&scope=org:create_api_key+user:profile+user:inference+user:sessions:claude_code+user:mcp_servers+user:file_upload+user:plugins&code_challenge=<S256>&code_challenge_method=S256&state=<32-byte base64url>` |
| client_id | UUID `9d1c250a-e61b-44d9-88ed-5944d1962f5e`, embedded twice in the CLI binary. The `claude-cli` string OpenCode uses FAILS the claude.ai front-end today: `client_id: Input should be a valid UUID`. The UUID is required. |
| Exchange endpoint + body | `POST https://platform.claude.com/v1/oauth/token`, body `{grant_type: "authorization_code", code, redirect_uri, client_id, code_verifier, state}`. No scope, no client_secret, no code_challenge_method in the body. Captured from the CLI's real exchange. |
| Transport | System curl passes; Python urllib is blocked at the Cloudflare edge with 403 1010 "Access denied based on your browser's signature" on platform.claude.com. curl probe: GET 200 on the host, 405 on the token endpoint (reached the API). The exchange MUST use curl. |
| Loopback callback | stdlib `http.server` on 127.0.0.1, random port; callback carries code+state; state verified; a 200 text page is returned to the browser. Worked in probe. |
| Token endpoint rate limit | 429 `rate_limit_error` on the exchange, persisting 15+ minutes during heavy account usage (probe day, while a smoke run burned quota). The subcommand must surface 429 with retry-later guidance. |
| Token semantics | From the keychain pairs minted by Claude Code's own login: access `sk-ant-oat01-` and refresh `sk-ant-ort01-`, separate tokens with separate expiry; scopes include `user:sessions:claude_code`. The engine consumes the access token only. |
| Engine integration | Zero engine change: `resolve_api_key` already resolves the `api_key_secret` FileProvider and fails fast naming the file; `build_llm` already injects the billing prefix for the Claude adapter. |

## Not yet observed (pre-ship gate)

The exchange 200 response field names from OUR OWN flow. Every probe
attempt returned 429 (rate limited); we never saw a success body. Standard
OAuth fields are assumed: `access_token`, `refresh_token`, `expires_in`,
`scope`, `token_type`. This is a probe-validated repo, so shipping waits on
one successful exchange that confirms the field names and that the minted
token works on the engine path (billing block + litellm OAuth auto-detect).
The CLI's own exchange succeeded with the same client_id and body shape, so
this is a confirmation gate, not an open design question.

## Design

**D1 - Owned PKCE flow, same shape as copilot-login.** New subcommand
`agentic-dev-loop claude-login`: build the authorize URL, open the browser,
serve the loopback callback, exchange the code via curl, write
`~/.agentic-dev-loop/auth/claude.json` mode 600 with
`{"access_token": ..., "refresh_token": ..., "expires_at": ...}`. The engine
reads `access_token` only (unchanged `json_key`).

**D2 - Login only.** No refresh-on-read in this plan. Expired token fails
fast naming `claude-login` (existing behavior, message updated to name the
subcommand). `refresh_token` and `expires_at` are persisted now so the
follow-up plan changes code, not schema.

**D3 - Transport split.** stdlib `urllib`/`http.server`/`webbrowser` for the
authorize URL, callback, and browser open. The token exchange uses a system
curl subprocess (argument list, no shell). This is a documented exception to
the copilot-login D7 stdlib-only pattern, justified by the Cloudflare 403
1010 probe result above. Missing curl is a loud error naming curl, never a
silent urllib fallback.

**D4 - Pinned client_id, overridable.** `CLAUDE_CLIENT_ID` constant with the
UUID default, overridable via `AGENTIC_DEV_LOOP_CLAUDE_CLIENT_ID` (mirrors
`AGENTIC_DEV_LOOP_COPILOT_CLIENT_ID`). Ownership caveat documented: this is
Claude Code's own first-party OAuth app id, the same borrow pattern the
Copilot path uses with OpenCode's app.

**D5 - Loud failure modes.** Exchange 429 prints the endpoint's message plus
"the token endpoint is rate limited; retry in a few minutes". Any other
non-200 prints the response error. A non-JSON 200 is a loud error, not a
best-effort parse.

**D6 - Module layout mirrors copilot_auth.** New
`src/agentic_dev_loop/engine/claude_auth.py` with `client_id()`,
`authorize_url(...)`, `exchange_token(...)` (injectable token_endpoint and
curl runner for tests), `write_token(...)`, `login(...)`, and
`ClaudeAuthError`. CLI wiring mirrors `_copilot_login_parser` /
`_run_copilot_login` with a `--token-file` argument and dispatch entry.

## Tasks (TDD, red -> green -> refactor)

0. **Blocking bug fix (see "Blocking bug found on plan load")**: red test in
   `tests/test_config.py` for the trailing-period custom_prompt; green via
   `Path(value.rstrip(".")).suffix` in `_custom_prompt_looks_like_path`;
   keep the path-shaped hard-error cases green.
1. RED: `tests/test_claude_auth.py` (mirrors tests/test_copilot_auth.py):
   - authorize URL pinned param by param: `code=true` first, UUID client_id,
     response_type, redirect_uri with the port, the exact 7-scope string,
     S256 challenge, 32-byte state.
   - exchange body `{grant_type, code, redirect_uri, client_id,
     code_verifier, state}` and curl argv (list args, no shell, `-w` status
     capture).
   - token file write: schema, mode 600, `--token-file` path override.
   - 429 -> ClaudeAuthError with retry guidance; other non-200 -> error with
     the response message; non-JSON 200 -> error.
   - missing curl -> loud error naming curl.
   - callback handler: state verification, 200 page.
   - CLI: `agentic-dev-loop claude-login --token-file` runs the flow
     (network boundary stubbed per mock policy), exit codes, default path
     `~/.agentic-dev-loop/auth/claude.json`.
2. GREEN: implement `claude_auth.py` + `cli.py` wiring. Unit tests use a
   local `http.server` as the token endpoint (real curl, real HTTP, local)
   so the exchange path is exercised without the network.
3. Docs: `docs/llm-providers.md` Claude section replaces the keychain
   extraction recipe with `claude-login`; README mentions it; the smoke
   preflight hint for the claude provider names `claude-login`.
4. Refactor: no dead code, ASCII only, no em dash.

## Verification

- Full suite green (`pytest tests/ -m "not docker and not integration"
  --ignore=tests/smoke`).
- Pre-ship gate (the "not yet observed" item): one real login run when the
  token-endpoint rate limit has slid, confirming the 200 field names and
  that the minted token completes an engine call (the `-k model` viz smoke
  pattern or a direct build_llm completion).
- Manual: after `claude-login`, the file exists mode 600 with the three
  keys, and `./run-smoke.sh claude tests/smoke/viz-model` starts without the
  extraction step.

## Risks

- **200 shape unobserved**: gated pre-ship as above; the parser tolerates
  unknown extra fields and verifies the three required keys.
- **Rate limit during login**: cannot be auto-waited around (persists many
  minutes); the retry-later message is the mitigation.
- **client_id rotation**: Anthropic could rotate the CLI's client_id; the
  env override is the escape hatch, same accepted risk as the Copilot
  client_id.
- **curl dependency**: required on the operator host; macOS and Windows
  (curl.exe bundled since 1803) ship it, Linux has it universally. Named in
  the error message when absent.
