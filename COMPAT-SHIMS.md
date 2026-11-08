# SDK compat shims

Import-time monkeypatches in `src/agentic_dev_loop/engine/llm.py` that paper over
OpenHands SDK bugs. Each targets a specific SDK version; each has a removal
criterion and a pinning test. They are engine-side by design - they survive
SDK reinstalls - but they go stale silently when the SDK changes, so the repo
guards both directions:

- **SDK upgraded outside the tested range** →
  `tests/test_sdk_compat.py::test_openhands_sdk_within_tested_range` fails
  loudly. Re-verify every shim below against the new SDK before updating the
  pin.
- **SDK self-heals** (bug fixed in a patch) →
  `tests/test_sdk_compat.py::test_canary_sdk_still_drops_reasoning_on_split_tool_call_turn`
  fails loudly. Remove the now-dead shim per its removal criterion.

## Registry

| Shim | SDK bug | Failure without shim | Removal criterion | Pinning test |
|---|---|---|---|---|
| wire-shape validation - `LLM.completion` / `LLM.responses` wrappers `_wire_checked_completion` / `_wire_checked_responses` (`engine/llm.py`, openhands-sdk 1.17.x) | the SDK surfaces a response whose `finish_reason` is `tool_calls` with an empty `tool_calls` list (the opus-4.8 mis-shape) and sends an outgoing history carrying an assistant tool call with no matching tool result (the kimi array-drop blindness) - the engine must validate both shapes at the adapter boundary (FR-11.4 D-1/D-2) | a malformed response is read as a narration turn and the step collapses to a phantom completion; a dangling tool call is sent and the model never answers it | the SDK validates both shapes itself (surfaced finish/tool-call parity plus outgoing request pairing), or `LLM.completion` / `LLM.responses` stop being the single request entry point the engine can wrap | `tests/test_sdk_compat.py::test_canary_sdk_wire_checks_still_wrap_completion_and_responses` and `::test_canary_responses_wrapper_still_validates_the_surfaced_shape`; D-1/D-2/D-3 behaviour pinned in `tests/test_unit11.py::TestWireShape` and `tests/test_provider_wire.py` |
| Copilot multi-choice merge - `LLM._transport_call` wrapper `_merge_checked_transport_call` (`engine/llm.py`, openhands-sdk 1.17.x) | the Copilot gateway splits an Anthropic model's turn across multiple choices (a narration-only choice with `finish_reason='tool_calls'` and empty tool_calls, plus one choice per tool call), and the SDK's `LLM.completion` reads only `choices[0]` (`llm.py`: `first_choice = resp["choices"][0]`) | the agent reads the narration-only choice, so the tool calls in the other choices are dropped and the step collapses to a phantom completion (the 059 collapse) or the D-1 validator raises WireShapeError | the SDK merges multiple choices itself (or the gateway stops splitting them), so `LLM.completion` surfaces one choice carrying narration plus all tool calls | `tests/test_sdk_compat.py::test_canary_sdk_transport_call_merge_still_wrapped`; merge behaviour pinned in `tests/test_unit11.py::TestWireShape` |
| reasoning passback serializer - `LLM.format_messages_for_llm` override (`engine/llm.py`) | SDK 1.17.x splits a multi-tool-call turn into one assistant message per call and attaches reasoning_content only to the first (`openhands/sdk/agent.py:595` - `reasoning_content=... if i == 0 else None`); `Message.to_chat_dict` also drops empty-string reasoning (`message.py:315` truthy check), and passback is gated on a closed model-name list (`SEND_REASONING_CONTENT_MODELS`) the engine cannot extend per provider | a thinking model whose name the list misses 400s any multi-turn request ("The `reasoning_content` in the thinking mode must be passed back to the API.") | SDK distributes reasoning_content to every assistant tool-call message for every model, and the engine can gate passback on its own config without the name list | `test_llm.py::TestReasoningPassback::test_*` (gate on/off/"none", borrow-and-reset) |

## Upgrade procedure

1. Bump `openhands-sdk` in `pyproject.toml` (currently pinned `~=1.17.0`).
2. Run the tests - the version guard fails. That is intentional.
3. For each shim, read its removal criterion against the new SDK source.
   - **Bug fixed** → delete the shim, update its pinning test to assert the
     SDK's native behaviour instead (or delete the test if the SDK now covers
     it), update this registry.
   - **Bug present but API changed** → adapt the shim, update the pinning
     test, update this registry.
4. Update the tested-range pin in `tests/test_sdk_compat.py` to the newly
   verified version.
5. Full suite green.

## Terminal sandbox seam (unit-3)

The sandboxed terminal relies on an SDK extension point rather than an
import-time monkeypatch: `SandboxTerminalExecutor(ToolExecutor)` passed as
`TerminalTool.create(conv_state, executor=...)`, with the `terminal` registry
name re-registered per run via `register_tool(name, factory)` where `factory`
is a **callable returning `Sequence[ToolDefinition]`** (`agents.py`).

- **Version range**: `openhands-sdk==1.17.0` (tested).
- **Removal criterion**: the SDK drops the `executor=` kwarg on
  `TerminalTool.create` or changes the callable-factory convention in
  `register_tool` → replace the seam with an agentic-dev-loop-owned terminal
  tool (the `terminal` name stops delegating to the SDK's `TerminalTool`).
- **Pinning test**: `tests/test_sdk_compat.py::test_canary_sdk_terminal_seam_still_present`
  fails loudly when the seam disappears (same upgrade procedure as the shims).

## STUCK fast-stop (unit-5, plan 030)

`SandboxTerminalExecutor.__call__` writes `ConversationExecutionStatus.STUCK`
on the SDK conversation when the sandbox raises its sticky infra error, so
the SDK run loop breaks within one iteration instead of flapping to
`max_iterations` (D3). This is a reliance on undocumented loop behaviour: the
SDK docstring says tool executors must not mutate shared conversation state,
so it is pinned here.

- **Version range**: `openhands-sdk==1.17.0` (tested).
- **Removal criterion**: the SDK grows a supported stop mechanism for tools,
  or `LocalConversation.run` stops checking STUCK in its break condition.
- **Pinning test**: `tests/test_sdk_compat.py::test_canary_sdk_run_loop_still_breaks_on_stuck`
  fails loudly when the run loop no longer breaks on STUCK.

## Turn-level pause seam (035 finalize-nudge turn loop)

`_TurnPauseController.on_event` (stages.py) runs as a user `callbacks=` entry on
`LocalConversation` and pauses after every agent-sourced event by writing
`ConversationExecutionStatus.PAUSED` to `conversation.state`; the one-turn run
loop (`Runtime._run_finalize_loop`) relies on `LocalConversation.run` breaking
on PAUSED and on the SDK passing its composed callback to
`agent.step(on_event=...)` so the callback sees agent events. This is a reliance
on undocumented loop and callback behaviour (same class as the STUCK fast-stop
above), so it is pinned here.

- **Version range**: `openhands-sdk==1.17.0` (tested).
- **Removal criterion**: the SDK grows a supported turn-level pause/stop
  mechanism, `LocalConversation.run` stops checking PAUSED in its break
  condition, or the SDK stops passing user callbacks to `agent.step`.
- **Pinning test**: `tests/test_sdk_compat.py::test_canary_sdk_turn_level_pause_seam_still_present`
  fails loudly when the callback kwarg, the `on_event=self._on_event` wiring,
  or the PAUSED break disappears.

## Provider wire-format policy (Copilot gateway)

The Copilot gateway silently drops ARRAY-encoded tool-result content (HTTP 200,
the model never sees it; probe-proven 2026-11-06, see aidlc-docs/audit.md).
The remedy is the SDK's documented `LLM.force_string_serializer` field
(`llm.py:347`): `provider_wire.py` holds the policy (which gateway base URL
requires string content) and `build_llm` applies it per instance. This is a
reliance on a documented SDK field, not a monkeypatch, so it is pinned here as
a seam.

- **Version range**: `openhands-sdk==1.17.0` (tested).
- **Removal criterion**: the Copilot gateway honors array-encoded tool
  content (the policy can then be removed), or the SDK renames/removes the
  `force_string_serializer` field (pydantic then fails loudly at LLM
  construction - replace with an engine-owned serializer or a new SDK
  equivalent).
- **Pinning tests**: `tests/test_provider_wire.py::TestStringWirePolicy`
  (Copilot config must serialize tool content as a string, non-Copilot configs
  keep the SDK default) and
  `tests/test_provider_wire.py::TestBuildLlmPolicy` (the field is set for
  Copilot instances only).
