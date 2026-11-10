# Viz Carbon Web Components port: effort analysis

Analysis, not a plan. It answers: what would it cost to port the shipped viz
(live page + run history) from the hand-written token skin to styled Carbon
Web Components (the original design intent), with no or minimal visual or
behavioural change. If the answer is "go", a separate implementation plan
follows.

**Decision update (2026-11-10, user directives in chat):**
1. The graceful degradation fallback is rescinded. If the port happens,
   Carbon becomes a hard dependency and the plain-HTML fallback skin is
   deleted, not kept. The spec's R30/AC6 get amended at implementation time.
2. Loading path: **vendored**, not CDN. Probed on 2026-11-10; results in the
   probe section below.

## 1. How we got here

- The unit-9 design artefacts (`planning-docs/design/`) are explicitly a
  **Carbon Web Components re-skin**: `viz-redesign-spec.md` section 3 mandates
  `@carbon/web-components` v2 from a pinned CDN, section 5 maps every page
  region to a `cds-*` component, and `tokens.css` was written as `--cds-*`
  overrides precisely because "Carbon Web Components read --cds-* custom
  properties".
- The spec also mandates a **graceful-degradation baseline** (R30, acceptance
  check 6): with the Carbon CDN blocked, the page must still work using plain
  HTML styled with the same tokens.
- During construction, the Carbon runtime was removed entirely by user
  directive. The recorded reason
  (`aidlc-docs/construction/plans/unit-9-code-generation-plan.md`, completion
  note 2026-11-04): "the CDN module threw bare-specifier resolution errors
  and the cds-tile elements were purely presentational. The hand-written skin
  is the authoritative styling."
- What shipped is therefore the spec's **fallback baseline alone**: plain HTML
  + a hand-written CSS skin using the exact token values. The
  `frontend-development` skill formalised this: the viz is exempt from the
  Carbon-only rule because "the Carbon CDN is an enhancement, never a
  dependency" (`frontend/viz-register.md`, 159 registered classes).

So "porting to Carbon" is not a rewrite; with the fallback rescinded it is a
**replacement** of the hand-written chrome skin with the real components,
deleting the fallback rather than layering over it.

## 2. Why the first attempt failed, and why it would not fail again

The 2026 attempt loaded raw npm ES modules, which import bare specifiers
(`from "lit"`) that browsers cannot resolve. That failure mode is avoidable:

**Probed facts (2026-11-10, live `curl` against the IBM CDN):**

| # | Fact | Probe |
|---|---|---|
| F1 | IBM hosts official **pre-bundled per-component** ESM artifacts at `https://1.www.s81c.com/common/carbon/web-components/version/v2.65.0/<name>.min.js` | `curl -I` 200 for `accordion`, `tag`, `tile`, `code-snippet`, `copy-button`, `ui-shell`, `button`, `checkbox`, `date-picker`, `notification`, `data-table`, `structured-list`, `link`, `toggle` |
| F2 | Bundle names differ from component names in two places: notifications ship as `notification.min.js` (not `inline-notification`/`actionable-notification`, both 404); the table ships as `data-table.min.js` (`table` 404) | `curl -I` 404/200 pairs |
| F3 | Bundles are self-contained ESM graphs: internal imports are **relative chunk files** (`from"./16-DIfh6y3Q.js"` etc.), no bare specifiers | fetched `accordion.min.js` (31,978 bytes), grepped import specifiers |
| F4 | A theme-token sheet exists at `.../tag/v2/latest/themes.css` (~74 KB, ~1,900 `--cds-*` declarations) scoped to theme-zone classes (`cds-theme-zone-white`, `-g10`, `-g90`, `-g100`) | `curl` 200 + content sample |
| F5 | Theming model: components read `--cds-*` custom properties, which inherit into shadow roots, so the existing `tokens.css` overrides apply as-is | Carbon styling docs; `tokens.css` header comment; consistent with frontend/ usage |
| F6 | Carbon documents the `:not(:defined) { visibility: hidden }` FOUC pattern; we would do the inverse (style un-upgraded hosts) to keep the fallback visible | npm README |

F3 means the exact-pinned-CDN approach in spec section 3 works with plain
`<script type="module" src="...">` tags: no import map, no bundler, no
bare-specifier errors. It also means **vendoring** (the echarts pattern) is a
flat mirror of entry bundles plus chunks with SHA pins; probed and chosen on
2026-11-10 (see the probe results in section 4).

**Unprobed (must be verified in a spike before any build, per the repo's
probe-the-pinned-artifact rule):**

- U1: `hide-close-button` (or equivalent) on `cds-inline-notification` /
  `cds-actionable-notification` at the pinned version (the run banner is not
  dismissible today).
- U2: `cds-tag` colour reachability: tag colours come from global
  `--cds-tag-background-*` tokens; there is **no warning/yellow tag type**
  and no `::part`. Whether the current bordered-chip look (transparent fill,
  1px coloured border) is reachable at all.
- U3: `cds-accordion-item` title-slot layout freedom (7-part stage header
  row) and `::part(title)` coverage. Precedent exists:
  `frontend-development` SKILL.md sanctions
  `cds-accordion-item::part(title)` for exactly this kind of fix.
- U4: `cds-code-snippet type="inline"` feedback UX (tooltip) versus the
  current "Copied" text swap.
- U5: `cds-table` `size` density versus the current 7px/10px row padding.
- U6: g100/g10 theme-zone token coverage under our overrides (cascade order:
  theme sheet first, `tokens.css` second).
- U7: `cds-accordion-item` `open` attribute driving and the
  `cds-accordion-item-toggled` event contract at the pinned version.

## 3. Current implementation surface (what the port touches)

| Asset | Size | Role |
|---|---|---|
| `viz/assets/page.html` | 68 lines | Live page skeleton (header, tiles, banners, graph box, stages, log) |
| `viz/assets/page.js` | 1,028 lines | All live-page behaviour: events, graph render, stages accordion, consoles, watchdog, theme |
| `viz/assets/runs.html` | 100 lines | History page skeleton (filters, aggregate, table, detail, charts) |
| `viz/assets/runs.js` | 887 lines | History behaviour; builds large DOM via `innerHTML` template strings |
| `viz/assets/styles.css` | 464 lines | The full hand-written skin (both themes) |
| `viz/assets/pipeline-layout.js` | 186 lines | Graph geometry (normative reference port) |
| `viz/assets/echarts.min.js` | ~1.1 MB | Vendored, SHA-pinned, ASCII-sanitised (the vendoring precedent) |
| `tools/build_viz_page.py` | inlines the above into `viz/page.py` | drift-guarded by `test_build_viz_page.py` |

Test surface: `tests/test_viz.py` (1,599 lines; includes a **Node DOM-shim
harness that executes the real `page.js`** and asserts on classes, ids, and
attributes), `tests/test_unit13_runs_viz.py` (288 lines; HTTP/payload level
plus HTML substring checks), `tests/test_viz_layout.py` (golden layout),
`tests/test_viz_demo.py` (demo event coverage). No browser-level tests
(no Playwright/Selenium).

## 4. Component mapping and parity assessment

Surfaces that **stay custom either way** (no Carbon equivalent; the spec
itself marks them custom): the pipeline graph (stations, rails, arrows,
labels), the ANSI console renderer, the event log, the attempt meter, the
ECharts charts, the metric-expander `<details>` widgets, metric/artifact
cards. Roughly 60-70% of `styles.css` supports these custom-only surfaces.
The port only touches the container/chrome layer below.

### Live page (spec section 5 mapping)

| Region (today) | Carbon component | Visual parity | Behavioural parity |
|---|---|---|---|
| `.hdr` header | `cds-header`, `cds-header-name`, `cds-header-global-action` (in `ui-shell.min.js`) | HIGH: Carbon header is 48px, ours is 48px. Brand mark and stream LED stay custom slotted content | HIGH: theme toggle keeps its handler; target grows 44px to 48px (negligible) |
| `#stream-banner` | `cds-actionable-notification kind="warning"` | MEDIUM: Carbon adds a status icon and its own padding; icon cannot be removed (no API). Visibility logic (`recover()`) unchanged | HIGH if U1 confirms hiding the close button; otherwise a dismiss control appears that does not exist today |
| Summary `.tile` strip | `cds-tile` x5 | MEDIUM-HIGH: cds-tile padding (16px) and borderless fill differ from the 8px/10px + 1px-gap strip; host-element CSS restores the strip look | HIGH (purely presentational, the exact criticism from attempt 1) |
| `#thread-id` | `cds-code-snippet type="inline"` | MEDIUM | MEDIUM: built-in copy shows a tooltip; today shows a 1.2s "Copied" text swap + green border (`.copied` test). Cheapest parity: keep the custom chip, skip this swap (25 lines of JS already work) |
| `#verdicts` badges | `cds-tag` | LOW-MEDIUM: no warning/yellow type; Carbon tags are filled, ours are bordered outline chips. Filled chips are a visible change; the outline look needs U2 to succeed | HIGH (text stays `step: verdict`) |
| `#run-banner` | `cds-inline-notification kind="success"\|"error" low-contrast` | MEDIUM: gains status icon | HIGH given U1 |
| `#graph` | custom | unchanged | unchanged |
| `#stages` accordion | `cds-accordion` / `cds-accordion-item` | MEDIUM: Carbon chevron (left, matches our glyph side), Carbon row metrics (40/48px vs our 44px), expand animation appears (respects reduced-motion). Status colour left-border needs `::part(title)` (U3). The 7-part title row goes in the title slot; consoles in the content slot | MEDIUM-HIGH: our JS drives `open` attribute (U7) instead of `.collapsed` class; click handlers move to Carbon events. The user-directed "no auto expand/collapse" deviation is preserved trivially (we simply never set `open` ourselves) |
| `.copy-btn` | `cds-copy-button` | HIGH (icon button) | MEDIUM: Carbon feedback is a tooltip; today's icon-swap + title changes ("Nothing to copy") are custom. Keep gating logic around it |
| `#run-log`, `.stage-console`, `.meter`, theme icons | custom | unchanged | unchanged |

### Run history page (unit 13; not in the original design spec, same skin)

| Region | Carbon component | Notes |
|---|---|---|
| Since/until inputs | keep native `type="date"` | `cds-date-picker`'s calendar needs flatpickr (extra CDN dep); without it Carbon is a plain text input. Native date inputs are the minimal-change choice |
| Status checkboxes | `cds-checkbox` | Straightforward swap; small visual change (Carbon 20px box) |
| Reset button | `cds-button kind="tertiary"` | Trivial |
| `.runs-table` | `cds-table` (in `data-table.min.js`) | Density via `size` (U5); current table is denser than Carbon default. `runs.js` builds rows via `innerHTML`; cds-table markup is heavier (shadow-wrapped), so the row template changes shape |
| Expanders | keep native `<details>` | Carbon has no disclosure widget besides accordion; `<details>` is semantic and tested |
| Metric cards, stat bands, artifacts, facts | `cds-tile` or keep custom | Product surfaces; custom is already registered |

### Loading posture (fallback rescinded; vendored chosen)

The pinned-CDN option (spec section 3) is superseded by the 2026-11-10
directive: **vendor the chunk graph** into `viz/assets/vendor/`, echarts
precedent (SHA pins + ASCII check at vendor time), served as static files by
`server.py`. Fully self-contained and deterministic; no runtime network
dependency; matches the stdlib-only ethos.

Un-upgraded-element CSS shrinks to Carbon's FOUC one-liner; the former
fallback host rules are deleted outright.

#### Vendored-path probe results (2026-11-10, scratch: opencode tmp `carbon-vendor-probe/`)

Probed by fetching the real pinned artifacts and walking their transitive
import graph (`manifest.json` in the scratch dir holds per-file SHA-256 and
sizes):

| # | Fact | Probe |
|---|---|---|
| F7 | 11 entry bundles (`ui-shell`, `notification`, `tile`, `tag`, `accordion`, `code-snippet`, `copy-button`, `checkbox`, `button`, `data-table`, `link`) pull a **closed flat graph of 58 JS files**: zero bare imports, no subdirectories, every dependency a sibling chunk | recursive fetch + import-regex walk from the 11 entries |
| F8 | Payload: **1,688,010 bytes raw, ~219 KiB gzip** for all 58 files (echarts alone is ~1.1 MB for comparison) | byte/gzip measurement per file |
| F9 | Exactly one non-ASCII byte sequence in the graph: a corrupted `ß` inside a `data-table.min.js` CSS selector (a dead rule; sanitise at vendor time like the echarts script) | per-file ASCII scan + context inspection |
| F10 | A theme sheet is **mandatory**: 250 of 268 referenced `--cds-*` tokens rely on baked **white-theme** fallbacks inside the bundles (sample: `--cds-text-primary,#161616`, `--cds-layer-01,#f4f4f4`); our skin defines only 19 | token census across the graph vs `styles.css` |
| F11 | `themes.css` fills the gap: 74,258 bytes, ASCII-clean, 1,832 declarations, all four zones (`cds-theme-zone-white/g10/g90/g100`), SHA-256 `7dfb6a11c6b69ae0af8975b51cf10c50eda4ca914be04ab2ae885e338584516` as fetched. Caveat: only served at the mutable `/tag/v2/latest/themes.css` URL (versioned paths 404), so the vendored bytes + SHA + fetch date are the pin; re-vendoring is manual and diff-reviewed | curl fetch + hash + zone/token census |
| F12 | Of the 18 graph-wide no-fallback tokens, 11 come from `themes.css`; the other 7 (`--cds-layout-size-height-*`, `--cds-layout-density-padding-inline-*`, `--cds-layer-background`) are **self-declared on component `:host` rules** inside the bundles. Complete coverage: themes.css + existing 19-token skin | declaration grep across graph + themes.css |
| F13 | All 20 expected `cds-*` tags register (verified across each entry's transitive chunk closure, e.g. `cds-checkbox` via `checkbox-D7PSLQCq.js`) | per-entry reachability + tag registry scan |

Serving consequences: the JS graph must be served as static files (relative
chunk imports; cannot be inlined into `page.py` the way echarts is), so
`server.py` gains one `/vendor/` static route plus tests. `themes.css` is
plain CSS with no path dependencies and can be inlined as a `<style>` block
at build time like the rest of the skin.

## 5. Impact on tests and tooling

- **Node DOM-shim harness** (`test_viz.py`): the shim's `createElement` is
  tag-agnostic, so `cds-*` elements work as plain stub elements; shadow DOM
  never loads under Node. Assertions that target swapped components must move
  from class/innerHTML checks to attribute checks (`open`, `kind`,
  `hide-close-button`): an estimated **10-20 test methods** need rewrites
  (badges, stage headers/`aria-expanded`, copy button icon/title, banners,
  `.copied` thread feedback if code-snippet is adopted). Graph, stations,
  meter, log, watchdog, theme tests are untouched. If `page.js`
  feature-detects upgrades (`customElements.whenDefined`), the shim needs a
  small `customElements` stub.
- **HTTP/payload tests** (`test_unit13_runs_viz.py`): unaffected except
  `test_runs_page_has_back_marker_and_expander_affordances` (HTML substrings;
  keep the marker and the expander affordance strings).
- **Drift guard** (`test_build_viz_page.py`): CDN `<script>`/`<link>` tags
  pass through the inliner untouched (only local markers are inlined); add
  the new markers to the exactly-one checks. Rebuild `page.py`.
- **Viz register / lint** (`frontend/viz-register.md`): sync class removals
  and additions. The viz lint mode tracks **classes only**, so `cds-*` host
  element rules in `styles.css` do not trip it; still, document the host-rule
  convention in the register for honesty.
- **ASCII / em-dash bans**: all edits ASCII; CDN URLs ASCII. Vendored
  variant adds the echarts-style SHA + ASCII sanitisation step for the
  chunk graph, plus `server.py` static-route tests for the vendor files.
- **Supply chain**: exact version pin only (already the FD-plan answer for
  R60; SRI does not cover module graphs). Record the pin and the v3 migration
  note (v3 changes registration; the pin protects us until a deliberate
  upgrade).

## 6. Effort estimate

One engineer, repo-familiar, TDD throughout.

| Phase | Scope | Estimate |
|---|---|---|
| 0. Probe spike | Component-behaviour questions only (U1-U5, U7): hide-close-button, tag override reach, accordion `::part(title)`, code-snippet feedback, table density, theme cascade, accordion events. Vendored chunk-graph question already answered (F7-F13). `spikes/` precedent exists (unit-13-charting) | 0.5 day |
| 1. Vendoring + theming plumbing | Vendor script (fetch pinned entries + transitive chunks + themes.css, SHA-256 manifest, ASCII sanitise incl. the `ß` exception), `server.py` `/vendor/` static route + tests, module script tags, theme-zone classes, FOUC one-liner, fallback CSS deletion, build-tool markers, drift rebuild | 1.0-1.5 days |
| 2. Live page swaps | Header, both banners, tiles, tags, accordion + copy-button; optional code-snippet; `::part` and host overrides to match the current look | 2.0-3.0 days |
| 3. page.js rewiring + test harness | `open`-driven accordion, notification APIs, shim updates, 10-20 test rewrites, full suite green | 1.0-1.5 days |
| 4. History page swaps (optional) | Checkboxes, reset button, table, tiles; native date inputs and `<details>` stay | 1.0-2.0 days |
| 5. Parity and a11y pass | 4 scenarios x 2 themes x breakpoints against the prototype oracle, keyboard/focus/reduced-motion, register sync, CHANGELOG | 1.0-1.5 days |

**Vendored path (chosen), live page only: ~5.5-8 days. Including the history
page: ~6.5-10 days.**

## 7. What changes visually no matter how careful we are

1. Notifications gain Carbon status icons and Carbon padding (no icon-off
   API).
2. `cds-tag` verdict chips become filled, not bordered-outline, unless U2
   succeeds; `fail` needs a token-level warning colour override regardless.
3. The accordion gains Carbon's expand animation and Carbon row metrics;
   the `>`/`v` text glyph becomes Carbon's chevron SVG.
4. Copy/code-snippet feedback becomes Carbon tooltips instead of the text
   swap / icon swap, if those swaps are adopted (both can be skipped).
5. Focus rings, hover states, and the theme-toggle hit target become
   Carbon's (all already token-aligned; directionally closer to the design
   system, measurably not pixel-identical).

Everything else (colours, type stack, spacing rhythm, graph, consoles,
motion limits) is already token-exact and stays so. Do **not** add Plex
webfont loading: today the stack falls back to system fonts, and adding the
webfont would itself be a visual change.

## 8. Options

| Option | Scope | Cost | Effect |
|---|---|---|---|
| A. Live page port | Phase 0-3 + 5, vendored | ~5.5-8 days | Fulfils the unit-9 spec's component intent on the page it was written for |
| B. Full port | A + history page (phase 4) | ~6.5-10 days | Both surfaces on Carbon WC; consistent with the frontend/ app direction |
| C. Status quo | none | 0 | Keep the token-exact hand skin; the skill note and register already bless this; the original intent stays unfulfilled on paper only |

Loading is decided: vendored chunk graph (F7-F13). No runtime network
dependency, no fallback skin.

The functional (non-presentational) wins of a port are concentrated in two
components: `cds-accordion` (keyboard navigation, focus management, ARIA
wiring done properly) and the notifications (role/aria semantics, standard
dismiss/focus behaviour). Tiles, tags, and code-snippet are indeed
"purely presentational" here, which is what sunk attempt 1. The honest
framing: a port buys design-system fidelity and a11y behaviour at the chrome
layer, at the price of a hard Carbon dependency (CDN or vendored), a
one-time test-harness rework, and the five unavoidable visual deltas in
section 7.

## 9. Risks

| Risk | Mitigation |
|---|---|
| `themes.css` only exists at a mutable `latest` URL | Vendored bytes are the artifact; SHA-256 + fetch date recorded in the manifest; re-vendoring is manual and diff-reviewed |
| Vendor tooling bug silently drops a chunk | Manifest counts + browser smoke test in phase 0/1; graph closure is regex-verified (F7) |
| Shadow-DOM reach limits make pixel parity impossible for tags/notifications | U1/U2 probe first; keep custom for any component that cannot hit the current look |
| Repeating attempt 1's bare-specifier failure | Impossible on this path: F7 proves the vendored graph has zero bare imports |
| Pin rot / v3 registration break | Exact pin + written rationale; v3 note recorded for the next deliberate upgrade |
| Shim-test fidelity drops (no shadow DOM under Node) | Keep all assertions on light-DOM attributes; Carbon-internal behaviour is manually verified in phase 5 |
| Spec/skill drift: R30/AC6 and the frontend skill's viz scope note still describe the fallback | Amend both in the implementation change (spec is planning-docs; skill note is a one-line edit) |
