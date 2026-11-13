# Plan: viz-model

Status: APPROVED (approval = operator running this smoke test)

## Problem

The pipeline viz shows each step as a station in the pipeline graph, but a
station does not say which model drives that step. The pipeline can mix
models (the default `llm:` block plus per-step `llm:` profile overrides), and
the diagram should make that visible at a glance.

## Decisions

- D1: The model name is rendered as a new line inside each station of the
  pipeline graph (the DOM station graph the viz page renders), between the
  step name and the status row. This is the diagram the page actually shows.
- D2: The mermaid source string is untouched. The page never renders mermaid
  (it renders the station graph), and mermaid is a byte-identical guarantee.
- D3: The model for a step is resolved with the same rule the engine uses to
  run the step: `step.llm` names a top-level `llms:` profile whose model is
  used; otherwise the default `llm:` block's model is used; steps that run no
  model (command/action steps and the success/failure terminals) show no
  model line. In this smoke pipeline that resolves to: brief = default
  profile, deps = none, dev = flash profile, test = none, review = default
  profile, success/failure = none. The concrete model names per provider are
  the table in Verification.
- D4: The model names are resolved once, in `viz/server.py`, as a pure helper
  `step_models(cfg)` returning a dict keyed by every node id the topology
  carries (value = model name, or `None` when the node runs no model). The
  dict travels with the topology DTO through the existing pipe
  (`topology_graph` -> `serve_viz` -> `_handler_class` -> `/topology` JSON ->
  `page.js`), the same route the provider name already takes.
- D5: `page.js` reads the model from the topology payload, so the station
  shows the model without any extra request. Stations without a model show
  nothing (blank line).

## Scope

All changes live in `src/agentic_dev_loop/`:

1. `viz/server.py`: `step_models(cfg)` helper; `topology_graph` DTO gains a
   `"models"` key; `serve_viz` and `_handler_class` forward it; `/topology`
   JSON includes it.
2. `engine/graph.py`: pass `topology["models"]` into `serve_viz`.
3. `viz/static/page.js`: `ensureStation` gains the model element; the
   station update sets it from the topology payload (empty string when the
   node has no model).
4. `viz/static/styles.css`: style for the new element (small, muted,
   monospace, one line).
5. `viz/static/page.py`: regenerated with `python tools/build_viz_page.py`
   (there is a drift guard: `tests/test_build_viz_page.py`).

No changes to `pipeline.yaml`, agent prompts, or the mermaid source.

## Tasks (TDD, red -> green -> refactor)

1. RED: add tests to `tests/test_viz.py`, all with `model` in their names so
   the smoke gate `-k model` selects exactly them:
   - `test_step_models_resolve_profiles_and_default`: a pipeline whose
     `llm:` block model is `default-model` and whose `llms:` block has
     `flash` -> `flash-model`; steps `brief` (no llm), `deps` (command),
     `dev` (agent, llm: flash), `review` (agent, no llm), `success`
     (terminal). Expect
     `{"brief": "default-model", "deps": None, "dev": "flash-model",
     "review": "default-model", "success": None}`.
   - `test_topology_graph_dto_carries_models`: `topology_graph(cfg)["models"]`
     equals `step_models(cfg)` and has one key per topology node.
   - `test_topology_endpoint_carries_models`: `serve_viz` with a models dict,
     then read `/topology` and assert the body's `models` field matches.
   - `test_models_handler_class_forwards`: `_handler_class(...)` sets
     `cls.viz_models`.
   - `test_station_shows_model_when_configured`: page.js harness; the fetch
     stub's topology JSON carries `models: {"dev": "claude-sonnet-5-5",
     "test": null}`; after load, `station-dev`'s model element text is
     `claude-sonnet-5-5` and `station-test`'s is `""`.
   Run the selection and confirm red (import/attribute errors for the new
   names, missing elements otherwise).
2. GREEN: implement the scope above; run
   `.venv-sandbox/bin/python -m pytest tests/test_viz.py -q -m "not docker"`
   until green, including `test_build_viz_page.py` and the existing
   `TestPageScriptBehavior` and `TestTopologyMermaid` tests staying green.
3. REFACTOR: no dead code, ASCII only (no em dash anywhere), keep the mermaid
   string byte-identical.

## Verification

- The smoke gate runs
  `.venv-sandbox/bin/python -m pytest tests/test_viz.py -q -m "not docker"
  -k model`, which passes only when all five tests exist and pass.
- The final response must include the verification matrix per the agent
  contract.
- Manual check: the viz page of this very smoke run shows a model line on
  exactly the brief, dev, and review stations, with these values per
  provider (deps, test, and the terminals show no model line):

  | Station | Profile  | openhands                | copilot                 | claude          |
  |---------|----------|--------------------------|-------------------------|-----------------|
  | brief   | default  | openai/deepseek-v4-pro   | openai/claude-opus-5.5  | claude-opus-5.5 |
  | deps    | none     | (blank)                  | (blank)                 | (blank)         |
  | dev     | flash    | openai/deepseek-flash    | openai/claude-sonnet-5.5| claude-sonnet-5.5 |
  | test    | none     | (blank)                  | (blank)                 | (blank)         |
  | review  | default  | openai/deepseek-v4-pro   | openai/claude-opus-5.5  | claude-opus-5.5 |

## How to run

- `./run-smoke.sh openhands tests/smoke/viz-model`
- or `.venv/bin/python tests/smoke/run_smoke.py tests/smoke/viz-model
  --provider copilot` (or `--provider claude`; the provider picks the model
  names that end up in the diagram)

## Risks

- The page script is generated into `page.py`; forgetting the regeneration
  step fails the drift guard in CI, not silently.
- The station DOM is asserted by existing page-script tests; the new element
  is added without touching the existing children, and the existing tests
  still pass.
