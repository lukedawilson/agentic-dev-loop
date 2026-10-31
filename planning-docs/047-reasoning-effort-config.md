# Plan 047: reasoning_effort as an opt-in configurable pipeline and per-step field

> **Status: PROPOSED** (flips to COMPLETED after implementation lands)

## Scope

Add `reasoning_effort` as an opt-in field on the pipeline's `llm` block
and on each step. The Oct 31 post-mortem of the consumer run showed LLM
turn latency, not tool work, dominates dev-lane time (26 terminal actions
carrying ~35s of tool execution inside ~35.5 min of wall time), and
ranked this as lever (3). The reasoning bug history (plans 007 and 032,
the two compat shims) constrains the design: no default may move, and no
pipeline sets a value until one observed run proves it. Per-step override
is now in scope (D7): lane needs differ, and the review lane must be able
to hold a higher effort than the rest (D8).

## Context

Verified facts:

- `build_llm` (engine/llm.py lines 51-60) constructs the SDK LLM with
  model/api_key/base_url/timeout only; `reasoning_effort` is never set.
- `LlmConfig` (core/config.py lines 510-516) is `extra="forbid"` with
  model/base_url/api_key_env/timeout, so pipeline.yaml cannot express the
  field today: a YAML key would be rejected at load.
- The SDK (openhands-sdk 1.17.x, llm.py lines 356-362) declares
  `reasoning_effort: Literal["low","medium","high","xhigh","none"]` with
  default `"high"`. Every run today therefore runs at "high".
- The SDK forwards the value to the API only when
  `get_features(model).supports_reasoning_effort` is true
  (options/chat_options.py lines 36-41), which consults LiteLLM's
  supported OpenAI params. Probe on the installed SDK:
  deepseek-v4-pro -> True, so a configured value would be forwarded.
- Bug history, plan 007: 2026-09-14 incident, DeepSeek transient 400
  (`reasoning_content ... must be passed back`) inside the checks agent
  after a full day of green runs; fixed by crash-resume parking (007) and
  the two reasoning_content passback shims (engine/llm.py lines 11-48),
  registered in COMPAT-SHIMS.md with pinning tests
  (`test_llm.py::TestReasoningContentShim`, `test_sdk_compat.py` version
  guard and canaries). SDK pinned `~=1.17.0`. 007's follow-up list
  deferred automatic transient-error retry: a rejected reasoning_effort
  value would surface as exactly that provider-400 class, with no
  auto-retry.
- Bug history, plan 032: after a green suite the dev agent spent five
  further steps (~140M input tokens) re-reading files and reasoning about
  gates it cannot run offline; produced stop-when-green (032), removal now
  plan 044. That waste is prompt/cap territory, not an effort knob; this
  plan is a latency dial, not a correctness fix.
- Interaction with shim 2 is safe by construction: the repair writes
  reasoning_content only when it has something to borrow; a mode that
  stops emitting reasoning yields nothing to borrow and nothing is
  injected. Effort is fixed per LLM instance, so mixed-mode history
  cannot occur within a run.
- The `llm` block is pipeline-global: one block serves brief, dev, qa and
  review. A lane-level knob is therefore needed to give review a
  different effort from the rest (D7).

## Decisions

| # | Decision | Proposal | Answer |
|---|---|---|---|
| D1 | Field shape: `LlmConfig.reasoning_effort: Literal["low","medium","high","xhigh","none"] \| None = None`. Unset means the field is not passed at all: the SDK default (high) applies and today's behavior is byte-identical. Full SDK literal, passthrough semantics (the engine is provider-generic) | Opt-in optional, full SDK literal | PENDING |
| D2 | Passthrough: `build_llm` adds `reasoning_effort` to the constructor kwargs only when it is not None. `timeout` keeps its existing explicit-pass behavior | Conditional kwarg | PENDING |
| D3 | Validation: pydantic Literal rejects unknown values at config load, so a bad value fails at `--dry-run` / run start, never mid-run. No provider-specific value restriction: whether DeepSeek accepts every literal value for deepseek-v4-pro (notably `"none"`) is unproven and documented as operator risk; D6 covers the safe path | Literal validation only | PENDING |
| D4 | Tests (TDD, red first): (a) config: reasoning_effort accepted, defaults to None, unknown value raises ValidationError; (b) llm: reasoning_effort omitted from kwargs when unset, passed through when set | Red first, then green | PENDING |
| D5 | Docs: README `llm` block example gains an optional commented `reasoning_effort` line and a note that the value is forwarded only for models the SDK/LiteLLM mark as supporting it, and that an unsupported value fails at the first LLM call | README only | PENDING |
| D6 | Rollout: no pipeline sets a value at implementation time. The engine change requires a reinstall of the pipx package before any run can see it. Before the consumer pipeline adopts a value, one observed run at the D8 lane defaults must prove it end to end: lower per-turn latency, green suite, no 400s. Only then does the consumer pipeline set a value | Observed run gates adoption | PENDING |
| D7 | Per-step override: `StepConfig.reasoning_effort: Literal["low","medium","high","xhigh","none"] \| None = None`. Resolution order at LLM construction: the step's value if set, else the `llm` block's value (D1), else the SDK default ("high"). Unset anywhere means untouched today-behavior | Per-step opt-in, falls back to the block | APPROVED |
| D8 | Lane defaults shipped as guidance, not enforced values: brief=medium (digest and classify, no design work); dev=high (hold the line: dev caught a real plan bug and a real sandbox triage this unit; revisit only if plans demonstrably tighten); qa=medium (classification discipline must stay reliable: misclassifying an environmental failure as a code defect sends dev on a futile cycle); review=xhigh (shortest lane, highest stakes: a missed detection sails past the last gate into commit) | Recommended per-lane table in README | APPROVED |

## Out of scope

- Automatic retry of LLM API errors (007 deferred follow-up).
- Setting `reasoning_effort` in the consumer pipeline (D6 gates that
  later).
- No COMPAT-SHIMS.md entry: this is a passthrough of a public SDK field,
  not a monkeypatch seam.

## Changes (file by file)

1. `src/agentic_dev_loop/core/config.py`: add `reasoning_effort` to
   `LlmConfig` (D1) and to `StepConfig` (D7); import `Literal` from
   typing.
2. `src/agentic_dev_loop/engine/llm.py`: `build_llm` conditional
   passthrough (D2); the per-step resolution (step value, else block
   value, else unset) happens where the per-step LLM is constructed (D7),
   with a comment noting the shims above stay untouched.
3. `tests/test_llm.py`: two tests in `TestBuildLlm` (D4b) plus the
   per-step fallback-chain tests (D7).
4. `tests/test_config.py`: three tests (D4a) plus StepConfig accept /
   default-None / reject (D7).
5. `README.md`: `llm` block example and note (D5), a per-step
   `reasoning_effort` example, and the D8 lane-defaults table.

## Risks

- Provider-400 class (007): a configured value the upstream API rejects
  fails at the first LLM call with no auto-retry. Mitigated by opt-in
  (D1), load-time validation (D3) and the observed-run gate (D6).
- Review-lane quality: a pipeline-global low effort would apply to
  review's adversarial analysis. Mitigated by the D7 per-step override
  and the D8 guidance (review=xhigh); adopt only what the observed run
  proves.
- Benefit is unmeasured: the Oct 31 waste was addressed by the prompt
  changes (prior-attempt residue, read discipline) and cap/nudge; effort
  tuning only cuts per-turn generation time. The observed run is the
  measurement.
