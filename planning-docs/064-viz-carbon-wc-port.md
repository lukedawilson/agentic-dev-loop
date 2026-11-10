# Plan 064: Port the viz to vendored Carbon Web Components

> **Status: APPROVED**

## Problem

The unit-9 design artefacts specify a Carbon Web Components re-skin of the
viz (`planning-docs/design/viz-redesign-spec.md` section 3 and 5). During
construction the Carbon runtime was dropped after the CDN module hit
bare-specifier resolution errors and the adopted `cds-tile` elements proved
purely presentational; what shipped is the spec's hand-written fallback skin
(plain HTML + token-exact CSS). The result works, but it is not the designed
product surface: accordion keyboard/ARIA behaviour and notification semantics
are hand-rolled, and every chrome component is a custom re-implementation of
something the design system already ships.

This plan ports the viz (live page and run-history page) to the real Carbon
Web Components, vendored into the repo, with no or minimal visual and
behavioural change, per the analysis in
`brainstorming/viz-carbon-wc-port-analysis.md`.

## Approved decisions (from brainstorming)

1. **Fallback rescinded** (user directive, 2026-11-10): Carbon becomes a hard
   dependency. The plain-HTML fallback skin is deleted as swaps land, not
   kept. `viz-redesign-spec.md` R30/AC6 and the `frontend-development` skill's
   viz scope note ("the Carbon CDN is an enhancement, never a dependency")
   are amended in this change (task 5.4).
2. **Vendored, not CDN** (user directive, 2026-11-10): the Carbon chunk graph
   and theme sheet are vendored under
   `src/agentic_dev_loop/viz/assets/vendor/` with a SHA-256 manifest, the
   echarts precedent. No runtime network dependency.
3. **Scope: full port** (analysis option B): live page plus run-history page.
   Deferring task block 4 yields the live-page-only option (A) if the user
   prefers; the plan as written covers B.
4. **Theme sheet is mandatory** (finding F10/F11/F12): `themes.css` is
   vendored and inlined ahead of the skin. Without it, 250 of 268 referenced
   `--cds-*` tokens fall back to baked white-theme values inside the bundles
   and both themes silently render Carbon-stock white (our skin defines only
   19 tokens).
5. **Minimal-change swaps skipped**: `cds-code-snippet` (thread-id chip) and
   `cds-copy-button` are NOT adopted. Their Carbon feedback UX (tooltips)
   differs from the shipped text-swap/icon-swap behaviour, and the existing
   25 lines of copy logic are registered, tested, and token-styled. Native
   `<input type="date">` and `<details>` expanders stay for the same reason
   (Carbon's date picker drags in flatpickr; Carbon has no disclosure widget
   besides accordion).

## Scope

- `tools/vendor_carbon_wc.py`: new vendoring tool (fetch, walk, sanitise,
  hash, manifest, `--check` mode).
- `src/agentic_dev_loop/viz/assets/vendor/`: new vendored tree (58 JS files +
  `themes.css` + `manifest.json`).
- `src/agentic_dev_loop/viz/server.py`: new `/vendor/` static route.
- `src/agentic_dev_loop/viz/assets/{page.html,page.js,runs.html,runs.js,
  styles.css}`: component swaps, theme-zone wiring, deleted superseded rules.
- `tools/build_viz_page.py`: marker handling for the theme-sheet `<style>`
  block and the vendor script tags; drift rebuild of `viz/page.py`.
- `tests/test_vendor_carbon_wc.py` (new), `tests/test_viz.py` (harness +
  swapped-component rewrites), `tests/test_unit13_runs_viz.py` (markup
  substring updates), `tests/test_viz_serve.py`-level route tests.
- `planning-docs/design/viz-redesign-spec.md`: amend R30/AC6 to the vendored
  hard-dependency posture.
- `.opencode/skills/frontend-development/SKILL.md`: rewrite the viz scope
  note (vendored Carbon is now the viz baseline).
- `frontend/viz-register.md`: class sync (removals of swapped-skin classes,
  registration of new host/override classes).
- `CHANGELOG.md`: one entry.

No tooling-path deliverables: none of `agentic-dev-loop/pipeline.yaml`,
`agentic-dev-loop/sdk_agents/`, or `README.md` is touched.

Out of scope: the pipeline graph, ANSI console, event log, attempt meter,
ECharts charts, `<details>` expanders, metric/artifact cards (all stay custom
by design, per the spec's own custom-surface list); any ECharts or
pipeline-layout change; adding Plex webfonts (the stack keeps falling back to
system fonts, adding the webfont would itself be a visual change); upgrading
past `v2.65.0`.

## Context (verified facts)

Full evidence, probes, and parity analysis:
`brainstorming/viz-carbon-wc-port-analysis.md`. The load-bearing facts:

- **F3/F7**: the official per-component bundles at
  `https://1.www.s81c.com/common/carbon/web-components/version/v2.65.0/<name>.min.js`
  use relative chunk imports only; the 11 entries we need (`ui-shell`,
  `notification`, `tile`, `tag`, `accordion`, `code-snippet`, `copy-button`,
  `checkbox`, `button`, `data-table`, `link`) close into a flat graph of 58
  JS files, zero bare imports, no subdirectories. (code-snippet and
  copy-button are fetched anyway: negligible bytes, keeps the door open
  without re-vendoring; they are simply not referenced by the pages.)
- **F8**: 1,688,010 bytes raw, ~219 KiB gzip total.
- **F9**: one non-ASCII byte sequence in the graph (a corrupted `ß` in a dead
  `data-table.min.js` CSS selector); the vendor tool sanitises it.
- **F10/F11/F12 (finding 1)**: `themes.css` (74,258 bytes, ASCII-clean, 1,832
  declarations, zones `white/g10/g90/g100`) plus our existing skin gives
  complete token coverage; the 7 tokens it does not define self-declare on
  component `:host` rules inside the bundles.
- **F11 (finding 2)**: `themes.css` is only served at the mutable URL
  `https://1.www.s81c.com/common/carbon/web-components/tag/v2/latest/themes.css`
  (versioned paths 404; no prebuilt equivalent in `@carbon/themes`). The pin
  is therefore the vendored bytes + SHA-256
  `7dfb6a11c6b69ae0af8975b51cf10c50eda4ca914be04ab2ae885e338584516` + fetch
  date 2026-11-10, recorded in the manifest. Re-vendoring is manual and
  diff-reviewed.
- **F13**: all 20 expected `cds-*` tags register from these entries (verified
  via per-entry chunk-closure reachability; registration is decorator-based
  and invisible to a naive grep).
- Serving: the JS graph must be static files (relative chunk imports cannot
  be inlined into `viz/page.py` the way echarts is). `themes.css` is plain
  CSS and inlines as a `<style>` block at build time.
- Test surface: `tests/test_viz.py` (1,599 lines) includes a Node DOM-shim
  harness executing the real `page.js`; its `createElement` is tag-agnostic
  so `cds-*` elements work as stubs, but ~10-20 methods asserting on swapped
  components' classes/innerHTML must move to attribute assertions (`open`,
  `kind`, `hide-close-button`). `tests/test_unit13_runs_viz.py` has HTML
  substring checks. No browser-level tests exist; the phase-5 parity pass is
  manual against the prototype oracle.
- Unprobed component behaviours (U1-U5, U7 in the analysis): notification
  `hide-close-button`, `cds-tag` warning-colour override reach,
  `cds-accordion-item::part(title)` coverage, `cds-code-snippet` feedback
  (only relevant if decision 5 is revisited), `cds-table` density sizing,
  theme cascade order, accordion toggle event contract. These gate task
  block 2 and are resolved by task 0.1's browser spike before any swap lands.

## Design

### Vendoring tool (`tools/vendor_carbon_wc.py`)

Stdlib-only, mirroring the echarts vendor script's shape:

1. Fetch the 11 pinned entry bundles from the `v2.65.0` base URL.
2. Recursively walk relative `from"./<chunk>"` import specifiers to pull the
   transitive chunk graph (the probe's `probe.py` is the working reference
   implementation; it lives in the scratch dir and gets ported into the
   tool).
3. Fetch `themes.css` from its `latest` URL.
4. ASCII-sanitise every file (the echarts precedent); the `data-table.min.js`
   `ß`-in-dead-selector case is the known exception and is handled by the
   same byte-substitution path, recorded in the manifest.
5. Write `vendor/manifest.json`: per file `{"sha256": ..., "bytes": ...,
   "source_url": ..., "fetched": "2026-11-10"}`, plus the entry list and the
   Carbon version.
6. `--check` mode (no network): re-hash every file on disk and fail on any
   mismatch against the manifest; also fail on non-ASCII bytes, bare imports,
   or imports escaping the flat vendor dir. Runs in tests (task 1.3), keeping
   the vendored tree honest the way the echarts checks do.

### Serving and loading

- `server.py` gains a `/vendor/<path>` route: static, read-only, `GET` only,
  correct `Content-Type` (`text/javascript` for `.js`), path-traversal
  rejection (resolve-and-check-prefix, 404 on escape), served from the
  package's `assets/vendor/` directory. Tests cover 200 for a known chunk,
  MIME, and traversal rejection.
- `page.html` / `runs.html` gain, in order:
  1. The FOUC rule (in `styles.css`): `cds-accordion-item:not(:defined), ...
     { visibility: hidden }` for every adopted tag.
  2. `<script type="module" src="/vendor/ui-shell.min.js">` (and one per
     adopted entry: `notification`, `tile`, `tag`, `accordion`, `checkbox`,
     `button`, `data-table`, `link`).
- `tools/build_viz_page.py` inlines `themes.css` as a `<style>` block
  **before** the existing skin block (finding 1: cascade order is the whole
  game; theme sheet first, then our 19-token overrides and skin rules win).
  The vendor `<script src>` tags pass through the inliner untouched, like the
  existing external references; the drift-guard's exactly-one-marker checks
  are updated to include the new theme marker.
- Theme wiring: the existing theme toggle sets `cds-theme-zone-g100` (dark)
  or `cds-theme-zone-white` (light) on `<html>` alongside the current
  `data-theme` attribute. The current skin keys off `data-theme` and keeps
  working for custom surfaces; Carbon internals read the theme-zone tokens.

### Component mapping and parity handling

Per the analysis (section 4). Visual deltas accepted as part of the port:
notification status icons, filled (not bordered) tag chips unless task 0.1
proves the override, Carbon accordion chevron + row metrics, Carbon focus
rings. Everything else targets pixel parity via `tokens.css`-era overrides,
host resets, and the sanctioned `::part(title)` hook. Behavioural invariants
that must survive: no auto expand/collapse of stage sections (user-directed
deviation, preserved by never setting `open` programmatically), watchdog
banner show/hide semantics, theme toggle behaviour, copy gating.

## Tasks

TDD throughout (red, green, refactor). Estimates are one engineer.

### Block 0: behavioural probe spike (0.5 day)

- 0.1: Browser spike answering U1-U5 and U7 against the pinned artifacts:
  `hide-close-button` support, `cds-tag` colour override reach (target: the
  current bordered-chip look, or fail = accept filled chips),
  `::part(title)` coverage for the stage status border, `cds-table` `size`
  density, theme cascade order in practice, accordion `open` attribute +
  `cds-accordion-item-toggled` event contract. Port the probe harness into
  `spikes/` (unit-13-charting precedent) and append the validated-model
  table to `brainstorming/viz-carbon-wc-port-analysis.md`. Go/no-go gate for
  block 2: any component that cannot hit the accepted look stays custom and
  its swap task is dropped from this plan.

### Block 1: vendoring + plumbing (1.0-1.5 days)

- 1.1: Red: `tests/test_vendor_carbon_wc.py` (manifest hash check, ASCII
  check, closure check, no-bare-imports check) fails because `vendor/` does
  not exist. Green: `tools/vendor_carbon_wc.py` fetches and writes the tree
  + manifest; `--check` passes. Human runs the fetch (network); the test
  path uses `--check` only.
- 1.2: Red: `/vendor/ui-shell.min.js` 404s; traversal test
  (`/vendor/../server.py`) expects 404. Green: `server.py` static route.
- 1.3: Wire `--check` into the test suite permanently.
- 1.4: Red: built `viz/page.py` contains the theme `<style>` block before
  the skin block (cascade-order test) and theme-zone class wiring. Green:
  `build_viz_page.py` marker + inline, `page.html`/`runs.html` script tags
  and theme-zone toggle in `page.js`/`runs.js`, FOUC rule, drift rebuild.
- 1.5: Browser smoke: both pages load with zero network requests outside
  `/vendor/` (server access log), all 20 tags defined, both themes render.

### Block 2: live page swaps (2.0-3.0 days), one commit per component

Per component, in dependency order: update `page.html`, `page.js`,
`styles.css` (delete superseded class rules, add host/`::part` overrides),
update the Node-shim tests to attribute assertions, sync
`frontend/viz-register.md`, drift rebuild, suite green.

- 2.1: Header: `cds-header`, `cds-header-name`, `cds-header-global-action`.
- 2.2: Stream banner: `cds-actionable-notification kind="warning"` with
  `hide-close-button` (per 0.1), `recover()` visibility logic unchanged.
- 2.3: Summary tiles: `cds-tile` x5 with host resets restoring the strip
  metrics.
- 2.4: Verdict badges: `cds-tag` per the 0.1 go/no-go (fail = warning-colour
  token override).
- 2.5: Run banner: `cds-inline-notification` (`success`/`error`,
  `low-contrast`, `hide-close-button`).
- 2.6: Stages accordion: `cds-accordion`/`cds-accordion-item`; title slot
  carries the 7-part stage header; status border via `::part(title)`; JS
  drives `open` instead of `.collapsed`; toggle handlers move to
  `cds-accordion-item-toggled`.

### Block 3: page.js + harness consolidation (1.0-1.5 days)

- 3.1: Finish the Node-shim updates spanning components (`customElements`
  stub if `page.js` feature-detects upgrades); the ~10-20 rewritten test
  methods land here and in block 2.
- 3.2: Delete the dead fallback-era CSS and register entries; suite green;
  viz lint green.

### Block 4: run-history page swaps (1.0-2.0 days)

- 4.1: `cds-checkbox` filter group, `cds-button kind="tertiary"` reset.
- 4.2: `cds-table` for `.runs-table` (density per 0.1); `runs.js` row
  template reshaped for shadow-wrapped markup; expanders stay `<details>`.
- 4.3: Metric cards/stat bands/artifacts: `cds-tile` or keep custom, decided
  per element against the register.
- 4.4: `test_unit13_runs_viz.py` substring updates (keep the back marker and
  expander affordance strings stable).

### Block 5: parity, a11y, docs (1.0-1.5 days)

- 5.1: Manual parity pass: 4 run scenarios x 2 themes x the standard
  breakpoints, against the current pages as oracle; keyboard navigation,
  focus order, reduced motion.
- 5.2: Amend `planning-docs/design/viz-redesign-spec.md` R30/AC6 to the
  vendored hard-dependency posture (no fallback skin; Carbon vendored and
  SHA-pinned).
- 5.3: Rewrite the `frontend-development` skill's viz scope note: the viz is
  vendored-Carbon-native; the register stays the class ledger for the
  custom surfaces.
- 5.4: `frontend/viz-register.md` final sync; `CHANGELOG.md` entry; this
  plan's status flips when everything is on main.

Total estimate: ~5.5-8 days (live page through block 3), ~6.5-10 days (full,
through block 5).

## Risks / notes

- `themes.css` mutable-URL pin (finding 2): the vendored bytes are the
  artifact of record; manifest carries SHA-256 + fetch date; re-vendoring is
  manual and diff-reviewed. Never scripted to auto-update.
- Shadow-DOM reach limits may block pixel parity for tags/notifications:
  block 0 gates this; worst case those components stay custom and the plan's
  swap tasks shrink, the rest is unaffected.
- Shim tests cannot see shadow DOM: assertions stay on light-DOM attributes;
  Carbon-internal behaviour is verified manually in task 5.1.
- Pin rot / future Carbon v3 registration change: the exact `v2.65.0` pin
  plus the manifest protect us; an upgrade is a deliberate re-vendor with
  diff review, not a background drift.
- The stage accordion's "no auto expand/collapse" deviation is preserved by
  construction (we never set `open`); do not "fix" it during the swap.

## Acceptance

1. Both pages render from vendored Carbon components with zero network
   requests outside `/vendor/` (proven by server access log in 1.5 and
   re-verified in 5.1).
2. Full test suite green, including `test_repo_ascii.py`, the vendor `--check`
   test, the drift guard, and the viz lint.
3. Visual parity within the accepted deltas (notification icons, tag fill,
   accordion chevron/metrics, focus rings) across 4 scenarios x 2 themes.
4. Behavioural invariants hold: no auto accordion mutation, watchdog banner
   semantics, theme toggle, copy gating, per-node console replay.
5. R30/AC6 and the skill scope note amended; register synced; CHANGELOG
   entry; no fallback-era CSS left behind.
