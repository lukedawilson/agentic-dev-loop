# Unit 11 Functional Design - Validated Model (External Surfaces)

Extension: External Surfaces Validation (enabled, born from the unit-10
postmortem). The external surface for unit-11 is the pinned openhands-sdk
(~1.17.0) conversation/event/tool layer that the engine consumes.

## Validated-model table (EXTSRF-04)

| Fact | Established by (probe) | Date |
| --- | --- | --- |
| ActionEvent exposes `tool_name`, `action` (parsed Action or None), `tool_call`, `tool_call_id`, and `thought` | Read pinned artifact openhands/sdk/event/llm_convertible/action.py (class fields) in .venv/lib/python3.12/site-packages/openhands/ | 2026-11-07 |
| Counting ActionEvents in conv.state.events counts the agent's tool calls (each tool call emits one ActionEvent) | Same artifact plus engine capture-first path (stages.py run_agent reads conv.state.events before close) | 2026-11-07 |
| `Action` is a pydantic `Schema` ABC; tool inputs are typed subclasses (FinishAction.message example) so a RecordVerdictAction(verdict: str) exposes a plain attribute | Read pinned artifact openhands/sdk/tool/schema.py (class Action) and openhands/sdk/tool/builtins/finish.py (FinishAction) | 2026-11-07 |
| Custom tools register at import through the SDK registry; the engine already registers the sandbox terminal via `register_tool` | Read engine/agents.py (Registry/register_tool usage, terminal re-registration) | 2026-11-07 |
| FinishTool and ThinkTool are auto-added to agent steps; the frontmatter `tools:` list is the exact capability allowlist (BR-1) | Read engine/agents.py load_agent docstring and factory code | 2026-11-07 |
| A content-bearing turn with zero tool calls sets execution_status FINISHED (the 059 collapse mechanism) | Probe recorded in planning-docs/059 (SDK agent.py ~643 read during the collapse investigation) | 2026-11-06 |
| get_agent_final_response accepts ANY final agent MessageEvent as the response (capture accepts narration) | Read pinned artifact openhands/sdk/conversation/response_utils.py during the 059 investigation | 2026-11-06 |
| Steps without `verdicts:` config pass on capture-present; only verdicts-configured steps parse verdicts (review, qa) | Read engine/stages.py:744-747 and agentic-dev-loop/pipeline.yaml step configs | 2026-11-07 |

## Unverified assumptions (listed, not facts until probed)

| Assumption | Probe planned | When |
| --- | --- | --- |
| The Responses API path surfaces finish_reason/tool_calls in the same shape the chat path does (D-5 parity) | Code Generation probe against the litellm/Responses surface in llm.py | Code Generation |
| A RecordVerdictAction registered like terminal is surfaced by the SDK factory without extra executor work (it needs no sandbox execution) | Code Generation probe: register the tool, build an agent, run a fake conversation | Code Generation |
| The Copilot endpoint passes the structured tool call through untouched for Anthropic pins | Live smoke re-run (FR-11.6) | Build and Test |

All architecture-driving claims above rest on the validated rows; the three
assumptions are spike conditions (EXTSRF-03): if any probe fails, Code
Generation reports loudly before building on it.
