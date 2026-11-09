# Unit 13 Charting Software Selection

Working document for the charting-library selection triggered by unit-13
FD-Q4 (see `aidlc-docs/construction/plans/unit-13-functional-design-plan.md`).
Process: structured software selection (Phase 1 criteria, Phase 2 longlist,
Phase 3 community verification, Phase 4 shortlist, Phase 5 optional test,
Phase 6 recommendation). Every phase ends in a user gate.

Canonical copy lives in this repo (planning-docs). The user may mirror it
to a shared live tool if a shareable URL is wanted.

## Phase 1: Project Context and Criteria

### Inputs read

- `aidlc-docs/unit-13-intent-viz-reporting.md` (intent, FR-13.1..13.8)
- `aidlc-docs/construction/unit-13/functional-design/domain-entities.md`
  (ChartSlot deferred seam, RunDetailPayload)
- `aidlc-docs/construction/unit-13/functional-design/business-rules.md`
  (BR-11 skin only, BR-15 chart seam, BR-16 ASCII, BR-18 build pipeline)
- `aidlc-docs/construction/unit-13/functional-design/business-logic-model.md`
  (request flows, build pipeline, deferred chart rendering)
- `aidlc-docs/construction/plans/unit-13-functional-design-plan.md`
  (FD-Q4 question text: the constraint set)
- Live code: `viz/server.py`, `viz/__main__.py`, `viz/assets/*`,
  `tools/build_viz_page.py`, `planning-docs/design/tokens.css`,
  `pyproject.toml` (Apache-2.0, stdlib-only server)

### What is being selected

The client-side rendering for a small, fixed set of charts in the unit-13
historical runs view. The server already serves the structured chart data
(verdict sequences with provenance, durations, distributions) inside
RunDetailPayload; only rendering is deferred. Candidates: a vendored
charting library, or hand-rolled vanilla JS. Hand-roll is scored as a
candidate too, on the same rubric.

### Hard constraints (disqualifying)

| # | Constraint | Source |
| --- | --- | --- |
| HC-1 | No runtime internet dependency. The viz is served from localhost; a CDN script tag breaks offline usage. The shipped artifact must be vendored as committed local files. | FD-Q4 |
| HC-2 | No build step. `tools/build_viz_page.py` only inlines local assets into the generated page module; the library must be usable as a plain `<script>` include or local-file ESM import, no npm/bundler/transpile. | FD-Q4, BR-18 |
| HC-3 | License must permit vendoring into an Apache-2.0 repo and redistribution with it. Permissive licenses only; copyleft (GPL/AGPL) excluded. Commercial/paid licenses are excluded outright: the code is redistributed inside this repo, which paid charting licenses (e.g. Highcharts) do not permit. | pyproject license, repo rules |
| HC-4 | ASCII-only committed files. All vendored assets must pass `tests/test_repo_ascii.py` (no non-ASCII bytes; most minified JS is ASCII, verify per artifact). | BR-16, AGENTS.md |
| HC-5 | Theming via the unit-9 token system. Colors must be driven by CSS custom properties (`--cds-*`, `--adl-*`) and follow `data-theme="dark"|"light"` on `<html>` without a rebuild. No new styling framework. | FD-Q4, BR-11, tokens.css |
| HC-6 | Any external charting library makes EXTSRF apply: the pinned artifact must be probed against the real interface before commitment, and the validated model recorded alongside it. | FD-Q4 answer, AGENTS.md reverse-engineering rules |
| HC-7 | Charts must degrade gracefully: unavailable metrics (with reason) must render as labeled unavailable states, never crash or fabricate. | BR-8, BR-15 |

### Soft constraints (preferences)

- SC-1 Small bundle preferred: the asset is inlined into page.py and served
  on every page request (target: under roughly 100 KB minified).
- SC-2 API ergonomics consistent with the existing hand-written vanilla JS
  (35 KB page.js, DOM-based, no framework conventions).
- SC-3 Modern evergreen browsers only (Chrome/Firefox/Safari/Edge); no
  legacy IE support needed.
- SC-4 Accessibility is valued: SVG-based, ARIA-annotated, keyboard
  reachable, with a readable fallback (the pages already carry structured
  tables and cards for the same data).

### Required feature/component inventory

1. Verdict sequence strips: per-attempt colored segments (mechanical /
   parsed / claimed / unverifiable), hover shows step + verdict + provenance.
2. Step duration bars: horizontal bars, one row per thread (or per step).
3. Status/verdict distributions: donut or bar of success/failed/interrupted/
   running and verdict breakdowns.
4. TDD red-green timelines: state transitions over time for a thread.
5. Aggregate trend lines: a metric plotted across the run history.
6. Tooltips carrying provenance labels and unavailability reasons (BR-12).
7. Dark/light theme switch with zero rebuild (HC-5).
8. Re-render on visibilitychange and manual refresh, no polling timers
   (BR-13).

### Design deliverables

None. No Figma or design handoff exists for the charts; FD-Q4 defers chart
layout to post-selection work against the existing unit-9 skin. The
template criteria for design-tool integration are therefore N/A.

### Template criteria explicitly N/A for this selection

Free tier / pricing (all viable candidates are OSS and free; paid
licensing is excluded by HC-3), GDPR/SOC2/ISO compliance, data residency,
multitenancy/org support, admin UX, token/session formats: these apply to
SaaS platforms, not to a vendored client-side JS library. They are covered
by HC-1..HC-7 and the licensing criterion instead.

### Weighted criteria (12)

Scoring: 1-5 per criterion. Weight 5 = critical: a score of 2 or less on
any weight-5 criterion disqualifies a candidate. Every criterion is
falsifiable from public sources (docs, LICENSE, GitHub, npm/unpkg,
bundlephobia, issue trackers).

| ID | Criterion | Weight | Falsifiable from |
| --- | --- | --- | --- |
| C1 | Offline operability: works entirely from committed local files, no CDN/network fetch at runtime | 5 | distribution docs, artifact inspection (unpkg/jsDelivr file self-containment) |
| C2 | Zero-build integration: usable as a plain script include (UMD/IIFE) or local-file ESM import, no npm/bundler/transpile | 5 | README install section, dist directory on GitHub/unpkg |
| C3 | License compatibility: permissive (MIT/BSD/Apache/ISC/Zlib), not copyleft, license text vendorable | 5 | LICENSE file in repo and npm metadata |
| C4 | Chart-type coverage: all five required charts (verdict strips, duration bars, distributions, timelines, trend lines) supported natively or with minimal composition | 5 | docs gallery, examples per chart type |
| C5 | Vanilla JS API: no React/Vue/Angular/jQuery peer dependency | 5 | docs framework-support pages, package metadata |
| C6 | Theming: runtime color control via CSS custom properties, dark/light switch without rebuild | 4 | docs theming/customization pages |
| C7 | Maintenance and integration burden: release cadence, breaking-change frequency, issue responsiveness (for hand-roll: our own ongoing cost) | 4 | GitHub releases/issues, npm publish dates |
| C8 | Bundle size: minified size of what gets inlined into page.py (target < 100 KB) | 4 | bundlephobia, unpkg file sizes |
| C9 | Documentation quality: API reference, per-chart examples, theming guide | 4 | docs site |
| C10 | Accessibility: SVG + ARIA, keyboard navigation, screen-reader support, graceful fallback | 4 | docs a11y sections, GitHub a11y issues |
| C11 | Browser compatibility: all evergreen browsers, plain localhost/file serving, no SW/CORS assumptions | 3 | docs browser-support matrix |
| C12 | Ecosystem size and lock-in risk: stars, contributors, community Q and A activity, exit path (data is plain JSON, charts are replaceable DOM) | 3 | GitHub metrics, Stack Overflow |

Weighted max score: 5*(5+5+5+5+5) + 4*(5+5+5+5+5) + 3*(5+5) = 125 + 100 + 30
= 255. (Corrected in Phase 2: the original Phase 1 arithmetic understated
the weight-4 and weight-3 maxima. Criteria and weights are unchanged; only
the denominator is corrected.) Scores are reported as weighted totals and
as a 0-100 percentage of max.

### Hand-roll candidate note

Hand-roll is scored on the same 12 criteria using repo evidence (the 35 KB
hand-written page.js demonstrates the team's vanilla JS/SVG capability):
it trivially satisfies C1, C2, C3, C5, C6, C8, C11, C12 (zero lock-in), and
is penalized on C4 (we must build all five chart types), C7 (ongoing
maintenance cost lands on us), C9 (we write the docs), C10 (we must build
a11y from scratch). Its score reflects effort, not capability.

## Phase 1 gate status

Awaiting user approval of the constraints and the 12 weighted criteria.

## Phase 2: Longlist and evidence

### Method

Gathered Nov 9 2026, live sources only:

- GitHub repo metadata (stars, latest release, last push) via `gh api`.
- npm metadata for published versions and peer dependencies.
- Exact dist file sizes by downloading pinned versions to a temp dir and
  measuring bytes (jsDelivr CDN). This is the number HC-4 and C8 care
  about: the bytes that would be committed and inlined into page.py.
- bundlephobia for gzipped cross-checks and for dists too large to
  re-download (Plotly).
- Official docs fetched for feature claims (chartjs.org,
  echarts.apache.org, chartist.dev, chartscss.org, uplot README,
  plotly.com, observablehq.com/plot, billboard.naver.com).

### Scoring correction

Phase 1 recorded the weighted max as 223. That was arithmetic error:
weight-4 criteria (C6-C10) each max at 4 * 5 = 20, five criteria = 100,
not 80. Weight-3 criteria (C11, C12) each max at 3 * 5 = 15, two = 30, not
18. Corrected max = 125 + 100 + 30 = 255. Criteria definitions and weights
are unchanged; only the denominator is corrected. All percentages below
use 255.

### Longlist (15 candidates + hand-roll)

| Candidate | Repo | License | Latest release | Stars | Status |
| --- | --- | --- | --- | --- | --- |
| Chart.js | chartjs/Chart.js | MIT | 4.6.1 (2026-10-22) | 67,784 | Active |
| ECharts | apache/echarts | Apache-2.0 | 6.1.0 (2026-05-19) | 67,696 | Active |
| D3 | d3/d3 | ISC | 7.9.2 (2026-11-03) | 113,887 | Active |
| uPlot | leeoniya/uPlot | MIT | 1.6.32 (2025-03-14) | 10,627 | Single maintainer, low activity |
| ApexCharts | apexcharts/apexcharts.js | Revenue-based (ex-MIT) | 8.8.1 (2026-11-08) | 15,187 | Active |
| Frappe Charts | frappe/charts | MIT | 1.6.3 tag, npm stuck at 1.6.2 (2022-04-27) | 15,093 | Dead since 2022 |
| Charts.css | ChartsCSS/charts.css | MIT | 1.2.0 | 6,583 | Slow but alive (pushed 2026-04) |
| Plotly | plotly/plotly.js | MIT | 4.2.0 (2026-10-29) | 18,377 | Active |
| Observable Plot | observablehq/plot | ISC | 0.6.19 (2026-11-05) | 5,421 | Active, pre-1.0 |
| Chartist | chartist-js/chartist | MIT or WTFPL | 2.2.0 (2026-10-20) | 13,460 | Revived; v2 rewrite |
| Recharts | recharts/recharts | MIT | 3.10.1 | 27,692 | Active (React only) |
| Highcharts | highcharts/highcharts | Proprietary paid | current | 12,502 | Active |
| billboard.js | naver/billboard.js | MIT | 4.1.3 (2026-10-16) | 6,025 | Active |
| C3 | c3js/c3 | MIT | 0.7.20 (2020-08-08) | 9,350 | Dead since 2020 |
| amCharts 5 | amcharts/amcharts5 | Proprietary paid | current | 449 | Active |
| Hand-roll | this repo | Apache-2.0 (ours) | n/a | n/a | Precedent: 35 KB page.js |

### Measured dist sizes (minified bytes, the C8 number)

| Candidate | Dist file (pinned version) | Minified bytes | gzip |
| --- | --- | --- | --- |
| charts.css | charts.min.css 1.2.0 | 60,113 | ~6 KB |
| uPlot | uPlot.iife.min.js 1.6.32 | 51,081 | ~22 KB |
| Chartist | dist/index.umd.js 2.2.0 | 77,803 | ~27 KB |
| Chart.js | chart.umd.min.js 4.6.1 | 210,082 | ~70 KB |
| Observable Plot | plot.umd.min.js 0.6.19 | 211,860 | ~70 KB (est) |
| D3 | d3.min.js 7.9.2 | 280,622 | ~93 KB |
| billboard.js | billboard.pkgd.min.js 4.1.3 | 830,292 | ~142 KB |
| ApexCharts | apexcharts.min.js 8.x | ~947 KB (bundlephobia) | ~277 KB |
| ECharts | echarts.min.js 6.1.0 | 1,121,883 | ~368 KB |
| Plotly | plotly.min.js (plotly.js-dist-min) | ~4.6 MB (bundlephobia) | ~1.4 MB |

Notes: Chartist also ships compat.umd.js (7,850 bytes), a thin shim that
requires index.umd.js; both are plain script includes. Frappe Charts npm
has no 1.6.3 (tag never published) - the GitHub tag outruns npm by four
years. ECharts offers an official custom-build path
(echarts.apache.org/en/builder.html): a pre-built bundle restricted to
chosen chart types; size for our subset is unverified and would need a
Phase 5 measurement if ECharts advances.

### Candidate findings

Chart.js: canvas-based. Line/bar/doughnut/pie/polar/radar/scatter/bubble
and mixed charts; horizontal bars via indexAxis; stacked bars; line
`segment` scriptable styling. Verdict strips and red-green timelines are
buildable as horizontal stacked bars with per-segment colors plus custom
tooltips. Official a11y docs state canvas content is not accessible to
screen readers and ARIA is do-it-yourself
(chartjs.org/docs/latest/general/accessibility.html). Colors come from JS
options; CSS custom properties need a getComputedStyle bridge and a
re-render on theme switch.

ECharts: 20+ series types including a custom series (full control of
canvas or SVG), heatmap, pictorialBar, themeRiver; components
tooltip/legend/dataZoom/timeline/visualMap/markPoint/markLine/markArea.
Apache-2.0, matching this repo's license exactly. The `aria` option ships
in core: source-verified component
(src/component/aria/install.ts registers an aria preprocessor and a visual
stage handler that generate ARIA descriptions). Official custom builds and
a style handbook (themes, CSS, dark mode) exist
(echarts.apache.org/handbook/en/concepts/style). Full bundle is 1.1 MB.

Chartist v2: ESM-first rewrite; docs say "No build step, no bundler, no
framework... a `<script type=module>` is all you need", plus the UMD dist
above. Charts are SVG styled by CSS classes and custom properties
(--ct-series-a etc.); "There's no theme option... style them the way you
style everything else". Layer composition API: chart() with
cartesianAxes/axisGrid/axisLabels/line/bar/pie/donut layers, plus band()
layers for shaded ranges (mark:true adds ct-in-band/ct-out-band classes,
a natural fit for red-green timelines). Composed charts, custom layers,
events, plugins, CSP-friendly. MIT or WTFPL. v1 compat preserved via the
compat UMD shim.

Charts.css: CSS-only framework (no JS), MIT. Semantic HTML tables styled
into bar/column/area/line/pie/radar/stacked/3D charts. Inherently
accessible (real table markup). No tooltips or interactivity: all hover
JS (provenance tooltips) would be hand-written on top. A verdict strip is
representable as one table row whose cells are colored segments, but the
convention is ours to invent (docs: chartscss.org).

Plotly: MIT, dozens of chart types (statistical, 3D, scientific, maps,
financial, range bars, Gantt-like). Loads via plain script tag. The
4.6 MB minified bundle is the whole story against it.

Observable Plot: ISC. Layered grammar of graphics (marks: line/bar/dot/
area/rect etc.), good for distributions and aggregates. v0.6.x is
pre-1.0 with API churn risk. Styling via JS options; no a11y story. UMD
dist exists but the project is ESM-first.

billboard.js: MIT, D3-based (packaged UMD bundles D3). Line/bar/area/
scatter/pie/donut/gauge/radar plus grid regions (red-green banding).
v4 is modular ESM with a canvas renderer option. 830 KB packaged.

Hand-roll: this repo already ships 35 KB of hand-written vanilla JS
(page.js) plus hand-written SVG icons, so the capability is proven. Full
control of tokens.css theming (CSS custom properties drive SVG fills
directly), zero new dependencies, zero vendor files to audit. Cost: all
five chart types, tooltips, and the a11y story are ours to build and
maintain forever.

### Scoring (1-5, weights 5/5/5/5/5/4/4/4/4/4/3/3; max 255)

Weight-5 score of 2 or less disqualifies (per Phase 1 rule).

| Candidate | C1 | C2 | C3 | C4 | C5 | C6 | C7 | C8 | C9 | C10 | C11 | C12 | Weighted | % | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Chart.js | 5 | 5 | 5 | 4 | 5 | 3 | 5 | 3 | 5 | 2 | 5 | 5 | 222 | 87.1 | Advance |
| ECharts | 5 | 5 | 5 | 5 | 5 | 3 | 5 | 1 | 4 | 4 | 5 | 4 | 220 | 86.3 | Advance |
| Charts.css | 5 | 5 | 5 | 3 | 5 | 5 | 3 | 5 | 3 | 5 | 5 | 2 | 220 | 86.3 | Advance |
| Hand-roll | 5 | 5 | 5 | 3 | 5 | 5 | 2 | 5 | 2 | 4 | 5 | 5 | 217 | 85.1 | Advance |
| Plotly | 5 | 5 | 5 | 5 | 5 | 3 | 5 | 1 | 4 | 3 | 5 | 4 | 216 | 84.7 | Cut (bundle) |
| Chartist | 5 | 5 | 5 | 3 | 5 | 5 | 3 | 5 | 3 | 3 | 5 | 3 | 215 | 84.3 | Advance |
| billboard.js | 5 | 5 | 5 | 4 | 5 | 3 | 4 | 1 | 4 | 2 | 5 | 3 | 200 | 78.4 | Cut |
| Observable Plot | 5 | 4 | 5 | 3 | 5 | 2 | 3 | 3 | 4 | 2 | 5 | 3 | 190 | 74.5 | Cut |
| C3 | 5 | 5 | 5 | 3 | 5 | 3 | 2 | 2 | 3 | 2 | 5 | 2 | 184 | 72.2 | Cut |
| D3 | 5 | 5 | 5 | 2 | 5 | 4 | 5 | 2 | 5 | 3 | 5 | 4 | 213 | 83.5 | DQ (C4) |
| uPlot | 5 | 5 | 5 | 2 | 5 | 2 | 3 | 5 | 2 | 2 | 5 | 2 | 187 | 73.3 | DQ (C4) |
| Frappe Charts | 5 | 5 | 5 | 2 | 5 | 3 | 1 | 5 | 2 | 2 | 5 | 1 | 180 | 70.6 | DQ (C4) |
| Recharts | 5 | 2 | 5 | 4 | 2 | 2 | 5 | 2 | 5 | 3 | 4 | 5 | 185 | 72.5 | DQ (C2, C5) |
| ApexCharts | 5 | 5 | 2 | 5 | 5 | 4 | 5 | 1 | 5 | 5 | 5 | 4 | 222 | 87.1 | DQ (C3) |
| Highcharts | 5 | 4 | 2 | 5 | 5 | 4 | 5 | 2 | 5 | 5 | 5 | 5 | 224 | 87.8 | DQ (C3) |
| amCharts 5 | 5 | 4 | 2 | 5 | 5 | 4 | 4 | 2 | 5 | 4 | 5 | 3 | 210 | 82.4 | DQ (C3) |

Disqualification evidence:

- ApexCharts: license changed from MIT to a revenue-based model. The
  Community License covers orgs under $2M USD revenue and allows
  redistribution of the library itself with attribution, but embedding in
  a product distributed to other users requires the paid OEM license
  regardless of org size. This repo is distributed via PyPI, so the
  Community License does not cover us. Without the license it would score
  222, tied with Chart.js - the license is the decisive fact.
- Highcharts: proprietary Standard License Agreement 19.1 (Internal/SaaS/
  OEM, paid, no redistribution). Fails HC-3 outright. Would score 224 if
  free - the highest would-be score, which is worth remembering when
  weighing a11y quality.
- amCharts 5: paid seats ($80-$2700+) and a paid OEM license for
  redistributable apps. Fails HC-3.
- Recharts: React peer dependency (react ^16.8-^19 confirmed on npm), so
  C5 fails; the JSX/bundler toolchain also fails C2.
- uPlot: no pie/donut, time-series oriented, categorical axes need manual
  tick work; four of five inventory charts would be heavy composition.
- Frappe Charts: dead since 2022 (npm three releases behind the GitHub
  tag); no donut/timelines/strips.
- D3: not a chart library; out-of-the-box coverage of the five inventory
  charts is zero (scales/axes/shapes are primitives). D3 remains on the
  table only as a toolkit under the hand-roll option, not as a standalone
  candidate.

### Score cluster note

The top six survivors sit within 7 weighted points (222 to 215): scores
cluster because every survivor passes the weight-5 criticals and the
difference lives in C6-C12 tradeoffs. Ranking alone cannot pick a winner;
the discriminating questions are qualitative: canvas a11y reality
(Chart.js C10), bundle cost of ECharts vs its coverage, Charts.css
interactivity gap, and Chartist's post-rewrite stability. Those are
exactly Phase 3 (community verification) and Phase 5 (spike) questions.

### Phase 3 proposal

Advance Chart.js, ECharts, Charts.css, Chartist, and Hand-roll to
community verification. Cut Plotly (4.6 MB inlined into page.py is
indefensible for a localhost tool), billboard.js (830 KB + weak a11y),
Observable Plot (pre-1.0 churn), C3 (dead).

Community verification targets per candidate (GitHub issues, HN/Reddit
threads, Stack Overflow activity, release cadence):

- Chart.js: real-world a11y experience; theme-switch re-render patterns;
  maintenance of the segment/scriptable APIs we would lean on.
- ECharts: quality of the aria implementation in practice (issue
  reports); measured custom-build sizes people have published; bundle
  complaints.
- Charts.css: real-world usage reports; whether complex layouts (strips,
  timelines) hold up; maintenance energy of a small project.
- Chartist: v2 rewrite bug reports and stability; v1-to-v2 migration
  pain; whether the revival is durable.
- Hand-roll: not community-verifiable; the repo's own page.js precedent
  is the evidence base.

## Phase 2 gate status

Awaiting user approval of the longlist, scores, and the Phase 3
candidate set before starting community verification.

## Phase 2 gate: APPROVED (2026-11-09)

User approved the longlist, scores, and the Phase 3 candidate set. Added
directive: hand-roll is the baseline against which every library candidate
is compared. A library only wins if it beats hand-roll on the dimensions
that matter (C4 effort, C7 maintenance, C10 a11y), not merely if it
survives scoring.

## Phase 3: Community verification (in progress)

Method: GitHub issue search via `gh api`, HN search via the Algolia API,
web search for Reddit and blog reports. Every claim below cites its
source. Candidates: Chart.js, ECharts, Charts.css, Chartist, compared
against the hand-roll baseline.

### Ecosystem scale (npm weekly downloads, npm API, Nov 2026)

| Candidate | Weekly downloads | SO questions |
| --- | --- | --- |
| Chart.js | 16,837,102 | 12,335 |
| ECharts | 10,394,814 | 1,465 (community skews to GitHub discussions) |
| Chartist | 137,195 | 234 |
| Charts.css | 23,264 | 0 |

### Chart.js community findings

- Accessibility: github issue chartjs/Chart.js#1976 "Accessibility support:
  keyboard navigation" has been OPEN since 2016-02-03 (36 comments, 10
  years). Canvas content is not readable by screen readers; the docs
  themselves prescribe DIY role/aria-label on the canvas element.
- Academic corroboration: Sharif et al. 2021 (UW, 143 citations) report
  screen-reader users "do not have access to the data contained in"
  ChartJS-produced visualizations.
- Dark mode: prefers-color-scheme support landed 2021 via JS options;
  community pattern is re-render on theme change. No native CSS-variable
  reading.
- Maintenance: v4.6.1 (2026-10-22), steady cadence, huge ecosystem.

### ECharts community findings

- Accessibility is opt-in and partial. The aria option exists (component
  source-verified in src/component/aria), but the official handbook says
  aria is no longer imported by default since ECharts 5 and must be
  manually imported. Keyboard navigation does not exist:
  apache/echarts#18585. Recent aria edge-case bugs (label text corruption
  via dollar sequences, stale aria after disabling) were actively fixed
  Oct 2026, so the feature is maintained, but it is a describing label,
  not interactive access.
- GitLab evaluated ECharts and filed their own a11y gap issue
  (gitlab-org/gitlab#25993) before committing.
- Bundle: the online builder and modular imports are the community's
  standard answer to the 1.1 MB full bundle (HN ECharts 6 thread, Aug
  2025). Note: builder output size for our chart subset is unverified.
- Reputation: HN threads ("Apache ECharts", Apr 2025, 250 comments;
  "Apache ECharts 6", Aug 2025) call it "the best overall charting
  library", "the most powerful open source library that's not D3", with
  props for backward compatibility across major releases, and complaints
  about API confusion across upgrades and difficulty extending toward
  Gantt-style custom charts.
- Maintenance: Apache governance, 6.1.0 (2026-05-19).

### Charts.css community findings

- Accessibility is its core pitch and HN backs it: "It requires a lot
  more work to make it accessible for screen readers. This just falls
  back to a table element" (HN 2025-04-12 thread, 23 comments).
- Functional gap: charts cannot show labels on both axes; the fix is
  blocked on "replace flex with grid", open since 2021-05-04
  (ChartsCSS/charts.css#45). Data labels on bars are also missing.
- Maintenance energy is low: releases 1.2.0 (2025-07) then 1.1.0
  (2023-10); a line-clipping bug open since 2022; "from github it looks
  unmaintained" comment in the same HN thread. 23K weekly npm downloads.

### Chartist v2 community findings

- v2.0.0, v2.1.0, and v2.2.0 all shipped 2026-10-14 through 2026-10-20:
  the rewrite is three weeks old at research time. Zero real-world soak.
  Real v2 bugs already filed and fixed (animationBegin/animationEnd never
  fire, Oct 2026).
- Migration path is documented (chartist.dev/docs/coming-from-1x): v1
  charts keep running under v2, functions migrate one chart at a time.
- Ecosystem is tiny: 137K weekly downloads, 234 SO questions, the v2 Show
  HN (2026-10-16) drew zero comments. The revival is genuine but young.

### Phase 3 verdict against the hand-roll baseline

User directive: hand-roll is the baseline every candidate must beat.
Baseline score: 217 (85.1%). Deltas versus the baseline:

| Candidate | Weighted | vs baseline | Wins versus baseline | Loses versus baseline |
| --- | --- | --- | --- | --- |
| Chart.js | 222 | +5 | C4 coverage, C7 maintenance, C9 docs | C6 theming, C8 bundle, C10 a11y |
| ECharts | 220 | +3 | C4, C7, C9 | C6, C8 (32x bundle), C12 |
| Charts.css | 220 | +3 | C10 a11y, marginal C7/C9 | C12 ecosystem, C4 ties |
| Chartist | 215 | -2 | marginal C7/C9 | C10 a11y, C12 ecosystem |

Reading: only Chart.js, ECharts, and Charts.css beat the baseline
numerically, and each buys its win by trading away something the baseline
is already good at. Chartist loses to the baseline outright (215 < 217)
while also being three weeks old. The pattern across sources is
consistent: canvas libraries (Chart.js, ECharts) cannot match the a11y
story we would hand-build as SVG plus the structured tables the pages
already carry (SC-4); the CSS/SVG candidates (Charts.css, Chartist) match
our theming model but bring thin ecosystems and, in Chartist's case, an
unsoaked rewrite.

## Phase 4 proposal (shortlist)

Shortlist for spike testing: Chart.js, ECharts, and the hand-roll
baseline.

- Cut Chartist: scores below the baseline and v2 is three weeks old.
- Cut Charts.css: its only decisive win is a11y, which hand-roll already
  matches via table fallback; its axis-label limitation (5 years open)
  and slow maintenance do not justify adopting it.

Phase 5 spike plan (proposed, pending approval): throwaway HTML pages
under a scratch directory (not committed, or committed under spikes/ per
the phase plan) exercising the five inventory charts plus theme switch
and tooltip/provenance for Chart.js and ECharts against the same data
fixtures, measuring: bytes added to the generated page.py, integration
friction with tokens.css, a11y output (what a screen reader gets), and
effort per chart type, each fact recorded as a validated-model entry per
HC-6.

## Phase 3 gate status

Awaiting user approval of the Phase 3 findings and the Phase 4 shortlist
(Chart.js, ECharts, hand-roll baseline).

## Phase 4 gate status

APPROVED (2026-11-09): shortlist is Chart.js, ECharts, and the hand-roll
baseline. Chartist and Charts.css are cut. Phase 5 spike plan approved.
Spike artifacts are built in the opencode temp directory while the
session is in planner mode, and will be copied to spikes/ when a
library is chosen.

## Phase 5: spike test results (Chart.js vs ECharts vs hand-roll)

Approved by the Phase 4 gate. Method per HC-6: every fact below was
established by a probe against the pinned artifacts, recorded as a
validated-model entry. Spike artifacts live in the opencode temp dir
until a library is chosen, then move to spikes/. Screenshots exist for
human review (dark and light PNG per variant); the agent's evidence is
programmatic because this model cannot view images. The user is invited
to open the screenshots in the spike results dir.

### Validated model (fact, probe)

Probe environment: headless Chrome (--headless=new), Node v24 CDP
harness (zero deps), file:// pages, 1280x1400 viewport, Emulation scale
2. Probe tools: Runtime.evaluate, Accessibility.getFullAXTree,
Page.captureScreenshot, Log.entryAdded, pixel sampling via getImageData.

| Fact | Probe that established it |
| --- | --- |
| Chart.js 4.6.1 renders all 5 inventory charts from vendored UMD | spike-chartjs.html, plain script tag, 1 canvas per slot, pixelVariety 112-390 |
| Chart.js requires a canvas element; init on a div silently fails | console error "Failed to create chart: can't acquire context from the given item", chart absent; fixed with canvasFor helper |
| Chart.js canvas interiors expose nothing to screen readers | AX tree 59 nodes: 6 image roles (our aria-labels), 5 unnamed Canvas roles, 12 StaticText |
| ECharts 6.1.0 full bundle renders all 5 charts as SVG | spike-echarts.html?variant=full, 1 svg per slot, 8-22 text nodes per slot |
| ECharts SVG text reaches screen readers as data | AX tree 300 nodes, 89 StaticText including 78 data-bearing (ticks, category labels, axis names, legend labels) |
| ECharts aria:{enabled:true} is a no-op for SVG rendering | ?aria=1 run: AX tree identical (300 vs 300, identical signature sets) |
| ECharts simple bundle lacks the SVG renderer | ?variant=simple: falls back to canvas (canvas:1, svg:0 per slot), AX collapses to 64 nodes, canvas-like profile |
| ECharts common bundle behaves like full for our charts | ?variant=common: 303 AX nodes, SVG present |
| Theme switch re-render works for both libraries | theme flip: renderCount 1->2, computed tokens #42BE65->#198038 etc., aria-labels update |
| visibilitychange re-render works for both | visibilityState defineProperty trick: handler fires, renderCount increments (+1 per event) |
| HC-7 degradation path works for both | thread verify-9c1d with metrics {} renders fallback text + verdict badges, 0 exceptions |
| No console errors or exceptions in any variant | Log.entryAdded + Runtime.exceptionThrown captured 0 errors across all 4 probe runs |
| ECharts 6.1.0 has no working Node SSR path | echarts.init(null, null, {ssr:true}) throws "Cannot read properties of null (reading 'nodeName')" in Node v24.2.0 |
| Exact vendor bytes | downloaded from jsDelivr, measured: chart.js 210,082; echarts simple 500,315; common 715,020; full 1,121,883 |

### Cost model: bytes inlined into generated page.py

page.py today is 66,490 B (assets inlined, unit-9 pattern). Adding a
library inlines the vendor file plus chart code into the generated
page.py (BR-18 build emits it). Estimated page.py sizes:

| Route | Added | Resulting page.py | Growth |
| --- | --- | --- | --- |
| hand-roll | ~12,000 B chart JS | ~78,500 B | 1.2x |
| Chart.js | 210,082 + ~20,000 | ~297,000 B | 4.5x |
| ECharts simple | 500,315 + ~19,000 | ~586,000 B | 8.8x |
| ECharts common | 715,020 + ~19,000 | ~800,000 B | 12.0x |
| ECharts full | 1,121,883 + ~19,000 | ~1,207,000 B | 18.2x |

### Effort model: chart code lines per chart (spike measured)

| Chart | Chart.js | ECharts |
| --- | --- | --- |
| verdict sequence strips | 43 | 33 |
| step duration bars | 25 | 13 |
| red-green timeline | 32 | 14 |
| verdict distribution | 35 | 36 |
| aggregate trend | 26 | 28 |
| total for 5 charts | 161 | 124 |

Both spike pages carry ~470-490 JS lines total including the shared
shell (theme plumbing, degradation, report hooks, ~300 lines that any
approach needs). Hand-roll estimate for the same 5 charts: ~195-230
LOC (SVG renderers plus a11y text), because the inventory is simple
(strips, bars, stepped line, pie, two-line trend). The library's chart
coverage advantage only materializes if unit-13 later needs exotic
chart types; for this inventory the hand-roll cost is modest.

### Theming friction (C6)

Both libraries require the same bridge: read CSS tokens via
getComputedStyle at render time and pass them as JS options, then
re-render on data-theme change. No library reads CSS variables
natively. Verified working for both (tokens flip in the spike).
Hand-roll SVG would use var(--cds-*) directly, zero bridge, zero
re-render (CSS does the work). This is the one area where the
libraries demonstrably cannot beat the baseline.

### Accessibility (C10), the decisive spike finding

- Chart.js: screen readers get our DIY aria-label summaries on role=img
  containers; the chart interiors (canvas) expose nothing. Same
  posture as the repo's existing pipeline graph, and same limit.
- ECharts full/common: screen readers get the real chart data: axis
  ticks, category labels ("A1 deps"), "Attempt 1/2/3", axis names,
  legend labels ("pass", "fail", "env", "unverifiable") as 78
  data-bearing StaticText nodes, plus our summaries. Validated, not
  asserted. The aria option adds nothing on top (no-op for SVG).
- Hand-roll: would match ECharts (SVG text is naturally exposed), at
  our own code cost.
- ECharts simple is ruled out as an a11y option: without the SVG
  renderer it silently degrades to canvas and the AX tree collapses.

### Phase 5 verdict vs baseline

Baseline hand-roll: 217 weighted. Spike evidence changes the Phase 2
numbers qualitatively, not numerically:

- Chart.js 222: beats baseline on coverage, maintenance, docs. Spike
  confirms the C10 loss is real (canvas interiors are invisible to
  screen readers). 210 KB vendor cost.
- ECharts 220: beats baseline on coverage, maintenance, docs, and adds
  a spike-validated a11y story hand-roll would have to build: SVG
  charts whose text is real DOM text. Cost: 715 KB (common) or 1.1 MB
  (full) inlined into page.py, and the getComputedStyle bridge.
- Hand-roll 217: wins theming (direct CSS vars, no re-render), bytes
  (1.2x page.py), and lock-in freedom; pays with ~200 LOC of chart
  code we own forever.

Recommendation for the gate: ECharts, common bundle. It is the only
candidate whose charts are readable by screen readers out of the box
(validated), it carries Apache-2.0, and its score beats the baseline.
The accepted cost is page.py growing to ~800 KB (localhost serving,
no network: acceptable) and the JS-options theming bridge (proven in
spike). Hand-roll remains the fallback if the user weighs bytes and
theming purity above a11y-for-free; Chart.js is the middle ground if
a11y posture equal to today's pipeline graph is acceptable.

## Phase 5 gate status: APPROVED - ECharts common bundle (2026-11-09)

User verdict (verbatim): "Ok, ECharts is good enough now. Record the
verdict in the spike docs". This resolves the Phase 5 gate. The
selection for unit-13 is ECharts 6.1.0, common bundle, SVG renderer,
vendored and pinned.

How the verdict was reached: the first side-by-side showed Chart.js
charts reading better than the ECharts spikes. The user scoped the
difference as chart form, not styling ("don't care about doughnut vs
pie, but the other differences are important"). A convergence round
rewrote the ECharts spike to mirror the Chart.js forms (verdict
strips, timeline bands, duration bars, trend smoothing), and a layout
round fixed two real bugs: the CSS grid min-width deadlock (panels
refused to shrink, freezing Chart.js canvases oversized and ECharts
SVGs undersized) and ECharts' missing resize observation. After
convergence plus fixes the user judged ECharts acceptable and chose
it, keeping the validated screen-reader data exposure (SVG text) that
Chart.js canvas cannot provide, at the cost of the larger vendor file.

### Final selection record

- Library: Apache ECharts 6.1.0, pinned artifact echarts.common.min.js
  (jsDelivr, 715,020 bytes, SHA256
  61f13280aba3161f77fc801bf5f9c786f55f10432153a0683fc5cc0700b013d5),
  Apache-2.0. Vendor byte cost accepted: page.py grows from ~66 KB to
  ~800 KB (12x), localhost-only serving.
- Renderer: SVG (screen-reader data exposure validated; AX tree 309
  nodes with data-bearing StaticText).
- Ruled out in spike: simple bundle (no SVG renderer, silently falls
  back to canvas, a11y collapses); full bundle only if custom series
  becomes necessary (not needed for the inventory).
- Chart idioms (validated on the common bundle): verdict strips via
  thick-line segments (line series, symbol none, lineWidth 14, one
  series per verdict segment, [i, attempt] pairs); timeline via
  stacked area bands (failing/passing) plus a stepped verdict line on
  a secondary axis; durations and trend as bars and smoothed lines.
  The official custom series is NOT in the common bundle (grep
  renderItem: 0 occurrences; unknown series types are silently
  dropped by setOption).
- Theming: getComputedStyle token bridge plus full re-render on theme
  change (BR-11 compliant; proven in spike for both themes).
- Sizing: explicit window resize handler calling inst.resize() on
  every instance (ECharts has no built-in resize observation);
  product CSS must set min-width: 0 on grid children to avoid the
  resize deadlock (validated).
- aria option: validated no-op for SVG rendering in 6.1.0; do not
  rely on it. The SVG text exposure is the a11y story.
- A11y priority note: the user deprioritized screen-reader support
  during the gate discussion; the re-weighted sensitivity (C10 weight
  4 -> 1) favors Chart.js 216 vs ECharts 208. The user chose ECharts
  anyway on converged visual parity plus free SVG data exposure.

### Follow-ups queued for the build phase

- Copy spike artifacts from the opencode temp dir into the repo
  spikes/ directory (queued; planner mode cannot write there).
- build_viz_page.py must emit: the vendor file inlined, the resize
  handler, the token bridge, and the min-width: 0 CSS rule. No
  canvasFor guard needed (that was a Chart.js-only trap).
- HC-6/EXTSRF: the pinned artifact plus SHA256 above is the vendored
  fixture; re-probe before any version bump.

## Selection outcome

Final: ECharts 6.1.0 common bundle (weighted 220 vs hand-roll
baseline 217), chosen after a convergence round that closed the visual
gap the user flagged against Chart.js. The selection is final for
unit-13 functional design. Chart.js 4.6.1 remains the documented
runner-up; hand-roll remains the documented fallback.
