# 059 - Copilot Anthropic models collapse on agent review (DONE)

> **Status: COMPLETED (resolved 2026-11-08)**

## The problem

The Copilot endpoint's Anthropic models (claude-opus-5.5, claude-sonnet-5.5)
collapse on agent steps: the agent ends its turn with a tool-less narration
and no tool calls, so the SDK conversation FINISHES immediately and the step
produces nothing usable. Observed shape, reproduced 4/4 times across two
smoke runs (27d01d65f858, 91fc26500788) and confirmed by live probes through
the engine's exact path:

- review step (openai/claude-opus-5.5, reasoning_effort xhigh, the dogfood
  review agent): first turn is "I'll start by checking the workspace state
  and the files in scope." with zero terminal requests, then FINISHED.
- An engine nudge ("Your previous response made no tool calls and inspected
  nothing...") was sent and ignored: the second turn was another tool-less
  narration ("I'll check the workspace before giving a verdict..."), zero
  terminal requests, still no verdict. The model will not act even when
  directly ordered to inspect.
- A dev-step collapse (claude-sonnet-5.5, flash profile) was diagnosed
  earlier and fixed by the own-artifact filter: on retry the dev step's own
  previous final response was being injected under "## Stage artifacts",
  and the dogfood dev.agent.md rule 3 ("If the touchpoints are already at
  target state, do not re-implement...") made the model read its own last
  message as evidence the work was done. That fix is real but does not
  explain the review collapse, which happens on a first attempt with no
  own-artifact section.
- claude-opus-4.8 is separately broken on the non-streamed path the engine
  uses: the gateway returns two choices and the litellm-surfaced one has
  finish_reason tool_calls with tool_calls=None.

So the whole Anthropic family at the Copilot gateway is unusable for agent
steps as-is: opus-4.8 mis-shapes tool calls, opus-5.5/sonnet-5.5 refuse to
act. Two attempted engine mitigations (zero-tool-call nudge, unclear-verdict
self-retry) were probed after shipping and reverted; the self-retry worked
mechanically (retry_target stayed on the failing step) but only re-ran the
same broken review.

## What we did instead (2026-11-06)

- Reverted the nudge and self-retry round (git checkout 8ea4f07 of the six
  touched files; the own-artifact filter from the earlier round stays).
- Re-pinned the smoke copilot models to kimi-k3 for both pro and flash:
  - probed through litellm (the engine's path): clean single-choice
    non-streamed tool calls, tool roundtrip OK, effort low/high accepted,
    medium/xhigh hard-400 ("supported values: [low high max]").
  - the only usable Kimi tier entry: kimi-k3-base and kimi-k3-copilot
    return 400 model_not_supported.
  - bare "kimi-k3" makes litellm refuse outright ("LLM Provider NOT
    provided"); the openai/ prefix is required, same as the claude pins.
  - every smoke step runs reasoning_effort high (brief and review used
    medium/xhigh under Anthropic; both are rejected by kimi).
- Second re-pin (2026-11-06): kimi-k3 failed end-to-end (run
  c278697c9263): the Copilot gateway silently drops ARRAY-encoded
  tool-result content, so the dev agent worked blind. The wire shape is
  fixed in the engine (force_string_serializer policy,
  src/agentic_dev_loop/engine/provider_wire.py), and the pins are now
  gpt-5.6-luna (pro, Responses API) and gpt-4.1 (lite, no
  reasoning_effort: any effort hard-400s on gpt-4.1). Engine-path probe
  of both pins: gpt-4.1 makes clean tool calls with string tool content;
  gpt-5.6-luna acts on the review message and returns a real verdict at
  xhigh.
- Brief-step adjustment (same day): the smoke brief step now runs the
  default pro model at reasoning_effort medium. No-effort gpt-4.1 cannot
  satisfy the mechanical 041 coverage gate (run 9d3f0fca20ea oscillated
  15 -> 7 -> 15 missing anchors across three attempts and burned the
  pipeline attempt budget). Engine-path probe of the fix: gpt-5.6-luna
  at medium covers all 23 anchors on the first attempt. dev stays on
  flash (no step effort); review stays pro at xhigh.

## Root cause and fix (2026-11-08)

Q1 answered. The collapse was not instruction-following drift: the Copilot
gateway splits an Anthropic model's non-streamed chat response into
MULTIPLE choices, a narration-only choice (finish_reason tool_calls with an
empty tool_calls list) plus one choice per real tool call. The openhands
SDK's LLM.completion reads only choices[0], which was the narration choice,
so every agent turn surfaced with zero tool calls and the step collapsed to
a phantom completion. The exact 059 shape, explained at the wire.

- Fix: a WireAdapter.merge_choices flag (set for Copilot only) merges the
  split choices at the _transport_call boundary, before the SDK reads
  choices[0]; validate_response_shape now scans every choice and only
  fails when no choice carries tool calls (commit 6fc8988, merged
  108cc29, shipped in v0.12.2).
- The merge also covers the claude-opus-4.8 two-choice mis-shape, though
  opus-4.8 has not been re-pinned or re-probed end-to-end.
- Everything else from the earlier rounds stays: the own-artifact filter,
  the force_string_serializer policy, the openai/ model-name prefix.

## Re-verification (2026-11-08)

The smoke pins were restored to the Anthropic pair (openai/claude-opus-5.5
pro, openai/claude-sonnet-5.5 flash) on branch smoke-copilot-anthropic, and
the full smoke ran against the fixed engine from source: run bdbd5597cbd6,
last checkpoint 2026-11-08T11:33:10Z, status success, steps dev/test/review
all pass. The review step, the exact step that collapsed 4/4 before,
returned a real NEEDS CHANGES verdict with substantive findings on attempt
1 and a real APPROVED verdict on attempt 2. The Anthropic pins stay.

Q2 and Q3 are superseded: the gpt pair (gpt-5.6-luna / gpt-4.1) was an
interim pin set and is no longer the smoke configuration; both Copilot
smoke pins now ride the chat path, so the Responses/chat wire-split
question no longer applies to the smoke. (gpt-5.6-luna on the Responses
path and no-effort gpt-4.1 on the chat path remain valid, previously
probed options for users who want them; see docs/setup.md.)

## Evidence

- Probe scripts (engine path, real token): review-message probe reproduced
  the collapse with the nudge firing and being ignored
  (2 send_message calls, 2 runs, 0 terminal requests, no verdict).
- kimi-k3 litellm probe: tool call + roundtrip OK, effort matrix as above.
- Checkpoint decodes of runs 27d01d65f858 and 91fc26500788: review None ->
  unclear note -> retry, every attempt tool-less.
