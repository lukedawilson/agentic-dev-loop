# 063: Runs page stats reorganisation - key stats, charts, drill-down

Status: spec approved (brainstorming 2026-11-09), ready for implementation plan.

## Problem

The historical runs page renders its aggregate metrics as a flat grid of
~29 cards across four sections (Loop health, TDD, Review, QA), every metric
at equal visual weight. It is an unorganised mass: nothing is scannable,
nothing is prioritised. Separately, `verdict_distribution` - the one metric
whose value is a nested dict (`{step: {verdict: count}}`,
metrics.py `_aggregate_loop`) - renders its inner objects as
`[object Object]` because `formatMetricValue` in runs.js only flattens one
level (shipped in 0.14.1, live in 0.14.3).

## Goals

- Each category shows 2-3 key stats, in chart form where the data suits it.
- Full detail stays available one click away via an expandable section.
- The same key-stats-plus-drill-down pattern applies to per-thread metrics
  on the run detail page.
- The nested-dict rendering bug dies as part of the redesign.

## Non-goals

- No Python/metrics changes. `pure_metrics` already emits everything the UI
  needs: `value`, `numerator`/`denominator` for rates, `basis` (the n=...
  line), `provenance`, `availability`.
- No new charting dependency. The vendored echarts 6.1.0 common bundle was
  probed (SSR render against the pinned artifact): bar, line, scatter, pie,
  radar, gauge, funnel all render. No chart-type constraint.
- The three existing charts at the top of the run detail page stay as-is.
- No routing changes, no new endpoints, no filters changes.
- No consumer pipeline.yaml or agent-prompt changes; release notes need no
  consumer-adoption subsection.

## Approved decisions (from brainstorming)

1. Drill-down mechanism: expandable sections per category (not sub-pages,
   not modals).
2. `verdict_distribution` bug: fold into this redesign - the metric becomes
   a chart and never renders through `formatMetricValue`; the text path
   gains recursion as a safety net for any future nested metric.
3. Scope: aggregate panel on `/runs` AND thread metrics on `/runs/<id>`.
4. Structure: chart-forward category panels (approach A), not headline
   numbers only (B) and not tabs (C).

## Design

### Panel structure

Each of the four categories (Loop health, TDD, Review, QA) becomes a panel
containing:

- a key-stat band (charts and/or big numbers) at the top;
- a native `<details>`/`<summary>` expander, collapsed by default, whose
  body is the existing metric-card grid rendering the category's remaining
  metrics. Summary text: "Details (n metrics)".

Nothing is deleted; the long tail moves behind the expander. Expander body
renders lazily (innerHTML populated on first `toggle` open) so the initial
list render stays cheap. `<details>` gives open/close state and keyboard
accessibility for free; no JS state is held.

### Key stats per category

| Category | Key stat | Source metric | Render |
|----------|----------|---------------|--------|
| Loop health | Verdict mix | `verdict_distribution` | stacked horizontal bar, one bar per step, segments coloured by verdict kind |
| Loop health | Attempts per thread | `attempt_count_distribution` | vertical bar, x = attempt count, y = threads |
| Loop health | Threads | `thread_count` | big number |
| TDD | Attempts red-then-green % | `pct_attempts_red_then_green` | gauge |
| TDD | Threads red-then-green % | `pct_threads_red_then_green` | gauge |
| TDD | Zero-test attempts | `attempts_zero_test_verdicts` | big number |
| Review | Missed rate % | `review_missed_rate` | gauge |
| Review | Defect yield | `defect_yield` | big number |
| Review | Rubber stamp | `rubber_stamp` | big number |
| QA | Catch rate % | `catch_rate` | gauge |
| QA | QA pass rate % | `qa_pass_rate` | gauge |
| QA | Unverifiable rate | `unverifiable_rate` | big number |

Everything else in each category goes to the detail grid, unchanged in
content.

### Chart specs

- Gauges: half-donut, range 0-100, needle at the metric value, percentage
  printed in the centre, the `basis` line under it. Fill uses the existing
  success-green token. Height matches the existing card grid (~120px);
  three across on desktop, stacking on mobile via the existing 800px media
  query pattern.
- Verdict mix: horizontal stacked bars, one per step, segments = verdict
  counts, colours from the existing `colorsBy` palette (pass green, fail
  yellow, unverifiable purple, env red). Legend on.
- Attempts bar: vertical bars, integer axis (`minInterval: 1`, same as the
  detail page's attempts chart), info colour.
- Big numbers: large value, small label, basis line, provenance badge kept.
- Unavailable metrics degrade gracefully: a gauge with
  `availability != "available"` renders its basis line and `--` instead of
  a needle; big numbers show `--`.

### Renderer dispatch

A small declarative table in runs.js maps metric names to renderers per
category, e.g. `{ catch_rate: "gauge", qa_pass_rate: "gauge", ... }`.
Metrics not listed for a category's band fall through to the detail grid.
Promoting or demoting a stat later is a one-line move.

### Thread metrics (detail page)

Per thread: a key-stat band (attempt count, red-then-green %, QA pass rate)
with the full 12-card grid behind a `<details>` expander. The three
top-of-page charts (attempts by thread, verdicts by kind, verdict sequence)
are untouched.

### formatMetricValue recursion (safety net)

Nested dicts render one level deeper:
`Test (Pass: 3, Fail: 1), Review (Pass: 2)`. Maximum two levels of nesting;
anything deeper bails to the existing join. This covers any future nested
metric that has not been promoted to a chart.

### CSS and the class register (0.14.1 lesson)

New classes (stat band, big-number stat, gauge slot, expander styling) MUST
be added to `frontend/viz-register.md` in the same change. The 0.14.1
release died in CI because `.filters-row` landed in styles.css without a
register row; that must not recur. If `<details>`/`<summary>` need
element-level rules, they go into the register's sanctioned element-rules
table with a written justification. The register lint
(`test_viz_asset_register_lint`) is part of the change's own verification,
run before commit, not after.

## Testing (TDD, plus the 0.14.2 lesson)

Red-green throughout. Node-vm driver tests (existing pattern in
tests/test_runs_js_metrics.py and tests/test_runs_js_filters.py) for:

- gauge option values derived from a rate metric (value, min/max, centre
  label);
- stacked-bar series shape built from the nested `verdict_distribution`;
- expander markup and lazy render-on-first-open;
- `formatMetricValue` recursion: two levels render, third level bails;
- unavailable-metric empty states (gauge `--`, big-number `--`).

Layout tests (tests/test_runs_layout.py pattern) covering every new class
and the register sync. Rebuild `page.py` via `tools/build_viz_page.py`;
drift check clean.

Before any release is cut: run the FULL `pytest tests/` suite locally, not
just the related files. The 0.14.1 failure happened because only the six
related test files were run before release.

## Acceptance

- All four categories render their key stats as charted/big-number bands
  with real data.
- Expanders reveal the full grids; no metric is lost.
- `verdict_distribution` never renders as text; no `[object Object]`
  anywhere on the page.
- Register lint passes; full test suite passes; page.py drift check clean.
- Release notes: consumer-facing UI change, no consumer-adoption
  subsection.
