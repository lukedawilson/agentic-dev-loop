# Frontend Components - Unit 9 (Viz Redesign)

## 1. Region -> component mapping (spec section 5)

| Region (current id) | Component | Notes |
| --- | --- | --- |
| Header | cds-header, cds-header-name, cds-header-global-action (theme) | Stream indicator = plain markup: light + mono label |
| #stream-banner | cds-actionable-notification kind=warning | "Connection lost, reconnecting..."; detail line = watchdog attempt n/3 + 10 s rule; visibility logic unchanged (recover()) |
| #subject, #attempt, #total, #thread | cds-tile x4 in a 1px-gap strip | attempt = segmented meter (max_attempts segments, filled = used, error-red when exhausted); total = LIVE/FINAL note; thread = cds-code-snippet inline, click-to-select kept |
| #verdicts | cds-tag | colours overridden to section 4 status tokens; text stays "step: verdict" |
| #run-banner | cds-inline-notification success/error low-contrast | moves to under the summary; title SUCCESS/FAILURE; subtitle "Run finished - subject - attempt n/m - total m:ss" |
| #mermaid-container -> graph box | Custom: absolute-positioned HTML inside one role="img" box | aria-label summarises each node's status; section 7 layout |
| #divider | Custom <button>, cursor: row-resize | resizes pipeline panel height (was sidebar width); pointer drag, arrow keys 16px steps, double-click reset |
| #stages | cds-accordion / cds-accordion-item | title slot = chevron, NN index, status light, name, status word, verdict tag, right-aligned duration; open attribute drives existing logic |
| .stage-console | Custom <pre> + cds-copy-button | ANSI renderer unchanged; max-height 320px; sticky copy top-right |
| #run-log | Custom numbered <ol> | mono 12px; started=primary, interrupted=warning, failed=error, success=success |

Keep old ids where cheap (spec section 12 item 5): #subject, #attempt,
#thread-id, #total, #verdicts, #stages, stage-<node>, #run-log,
#stream-banner, #run-banner.

## 2. Custom component inventory

| Component | Role | State |
| --- | --- | --- |
| Graph box (#graph) | role="img" container; absolute children placed from layoutPipeline result | width/height from layout; centred |
| Station (.node st-<node>) | 112x76 box: index+time row, name, LED+label row | status-driven |
| LED (.led) | 8px square, status colour, hollow when pending | status-driven |
| Rail/route segments (.ln) | layout lines; flow green when on | layout-driven |
| Arrowheads (.arr) | CSS-border triangles | layout-driven |
| Edge label chips (.elabel) | mono chip on the edge's lane | layout-driven |
| Wrap connector | rail sub-case | layout-driven |
| Divider (#divider) | pipeline panel resize control | drag state |
| Theme toggle | cds-header-global-action button | theme state |
| Meter (#meter) | attempt segments | attempt state |
| Outcome notification (#outcome) | run_finished summary | run result |
| Console (.stage-console) | ANSI pre + copy button | per-stage |

## 3. Class register draft (decision Q1=A, Q3=A, Q4=A)

Final inventory is fixed at Code Generation against the shipped markup and
lint-synced into `frontend/viz-register.md` (SKILL.md table conventions).
Draft, grounded in the prototype's class list:

| Class | Purpose | Register status |
| --- | --- | --- |
| .node, .st-<status> | station box; status-driven skin | Component-class behaviour (status skins); Position X/Y + Frame W/H sanctioned for placement |
| .led, .lg-<status> | 8px status light | Component-class behaviour; Fill maps to Carbon support tokens or --adl-* with justification |
| .ln | rail/route segment | Component-class; stroke = Carbon token; Position/Frame for geometry |
| .arr | arrowhead | Component-class; CSS-border triangle; justified (no Figma arrow control) |
| .elabel | edge label chip | Component-class; justified (edge label = Figma Text props subset: mono 11px, chip fill) |
| .tile / .tiles / .panel / .panel-h | Carbon fallback geometry | Implementation note: only the no-CDN baseline uses them (Carbon replaces the enhanced path) |
| .meter | attempt segments | Component-class; justified |
| .mono | mono typography | Maps to Text Style (IBM Plex Mono, tabular) |
| .big | hero number | Maps to Text Style (IBM Plex Mono, tabular figures) |
| .lbl / .t / .i / .w / .n / .nm / .nname | small labels/time/name pieces | Merge/replace with token-mapped text classes at CG; prototype-only names |
| .stream | stream indicator | Component-class; justified |
| .snippet | thread display fallback | Implementation note: Carbon code-snippet replaces it when CDN loads |
| .shell / .stage-console | console surface | Component-class; --adl-console* product tokens |
| .omitted | console cap counter | Component-class; justified |
| .legend | pipeline legend | Component-class; justified |
| .grow / .spacer / .panel-scroll / .lower | layout helpers | Implementation note: grid/flex mechanics, no visual styling (sanctioned mechanics) |
| .hdr-action | header action | Replaced by cds-header-global-action; fallback = button element |

Element-rules register additions (with justification): `*` box-sizing
reset; `html, body` height/font/background; `button` reset for the custom
divider and fallback controls; `pre` console reset; `ol` run-log numbering.

Keyframes registered with justification: `blink` (running LED, stops under
prefers-reduced-motion), `sweep` (running station bottom edge, same guard).

Sanctioned dynamic inline positioning (R52): style.left/top on station,
line, arrow and label elements, values from layoutPipeline output only.

## 4. Interaction flows

1. **Theme toggle**: click -> flip data-theme -> persist (try/catch) ->
   swap icon and aria-label.
2. **Accordion**: header click -> toggle open attribute -> tail console on
   open. Existing open/close logic drives the attribute (R3, R4, R12).
3. **Divider**: pointer drag -> pipeline panel height (clamp 96px..70vh);
   arrow keys 16px steps; double-click reset (R13).
4. **Copy console**: click -> join .cline -> Clipboard API -> execCommand
   fallback -> check icon 1.5 s (R9).
5. **Thread select/copy**: click selects all; copy additive (R10).
6. **Graph relayout**: ResizeObserver / topology load / status change ->
   layoutPipeline -> reposition children (R52).

## 5. API integration points

| Endpoint | Use | Notes |
| --- | --- | --- |
| GET /topology | nodes, flow, edges for the graph | subject kept; mermaid ignored |
| GET /events (SSE) | all events | contract unchanged |

## 6. Fallback pairing (no-CDN baseline)

Each enhanced region has a baseline counterpart so the page works with the
Carbon CDN blocked: header bar, plain tiles strip, custom stage rows
(existing behaviour), token-styled notification bars, button elements for
all actions. Carbon elements are added as an enhancement, never required
(R30). The prototype is the reference for this baseline.
