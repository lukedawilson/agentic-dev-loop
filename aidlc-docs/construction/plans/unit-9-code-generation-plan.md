# Unit 9 (Viz Redesign) - Code Generation Plan

Single source of truth for unit-9 Code Generation. Brownfield: existing files
are modified in-place, never duplicated. TDD (red -> green -> refactor) applies
per repo policy: failing tests define behaviour before implementation.

## Unit Context

- **Unit**: 9 - Viz Redesign (Carbon re-skin, horizontal station-and-rail pipeline)
- **Design artifacts**: `aidlc-docs/construction/unit-9/functional-design/` (business-logic-model.md, business-rules.md, domain-entities.md, frontend-components.md)
- **Normative sources**: `planning-docs/design/viz-redesign-spec.md`, `tokens.css`, `pipeline-layout.js`, `prototype/index.html`
- **Story traceability**: User Stories stage skipped (user decision); traceability = FR/NFR table below. Stories are not marked; FRs are the acceptance source.
- **Dependencies**: none on other units (ordering open). Requires node only on the dev machine for golden capture + lint (CI stays Python-only).
- **Contracts owned**: `/topology` response shape (additive); `/events` contract (unchanged); layout pipeline contract (FD section 1.1).
- **Service boundary**: stdlib-only server runtime preserved (NFR-9.1); Node tooling is build/test-time only.

## Approach Summary

1. Port `pipeline-layout.js` to pure Python (tested source of truth), verify
   against golden outputs captured from the reference ES5 (dev-only node run;
   goldens committed so CI needs no node).
2. Author the page as real files under `src/agentic_dev_loop/viz/assets/`
   (HTML, CSS, JS), linted by `lint-figma-primitives.mjs` with new optional
   flags, register at `frontend/viz-register.md`.
3. Generate `viz/page.py` (inlined PAGE) and the shipped `pipeline-layout.js`
   with `tools/build_viz_page.py`; generated files are committed; server
   imports the generated module (runtime stays stdlib-only).
4. Modify `server.py` in place: use generated PAGE, extend `/topology`
   additively with `flow`/`edges`; update and extend `tests/test_viz.py`.

## Execution Steps

- [x] Step 1: Load unit design artifacts and normative sources; verify FD decisions Q1-Q4 and rules R1-R70 are the operative constraints.

- [x] Step 2: Create `src/agentic_dev_loop/viz/layout.py` - pure-Python port of the layout algorithm (contract and orderings per FD business-logic-model.md section 1; constants NW/NH/GAP/STEP/LANE/LANE_TOP/LANE_PAD/CH; no DOM, no I/O). (FR-9.9, NFR-9.4)

- [x] Step 3: Create `tests/test_viz_layout.py` (TDD RED, written before layout.py is complete): invariants (stations always 112x76, never scaled; perRow >= 2 when multi-row; gutter height formula; single-node degenerate result 112x76; determinism) + Hypothesis PBT (random node counts/flow chains/back-edge sets: no U/C crossings via pairwise segment-intersection, all geometry within bounds, arrows/labels on non-degenerate segments) + fewest-rows minimality property. (FR-9.9; extension: PBT partial scope)

- [x] Step 4: Golden verification (dev-only): run the reference `pipeline-layout.js` (node) over the spec's scenarios (N=10 @ 1336px, 1100px, 700px + short topologies) and a property sweep; commit the outputs as JSON fixtures; assert the Python port reproduces them exactly (row counts, perRow, positions, lane counts). No node in CI.

- [x] Step 5: Create `src/agentic_dev_loop/viz/assets/` - `page.html`, `styles.css`, `page.js` per FD frontend-components.md and business-rules.md: Carbon WC v2 exact pinned CDN (R60), region mapping (FD section 1), tokens.css values inline as CSS custom properties (both themes, data-theme), custom graph box + divider + consoles, no-CDN fallback baseline (R30), keep old ids where cheap (#subject #attempt #thread-id #total #verdicts #stages stage-<node> #run-log #stream-banner #run-banner), a11y (R40-R47), ASCII-only (R70), static inline styles converted to classes (R52); data-testid attributes on interactive elements (`theme-toggle`, `copy-console-<node>`, `divider`, `stage-<node>`, `run-log`, `stream-banner`, `run-banner`). (FR-9.1, FR-9.3, FR-9.4, FR-9.5, FR-9.6, FR-9.7, NFR-9.2, NFR-9.3, NFR-9.5)

- [x] Step 6: Create `frontend/viz-register.md` - viz class register in the SKILL.md mapping-table conventions, from the FD section 3 draft (honest Figma mappings for token-mapped classes; written justifications for component-class behaviours, keyframes, product tokens; sanctioned dynamic inline positioning note). (FR-9.7, FR-9.8)

- [x] Step 7: Modify `.opencode/skills/frontend-development/SKILL.md` - add scope note: viz assets live at `src/agentic_dev_loop/viz/assets/` with register at `frontend/viz-register.md` (FR-9.7).

- [x] Step 8: Modify `frontend/scripts/lint-figma-primitives.mjs` - add optional `--assets <dir>` and `--register <file>` flags; defaults preserve current behaviour exactly (walks frontend/src, reads the SKILL.md tables). (FR-9.8)

- [x] Step 9: Create `tools/build_viz_page.py` - Python build tool: reads `src/agentic_dev_loop/viz/assets/*`, inlines into generated `src/agentic_dev_loop/viz/page.py`; emits the shipped `pipeline-layout.js` (path in assets) from the Python port; ASCII validation pass over output; exits non-zero on drift between generated and committed files (no silent divergence). (FR-9.8, NFR-9.1, NFR-9.5)

- [x] Step 10: Create `src/agentic_dev_loop/viz/page.py` and the committed `pipeline-layout.js` - generated by Step 9's tool, committed as output artifacts. (FR-9.8, NFR-9.1)

- [x] Step 11: Modify `src/agentic_dev_loop/viz/server.py` in place: replace the inline `PAGE` string with an import of the generated page module; extend `topology_mermaid()` (or add a derived function next to it) to also return `flow` (consecutive chain pairs) and `edges` ({src,dst,label|null} from back_edges, label null when unlabelled); handler serves `flow`/`edges` alongside `mermaid` (mermaid byte-identical). (FR-9.1, FR-9.2, NFR-9.1)

- [x] Step 12: Modify `tests/test_viz.py` in place: extend topology contract tests for `flow`/`edges` with `mermaid` unchanged; page-serving test asserts the new page is served with old ids where cheap; behaviour contract tests updated for the new DOM structure to keep R1-R13 green (acceptance A5). (FR-9.2, FR-9.3)

- [x] Step 13: Verify: run `tools/build_viz_page.py` (drift check), run `node frontend/scripts/lint-figma-primitives.mjs --assets src/agentic_dev_loop/viz/assets --register frontend/viz-register.md` (dev), run the full pytest suite including `tests/test_repo_ascii.py`. (NFR-9.1, NFR-9.5)

- [x] Step 14: Write code summaries to `aidlc-docs/construction/unit-9/code/` (markdown only; modified vs created file lists, contract notes).

- Note (2026-11-04, at completion): Step 5's pinned Carbon CDN (R60) was superseded by user directive - the Carbon runtime was removed entirely (the CDN module threw bare-specifier resolution errors and the cds-tile elements were purely presentational). The hand-written skin is the authoritative styling; R30/R60 restated in the viz register. Also user-directed deviations landed after the original 14 steps: resize divider dropped (sections scroll instead), auto expand/collapse dropped, fail edges light amber when used, thread-id click-to-copy with feedback. Recorded in aidlc-state.md.

## FR/NFR Traceability

| Requirement | Steps | | Requirement | Steps |
| --- | --- | --- | --- | --- |
| FR-9.1 | 5, 11 | | NFR-9.1 | 9, 10, 11, 13 |
| FR-9.2 | 11, 12 | | NFR-9.2 | 5 |
| FR-9.3 | 5, 12 | | NFR-9.3 | 5, 11 |
| FR-9.4 | 5 | | NFR-9.4 | 2, 5 |
| FR-9.5 | 5 | | NFR-9.5 | 5, 9, 13 |
| FR-9.6 | 5 | | | |
| FR-9.7 | 5, 6, 7 | | | |
| FR-9.8 | 6, 8, 9, 10 | | | |
| FR-9.9 | 2, 3, 4 | | | |

## File Map (brownfield: modified in place, never duplicated)

**Created**: `src/agentic_dev_loop/viz/layout.py`, `tests/test_viz_layout.py`,
`src/agentic_dev_loop/viz/assets/{page.html,styles.css,page.js}`,
`frontend/viz-register.md`, `tools/build_viz_page.py`,
`src/agentic_dev_loop/viz/page.py` (generated), `pipeline-layout.js` (generated, in assets), golden JSON fixtures under `tests/`

**Modified**: `src/agentic_dev_loop/viz/server.py`, `tests/test_viz.py`,
`frontend/scripts/lint-figma-primitives.mjs`,
`.opencode/skills/frontend-development/SKILL.md`

## Estimated Scope

14 steps; 4 design/test-heavy steps (2-4), 3 page-authoring steps (5-7),
2 tooling steps (8-9), 2 integration steps (11-12), 2 verification steps
(13-14). Largest single artifact: the page assets (step 5).
