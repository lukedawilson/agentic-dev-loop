# Plan 054: per-step model specification via a named llm registry

> **Status: COMPLETED**

## Scope

Add per-step model specification. `pipeline.yaml` gains an optional
`llms:` map of named LLM profiles (each a full `LlmConfig`: model,
base_url, api_key_env, timeout, reasoning_effort), and each agent or
brief step may name a profile with `llm: <name>`. The existing `llm:`
block is unchanged and remains the default. Reasoning effort (047)
keeps its resolution chain, with the effective profile as the middle
link; the tail changes: absent effort now means reasoning is off
instead of the SDK default "high" (D6). The registry and resolution
are provider-agnostic: any openai-compatible endpoint expressible as
an `LlmConfig` works, and reasoning passback is gated on the
configured reasoning level, never on a model-name list (D8), so Kimi
and other compatible providers need no engine edits.
Consumer benefit: multi-provider pipelines (for example a
different-provider review lane beside the DeepSeek default) without
duplicating model/endpoint/key per step and without touching engine
code.

## Context

Verified facts:

- `LlmConfig` (core/config.py lines 583-593) is `extra="forbid"` with
  model (required), base_url, api_key_env (required), timeout, and
  reasoning_effort (047). A reusable profile is exactly this shape.
- `PipelineConfig` (core/config.py, 596+) holds one `llm: LlmConfig`
  (line 608) and `steps: list[StepConfig]` (min_length=1). Cross-field
  checks are `model_validator(mode="after")` classmethods
  (`_locked_requires_lockfiles`, `_mounts_avoid_state_dir`); the load
  happens once at line 863 (`PipelineConfig.model_validate`), so any
  cross-check added there fails at `--dry-run` / run start.
- `StepConfig._check_shape` (mode="after") already rejects per-type
  field mismatches (timeout requires command, custom_prompt requires
  agent, secrets rejected on action). Restricting a new step field by
  kind follows the same precedent.
- `build_llm` (engine/llm.py lines 51-67) resolves effort as
  `reasoning_effort if not None else cfg.reasoning_effort` (047 D2/D7),
  fail-fasts when `os.environ` lacks `cfg.api_key_env`, and constructs
  the SDK LLM from model/api_key/base_url/timeout. Today it passes
  effort only when set, so an unconfigured pipeline inherits the SDK
  Field default "high" (SDK llm.py lines 356-361) and chat_options
  forwards it whenever LiteLLM reports the param supported (chat_options
  lines 36-40). 054 flips that: build_llm always passes reasoning_effort,
  None when unset, so absent effort forwards nothing (D6).
- `Runtime` (engine/stages.py lines 113-130) carries one `llm: "LLM"`;
  `run_agent` (lines 179-190) rebuilds only when
  `step.reasoning_effort` is set (047 D7), else reuses the shared
  instance.
- graph.py line 184 builds the single LLM at run start:
  `Runtime(..., llm=build_llm(cfg.llm), ...)`.
- `child_env` (engine/proc.py) whitelists only `cfg.llm.api_key_env`
  into command/action subprocesses (NFR-1). LLM calls are host-side, so
  named-profile keys never belong in child envs; sandbox-side access to
  a profile key remains the existing `secrets:` mechanism.
- Reasoning passback in the SDK is gated on a model-name list, not on
  message content: `get_features(model)` substring-matches the id
  against `SEND_REASONING_CONTENT_MODELS` (model_features.py lines
  169-174: "kimi-k2-thinking", "kimi-k2.5", "openrouter/minimax-m2",
  "deepseek/deepseek-reasoner"), and `format_messages_for_llm` (llm.py
  lines 1384-1404) passes the resulting `send_reasoning_content` flag
  into every `to_chat_dict`, which includes the field only when the
  flag and the message's own reasoning_content are both present
  (message.py lines 314-316, commented "Required for model like
  kimi-k2-thinking"). The list is a conservative request-shape guard,
  not validation: the SDK only sends the field to models it believes
  need it. The consequence: Kimi thinking models pass back today only
  because the list happens to name them, and any thinking model the
  list misses by substring drops its history reasoning and 400s
  multi-turn (the 007 bug class). This plan removes the name gate:
  engine passback is gated on the configured reasoning level instead
  (D8), so the SDK list is never consulted on the engine's path.
- What the list gates, exactly: `get_features` derives one boolean from
  it (`send_reasoning_content`, model_features.py line 186), consumed
  at exactly one call site, llm.py line 1393, which hands it to each
  `to_chat_dict`; there it gates only whether the message's own
  reasoning_content is written into the outgoing request (message.py
  lines 314-316). No other SDK behavior consults this list: effort
  forwarding, string serializer, prompt cache, and stop words use
  other lists. The critic path hardcodes the flag False
  (critic.py line 70) and never reads the list. Emptying the list
  would not remove the gate: `model_matches` returns False for every
  model on an empty list, so the flag defaults False and passback
  breaks for everyone, including DeepSeek and Kimi today.
- Contract (D6, D8): reasoning is opt-in via `reasoning_effort`. Set
  (low/medium/high/xhigh): the param is forwarded per LiteLLM support
  and reasoning passback is on. Absent or "none": nothing is forwarded
  and reasoning is stripped from outgoing requests. Posture: we
  validate structurally-broken config at load; we cannot validate
  model-specific invalid config (a thinking model pointed at a step
  with no effort, or with "none", is exactly such config: it still
  reasons and its API requires the passback we strip). It cannot be
  rejected at load (detecting it would require the model-name lists
  this plan removes), but it is detected the moment the next request
  is serialized: gate off while history carries reasoning_content
  raises an actionable engine error before the doomed request is sent
  (D12). That is the documented "crap in, crap out" contract.
- Compat shim 1 (engine/llm.py lines 11-19) appends "deepseek-v4-pro"
  to that list. Shim 2 (lines 21-48) monkeypatches
  `LLM.format_messages_for_llm` to redistribute reasoning across the
  assistant tool-call messages of a split turn, borrowing the turn's
  last reasoning and preserving empty-string presence; it reads the
  raw message attributes, so its repair is already model-agnostic,
  but it only touches assistant tool-call messages.
- OpenHands SDK pinned ~=1.17.0. SDK LLM takes LiteLLM-style model ids
  and base_url is optional for well-known providers, so a first-party
  profile can omit it.
- The shipped pipeline `agentic-dev-loop/pipeline.yaml` and README.md
  are tooling paths (plan 020): the loop commit does not sweep them.

## Decisions

| # | Decision | Proposal | Answer |
|---|---|---|---|
| D1 | Registry: `PipelineConfig.llms: dict[str, LlmConfig] = {}`, optional and purely additive; each entry is a strict `LlmConfig` (`extra="forbid"` inherits) | Top-level `llms:` map of named profiles | APPROVED |
| D2 | Default: the existing `llm:` block is untouched and remains the default; every current pipeline.yaml loads byte-identically | Keep `llm:` block as-is | APPROVED |
| D3 | Step field: `StepConfig.llm: str \| None = None` names a profile; the profile is the whole override (model, endpoint, key, effort default move together; no partial merges) | Step `llm: <profile-name>` | APPROVED |
| D4 | Step-type restriction: `llm:` allowed only on agent and brief steps (the only LLM-using kinds); command and action steps reject it in `_check_shape` (precedent: timeout requires command) | Reject on command/action | APPROVED |
| D5 | Load-time cross-checks in a `PipelineConfig` mode="after" validator: (a) any step naming an unknown profile raises; (b) a profile literally named "default" is rejected (the `llm:` block already is the default). Both fail at dry-run, never mid-run | Unknown refs and "default" name rejected at load | APPROVED |
| D6 | Resolution chain: effective cfg = `llms[step.llm]` if set, else the `llm:` block; effort = step value, else effective profile's value, else off: nothing is forwarded and passback is off (054 supersedes 047's "SDK default high" tail). When a profile is named the whole config moves together | Profile name selects the whole config; absent effort = off | APPROVED |
| D7 | Eager fail-fast: at run start graph.py builds the default LLM (as today) plus one LLM per profile referenced by any step, so a missing api_key_env for a referenced profile fails at run start, not mid-run. Declared-but-unreferenced profiles are shape-validated at load; their keys are not checked. `Runtime` gains `llms: dict[str, LLM]` keyed by profile name | Build referenced profiles at run start | APPROVED |
| D8 | Reasoning passback, provider-generic and config-gated: remove shim 1 entirely and make shim 2 the config-gated serializer. Gate = the LLM instance's engine-configured reasoning_effort set and not "none". Gate on: reasoning_content is written whenever history carries it (the SDK handles the listed models; the extended repair covers plain assistant messages and split tool-call turns for every model). Gate off: reasoning is stripped from the request; if history carries
  reasoning, the D12 error fires instead of sending a doomed request. The engine never consults the SDK name list, and every openai-compatible thinking model works with no per-model engine edit. COMPAT-SHIMS.md drops the shim 1 entry and rewrites shim 2's entry and removal criterion; pinning tests and canaries updated | Config-gated passback, shim 1 removed, no name list | APPROVED |
| D9 | Rollout: no pipeline sets `llms:` or a step `llm:` at implementation time; one observed multi-profile run proves it end to end before any shipped pipeline adopts it (047 D6 pattern). The shipped pipeline gains a commented-out example only | Observed run gates adoption | APPROVED |
| D10 | Docs: README gains an `llms:` example, a step `llm:` example, an adaptation-checklist line, the note that profile keys are host-side (sandbox access still goes through `secrets:`), and the reasoning contract: absent effort = reasoning off; `reasoning_effort: none` = per-step opt-out; a thinking model without effort (or with "none" where the provider ignores it) is invalid config that breaks, crap in crap out | README examples, checklist, reasoning contract | APPROVED |
| D11 | Tests (TDD, red first): config (map round-trips, defaults {}, unknown ref rejected, "default" name rejected, command/action steps reject, step field round-trips), llm (build from profile cfg, resolution with effective cfg, unset effort yields reasoning_effort None so the SDK "high" default cannot engage, gate on/off/"none" serialization, gate off with reasoning in history raises the D12 error), stages/graph (eager referenced-profile build, missing key fails at start, unreferenced key not checked, per-step selection) | Red first, then green | APPROVED |
| D12 | Useful error, not provider 400: with the gate off, if any message in the conversation carries reasoning_content, the serializer raises a clear engine error before the doomed request is sent, naming the model, the step's effective reasoning config, and the fixes (set reasoning_effort, use "none" only where the provider honors it, or use a non-thinking model). No provider-400 text matching anywhere | Fail fast in the serializer with an actionable message | APPROVED |

## Out of scope

- Profile inheritance or partial merging: a step cannot override only
  the effort or only the key on top of a profile; the name is the whole
  override (D3).
- `viz/server.py` llm display: no llm/model display exists today.
- Changing the shipped pipeline to a multi-model setup (D9 gates that).
- Automatic retry of provider 400s (007 deferred follow-up, still
  open).
- Other SDK name-gated feature lists (FORCE_STRING_SERIALIZER_MODELS,
  PROMPT_CACHE_MODELS, RESPONSES_API_MODELS): SDK behaviors the engine
  does not consult for passback; untouched by this plan.
- Detecting "thinking model without reasoning_effort" at load time:
  impossible without the model-name lists this plan removes; the
  documented crap-in/crap-out contract covers it (D6, D8, D10).

## Changes (file by file)

1. `src/agentic_dev_loop/core/config.py`: add `llms: dict[str,
   LlmConfig] = {}` to `PipelineConfig` (D1); add `llm: str | None =
   None` to `StepConfig` (D3) with a non-empty check and the
   kind restriction in `_check_shape` (D4); add a
   `model_validator(mode="after")` for unknown references and the
   "default" name ban (D5).
2. `src/agentic_dev_loop/engine/llm.py`: build_llm always passes
   reasoning_effort, None when unset, overriding the SDK "high"
   default (D6); shim 2 becomes the config-gated serializer (gate
   on: extend the repair to plain assistant messages carrying their
   own reasoning_content; gate off: strip reasoning from the request,
   and raise the D12 error when history carries reasoning_content);
   remove shim 1 (D8).
3. `src/agentic_dev_loop/engine/stages.py`: `Runtime` gains `llms:
   dict[str, LLM] = field(default_factory=dict)`; `run_agent` selects
   `self.llms.get(step.llm, self.llm)` and rebuilds only when
   `step.reasoning_effort` is set, parameterized by the effective cfg
   (D6, D7).
4. `src/agentic_dev_loop/engine/graph.py`: after building the default
   LLM, eagerly build each profile referenced by any step and pass the
   map into `Runtime` (D7).
5. `COMPAT-SHIMS.md`: remove the shim 1 entry; rewrite the shim 2
   entry and its removal criterion (D8); keep the upgrade procedure.
6. `tests/test_config.py`: D11 config tests.
7. `tests/test_llm.py`: D11 llm tests, including the pinning tests
   rewritten for config-gated passback (gate on/off/"none") and the
   shim-1 removal (D8).
8. `tests/test_stages.py`, `tests/test_graph.py`: D11 stage/graph
   tests (profile selection, eager build, fail-fast).
9. `tests/test_sdk_compat.py`: update canaries for the shim-1 removal
   and the extended shim 2 (D8).
10. `README.md`: D10. Tooling path (plan 020): this deliverable will
    not be swept by the loop commit and will need a human commit.
11. `agentic-dev-loop/pipeline.yaml`: commented-out `llms:` and step
    `llm:` example, no live values (D9). Tooling path (plan 020): this
    deliverable will not be swept by the loop commit and will need a
    human commit.

## Risks

- Provider-400 class (007): a profile naming a thinking model the SDK
  list does not know would fail multi-turn without D8. Mitigated by
  D8's config-gated passback, which removes the name gate entirely
  and is pinned by tests (D11).
- Config-gated passback overrides the SDK's conservative name list:
  reasoning_content is sent to any model whose step configured an
  effort level, and stripped otherwise. Models that never emit
  reasoning are unaffected, and a thinking model's own API requires
  the passback by construction (the 007 lesson). The residual risk is
  a provider that emits reasoning_content yet rejects it on a plain
  assistant message (a final answer in multi-turn history): today's
  green runs only exercise the tool-call repair, so the D9
  observed-run gate is the proof for that case.
- New contract regression: an existing pipeline that runs a thinking
  model with no reasoning_effort anywhere works today (the SDK "high"
  default is forwarded and the shims pass reasoning back) and after
  054 raises the D12 engine error at the second call: a clear message
  naming the model and the fix, not a provider 400 and not silent
  quality degradation (the repo's no-silent-degradation rule). This
  is the deliberate crap-in/crap-out contract (absent effort =
  reasoning off) and is the one behavior change for existing files;
  the fix is a one-line `reasoning_effort:` on the default block.
- Shim-1 removal must stay behavior-preserving for deepseek-v4-pro
  when an effort level is configured: the extended shim 2 is a
  superset of the old two-shim behavior for every model, listed or
  not; the pinning tests assert this (D11).
- Key sprawl: each profile carries its own api_key_env. D7 makes a
  referenced profile's missing key fail at run start; an
  unreferenced profile with a typo'd env var fails only when a step
  first adopts it (same posture as the existing `llm:` block, which
  dry-run also does not env-check).
- Lane quality: moving a lane to a weaker model degrades its gate
  (review especially). Mitigated by D3 whole-profile granularity and
  the D9 observed-run gate; README guidance keeps review on the
  strongest model.
