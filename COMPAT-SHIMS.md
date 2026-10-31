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
| shim 1 - register `deepseek-v4-pro` in `SEND_REASONING_CONTENT_MODELS` (`engine/llm.py:11-19`) | SDK 1.17.x gates reasoning_content passback on a closed model-name list that does not know deepseek-v4-pro | DeepSeek thinking mode 400s every multi-turn request ("The `reasoning_content` in the thinking mode must be passed back to the API.") | SDK lists the model or adds an instance-level override | `test_llm.py::TestReasoningContentShim::test_deepseek_v4_pro_registered_for_reasoning_passback` |
| shim 2 - repair `LLM.format_messages_for_llm` output (`engine/llm.py:21-48`) | SDK 1.17.x splits a multi-tool-call turn into one assistant message per call and attaches reasoning_content only to the first (`openhands/sdk/agent.py:595` - `reasoning_content=... if i == 0 else None`); `Message.to_chat_dict` also drops empty-string reasoning (`message.py:315` truthy check) | same DeepSeek 400, triggered on any multi-tool-call turn | SDK distributes reasoning_content to every assistant tool-call message | `test_llm.py::TestReasoningContentShim::test_multi_tool_call_turn_repairs_reasoning_passback` (+ reset-at-user-turn test) |

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
