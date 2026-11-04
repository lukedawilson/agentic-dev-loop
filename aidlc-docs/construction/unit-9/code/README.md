# Unit 9 code summary (viz redesign)

## Created

| File | Purpose |
| --- | --- |
| `src/agentic_dev_loop/viz/layout.py` | Pure-Python 1:1 port of `pipeline-layout.js`; the tested source of truth. |
| `src/agentic_dev_loop/viz/assets/page.html` | Page shell, kept ids, a11y landmarks (no Carbon runtime; hand-written skin). |
| `src/agentic_dev_loop/viz/assets/styles.css` | Exact token values (both themes), status skins, station/rail geometry, consoles. |
| `src/agentic_dev_loop/viz/assets/page.js` | Page behaviour: status precedence, graph rendering, accordion, consoles, watchdog, theme, thread-id copy. |
| `src/agentic_dev_loop/viz/assets/pipeline-layout.js` | Shipped browser layout (emitted from the normative reference). |
| `src/agentic_dev_loop/viz/page.py` | Generated PAGE module imported by the stdlib-only server. |
| `tools/build_viz_page.py` | Builds `page.py`, emits the shipped layout JS, `--check` drift gate. |
| `tests/test_viz_layout.py` | Golden parity, invariants, fewest-rows minimality, U/C crossing check, Hypothesis properties. |
| `tests/viz_layout_golden.json` | Committed reference ES5 goldens (spec scenarios + property sweep). |
| `frontend/viz-register.md` | Viz class register, keyframes, element rules, product tokens. |

## Modified

| File | Change |
| --- | --- |
| `src/agentic_dev_loop/viz/server.py` | Imports the generated PAGE; `_topology_parts` shared by `topology_mermaid` and new `topology_graph`; `/topology` adds `flow`/`edges`; `serve_viz` accepts them. |
| `src/agentic_dev_loop/viz/__init__.py` | Exports `topology_graph`. |
| `src/agentic_dev_loop/viz/__main__.py`, `src/agentic_dev_loop/engine/graph.py`, `viz_demo.py` | Serve the structured topology. |
| `tests/test_viz.py` | Topology contract tests for `flow`/`edges`; page assertions for new ids; page-script harness renders layout stations instead of Mermaid. |
| `frontend/scripts/lint-figma-primitives.mjs` | Optional `--assets`/`--register` mode; defaults unchanged. |
| `.opencode/skills/frontend-development/SKILL.md` | Viz scope note. |

## Contract notes

- `/topology` is additive: `{mermaid, subject, nodes, flow, edges}`. `mermaid`
  is byte-identical to the previous renderer output.
- `/events` is unchanged.
- The layout port matches the reference ES5 exactly on all committed goldens,
  including row counts: 1 row at 1336px, 2 rows at 1100px, 3 rows at 700px for
  the 10-station topology.
- The server runtime stays stdlib-only; Node is build/test-time only.

## Post-plan fixes (2026-11-04, user-driven, TDD)

- Resize divider dropped; sections scroll internally (desktop) and the page
  scrolls on narrow screens; auto expand/collapse of the current step dropped.
- Fail edges (dashed) light amber when the route is used; arrowhead status
  classes fixed (`arr-<kind>` so the success/warning skins match).
- `build_viz_page.py` embeds the normative reference layout, so a single build
  run always leaves `page.py` and the shipped asset consistent.
- Carbon runtime removed (bare-specifier module errors); cds-tile wrappers
  unwrapped to plain `.tile` divs; summary tiles stack 2x2 on mobile with
  natural word breaks.
- Thread id: click (or Enter/Space) copies to the clipboard with transient
  "Copied" feedback; selection fallback when the clipboard API is unavailable.
