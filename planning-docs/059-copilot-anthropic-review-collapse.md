# 059 - Copilot Anthropic models collapse on agent review (NOT READY)

> **Status: NOT READY**

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

## Open questions (this plan is NOT READY)

1. Why do the Anthropic models at the Copilot gateway produce tool-less
   narration turns on agent-style messages when the same models answer
   direct tool prompts correctly? Gateway-side prompt/parameter handling,
   model instruction-following drift, or something in the message
   composition the probes have not isolated yet. Until this is understood,
   the Anthropic pins must not come back.
2. Does the gpt pair (gpt-5.6-luna pro / gpt-4.1 lite) hold up over the
   full smoke pipeline (brief digest quality, dev implementation, review
   verdict discipline)? The engine-path probes passed; the end-to-end
   smoke run is the next gate.
3. gpt-5.6-luna rides the Responses API while gpt-4.1 rides the chat
   path: two different wire paths in one pipeline. The
   force_string_serializer policy is chat-path-only; the Responses path
   proved clean without it. Long-term stability of that split is
   unverified.

## Evidence

- Probe scripts (engine path, real token): review-message probe reproduced
  the collapse with the nudge firing and being ignored
  (2 send_message calls, 2 runs, 0 terminal requests, no verdict).
- kimi-k3 litellm probe: tool call + roundtrip OK, effort matrix as above.
- Checkpoint decodes of runs 27d01d65f858 and 91fc26500788: review None ->
  unclear note -> retry, every attempt tool-less.
