# Domain Entities - Unit 9 (Viz Redesign)

The viz domain is presentation-layer only: no persistence, no new domain
model. Entities below are the data shapes the page consumes and produces.

## 1. Event contract (unchanged)

The `/events` SSE contract does not change (rule zero). Event types:
run_started, node_started, node_finished, heartbeat, state, run_finished,
console. Fields stay exactly as today; this unit adds no event fields.

## 2. Topology DTO (extended, additive)

`GET /topology` response, extended:

| Field | Type | Notes |
| --- | --- | --- |
| mermaid | string | unchanged, kept for compatibility; unused by the new page |
| subject | string | unchanged |
| nodes | string[] | display order; unchanged |
| flow | {src,dst}[] | NEW: consecutive pairs along the on_pass chain |
| edges | {src,dst,label}[] | NEW: on_fail routes; label = condition text or null |

Derivation: inside `topology_mermaid()` from the `chain` and `back_edges`
it already computes. No new parsing, no new data source.

## 3. Layout entities (pure function)

`layout_pipeline(opts) -> result`; contract in
`business-logic-model.md` section 1.1. Sub-entities:

- **Station**: {id, index, x, y, w, h}; w/h fixed 112x76 (NW x NH), never
  scaled. index is the zero-based position in `nodes`; the UI renders it
  1-based, zero-padded to two digits.
- **Segment**: {x, y, w, h, kind: flow|back, on: bool, dir: h|v}; h=0 for
  horizontal, w=0 for vertical; on=true once the downstream station is not
  pending.
- **Arrow**: {x, y, dir: right|up, kind, on}; x,y = top-left of a
  CSS-border triangle box.
- **Label chip**: {x, y, text}; x = right edge (rendered translateX(-100%)).

## 4. Token entities (normative values in tokens.css)

Theme-scoped groups (dark = :root / data-theme=dark; light =
data-theme=light):

- **--cds-* overrides** (the re-skin): background, layer-01..03,
  layer-hover-01, border-subtle-01, border-strong-01, text-primary,
  text-secondary, text-helper, icon-primary, icon-secondary, interactive,
  focus, link-primary, support-success, support-warning, support-error,
  support-info. All values exact per tokens.css.
- **--adl-* product tokens** (no Carbon equivalent): support-unverifiable
  (dark #BE95FF / light #8A3FFC), tint-run/ok/warn/err/unv (rgba tints),
  console + console-text/muted/control/control-hover/scroll-thumb
  (always-dark console), font-sans (IBM Plex Sans + system-ui stack),
  font-mono (IBM Plex Mono + monospace stack), radius: 0,
  motion-productive cubic-bezier(.2,0,.38,.9).
- **Status token mapping**: business-logic-model.md section 3 table.

## 5. Client state entities

| Entity | Shape | Notes |
| --- | --- | --- |
| theme | "dark" | "light" | data-theme on <html>; persisted adl-theme |
| topology | Topology DTO or null | null until loaded |
| pendingEvents | event[] | buffered before topology arrives |
| stepTimes | {node: seconds} | accumulated elapsed_seconds |
| verdictSets | 4 sets + badges | from state events |
| pendingCompletion / flushPending | existing mechanism | unchanged |
| stageOpen | {node: bool} | event-driven + user toggle |
| dividerHeight | px | clamp 96px..70vh; double-click reset |
| watchdog | {attempts, recovering} | 10 s rule, 3 attempts -> reload |

## 6. Non-goals (entity level)

- No new backend events, no persistence, no new endpoints, no domain-model
  change. `/topology` is a DTO extension only.
