# 058 - Copilot as endpoint

> **Status: COMPLETED**

## Background

Unit-10 (multi-provider runtime) is WONTSHIP: its code was reverted (commit
526c802) because the Copilot driver was built on openai-codex, which has no
Copilot-subscription auth surface at all (verified against openai-codex
0.163.0, the latest release: the app-server login types are apiKey | chatgpt
| chatgptDeviceCode | chatgptAuthTokens | amazonBedrock only). The Claude path
stays WONTSHIP too (units 4 and 10).

Copilot can still serve the original goal - developers with Copilot
subscriptions get the loop with kernel-level sandboxing and viz - not as a
harness driver but as an OpenAI-compatible endpoint on the EXISTING OpenHands
runtime: api.githubcopilot.com/chat/completions with the device-flow OAuth
token as the bearer credential. The community has validated this pattern for
custom OpenAI-compatible providers, and Pydantic AI ships it first-party
(GitHubCopilotOAuthFlow + GitHubCopilotProvider; the provider's api_key IS
the OAuth access_token). No new runtime, no MCP, no subprocess.

## Verified facts

- Endpoint: https://api.githubcopilot.com (OpenAI-compatible
  /chat/completions; GET /models returns the model list). Plan-specific hosts
  exist (api.individual.githubcopilot.com); we target the general one.
- Auth: `Authorization: Bearer <ghu_ OAuth access token>` from the GitHub
  device flow. No OpenAI API key is used. Metering = the Copilot subscription
  chat quota (same bucket as IDE Copilot Chat), NOT a separate meter.
- Required headers: Copilot-Integration-Id, Editor-Version, Openai-Intent.
  Working values, probe-validated at the real-token gate on 2026-11-06
  (accepted at /models and /chat/completions): Copilot-Integration-Id:
  vscode-chat, Editor-Version: vscode/1.94.0, Openai-Intent:
  conversation-panel. NOT X-GitHub-Api-Version: the original plan listed
  X-GitHub-Api-Version: 2022-11-28 as a fourth required header, but the
  live endpoint rejects it with 400 "invalid apiVersion" at both /models
  and /chat/completions, so it was removed everywhere (see aidlc-docs/
  audit.md).
- openhands-sdk 1.17.0 `LLM` has an `extra_headers: dict[str, str] | None`
  field ("Optional HTTP headers to forward to LiteLLM requests",
  sdk/llm/llm.py line 285). Header forwarding is a config passthrough; no SDK
  patching and no shim.
- Scope bug (from unit-10): the device flow requested scope read:user, but
  third-party Copilot API consumers need an OAuth app with scope copilot,
  otherwise the token 401s/403s. The re-added flow requests scope: copilot.
- Model gating: third-party OAuth integrations see a plan/integration-gated
  model list. Community evidence: base models (gpt-4.1, gpt-4o, gpt-4o-mini)
  work end-to-end including tool calls; premium models vary by plan. We claim
  base models only until the real-token probe proves otherwise.
- Client id: the pinned default Iv1.b507a08c87ecfe98 is OpenCode's OAuth app,
  not ours. Keep it as the zero-setup default, make it overridable, document
  the ownership caveat.

## Design

**D1 - Copilot is an llm-profile shape, not a driver.** No provider key, no
runtime, no MCP, no subprocess. The OpenHands runtime, the sandbox, and the
viz are untouched. Copilot appears only as an `llm:` (or `llms:`) block
pointing at the endpoint.

**D2 - LlmConfig gains `headers: dict[str, str] = {}`.** Forwarded verbatim
to `LLM(extra_headers=...)` by build_llm. Default empty = zero change for
every existing pipeline (backward compat).

**D3 - File-backed secret source.** Today KNOWN_SECRET_PROVIDERS = {"env"}
and the api key comes only from `api_key_env`. Changes:

- KNOWN_SECRET_PROVIDERS gains "file". A FileProvider joins the provider
  registry in secrets.py and resolves a locator path to the value of a JSON
  key (or the whole file when no key is given).
- SecretSpec gains an optional `json_key: str | None = None` (file provider
  only; rejected with any other provider). The locator (`key`) for the file
  provider is the JSON file path; values never appear in yaml, same as today.
- LlmConfig: `api_key_env: str | None = None` (was required) plus a new
  `api_key_secret: SecretSpec | None = None`, with an exactly-one validator:
  neither set or both set fails load with a message naming the fields. Every
  existing pipeline sets api_key_env and is unaffected.
- build_llm resolves the key from either mechanism; a missing value fails
  fast naming the mechanism (same class of error as today's missing-env
  error). The resolved value joins the redaction set wherever secret values
  are redacted today.

**D4 - `copilot-login` subcommand hosts the device flow.** copilot_auth.py is
re-added from the unit-10 git history (commit 4a00257; it was ~167 lines,
self-contained, stdlib-only) with two changes:

- DEFAULT_SCOPE "read:user" -> "copilot" (the scope bug fix).
- The pinned client id becomes overridable via
  AGENTIC_DEV_LOOP_COPILOT_CLIENT_ID (default Iv1.b507a08c87ecfe98, documented
  as OpenCode's OAuth app - ours for zero setup, override for production).

The engine hosts the flow (requirements Q3=A): `agentic-dev-loop
copilot-login` prints the code + URL, polls the token endpoint, writes
~/.agentic-dev-loop/auth/copilot.json (mode 600). build_llm never runs the
flow; a missing/expired token fails fast with "run agentic-dev-loop
copilot-login". Operators who ran the unit-10 flow must DELETE the old token
file first (it holds a read:user-scoped token; re-running copilot-login
overwrites it with the copilot scope).

**D5 - `model:` stays required; a probe validates it.** No runtime model
magic: the copilot profile pins `model:` (a base model, gpt-4o-mini-class, is
the safe default). A stdlib urllib helper `list_copilot_models(token)` hits
GET /models and is used by a `copilot-models` subcommand and by the smoke
pre-flight to confirm the pinned model exists for this token's tier. Premium
models are NOT claimed until the real-token probe shows them.

**D6 - Fail fast.** Missing token file, expired token, wrong-scope 401/403,
unreadable token file, model not in the /models list, and both/neither api
key mechanisms set are all loud errors naming the fix (see Risks for the
401/403 scope hint).

**D7 - No new dependencies.** Device flow and probe use stdlib urllib; no
SDK, no extras.

**D8 - Claude stays WONTSHIP.** Nothing here revives the Claude path (units
4 and 10 remain WONTSHIP; the runtime abstraction stays in git history).

**D9 - The smoke test supports three llm backends via config dirs.**
run_smoke.py stays llm-agnostic (it copies the config dir's pipeline.yaml
verbatim); backend support ships as committed config dirs:

> **Reversed 2026-11-06 (user directive).** Provider and plan directory are
> independent: run_smoke.py takes a required `--provider`
> (`openhands|copilot`) and injects the matching llm block from
> `PROVIDER_BLOCKS` into a provider-free template, rejecting templates that
> still carry their own `llm:` block. `tests/smoke/copilot-endpoint/` and
> `tests/smoke/openai-endpoint/` were deleted;
> `tests/smoke/viz-provider/` is the single provider-free plan dir, and
> `run-smoke.sh <provider> <config-dir>` wraps the invocation. A template
> carrying an `llm:` block is now a pre-flight error, never silently
> overridden.

- tests/smoke/viz-provider/ - DeepSeek (existing default; api_key_env
  OPENAI_API_KEY, base_url https://api.deepseek.com)
- tests/smoke/copilot-endpoint/ - the Copilot subscription endpoint (the
  D1-D7 shape: base_url https://api.githubcopilot.com, api_key_secret file
  secret, Copilot headers)
- tests/smoke/openai-endpoint/ - standard OpenAI (base_url
  https://api.openai.com, api_key_env OPENAI_API_KEY, model gpt-4o-mini)

Each dir carries pipeline.yaml + agents/dev.agent.md + plan.md (the viz
keep-alive plan; the copilot and openai copies get a one-line preamble).

**D10 - run_smoke.py gains a minimal llm pre-flight.** Before creating the
worktree, the script scans the config dir's pipeline.yaml for the llm
block's api_key_env / api_key_secret declarations (regex, stdlib-only): an
api_key_env whose env var is unset fails fast with the export command; an
api_key_secret whose file is missing fails fast with the copilot-login
command. A missing credential then fails in seconds instead of after a
worktree + full deps step, with per-backend guidance.

### Copilot profile example

```yaml
llm:
  model: gpt-4o-mini
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

(Header values probe-validated 2026-11-06 against the live endpoint; no
X-GitHub-Api-Version, which the live endpoint rejects. The model may be
updated by the same gate. reasoning_effort is omitted = off, the default.)

## Scope

- src/agentic_dev_loop/core/config.py - KNOWN_SECRET_PROVIDERS, SecretSpec
  json_key, LlmConfig headers + api_key_env/api_key_secret + validator.
- src/agentic_dev_loop/engine/secrets.py - FileProvider.
- src/agentic_dev_loop/engine/llm.py - build_llm key resolution + extra_headers.
- src/agentic_dev_loop/engine/copilot_auth.py - re-added from unit-10 history
  with scope fix + client id override.
- src/agentic_dev_loop/engine/cli.py - copilot-login + copilot-models
  subcommands.
- tests/ - test_config.py, test_secrets.py, test_llm.py, copilot auth + CLI
  tests (mocks only at the device-flow/probe HTTP boundary), plus unit tests
  for the run_smoke.py pre-flight (fixture yaml strings, no network).
- tests/smoke/run_smoke.py - llm pre-flight (D10).
- tests/smoke/copilot-endpoint/ - smoke config dir for the Copilot endpoint.
- tests/smoke/openai-endpoint/ - smoke config dir for standard OpenAI.
- No changes to graph.py, run_pipeline, the sandbox, or the viz. The dogfood
  pipeline (agentic-dev-loop/pipeline.yaml) stays on DeepSeek.

## Tasks (TDD, red-green-refactor)

1. Config: headers passthrough, api_key_env optional + api_key_secret +
   exactly-one validator, SecretSpec json_key rules, file provider
   registration. Tests first.
2. secrets.py FileProvider (JSON read, json_key selection, fail-fast
   messages) + tests.
3. build_llm: secret-spec key resolution + extra_headers forwarding, asserted
   on the constructed LLM object (no network) + tests.
4. copilot_auth.py re-add (from 4a00257) with the two changes + tests
   (device-flow HTTP mocked).
5. cli.py copilot-login + copilot-models subcommands + tests.
6. list_copilot_models probe helper + tests (HTTP mocked).
7. Red: run_smoke.py pre-flight tests (missing env var, missing token file,
   both present). Green: implement D10 in run_smoke.py.
8. tests/smoke/copilot-endpoint/ + tests/smoke/openai-endpoint/ assets
   committed (pipeline.yaml per backend, agents/dev.agent.md, plan.md
   copies).
9. Docs: README/docs note, aidlc-state.md note that 058 is now written,
   audit entry.

## Verification

Non-token gates (runnable without a subscription):

- Full suite green including the new tests; dogfood pipeline behavior
  byte-identical (rule zero: every existing config still works).
- test_repo_ascii + em-dash check on all new files.
- Smoke script pre-flight: a config dir with an unset api_key_env and one
  with a missing token file both fail fast with the right guidance.
- `./.venv/bin/python tests/smoke/run_smoke.py tests/smoke/viz-provider`
  still passes (DeepSeek profile; the restructured script).

Real-token gates (user-run):

1. Standard OpenAI smoke:
   `./.venv/bin/python tests/smoke/run_smoke.py tests/smoke/openai-endpoint`
   - preconditions: Docker, .venv, OPENAI_API_KEY exported.
2. Copilot device flow: `agentic-dev-loop copilot-login` -> token written
   (delete any old unit-10 token file first, wrong scope).
3. Copilot probe: `agentic-dev-loop copilot-models` -> model list for this
   tier; pin the model and confirm the header values with one
   chat/completions round trip through build_llm.
4. Tool-call flow: one terminal round trip through the SDK against the
   Copilot endpoint (confirm the endpoint accepts tool calls end-to-end).
5. Copilot smoke:
   `./.venv/bin/python tests/smoke/run_smoke.py tests/smoke/copilot-endpoint`
   - deps builds the sandbox venv, the dev agent implements the viz
   keep-alive change against the Copilot endpoint, the test step runs the
   keepalive selection, the keep-alive line appears, viz /topology responds,
   SIGINT exits, RESULT: PASS.

## Open questions

- O1: Which model does the example profile pin? Recommendation: gpt-4o-mini
  (base model, community-verified with OAuth tokens); premium models only
  after gate 3. ANSWERED: A, pin gpt-4o-mini (user: "A", 2026-11-06).
  REVERSED 2026-11-06 (user directive): the first real smoke run proved
  gpt-4o-mini unusable for the agent loop (Copilot caps its chat context at
  12288 tokens; the dev agent overran it and the run died). Live-endpoint
  probes then re-pinned the example config to claude-opus-5.5 (default) and
  claude-sonnet-5.5 (flash profile), per the validated-model rule. Probe
  record (all 2026-11-06, real token, the three validated headers):
  - claude-opus-5.5: clean single-choice non-streamed tool calls, effort
    medium/xhigh accepted, 65K-token context OK - pro pick.
  - claude-sonnet-5.5: clean non-streamed tool calls, effort medium/high
    accepted, tool roundtrip OK - flash pick.
  - claude-opus-4.8: REJECTED - two choices on the non-streamed path the
    engine uses; the litellm-surfaced choice has finish_reason tool_calls
    with tool_calls=None.
  - kimi-k3: REJECTED - effort values [low, high, max] only; the engine's
    effort set is low/medium/high/xhigh/none.
  - gpt-4o/gpt-4.1: REJECTED - hard-400 on any reasoning_effort.
  Wire-format note (2026-11-06): the pins carry an `openai/` prefix. The
  first mirror smoke run died on the brief step with "missing required
  Authorization header": litellm routes a bare `claude-*` name through its
  Anthropic provider path (x-api-key wire format), which the gateway
  rejects. The `openai/` prefix forces the OpenAI-compatible route (Bearer
  auth, chat/completions, bare name in the body); probe-reproduced:
  `litellm.completion(model="claude-sonnet-5.5", api_base=copilot)` fails,
  the same call with `openai/claude-sonnet-5.5` returns OK. A bare
  `kimi-k3` fails the same way ("LLM Provider NOT provided").
  REVERSED AGAIN 2026-11-06 (user directive): the Anthropic pins collapsed
  on the review step (tool-less narration, no verdict, ignored even a
  direct nudge to inspect; runs 27d01d65f858 and 91fc26500788, probe
  reproduced). The pins are now kimi-k3 for both pro and flash, every
  step at reasoning_effort high (kimi accepts [low, high, max] only).
  Details: planning-docs/059.
  REVERSED AGAIN (second time) 2026-11-06 (user directive): kimi-k3
  proved unusable in agent loops: the Copilot gateway silently drops
  ARRAY-encoded tool-result content, which the openhands SDK sends for
  kimi by default (probe-bisected: string tool content is seen by the
  model, array tool content is not; see the force_string_serializer
  policy in src/agentic_dev_loop/engine/provider_wire.py). The pins are
  now gpt-5.6-luna (pro, Responses API) and gpt-4.1 (lite, no
  reasoning_effort; gpt-4.1 hard-400s any effort). Engine-path probe of
  both pins (2026-11-06): gpt-4.1 makes clean tool calls with string
  tool content on the chat path, and gpt-5.6-luna acts on the review
  message and returns a real verdict at xhigh. Details: planning-docs/059.
  Adjustment same day: the smoke brief step now runs the default pro
  model at reasoning_effort medium instead of flash - no-effort gpt-4.1
  cannot satisfy the mechanical 041 coverage gate (run 9d3f0fca20ea
  burned all three attempts at brief); dev stays on flash with no
  effort, review pro at xhigh.
  REVERSED (third time) 2026-11-08 (user directive): with unit-11's wire
  hardening in place the Anthropic pins were restored - openai/
  claude-opus-5.5 (pro) and openai/claude-sonnet-5.5 (flash). The
  gateway splits an Anthropic model's non-streamed chat response into
  multiple choices (a narration-only choice plus one choice per tool
  call) and the SDK read only choices[0]; the new WireAdapter
  .merge_choices flag (Copilot-only) merges them at the _transport_call
  boundary before the SDK reads the response (commit 6fc8988). The full
  smoke then passed end-to-end: run bdbd5597cbd6, status success, the
  review step returned real verdicts (NEEDS CHANGES with substantive
  findings on attempt 1, APPROVED on attempt 2). The gpt pair was
  interim only. Details: planning-docs/059.
- O2: Client id override mechanism. Recommendation: the
  AGENTIC_DEV_LOOP_COPILOT_CLIENT_ID env var (host-side, never in yaml);
  alternative: a field in the llm block (but the client id is an operator
  credential concern, not a per-pipeline shape). ANSWERED: env var, accepted
  (user: "sure", 2026-11-06).
- O3: Is the copilot-models subcommand worth a permanent CLI surface, or
  should the probe helper stay internal to the smoke pre-flight and a
  one-off verification script? Recommendation: keep the subcommand (small,
  useful for diagnosing tier gating). ANSWERED: keep it (user: "yes",
  2026-11-06).

## Risks

- Model tier gating: third-party OAuth apps see gated model lists. Mitigated:
  base models only, probe before claiming, pinned model validated.
- Header values: litellm forwards extra_headers verbatim; if the endpoint
  rejects the reported set, tune Editor-Version/Openai-Intent at gate 3.
  (Names are verified; values are the community-reported set.)
- Client id ownership: the pinned default is OpenCode's OAuth app. If GitHub
  revokes or rate-limits it, operators override with their own OAuth app
  (env var, per O2).
- Token lifetime: the unit-10 flow observed expires_at null; if GitHub ever
  issues expiring tokens, re-run copilot-login (the fail-fast message names
  it).
- Quota exhaustion mid-run: surfaces as ordinary provider errors and retries
  like any other provider; no special handling.
- Wrong-scope token confusion: the 401/403 fail-fast message must point at
  deleting the old token file and re-running copilot-login (D4).
