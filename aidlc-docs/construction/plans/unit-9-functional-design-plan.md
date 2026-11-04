# Functional Design Plan - Unit 9 (Viz Redesign)

Unit intent: `aidlc-docs/unit-9-intent-viz-redesign.md` (design-vs-rules validation V-0..V-2)
Requirements: `aidlc-docs/inception/requirements/requirements.md` unit-9 section (FR-9.1..9.9, NFR-9.1..9.5)
Normative design: `planning-docs/design/` (viz-redesign-spec.md, tokens.css, pipeline-layout.js, prototype/index.html)

## Execution Checklist

- [x] Analyze unit context (unit-9 intent doc, requirements, normative spec; no Application Design artifacts exist for unit-9 - the spec fulfils that role, noted in the execution plan)
- [x] Resolve open design decisions Q1-Q4 (answers recorded below)
- [x] Generate `aidlc-docs/construction/unit-9/functional-design/business-logic-model.md` - layout engine algorithm, status presentation model, theme model, flow/edges derivation
- [x] Generate `aidlc-docs/construction/unit-9/functional-design/business-rules.md` - rule zero behaviour table (spec section 10), a11y rules (spec section 11), frontend-rules conformance rules (Q1=B/Q3=A/Q4=A), error/fallback rules (CDN-unreachable, topology-failure, watchdog)
- [x] Generate `aidlc-docs/construction/unit-9/functional-design/domain-entities.md` - event model, topology DTO (flow/edges), layout entities (station, row, route), token entities
- [x] Generate `aidlc-docs/construction/unit-9/functional-design/frontend-components.md` - Carbon component inventory, station/rail/connector structure, class register draft (every class with honest Figma mapping or justification), state model, interaction flows
- [x] Present Functional Design completion message (standardized 2-option format) and await approval

## Open Design Decisions

### Question 1
Where do the extracted viz assets and the viz class register live? (FR-9.8: the linter must be able to walk the assets and read the register; the register must stay viz-local per your Q1=B decision, not mixed into the shared SKILL.md tables.)

A) Assets at `src/agentic_dev_loop/viz/assets/` (next to the Python that inlines them); register at `frontend/viz-register.md` in the same SKILL.md table conventions; `lint-figma-primitives.mjs` gains optional `--assets` / `--register` flags defaulting to today's behaviour. (Recommended)

B) Assets under `frontend/viz/`; the linter's default walk roots extended to include it; register kept in the unit's construction docs (linter pointed there explicitly).

C) Other

[Answer]: A - Assets at src/agentic_dev_loop/viz/assets/ (next to the Python that inlines them); register at frontend/viz-register.md in the same SKILL.md table conventions; lint-figma-primitives.mjs gains optional --assets / --register flags defaulting to today's behaviour. (user answer 2026-11-04)

### Question 2
How do the assets get into the shipped `PAGE` string, keeping the runtime stdlib-only? (NFR-9.1)

A) A small Python build tool (`tools/build_viz_page.py`) inlines `viz/assets/*` into a generated, committed `viz/page.py` module the server imports. No build needed to install/run; stdlib-only runtime; single source of truth stays in the assets. (Recommended)

B) No build step - the server reads assets at import time via `importlib.resources`. The `PAGE` literal disappears as a string constant.

C) Other

[Answer]: A - Python build tool (tools/build_viz_page.py) inlines viz/assets/* into a generated, committed viz/page.py module the server imports. No build needed to install/run; stdlib-only runtime; single source of truth stays in the assets. (user answer 2026-11-04)

### Question 3
How are the pure-function layout invariants tested (FR-9.9), given CI is Python-only today and the algorithm ships as ES5 JS?

A) Port the layout algorithm to Python as the tested source of truth (Hypothesis PBT + invariants in native pytest); the build tool emits the shipped `pipeline-layout.js` from it. No node needed in CI; one source of truth; the committed JS is generated output. (Recommended)

B) Keep the authored ES5 `pipeline-layout.js` as source of truth; pytest runs it under node via subprocess; add `actions/setup-node` to CI (the lint enforcement also implies a node environment somewhere).

C) Authored JS stays source of truth; Python tests assert against golden vectors captured from the JS once (checked-in fixtures; dev-side regeneration tool; no node at test time).

D) Other

[Answer]: A - Port the layout algorithm to Python as the tested source of truth (Hypothesis PBT + invariants in native pytest); the build tool emits the shipped pipeline-layout.js from it. No node needed in CI; one source of truth; the committed JS is generated output. (user answer 2026-11-04)

### Question 4
Supply-chain hardening for the Carbon CDN (NFR-9.2 / Security Baseline extension)?

A) Exact version pin only, with a written rationale in the design (SRI covers direct script/link tags; Carbon loads via module imports where subresource integrity does not apply to the real payload). (Recommended)

B) Pin + SRI hashes on the direct script/link tags, with an honest note on what SRI does and does not cover here.

C) Other

[Answer]: A - Exact version pin only, with a written rationale in the design (SRI covers direct script/link tags; Carbon loads via module imports where subresource integrity does not apply to the real payload). (user answer 2026-11-04)

## Artifact References

- Layout algorithm: `planning-docs/design/pipeline-layout.js` (pure ES5, fewest-rows, wrap connectors, crossing-free U/C routing, O(N+E))
- Current backend to extend: `src/agentic_dev_loop/viz/server.py` - `topology_mermaid()` (line 60) already computes `chain`/`back_edges`; `PAGE` at line 438
- Current tests: `tests/test_viz.py` (contract suite to update); `tests/test_repo_ascii.py` (em dash guard)
