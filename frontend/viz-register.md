# Viz class register (unit 9)

Unit-9 page assets live at `src/agentic_dev_loop/viz/assets/`; this register is
the SKILL.md-convention mapping table for every custom class they ship. The
register is lint-synced by
`node frontend/scripts/lint-figma-primitives.mjs --assets src/agentic_dev_loop/viz/assets --register frontend/viz-register.md`.

The viz is a product surface, not a Figma component library: most classes are
component-class behaviours (a status skin, a layout mechanic, or an animation
hook). Each row below states the honest Figma mapping where one exists and a
written justification otherwise. Dynamic inline positioning (`style.left` /
`style.top`) is sanctioned for station, line, arrow and label geometry only
(R52); every static position is a class.

## Classes

| Class | Purpose | Figma mapping / justification |
| --- | --- | --- |
| `.arr` | Arrowhead geometry (CSS-border triangle) | Component-class behaviour (status/layout skin); justified in the viz register |
| `.arr-flow` | Flow arrowhead fill | Component-class behaviour (status/layout skin); justified in the viz register |
| `.arr-back` | Fail-route arrowhead fill | Component-class behaviour (status/layout skin); justified in the viz register |
| `.arr-right` | Right-pointing arrowhead | Component-class behaviour (status/layout skin); justified in the viz register |
| `.arr-up` | Up-pointing arrowhead | Component-class behaviour (status/layout skin); justified in the viz register |
| `.badge` | Verdict chip | Component-class behaviour (status/layout skin); justified in the viz register |
| `.badge-fail` | Fail verdict chip colour | Component-class behaviour (status/layout skin); justified in the viz register |
| `.badge-pass` | Pass verdict chip colour | Component-class behaviour (status/layout skin); justified in the viz register |
| `.badge-unclear` | Unclear verdict chip colour | Component-class behaviour (status/layout skin); justified in the viz register |
| `.badge-unverifiable` | Unverifiable verdict chip colour | Component-class behaviour (status/layout skin); justified in the viz register |
| `.big` | Summary hero value (mono, tabular) | Component-class behaviour (status/layout skin); justified in the viz register |
| `.cline` | One console line | Component-class behaviour (status/layout skin); justified in the viz register |
| `.collapsed` | Collapsed stage/console state | Component-class behaviour (status/layout skin); justified in the viz register |
| `.console-wrap` | Console scroll port | Component-class behaviour (status/layout skin); justified in the viz register |
| `.copied` | Transient copy-confirmation skin on the thread id | Component-class behaviour (status/layout skin); justified in the viz register |
| `.copy-btn` | Sticky console copy button | Component-class behaviour (status/layout skin); justified in the viz register |
| `.elabel` | Edge label chip | Component-class behaviour (status/layout skin); justified in the viz register |
| `.exhausted` | Attempt meter exhausted state | Component-class behaviour (status/layout skin); justified in the viz register |
| `.failed` | Run log failure line | Component-class behaviour (status/layout skin); justified in the viz register |
| `.graph-msg` | Graph placeholder message | Component-class behaviour (status/layout skin); justified in the viz register |
| `.graph-wrap` | Centred graph container | Component-class behaviour (status/layout skin); justified in the viz register |
| `.hdr` | Page header bar | Component-class behaviour (status/layout skin); justified in the viz register |
| `.hdr-action` | Header icon action | Component-class behaviour (status/layout skin); justified in the viz register |
| `.hdr-brand` | Header brand lockup | Component-class behaviour (status/layout skin); justified in the viz register |
| `.hdr-right` | Header right cluster | Component-class behaviour (status/layout skin); justified in the viz register |
| `.ic` | Icon glyph box | Component-class behaviour (status/layout skin); justified in the viz register |
| `.ic-moon` | Moon theme glyph (inline SVG) | Component-class behaviour (status/layout skin); justified in the viz register |
| `.ic-sun` | Sun theme glyph (inline SVG) | Component-class behaviour (status/layout skin); justified in the viz register |
| `.interrupted` | Run log interrupted line | Component-class behaviour (status/layout skin); justified in the viz register |
| `.lbl` | Tile label | Component-class behaviour (status/layout skin); justified in the viz register |
| `.led` | 8px square status light | Component-class behaviour (status/layout skin); justified in the viz register |
| `.ln` | Rail/route segment | Component-class behaviour (status/layout skin); justified in the viz register |
| `.ln-back` | Dashed conditional route | Component-class behaviour (status/layout skin); justified in the viz register |
| `.ln-flow` | Solid flow rail | Component-class behaviour (status/layout skin); justified in the viz register |
| `.lower` | Stages and event log row | Component-class behaviour (status/layout skin); justified in the viz register |
| `.mark` | Brand mark square | Component-class behaviour (status/layout skin); justified in the viz register |
| `.meter` | Attempt segmented meter | Component-class behaviour (status/layout skin); justified in the viz register |
| `.mono` | Mono typography helper | Component-class behaviour (status/layout skin); justified in the viz register |
| `.nidx` | Station index | Component-class behaviour (status/layout skin); justified in the viz register |
| `.nlabel` | Station status word | Component-class behaviour (status/layout skin); justified in the viz register |
| `.nname` | Station name | Component-class behaviour (status/layout skin); justified in the viz register |
| `.node` | Station box | Component-class behaviour (status/layout skin); justified in the viz register |
| `.nrow` | Station index/time row | Component-class behaviour (status/layout skin); justified in the viz register |
| `.nstat` | Station status row | Component-class behaviour (status/layout skin); justified in the viz register |
| `.ntime` | Station time | Component-class behaviour (status/layout skin); justified in the viz register |
| `.omitted` | Console cap counter | Component-class behaviour (status/layout skin); justified in the viz register |
| `.on` | Active rail segment | Component-class behaviour (status/layout skin); justified in the viz register |
| `.outcome` | Run outcome notification | Component-class behaviour (status/layout skin); justified in the viz register |
| `.outcome-failure` | Failure outcome skin | Component-class behaviour (status/layout skin); justified in the viz register |
| `.outcome-sub` | Outcome subtitle | Component-class behaviour (status/layout skin); justified in the viz register |
| `.outcome-success` | Success outcome skin | Component-class behaviour (status/layout skin); justified in the viz register |
| `.outcome-title` | Outcome title | Component-class behaviour (status/layout skin); justified in the viz register |
| `.panel` | Bordered panel | Component-class behaviour (status/layout skin); justified in the viz register |
| `.panel-h` | Panel header | Component-class behaviour (status/layout skin); justified in the viz register |
| `.panel-log` | Event log panel | Component-class behaviour (status/layout skin); justified in the viz register |
| `.panel-stages` | Stages panel | Component-class behaviour (status/layout skin); justified in the viz register |
| `.reconnecting` | Stream reconnecting state | Component-class behaviour (status/layout skin); justified in the viz register |
| `.shell` | Main content column | Component-class behaviour (status/layout skin); justified in the viz register |
| `.skip` | Skip link | Component-class behaviour (status/layout skin); justified in the viz register |
| `.st-fail` | Retryable-fail station skin | Component-class behaviour (status/layout skin); justified in the viz register |
| `.st-fatal` | Fatal station skin | Component-class behaviour (status/layout skin); justified in the viz register |
| `.st-pass` | Pass station skin | Component-class behaviour (status/layout skin); justified in the viz register |
| `.st-pending` | Pending station skin | Component-class behaviour (status/layout skin); justified in the viz register |
| `.st-running` | Running station skin | Component-class behaviour (status/layout skin); justified in the viz register |
| `.st-unverifiable` | Unverifiable station skin | Component-class behaviour (status/layout skin); justified in the viz register |
| `.stage` | Accordion stage row | Component-class behaviour (status/layout skin); justified in the viz register |
| `.stage-console` | ANSI console surface | Component-class behaviour (status/layout skin); justified in the viz register |
| `.stage-glyph` | Accordion chevron | Component-class behaviour (status/layout skin); justified in the viz register |
| `.stage-header` | Accordion header button | Component-class behaviour (status/layout skin); justified in the viz register |
| `.stage-index` | Stage number | Component-class behaviour (status/layout skin); justified in the viz register |
| `.stage-name` | Stage name | Component-class behaviour (status/layout skin); justified in the viz register |
| `.stage-placeholder` | Empty console placeholder | Component-class behaviour (status/layout skin); justified in the viz register |
| `.stage-status` | Stage status word | Component-class behaviour (status/layout skin); justified in the viz register |
| `.stage-time` | Stage duration | Component-class behaviour (status/layout skin); justified in the viz register |
| `.stage-verdict` | Stage verdict tag | Component-class behaviour (status/layout skin); justified in the viz register |
| `.started` | Run log started line | Component-class behaviour (status/layout skin); justified in the viz register |
| `.status-fail` | Stage header fail accent | Component-class behaviour (status/layout skin); justified in the viz register |
| `.status-fatal` | Stage header fatal accent | Component-class behaviour (status/layout skin); justified in the viz register |
| `.status-pass` | Stage header pass accent | Component-class behaviour (status/layout skin); justified in the viz register |
| `.status-running` | Stage header running accent | Component-class behaviour (status/layout skin); justified in the viz register |
| `.status-unverifiable` | Stage header unverifiable accent | Component-class behaviour (status/layout skin); justified in the viz register |
| `.stream` | Header stream indicator | Component-class behaviour (status/layout skin); justified in the viz register |
| `.success` | Run log success line | Component-class behaviour (status/layout skin); justified in the viz register |
| `.tile` | Summary tile block | Component-class behaviour (status/layout skin); justified in the viz register |
| `.tiles` | Summary tile strip | Component-class behaviour (status/layout skin); justified in the viz register |
| `.total-note` | Total LIVE/FINAL note | Component-class behaviour (status/layout skin); justified in the viz register |
| `.verdicts` | Verdict badge strip | Component-class behaviour (status/layout skin); justified in the viz register |

## Keyframes

| Keyframes | Purpose | Justification |
| --- | --- | --- |
| `blink` | Running LED pulse | Carbon has no exported motion primitive for a blinking indicator; stops under `prefers-reduced-motion`. |
| `sweep` | Running station bottom-edge bar | Carbon has no progress-sweep component at this size; stops under `prefers-reduced-motion`. |

## Sanctioned element rules

| Selector | Justification |
| --- | --- |
| `*` | Box-sizing and thin scrollbar reset; mechanics only, no visual styling. |
| `html, body` | Page height, font stack and background; the app shell has no component wrapper. |
| `button` | Reset for the header actions, stage headers and copy buttons. |
| `:focus-visible` | A single product focus ring token (R45). |

## Product tokens (no Carbon equivalent)

`--adl-support-unverifiable`, `--adl-tint-run|ok|warn|err|unv`,
`--adl-console*`, `--adl-font-sans|mono`, `--adl-radius`,
`--adl-motion-productive`. Values are the exact `tokens.css` values inlined in
`styles.css`. No Carbon runtime is loaded; the hand-written skin (tokens and
rules below) is the authoritative styling (R30, R60).

## History view classes (unit 13)

| Class | Purpose | Figma mapping / justification |
| --- | --- | --- |
| `.aggregate-body` | Aggregate metrics body | Component-class behaviour (status/layout skin); justified in the viz register |
| `.artifact` | Artifact presence card | Component-class behaviour (status/layout skin); justified in the viz register |
| `.artifact-file` | Artifact filename | Component-class behaviour (status/layout skin); justified in the viz register |
| `.artifact-grid` | Artifact card grid | Component-class behaviour (status/layout skin); justified in the viz register |
| `.artifact-name` | Artifact label | Component-class behaviour (status/layout skin); justified in the viz register |
| `.artifact-state` | Artifact presence word | Component-class behaviour (status/layout skin); justified in the viz register |
| `.artifacts` | Artifact panel body | Component-class behaviour (status/layout skin); justified in the viz register |
| `.attempt` | One attempt row | Component-class behaviour (status/layout skin); justified in the viz register |
| `.attempt-label` | Attempt number label | Component-class behaviour (status/layout skin); justified in the viz register |
| `.badge-prov` | Provenance badge | Component-class behaviour (status/layout skin); justified in the viz register |
| `.chart` | ECharts mount slot | Component-class behaviour (status/layout skin); justified in the viz register |
| `.chart-grid` | Chart grid with min-width guard | Component-class behaviour (status/layout skin); justified in the viz register |
| `.chart-slot` | Chart card | Component-class behaviour (status/layout skin); justified in the viz register |
| `.chart-title` | Chart card title | Component-class behaviour (status/layout skin); justified in the viz register |
| `.crumb` | Detail breadcrumb | Component-class behaviour (status/layout skin); justified in the viz register |
| `.detail-threads` | Detail thread stack | Component-class behaviour (status/layout skin); justified in the viz register |
| `.empty` | Empty-state text | Component-class behaviour (status/layout skin); justified in the viz register |
| `.facts` | Artifact fact list | Component-class behaviour (status/layout skin); justified in the viz register |
| `.field` | Filter field label | Component-class behaviour (status/layout skin); justified in the viz register |
| `.filters` | Filter control row | Component-class behaviour (status/layout skin); justified in the viz register |
| `.hdr-link` | Header navigation link | Component-class behaviour (status/layout skin); justified in the viz register |
| `.hdr-text` | Text header action | Component-class behaviour (status/layout skin); justified in the viz register |
| `.metric-basis` | Metric basis caption | Component-class behaviour (status/layout skin); justified in the viz register |
| `.metric-card` | One metric card | Component-class behaviour (status/layout skin); justified in the viz register |
| `.metric-grid` | Metric card grid | Component-class behaviour (status/layout skin); justified in the viz register |
| `.metric-head` | Metric card header | Component-class behaviour (status/layout skin); justified in the viz register |
| `.metric-name` | Metric name | Component-class behaviour (status/layout skin); justified in the viz register |
| `.metric-reason` | Unavailable metric reason | Component-class behaviour (status/layout skin); justified in the viz register |
| `.metric-section` | Metric section | Component-class behaviour (status/layout skin); justified in the viz register |
| `.metric-value` | Metric value | Component-class behaviour (status/layout skin); justified in the viz register |
| `.missing` | Missing artifact skin | Component-class behaviour (status/layout skin); justified in the viz register |
| `.not-found` | Unknown-run state | Component-class behaviour (status/layout skin); justified in the viz register |
| `.notice` | Degradation notice | Component-class behaviour (status/layout skin); justified in the viz register |
| `.num` | Numeric table cell | Component-class behaviour (status/layout skin); justified in the viz register |
| `.path` | Path table cell | Component-class behaviour (status/layout skin); justified in the viz register |
| `.runs-shell` | History content column | Component-class behaviour (status/layout skin); justified in the viz register |
| `.runs-table` | Runs list table | Component-class behaviour (status/layout skin); justified in the viz register |
| `.status-failed` | Failed run status skin | Component-class behaviour (status/layout skin); justified in the viz register |
| `.status-field` | Status filter fieldset | Component-class behaviour (status/layout skin); justified in the viz register |
| `.status-interrupted` | Interrupted run status skin | Component-class behaviour (status/layout skin); justified in the viz register |
| `.status-success` | Success run status skin | Component-class behaviour (status/layout skin); justified in the viz register |
| `.status-unreadable` | Unreadable run status skin | Component-class behaviour (status/layout skin); justified in the viz register |
| `.table-wrap` | Table horizontal scroll port | Component-class behaviour (status/layout skin); justified in the viz register |
| `.thread-body` | Thread panel body | Component-class behaviour (status/layout skin); justified in the viz register |
| `.thread-sub` | Thread metadata line | Component-class behaviour (status/layout skin); justified in the viz register |
| `.unavailable` | Unavailable metric skin | Component-class behaviour (status/layout skin); justified in the viz register |
| `.verdict-item` | Verdict and provenance pair | Component-class behaviour (status/layout skin); justified in the viz register |
| `.verdict-step` | Verdict step name | Component-class behaviour (status/layout skin); justified in the viz register |
| `.wide` | Full-width chart slot | Component-class behaviour (status/layout skin); justified in the viz register |
