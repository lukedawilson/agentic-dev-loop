# Business Logic Model - Unit 9 (Viz Redesign)

Source of truth: `planning-docs/design/viz-redesign-spec.md` (normative) and
`planning-docs/design/pipeline-layout.js` (normative algorithm). Where this
document and the spec disagree, the spec wins.

## 1. Pipeline layout model (spec section 7)

### 1.1 Contract

`layoutPipeline(opts) -> result` is a pure function (no DOM, no I/O).

Input `opts`:

| Field | Type | Notes |
| --- | --- | --- |
| `nodes` | string[] | display order, equals `/topology nodes` |
| `flow` | {src,dst}[] | the on_pass chain (solid rail) |
| `edges` | {src,dst,label|null}[] | on_fail routes (dashed) |
| `status` | {nodeId: status} | one of pass/running/fail/fatal/unverifiable/pending |
| `availWidth` | number | px available to the graph |

Output result:

| Field | Type | Notes |
| --- | --- | --- |
| `width`, `height` | number | explicit graph box size (box is centred) |
| `rows`, `perRow` | number | chosen layout |
| `nodes` | [{id,index,x,y,w,h}] | fixed size 112x76, never scaled |
| `lines` | [{x,y,w,h,kind:"flow"|"back",on:bool,dir:"h"|"v"}] | rail/route segments |
| `arrows` | [{x,y,dir:"right"|"up",kind,on}] | x,y = top-left of a CSS-border triangle |
| `labels` | [{x,y,text}] | x = right edge (translateX(-100%)), y = top |

Constants: NW=112, NH=76, GAP=24, STEP=136, LANE=18, LANE_TOP=14, LANE_PAD=16, CH=12.

### 1.2 Row planning

1. `plan(R)`: per = max(1, ceil(N/R)); rows = ceil(N/per); station (r,c) by
   index fill order. U/C/F edge sets classified and sorted exactly as the
   reference header: U by span asc; C by (source row asc, source index asc);
   F by (target row asc, source index desc).
2. Width = O + core + right where core = per*STEP - GAP; O = left channel
   margin (CH + CH*|C| + 8 when multi-row, else 0); right = 14 (multi) +
   8 + CH*|F| (F present).
3. **Fewest rows that fit**: start R=1; while width > availWidth and per > 2,
   increment R. perRow never drops below 2; if R=ceil(N/2) still does not
   fit, the panel scrolls sideways (caller behaviour, not layout).

### 1.3 Lane, channel and entry ordering (crossing-free)

- Gutter lane order (top -> bottom): U into row (shortest span first),
  C into row (outer channel first), F into row, C leaving row (leftmost
  source first), F leaving row, wrap connector last.
- C channels: inner -> outer by (source row asc, source index asc).
- Target entry points: U rightmost, then C inner -> outer, then F, stepping
  14px leftwards across the target's bottom edge.
- Gutter height: `14 + (lanes-1)*18 + 16`px; last row with no lanes: 4px.

### 1.4 Routing primitives

- **Flow rail (same row)**: 2px horizontal segment at y+37 with right
  arrowhead. Segment turns green once its downstream station is not pending.
- **Wrap connector**: right stub -> down to the row's deepest gutter lane
  (its wrap lane) -> left to the left channel -> down -> right into the next
  row's first station.
- **U (same row)**: drop from source bottom centre into the gutter, run
  along it, rise into the target's bottom edge, up-arrow.
- **C (back across rows)**: drop into source row's gutter, run left to the
  left-hand channel, rise to target row's gutter, run right, rise into target.
- **F (forward across rows)**: mirror of C through the right-hand channel.
- **Labels**: right-aligned against the source's drop line, on that edge's
  lane. Unlabelled edges get no chip.
- Flow edges whose dst is NOT the next station are routed as conditional
  edges with kind "flow" (solid), label null.

### 1.5 Relayout triggers

Resize (ResizeObserver on the graph container's content width), topology
load, and every status change (rail colouring). Cost O(N + E).

## 2. Layout test model (decision Q3=A)

- The algorithm is ported 1:1 to a pure Python function in the viz module
  (`layout_pipeline(opts) -> result`), same input/output shapes, same
  ordering rules. Python is the tested source of truth.
- `tools/build_viz_page.py` (decision Q2=A) emits the shipped
  `pipeline-layout.js` from the Python port. The emitted file is committed;
  a build check asserts the emitted JS matches the committed file (no silent
  drift). The reference ES5 file becomes redundant after the port is
  verified against it (port-verification = golden comparison of results on
  the spec's three row scenarios plus a property sweep).
- Invariant tests (pytest, no node in CI): stations always 112x76 and never
  scaled; perRow >= 2 whenever multi-row is chosen; fewest-rows property
  (for each availWidth, R is minimal subject to fit, except the
  last-resort scroll case); U and C crossings never occur (pairwise
  segment-intersection check over generated instances); gutter heights
  follow the lane formula; result of a layout with a single node and no
  edges is a 112x76 box.
- Hypothesis PBT (extension: PBT partial scope): random node counts,
  random flow chains, random back-edge sets; assert determinism (same input
  -> identical output), no crossings (U/C), all stations within
  [0,width]x[0,height], arrows/labels attached to non-degenerate segments.

## 3. Status presentation model (spec sections 2, 4)

- `statusOf()` and `renderStatusOf()` (env renders as fatal) keep their
  current precedence exactly. `fail` means "verdict fail, will retry"
  (RETRY); `fatal` covers fatal, env, and run failed (FATAL, or FAIL when
  the verdict was fail); `unverifiable` -> UNVERIF; `pending` -> PENDING;
  `pass` -> PASS (DONE when the step has no verdict, e.g. commit).
- Three cues always: fill/border colour, 8px square LED, uppercase mono
  label. Colour is never the only signal.
- Station status -> token mapping (exact values in tokens.css):

| statusOf | LED/border token | Fill tint | Border | Label |
| --- | --- | --- | --- | --- |
| pass | --cds-support-success | --adl-tint-ok | 1px solid | PASS / DONE |
| running | --cds-interactive | --adl-tint-run | 2px solid | RUNNING |
| fail (retry) | --cds-support-warning | --adl-tint-warn | 1px solid | RETRY |
| fatal | --cds-support-error | --adl-tint-err | 2px solid | FATAL / FAIL |
| unverifiable | --adl-support-unverifiable | --adl-tint-unv | 1px solid | UNVERIF |
| pending | --cds-border-strong-01 | --cds-layer-01 | 1px dashed, hollow light | PENDING |

- Motion model: only the running station animates (blinking LED + sweep bar
  across its bottom edge). All motion stops under prefers-reduced-motion.
- Verdict tags (#verdicts): pass, fail->warning, unverifiable, env->error,
  unclear->neutral; text stays `step: verdict`.

## 4. Theme model (spec section 4)

- Theme state: "dark" | "light", applied as `data-theme` on `<html>`.
  Default = system `prefers-color-scheme`.
- Override: header action toggles; persisted to `localStorage["adl-theme"]`;
  storage access wrapped in try/catch (failure degrades to session-only).
- Toggle button shows sun in dark mode, moon in light mode; aria-label
  "Switch to light theme" / "Switch to dark theme".
- Console surfaces use the `--adl-console*` tokens and stay dark in both
  themes.

## 5. Topology derivation model (spec section 7 backend change)

- `topology_mermaid()` already computes `chain` and `back_edges`. Extend its
  result: `flow` = consecutive (src,dst) pairs along `chain`;
  `edges` = back_edges mapped to {src, dst, label} where label is the
  back-edge's condition text as already carried for the mermaid edge labels,
  or null when unlabelled.
- Response shape: {mermaid, subject, nodes, flow, edges}. `mermaid` stays
  byte-identical to today for compatibility; the new page ignores it.
- The new page consumes nodes/flow/edges/status only; it never parses
  mermaid.

## 6. Client state model

| State | Source | Notes |
| --- | --- | --- |
| theme | localStorage / prefers-color-scheme | section 4 |
| topology | GET /topology | null until loaded |
| pendingEvents | buffered SSE events | unchanged from today |
| stepTimes, verdict sets, pendingCompletion | events | unchanged |
| stage open/closed | events + user toggle | unchanged semantics |
| dividerHeight | user drag / double-click reset | axis changed to pipeline height |
| runActive, watchdog attempts | events + watchdog timer | unchanged |

## 7. Degradation model (spec section 3)

- Carbon CDN unreachable: page still renders and updates using plain HTML
  styled with the same tokens. Carbon components are an enhancement, not a
  dependency. Concretely: every `cds-*` element has a plain-HTML counterpart
  (header -> simple bar; tiles -> bordered boxes; accordion -> the existing
  custom stage rows; notifications -> token-styled banners). The prototype
  is exactly this baseline.
- `/topology` failure: keep the fallback message "Graph unavailable - the
  event log below is still live" (spec section 3 replaces the old
  mermaid-CDN wording).
- Mermaid failure mode disappears with mermaid itself.
