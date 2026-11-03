# Viz redesign spec - Carbon re-skin, horizontal pipeline

> Design spec, not a plan. A plan that implements it should reference this
> file and follow `planning-docs/README.md` conventions.

Target: `src/agentic_dev_loop/viz/server.py` → the `PAGE` string (embedded
HTML/CSS/JS) plus a small, additive change to `GET /topology`.

Rule zero: **the UI changes, the UX does not.** Every behaviour listed in §10
exists today and must still exist afterwards. The event contract on `/events`
does not change.

## 1. Artefacts in this folder

| File | Role | Normative? |
|---|---|---|
| `viz-redesign-spec.md` | This spec | **Yes** |
| `tokens.css` | Theme tokens (dark + light), `--cds-*` overrides + `--adl-*` product tokens | **Yes**, values are exact |
| `pipeline-layout.js` | Reference implementation of the pipeline layout (§7). Pure function, ES5, no DOM | **Yes** for the algorithm; port or inline it |
| `prototype/index.html` | Runnable prototype. Open from disk. Driven by the real event types | Reference for look and behaviour |
| `reference/run-view.dc.html` | Source of the design-canvas mock | Informative only |

If the prototype and this spec disagree, the spec wins.

## 2. Brand → design rules

The brand values are rigorous, reliable, dependable, engineering, robust. The
persona is a software engineer who cares about repeatable, reliable delivery,
not a vibe-coder. The UI should read as **a reliable pipeline, a well-oiled
machine**. Concretely:

- Stations on a rail: each stage is a fixed-size, numbered station, connected by a solid rail. A rail segment turns green once its downstream station has been reached.
- Every status has three cues: colour, an 8px square status light (the "LED"), and an uppercase mono label (`PASS`, `RUNNING`, `RETRY`, `FATAL`, `UNVERIF`, `PENDING`, `DONE`). Colour is never the only signal.
- Square corners everywhere (`--adl-radius: 0`). 1px rules. No gradients, shadows, emoji or decorative motion.
- Motion is limited to the running station: a blinking light plus a progress bar sweeping across its bottom edge. Both stop under `prefers-reduced-motion`.
- Numbers use IBM Plex Mono with tabular figures. All times are `m:ss`, unchanged from today.
- One accent (teal) marks "running", focus and the live indicator, and nothing else.

## 3. Stack

- **Carbon Web Components** (`@carbon/web-components`, v2 line), loaded from a CDN with an **exact, pinned version**, the same way Mermaid is loaded today. The server stays stdlib-only (TS-4). Only load the components listed in §5.
- **Fonts:** IBM Plex Sans and IBM Plex Mono, from Carbon's font loading or Google Fonts. Fall back to system fonts if offline.
- **Themes:** start from Carbon `g100` (dark) and `g10` (light), then apply the overrides in `tokens.css`. Select the theme with `data-theme` on `<html>`.
- **Mermaid is removed.** The pipeline is drawn by §7's layout. This also removes the "Graph unavailable (mermaid CDN unreachable)" failure mode. Keep an equivalent fallback for when `/topology` fails ("Graph unavailable - the event log below is still live").
- **Graceful degradation:** if the Carbon CDN is unreachable, the page must still work using plain HTML styled with the same tokens. The prototype is exactly that baseline, so the Carbon elements are an enhancement, not a dependency.

## 4. Theme

- **Default:** the system `prefers-color-scheme`.
- **Override:** a header action toggles dark/light and persists the choice to `localStorage["adl-theme"]`. Wrap storage access in try/catch.
- **Toggle button:** shows a sun in dark mode and a moon in light mode. Its `aria-label` is "Switch to light theme" or "Switch to dark theme".
- **Console:** stays dark in both themes (`--adl-console*`).

### Status colours (exact values in `tokens.css`)

| Status (`statusOf`) | Token | Fill | Border | Label |
|---|---|---|---|---|
| `pass` | `--cds-support-success` | `--adl-tint-ok` | 1px solid | `PASS` (`DONE` if the step has no verdict, e.g. `commit`) |
| `running` | `--cds-interactive` | `--adl-tint-run` | 2px solid, blinking light, sweep bar | `RUNNING` |
| `fail` (verdict fail, will retry) | `--cds-support-warning` | `--adl-tint-warn` | 1px solid | `RETRY` |
| `fatal` (`fatal`, `env`, or run failed) | `--cds-support-error` | `--adl-tint-err` | 2px solid | `FATAL` (`FAIL` if the verdict was `fail`) |
| `unverifiable` | `--adl-support-unverifiable` | `--adl-tint-unv` | 1px solid | `UNVERIF` |
| `pending` | `--cds-border-strong-01` | `--cds-layer-01` | 1px **dashed**, hollow light | `PENDING` |

`statusOf()` and `renderStatusOf()` (where `env` renders as `fatal`) keep their
current precedence exactly.

## 5. Page anatomy and component mapping

```
┌ cds-header ─ [■] agentic-dev-loop viz ··········· ■ STREAM LIVE │ ☀ ┐
│ [stream banner: only while the watchdog is recovering]            │
│ ┌ Run ───────┬ Attempt ──┬ Thread ID ──────┬ Total ─────┐         │
│ │unit unit-15│ 3 / 10 ▮▮▮▯│ 44d0…6e74 [⧉]   │157:33 LIVE │         │
│ ├ Step verdicts: [brief: pass] [deps: pass] …                │    │
│ [outcome notification: only after run_finished]                   │
│ ┌ Pipeline ─────────────────────────────────────────── legend ┐   │
│ │  01 brief → 02 deps → 03 dev → … (wraps per §7)               │   │
│ └──────────────────────────────────────────────────────────────┘   │
│ ═══════════ divider (drag = pipeline panel height) ═══════════     │
│ ┌ Stages (accordion) ──────────────────┐ ┌ Event log ──────────┐   │
│ │ ▸ 01 ■ brief completed  verdict:PASS 1:04 │ 1 run started: …   │   │
│ │ ▾ 08 ■ qa running                 6:03 │                       │   │
│ │   └ console (ANSI, sticky copy)        │                       │   │
│ └────────────────────────────────────────┘ └──────────────────────┘   │
└───────────────────────────────────────────────────────────────────┘
```

| Region (current id) | Carbon WC | Notes |
|---|---|---|
| Header | `cds-header`, `cds-header-name`, `cds-header-global-action` (theme) | The stream indicator is plain markup: a light plus a mono label |
| `#stream-banner` | `cds-actionable-notification kind="warning"` (or a full-width bar) | Text "Connection lost, reconnecting…". The detail line gives the watchdog attempt (n/3) and the 10 s rule. The visibility logic is unchanged (`recover()`) |
| `#subject`, `#attempt`, `#total` | `cds-tile` ×4 in a 1px-gap strip | Attempt adds a segmented meter, `max_attempts` segments, filled = used. It turns error-red when the run fails with attempts exhausted. Total shows a `LIVE` / `FINAL` note |
| `#thread` | `cds-code-snippet type="inline"` | Click-to-select is kept (`user-select: all`). Copy uses Carbon's built-in feedback |
| `#verdicts` | `cds-tag` | Override the tag colours to the §4 status tokens: pass, fail→warning, unverifiable, env→error, unclear→neutral. Text stays `step: verdict` |
| `#run-banner` | `cds-inline-notification kind="success" \| "error" low-contrast` | Moves from under the graph to under the summary. Title `SUCCESS` / `FAILURE`; subtitle `Run finished · subject · attempt n/m · total m:ss` |
| `#mermaid-container` | Custom: absolutely positioned HTML inside one `role="img"` box, with an `aria-label` summarising each node's status | §7 |
| `#divider` | Custom `<button>` with `cursor: row-resize` | It now resizes the **pipeline panel height** (it used to resize sidebar width). Pointer drag, ↑/↓ keys in 16px steps, double-click resets |
| `#stages` | `cds-accordion` / `cds-accordion-item` | The title slot holds the chevron, `NN` index, status light, name, status word, verdict tag and right-aligned duration. Expand/collapse is driven by the existing logic (see §10), through the item's `open` attribute |
| `.stage-console` | Custom `<pre>` + `cds-copy-button` | The ANSI renderer is unchanged. Max height 320px; the copy button stays sticky in the top-right corner |
| `#run-log` | Custom numbered `<ol>` | Mono 12px. Colours: started = primary, interrupted = warning, failed = error, success = success |

## 6. Layout

- **Page:** the page fills the viewport (`100vh`) and the page itself does not scroll. Stages and the event log scroll inside their panels.
  - Phone width (≤760px): the page stacks into one column and scrolls normally.
- **Main column:** `max-width: 1600px`, padding `16px 24px`, 12px vertical gap.
- **Lower area:** a wrapping flex row. Stages: `flex: 999 1 640px`. Event log: `flex: 1 1 360px`.
- **Pipeline panel:** natural height by default (§7 computes it). The divider can override it, and the panel then scrolls.
- **Scrollbars:** thin and square.
  - Firefox: `scrollbar-width: thin` and `scrollbar-color: var(--cds-border-strong-01) transparent`.
  - WebKit: an 8px thumb in `--cds-border-strong-01`, `--cds-text-helper` on hover.
  - The console uses its own dark scrollbar.

## 7. Pipeline layout (normative)

Implemented in `pipeline-layout.js`; the contract is in its header comment. The
rules:

1. **Stations keep their natural size:** 112×76px, with 24px gaps. Never scale them.
2. **Fewest rows that fit:** measure the graph container's content width with a `ResizeObserver`. Choose the smallest row count R such that `perRow = ceil(N / R)` stations, plus the routing margins, fit. `perRow` never goes below 2; below that, the panel scrolls sideways as a last resort.
3. **Centring:** the graph box has an explicit width and height and is centred. A short pipeline stays at natural size in the middle, not stretched.
4. **Flow:** consecutive `on_pass` edges are a 2px rail with an arrowhead. When the next station is on a new row, a **wrap connector** runs: right stub → down to the deepest lane of the row's gutter → left to the left channel → down → right into the next row's first station. Flow segments go green once their downstream station is no longer `pending`.
5. **Conditional edges** (`on_fail` routes) are 1px dashed, right-angled routes with the label in a mono chip:
   - **U (same row):** drop from the source's bottom centre into the gutter below the row, run along it, then rise into the target's bottom edge, ending in an up-arrow.
   - **C (back across rows):** drop into the source row's gutter, run left to a **left-hand channel**, rise to the target row's gutter, run right, then rise into the target.
   - **F (forward across rows, e.g. into the `failure` terminal):** the mirror image of C, using a right-hand channel.
6. **No crossings.** Route order, lane order, channel order and entry order are fixed exactly as in the file header. This ordering guarantees U and C routes never cross. F is best effort.
7. **Gutter height:** each gutter is only as tall as its lanes, `14 + (lanes − 1) × 18 + 16`px. The last row with no lanes takes 4px.
8. **Labels** are right-aligned against their source's drop line, sitting on that edge's lane. Unlabelled edges (a non-retry `on_fail`) get no chip.
9. **Re-run layout** on resize, on topology load, and on any status change (rail colouring). Layout is O(N + E), so it is cheap.

Expected results for the default `full` topology:

| Graph content width | Rows |
|---|---|
| ≥ 1336px | 1 |
| ~700–1335px | 2 (5 + 5) |
| below that | 3 (4 + 4 + 2) |

### Backend change: structured edges on `/topology` (additive)

`GET /topology` currently returns `{mermaid, subject, nodes}`. Add `flow` and
`edges`, built in `topology_mermaid()` from the same `chain` and `back_edges`
lists it already computes:

```json
{
  "mermaid": "graph TD; ...",
  "subject": "unit unit-15",
  "nodes": ["brief", "deps", "dev", "be-tests", "fe-tests", "conformance", "review", "qa", "commit", "success"],
  "flow":  [{"src": "brief", "dst": "deps"}, ...],
  "edges": [{"src": "be-tests", "dst": "dev", "label": "fail"},
            {"src": "review",   "dst": "dev", "label": "NEEDS CHANGES"},
            {"src": "qa",       "dst": "dev", "label": "FAIL"}]
}
```

- `label` is `null` for an unlabelled `on_fail`.
- Keep `mermaid` for compatibility. It is unused by the new page and can be removed in a later plan.
- Update the topology and viz contract tests to match.

## 8. Station anatomy

```
┌──────────────────┐  112 × 76, padding 8/10 (7/9 when the border is 2px)
│ 03        139:55 │  mono 11px: index (helper colour) · time (secondary colour)
│ dev              │  sans 13px/600, ellipsis
│ ■ PASS           │  status light + mono 11px uppercase label in the status colour
└──────────────────┘  running: 3px teal bar sweeping across the bottom edge
```

- **Time:** the live `heartbeat` elapsed time while running; the accumulated `stepTimes` once complete; `-` before the stage has run.
- **Index:** the 1-based position in `nodes`, zero-padded to two digits.

## 9. Run states

| State | What's visible |
|---|---|
| Before `/topology` loads | Graph box shows "Loading graph…". Events are buffered (`pendingEvents`), as today |
| Running | Header shows `STREAM LIVE`. Total ticks with a `LIVE` note. The running station animates. Its accordion item is open and the console tails |
| Stream stalled (watchdog) | Warning banner appears and the header shows `RECONNECTING` (amber). After 3 failed attempts, `location.reload()`, as today |
| Success | `SUCCESS` notification. Total is frozen with a `FINAL` note. Everything collapses |
| Failure | `FAILURE` notification. Failed and running nodes become fatal (red, 2px border). The meter turns red if attempts are exhausted |

## 10. Behaviour that must not change

Check each of these against the current `PAGE` script:

1. Topology is fetched first. Events that arrive before it are buffered. After 5 s, log "topology load timed out - event log only".
2. `run_started`: reset times, consoles, verdicts and the open/closed state of stages. Set subject, thread and attempt. Start the total timer from `started_at`.
3. `node_started`: that stage becomes `running`, its accordion item opens and **every other item collapses**, and it scrolls into view and tails.
4. `node_finished`: accumulate `elapsed_seconds` into `stepTimes`. Word becomes `completed` or `errored`. The item collapses. The verdict suffix in the header waits for the matching `state` (`pendingCompletion` and `flushPending`).
5. `heartbeat`: updates the live time of the running stage only.
6. `state`: applies `step_verdicts` to the four verdict sets and the badges. `failed` marks running nodes as fatal and logs "state: failed".
7. `run_finished`: stop the timer, show the final total, mark failed and running nodes fatal if the run failed, collapse everything, log the result, show the outcome notification.
8. `console`: routed to the stage's console when it has a `node`, otherwise to the run log. Capped at 500 lines with a "…n lines omitted" counter. Sticks to the bottom only when it was already near the bottom.
9. Copy console: joins `.cline` text. Clipboard API with an `execCommand` fallback. The icon shows a check for 1.5 s on success; the title changes on failure or when there's nothing to copy.
10. Thread ID: click to select. Copy is additive.
11. Watchdog: if `runActive` and there's been no event for more than 10 s (checked every second and on `visibilitychange`), run `recover()` and show the banner. More than 3 attempts → reload.
12. Clicking a stage header toggles it. Opening one tails its console.
13. Divider: drag to resize. The axis changes from sidebar width to pipeline height. Keep the min/max clamp, now 96px to 70% of the viewport height.

## 11. Accessibility

- Every control is a real `<button>`. Icon-only buttons have an `aria-label`.
- Accordion headers set `aria-expanded` and `aria-controls`.
- The pipeline box is `role="img"` with an `aria-label` listing each node and its status, plus the row count.
- Notifications and the stream banner use `role="status"`.
- Text contrast is ≥ 4.5:1 in both themes, including 11px helper text (`--cds-text-helper` on `--cds-layer-01`).
- Focus ring: 2px `--cds-focus`, inset.
- Touch targets ≥ 44px (accordion rows, header actions).
- Respect `prefers-reduced-motion`.

## 12. Acceptance checks

1. The prototype and the shipped page match visually for the four scenarios (running, success, failure, reconnecting) in both themes.
2. With the `full` topology, the graph content width gives one row at ≥1336px, two rows at 1100px and three rows at 700px. There is no horizontal scroll at any of those widths, and no edge routes cross.
3. The `short` topology (6 stages, 2 back-edges) renders as one centred row at natural size at 1100px.
4. Resizing the window re-flows the rows live, with no reload.
5. Every behaviour in §10 passes the existing viz contract tests, updated for the new DOM ids where they change. Keep the old ids where cheap: `#subject`, `#attempt`, `#thread-id`, `#total`, `#verdicts`, `#stages`, `stage-<node>`, `#run-log`, `#stream-banner`, `#run-banner`.
6. With the Carbon CDN blocked, the page still renders and updates using the token-styled fallback.
7. `/topology` includes `flow` and `edges`, and `mermaid` is unchanged.
