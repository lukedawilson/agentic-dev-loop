# Code Structure

## Build System

- **Type**: setuptools (pyproject.toml, `[build-system] requires = ["setuptools>=68"]`)
- **Configuration**: `pyproject.toml` - project `agent-engine` 0.1.0, `requires-python = ">=3.12"`, dependencies `openhands-sdk ~=1.17.0`, `openhands-tools ~=1.17.0`, `langgraph>=1.2`, `langgraph-checkpoint-sqlite>=3.0`, `pydantic>=2.0`, `PyYAML>=6.0`; console script `agent-engine = agent_engine.cli:main`; packages found under `src/`; package-data `py.typed`.

## Key Classes/Modules

```mermaid
flowchart TD
    cli["cli.py - main()"] --> graph["graph.py - run_pipeline()"]
    graph --> config["config.py - PipelineConfig, StepConfig, LlmConfig"]
    graph --> stages["stages.py - nodes, routers, run_agent"]
    graph --> registry["registry.py - Registry, load_extensions"]
    graph --> llm["llm.py - build_llm + compat shims"]
    graph --> loaders["loaders.py - select_loader"]
    graph --> viz["viz.py - VizBus, serve_viz"]
    graph --> actions["actions.py - commit, kill_listeners_on_port"]
    stages --> agents["agents.py - load_agent"]
    stages --> verdicts["verdicts.py - parse_verdict"]
    registry --> ext["extensions/aidlc.py - AidlcExtension"]
```

### Existing Files Inventory

- `pyproject.toml` - build + dependency declaration; console script entry.
- `src/agent_engine/__init__.py` - package marker.
- `src/agent_engine/cli.py` - two-phase CLI, core flags, loader flags, exit codes.
- `src/agent_engine/config.py` - strict YAML schema (PipelineConfig, StepConfig, VerdictSpec, Route, LlmConfig), `load_config`.
- `src/agent_engine/graph.py` - LoopState, build_graph, run_pipeline, _resolve_docs; checkpoint + resume + viz wiring.
- `src/agent_engine/stages.py` - Runtime, run_agent, _run_command, build_context, clear_attempt_artifacts, make_agent_node / make_command_node / make_action_node / make_router / make_bump_node, success_node.
- `src/agent_engine/agents.py` - load_agent (fail-fast file existence + name match), SDK tool import registrations.
- `src/agent_engine/actions.py` - commit, git_worktree_clean, _listening_pids, kill_listeners_on_port.
- `src/agent_engine/registry.py` - Registry, default_registry, load_extensions, DocumentLoader/Extension protocols.
- `src/agent_engine/loaders.py` - PlanDocumentLoader, resolve_plan_docs, select_loader.
- `src/agent_engine/llm.py` - build_llm + two import-time SDK monkeypatch shims (registered in COMPAT-SHIMS.md).
- `src/agent_engine/verdicts.py` - parse_verdict, _parse_line (decoration/caveat/negation tolerance).
- `src/agent_engine/viz.py` - live HTTP graph viz (VizBus, NodeWatch, ConsoleCapture, HeartbeatThread, topology_mermaid, translate, serve_viz).
- `src/agent_engine/extensions/__init__.py` - package marker.
- `src/agent_engine/extensions/aidlc.py` - AidlcExtension / AidlcDocumentLoader, resolve_unit_docs, get_current_unit.
- `agent-engine/pipeline.yaml` - shipped self-construction pipeline (dev → test → review → qa → commit).
- `agent-engine/sdk_agents/dev.agent.md` - dev agent (tools: terminal, file_editor).
- `agent-engine/sdk_agents/review.agent.md` - review agent (tools: terminal, file_editor).
- `agent-engine/sdk_agents/qa.agent.md` - qa agent (tools: terminal only).
- `tests/` - 16 test files (conftest, sample_ext, per-module tests, e2e fixtures, sdk compat).
- `tests/smoke/viz_demo.py` - scripted viz replay demo (no live pipeline).
- `COMPAT-SHIMS.md` - registry of SDK monkeypatches with removal criteria.
- `README.md` - user documentation.
- `AGENTS.md` - agent instructions for this repo (incl. AI-DLC invocation).

## Design Patterns

### Node closures (factory functions)
- **Location**: stages.py (make_agent_node, make_command_node, make_action_node, make_router, make_bump_node).
- **Purpose**: Bind runtime + step config into LangGraph node functions.
- **Implementation**: closures capturing `runtime` and `step`; routers are pure functions of state for deterministic control flow.

### Strict-config (fail-fast validation)
- **Location**: config.py (extra="forbid" at every level, model_validators).
- **Purpose**: All config errors surface at load time, never mid-run.
- **Implementation**: Pydantic v2 validators; exactly-one-kind check; cross-field pairings.

### Registry (explicit extension seam)
- **Location**: registry.py.
- **Purpose**: Consumers contribute actions/tools/loaders without touching core.
- **Implementation**: module:Class strings; import + instantiate + register; every failure names the ref.

### Positive-only attribution
- **Location**: actions.py (listener sweep), verdicts.py.
- **Purpose**: Never destroy something whose identity can't be established.
- **Implementation**: pids whose cwd can't be determined are spared; unclear verdicts retry rather than route.

### Import-time side effects
- **Location**: agents.py (tool registration via imports), llm.py (SDK monkeypatches).
- **Purpose**: SDK globals (tool registry, model features) must be populated before use.
- **Implementation**: module-level imports with `# noqa: F401` registrations; compat shims guarded by sentinel attributes, registered in COMPAT-SHIMS.md.

## Critical Dependencies

### openhands-sdk (~=1.17.0)
- **Version**: ~=1.17.0
- **Usage**: LLM, Agent, Conversation, agent_definition_to_factory, register_tool, ConversationExecutionStatus.
- **Purpose**: Agent execution runtime; agent-definition loading; tool registry.

### openhands-tools (~=1.17.0)
- **Version**: ~=1.17.0
- **Usage**: TerminalTool, FileEditorTool, TaskTrackerTool (import-registered).
- **Purpose**: The agent toolset (terminal = full shell authority).

### langgraph (>=1.2) + langgraph-checkpoint-sqlite (>=3.0)
- **Version**: >=1.2 / >=3.0
- **Usage**: StateGraph, END/START, SqliteSaver, stream modes.
- **Purpose**: Graph execution, retry cycles, crash-safe checkpoint/resume.

### pydantic (>=2.0)
- **Version**: >=2.0
- **Usage**: BaseModel schemas with strict validation.
- **Purpose**: Config schema enforcement.

### PyYAML (>=6.0)
- **Version**: >=6.0
- **Usage**: yaml.safe_load.
- **Purpose**: Pipeline YAML parsing.
