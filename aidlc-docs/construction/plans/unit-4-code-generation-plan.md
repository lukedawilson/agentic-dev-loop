# Unit 4 Code Generation Plan - Claude Dynamic Workflows driver

Status: **Part 1 - AWAITING APPROVAL** (no code is written before explicit user approval, per repo AGENTS.md).

## Scope

Module split (`core` / `engine` / `viz`) with extras inversion, workflow projection + JS template rendering, viz HTTP ingest, docker-free commit shim, workflow CLI (`agentic-dev-loop-workflow`) + wheel-shipped skill payload, new CI job, migration docs.

## TDD steps

- [ ] 1. **Guard test (red first)** - `tests/test_module_boundaries.py`: `core` and `viz` import no `engine` module and no heavy deps (openhands-sdk, langgraph, docker client); fails today.
- [ ] 2. **Module split (hard break, NFR-2)** - move `config.py` → `core/config.py`, `viz.py` → `viz/`, everything else → `engine/`; update ALL importers (engine modules, tests, CLI). No re-export shims. Green = full existing suite passes under new paths.
- [ ] 3. **Projection (red)** - `tests/test_project.py`: `project_workflow` preserves step order, verdicts, `on_pass`/`on_fail`/`goto`, `max_attempts` for a fixture pipeline (engine-mode and workflow-mode variants).
- [ ] 4. **Projection (green)** - `core/project.py`: `project_workflow` + `validate_prompt_refs` (missing `.agent.md` → loud build failure naming the file, FD-1).
- [ ] 5. **Template render (red)** - `tests/test_render_golden.py`: golden-file comparison for a fixture pipeline → rendered JS; determinism incl. version + pipeline-hash header (FD-5).
- [ ] 6. **Template render (green)** - `core/template.py` + package-data template `skill/workflow.template.js`; `render_workflow_js(template, projection, core_version, pipeline_hash)`.
- [ ] 7. **Viz ingest (red then green)** - `tests/test_viz_ingest.py`: token-authenticated POST → SSE event emission; tokenless SSE view; localhost bind (FD-4/BR-9). Green: `viz/ingest.py` + `viz/server.py` (stdlib-only).
- [ ] 8. **Commit shim (red then green)** - `tests/test_commit_shim.py`: docker-free shim gates on `commit_allowed`, uses trusted hooksPath, templated message (BR-8/FR-4.6). Green: `engine/commit_shim.py` extracting the existing commit policy from `actions.py`.
- [ ] 9. **Pyproject extras inversion** - base install = `core`+`viz` deps only (pydantic, PyYAML); `[engine]` extra = openhands-sdk, openhands-tools, langgraph, langgraph-checkpoint-sqlite, agent-client-protocol<1.0.0. Entry points: `agentic-dev-loop-workflow` in base, `agentic-dev-loop` in `[engine]`, both with posix + powershell forms (ID-1).
- [ ] 10. **Workflow CLI** - `tests/test_workflow_cli.py`: `install-skill` copies SKILL.md + template into `~/.claude/skills/agentic-dev-loop/` idempotently + writes VERSION marker (ID-3; test via tmp HOME); `--version`.
- [ ] 11. **CI workflow-mode job** - `.github/workflows/workflow-mode.yml` (ubuntu + windows): install `[workflow]` extra only → run projection on fixture → render → golden compare. Proves no hidden engine imports (ID-4). Existing `ci.yml` + `release.yml` unchanged.
- [ ] 12. **PBT** - `tests/test_unit4_pbt.py`: projection round-trip, ingest-token uniqueness, template determinism (same inputs → same JS modulo header).
- [ ] 13. **Migration docs** - CHANGELOG old→new import table; `docs/setup.md` migration section; in-repo `agentic-dev-loop/pipeline.yaml` extensions string → `agentic_dev_loop.engine.extensions.aidlc`; README workflow-mode install/usage section; the consumer repo upgrade note (ID-5).
- [ ] 14. **Full regression** - existing suite green under new layout; docker-marked tests untouched; guard + golden + PBT green.

## Story traceability

| Source | Steps |
| --- | --- |
| FR-4.1 module split + guard | 1, 2, 9 |
| FR-4.3 build-time parse + template | 3-6 |
| FR-4.4/4.5 command steps + verdict contract | 3, 6 (projection encodes routing) |
| FR-4.6 commit shim | 8 |
| FR-4.7 viz + ingest | 7 |
| FR-4.8 no MCP | (nothing to build - omission) |
| FR-4.9 shared pipeline.yaml | 3 (engine-only fields ignored in projection) |
| NFR-4.1 extras split / 4.2 hard break | 9, 2, 13 |
| NFR-4.3 perf targets / 4.4 no journal / 4.5 CC audit / 4.6 no added security | design constraints, no steps |
| NFR-4.7 golden tests / NFR-4.8 install UX | 5, 11, 10 |
| BR-1..13 | covered by steps 3-13 |

## Files

**Created:** `src/agentic_dev_loop/core/{__init__,config,project,template}.py`; `src/agentic_dev_loop/viz/{__init__,server,ingest}.py`; `src/agentic_dev_loop/engine/{__init__,actions,agents,cli,dryrun,graph,isolation,llm,loaders,outputs,proc,registry,results,stages,commit_shim}.py` + `engine/extensions/` (moved); `src/agentic_dev_loop/skill/{SKILL.md,workflow.template.js}`; `src/agentic_dev_loop/workflow_cli.py`; `tests/test_module_boundaries.py`, `test_project.py`, `test_render_golden.py` (+ fixtures), `test_viz_ingest.py`, `test_commit_shim.py`, `test_workflow_cli.py`, `test_unit4_pbt.py`; `.github/workflows/workflow-mode.yml`.

**Modified:** `pyproject.toml`; `tests/*` (import paths); `agentic-dev-loop/pipeline.yaml` (extensions string); `README.md`; `CHANGELOG.md`; `docs/setup.md`; `aidlc-docs/aidlc-state.md` + `audit.md`.

**Unchanged:** Docker sandboxing (`engine/isolation.py` moves, logic untouched); `actions.py` logic (commit policy extracted into shim, action stays).

## Key decisions

- **KD-1** Extras inversion: base wheel = light (core+viz), `[engine]` extra = heavy deps (ID-1/NFR-4.1).
- **KD-2** Hard break, no shims; migration table in CHANGELOG (NFR-4.2).
- **KD-3** JS template ships as package data; golden tests lock rendering (NFR-4.7).
- **KD-4** Two console entry points, each with posix + powershell forms (ID-1).
- **KD-5** `core`/`viz` never import `engine`; guard test enforces (BR-2).
- **KD-6** `extensions` package moves under `engine`; YAML string update documented (ID-5).
- **KD-7** viz server = existing `viz.py` logic extracted stdlib-only + new ingest endpoint (FR-4.7).
