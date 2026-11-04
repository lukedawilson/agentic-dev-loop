# Normative Values - Unit 9 (Viz Redesign)

Self-containment reference for the implementing agent: exact values the FD
artifacts defer to `planning-docs/design/` files that the AI-DLC document
loader does not bundle (`.css`, `.js`, `.html` suffixes are not loaded).
The normative files remain authoritative; this document carries their exact
current values. ASCII only, per repo policy.

## 1. Token values (normative: planning-docs/design/tokens.css)

Theme switch: `data-theme` attribute on `<html>`; dark = `:root` /
`data-theme="dark"`, light = `data-theme="light"`.

### 1.1 Dark (--cds-* re-skin of Carbon g100)

| Token | Value |
| --- | --- |
| --cds-background | #0F1214 |
| --cds-layer-01 | #171B1F |
| --cds-layer-02 | #20262B |
| --cds-layer-03 | #2A3137 |
| --cds-layer-hover-01 | #20262B |
| --cds-border-subtle-01 | #2E353B |
| --cds-border-strong-01 | #4D565E |
| --cds-text-primary | #EEF1F3 |
| --cds-text-secondary | #A3ACB4 |
| --cds-text-helper | #8A949C |
| --cds-icon-primary | #EEF1F3 |
| --cds-icon-secondary | #A3ACB4 |
| --cds-interactive | #08BDBA |
| --cds-focus | #3DDBD9 |
| --cds-link-primary | #3DDBD9 |
| --cds-support-success | #42BE65 |
| --cds-support-warning | #F1C21B |
| --cds-support-error | #FF6B72 |
| --cds-support-info | #08BDBA |

### 1.2 Light (--cds-* re-skin of Carbon g10)

| Token | Value |
| --- | --- |
| --cds-background | #F2F4F5 |
| --cds-layer-01 | #FFFFFF |
| --cds-layer-02 | #F4F6F7 |
| --cds-layer-03 | #E6E9EB |
| --cds-layer-hover-01 | #F4F6F7 |
| --cds-border-subtle-01 | #DDE1E4 |
| --cds-border-strong-01 | #8D969E |
| --cds-text-primary | #15191C |
| --cds-text-secondary | #4C555D |
| --cds-text-helper | #5F6870 |
| --cds-icon-primary | #15191C |
| --cds-icon-secondary | #4C555D |
| --cds-interactive | #007D79 |
| --cds-focus | #007D79 |
| --cds-link-primary | #007D79 |
| --cds-support-success | #198038 |
| --cds-support-warning | #8E6A00 |
| --cds-support-error | #DA1E28 |
| --cds-support-info | #007D79 |

### 1.3 Product tokens (--adl-*, dark / light)

| Token | Dark | Light |
| --- | --- | --- |
| --adl-support-unverifiable | #BE95FF | #8A3FFC |
| --adl-tint-run | rgba(8,189,186,.14) | rgba(0,125,121,.09) |
| --adl-tint-ok | rgba(66,190,101,.11) | rgba(25,128,56,.08) |
| --adl-tint-warn | rgba(241,194,27,.12) | rgba(241,194,27,.18) |
| --adl-tint-err | rgba(250,77,86,.14) | rgba(218,30,40,.08) |
| --adl-tint-unv | rgba(165,110,255,.14) | rgba(138,63,252,.08) |
| --adl-console | #0B0E10 | #121619 |

### 1.4 Theme-independent

| Token | Value |
| --- | --- |
| --adl-console-text | #D6DCE0 |
| --adl-console-muted | #8A949C |
| --adl-console-control | #20262B |
| --adl-console-control-hover | #2A3137 |
| --adl-console-scroll-thumb | #4D565E |
| --adl-font-sans | 'IBM Plex Sans', system-ui, sans-serif |
| --adl-font-mono | 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace |
| --adl-radius | 0 |
| --adl-motion-productive | cubic-bezier(.2, 0, .38, .9) |

## 2. Layout constants (normative: planning-docs/design/pipeline-layout.js)

| Constant | Value |
| --- | --- |
| NW (station width) | 112 |
| NH (station height) | 76 |
| GAP | 24 |
| STEP | 136 |
| LANE | 18 |
| LANE_TOP | 14 |
| LANE_PAD | 16 |
| CH | 12 |

Gutter height for a row with n lanes: `14 + (n - 1) * 18 + 16`; a last row
with no lanes: 4. Edge label chip at (xs - 6, laneY - 9), right-aligned
(translateX(-100%)). Entry points per target step 14px leftwards from the
right edge of the target's bottom border. Full ordering rules: FD
`business-logic-model.md` section 1.

Expected row counts at N = 10 stations: 1 row at >= 1336px (perRow 10),
2 rows at ~1100px (5 + 5), 3 rows at ~700px (4 + 4 + 2).

## 3. Kept DOM ids (spec section 12 item 5)

`#subject`, `#attempt`, `#thread-id`, `#total`, `#verdicts`, `#stages`,
`stage-<node>`, `#run-log`, `#stream-banner`, `#run-banner`.

## 4. Status -> token mapping (spec section 4; full table in FD business-logic-model.md section 3)

| statusOf | LED/border | Fill tint | Border | Label |
| --- | --- | --- | --- | --- |
| pass | --cds-support-success | --adl-tint-ok | 1px solid | PASS / DONE |
| running | --cds-interactive | --adl-tint-run | 2px solid | RUNNING |
| fail (retry) | --cds-support-warning | --adl-tint-warn | 1px solid | RETRY |
| fatal | --cds-support-error | --adl-tint-err | 2px solid | FATAL / FAIL |
| unverifiable | --adl-support-unverifiable | --adl-tint-unv | 1px solid | UNVERIF |
| pending | --cds-border-strong-01 | --cds-layer-01 | 1px dashed hollow | PENDING |

## 5. CDN pin (spec section 3)

Carbon Web Components v2 line, exact pinned version, loaded the same way
Mermaid is loaded today; no SRI (rationale: business-rules.md R61).
Mermaid is removed from the page entirely.

## 6. /topology response shape (spec section 7)

`{mermaid, subject, nodes, flow, edges}` - `mermaid` byte-identical to
today; `flow` = consecutive (src, dst) pairs along the on_pass chain;
`edges` = on_fail routes as `{src, dst, label}` with `label: null` when
unlabelled. The new page consumes nodes/flow/edges/status only.

## 7. Rule reference

R1-R13 (frozen behaviours), R20-R26 (status/motion/colour), R30-R33
(fallback/error), R40-R47 (a11y), R50-R53 (frontend-rules conformance),
R60-R62 (supply chain), R70 (ASCII), A1-A7 (acceptance):
`functional-design/business-rules.md`. Layout contract and algorithm
orderings: `functional-design/business-logic-model.md`. Component mapping
and register draft: `functional-design/frontend-components.md`.
