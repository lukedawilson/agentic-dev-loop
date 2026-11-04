# Business Rules - Unit 9 (Viz Redesign)

## R1..R13: Rule zero behaviour preservation (spec section 10)

Each rule is enforced by the existing viz contract tests, updated for new
DOM ids where they change (old ids kept where cheap: #subject, #attempt,
#thread-id, #total, #verdicts, #stages, stage-<node>, #run-log,
#stream-banner, #run-banner).

| Rule | Behaviour (must not change) |
| --- | --- |
| R1 | Topology fetched first; earlier events buffered; after 5 s log "topology load timed out - event log only" |
| R2 | run_started: reset times, consoles, verdicts, open/closed state; set subject, thread, attempt; start total timer from started_at |
| R3 | node_started: stage becomes running; its accordion item opens and every other item collapses; scroll into view and tail |
| R4 | node_finished: accumulate elapsed_seconds into stepTimes; word completed/errored; item collapses; verdict suffix waits for matching state (pendingCompletion, flushPending) |
| R5 | heartbeat: updates the live time of the running stage only |
| R6 | state: applies step_verdicts to the four verdict sets and the badges; failed marks running nodes fatal and logs "state: failed" |
| R7 | run_finished: stop timer; final total; mark failed and running nodes fatal if run failed; collapse everything; log result; show outcome notification |
| R8 | console: routed to stage console when it has a node, else run log; capped at 500 lines with "...n lines omitted" counter; sticks to bottom only when already near bottom |
| R9 | Copy console: joins .cline text; Clipboard API with execCommand fallback; check icon 1.5 s on success; title changes on failure or nothing to copy |
| R10 | Thread ID: click to select; copy is additive |
| R11 | Watchdog: if runActive and no event for >10 s (checked every second and on visibilitychange), run recover() and show banner; >3 attempts -> location.reload() |
| R12 | Clicking a stage header toggles it; opening one tails its console |
| R13 | Divider: drag to resize; axis changes from sidebar width to pipeline height; min/max clamp 96px to 70% of viewport height |

## R20..R26: Status, motion and colour rules (spec sections 2, 4)

| Rule | Rule text |
| --- | --- |
| R20 | Every status has three cues (colour, 8px LED, uppercase mono label). Colour alone never carries status. |
| R21 | Status word set is exactly: PASS, RUNNING, RETRY, FATAL, UNVERIF, PENDING, DONE. |
| R22 | statusOf()/renderStatusOf() precedence is unchanged; env renders as fatal. |
| R23 | Square corners everywhere (--adl-radius: 0); 1px rules; no gradients, shadows, emoji or decorative motion. |
| R24 | Motion limited to the running station (blink + bottom-edge sweep); both stop under prefers-reduced-motion. |
| R25 | One accent (teal --cds-interactive) marks running, focus and the live indicator, and nothing else. |
| R26 | Numbers use IBM Plex Mono with tabular figures; all times are m:ss, unchanged from today. |

## R30..R33: Fallback and error rules (spec sections 3, 9)

| Rule | Rule text |
| --- | --- |
| R30 | Carbon CDN unreachable: page fully functional via token-styled plain HTML (prototype = baseline). |
| R31 | /topology failure: show "Graph unavailable - the event log below is still live". |
| R32 | Stream stalled: warning banner + header RECONNECTING (amber); reload after 3 failed watchdog attempts (R11). |
| R33 | Local storage access wrapped in try/catch; theme falls back to session-only on failure. |

## R40..R47: Accessibility rules (spec section 11)

| Rule | Rule text |
| --- | --- |
| R40 | Every control is a real <button>; icon-only buttons have aria-label. |
| R41 | Accordion headers set aria-expanded and aria-controls. |
| R42 | Pipeline box is role="img" with aria-label listing each node and its status plus the row count. |
| R43 | Notifications and the stream banner use role="status". |
| R44 | Text contrast >= 4.5:1 in both themes, including 11px helper text. |
| R45 | Focus ring: 2px --cds-focus, inset. |
| R46 | Touch targets >= 44px (accordion rows, header actions). |
| R47 | Respect prefers-reduced-motion. |

## R50..R53: Frontend-rules conformance rules (elaboration decisions)

| Rule | Rule text |
| --- | --- |
| R50 | Carbon Web Components first for every region in the spec section 5 mapping; custom markup only where the spec maps a region to "custom" or for the no-CDN baseline (Q1=B, Q4=A). |
| R51 | Every custom CSS class is documented in the viz register (frontend/viz-register.md, SKILL-table conventions) with an honest Figma mapping or a written justification; the register is lint-synced via lint-figma-primitives.mjs --assets/--register flags (Q1=A). |
| R52 | Dynamic inline positioning (style.left/top) is sanctioned for the pipeline drawing only (station, line, arrow, label placement from layoutPipeline output); all static inline styles are converted to classes (Q3=A). |
| R53 | Product tokens (--adl-*), the two running animations, scrollbar styling and the no-CDN fallback are kept and documented in the register as component-class behaviours / product tokens (Q4=A). |

## R60..R62: Supply chain rules (NFR-9.2, decision Q4=A)

| Rule | Rule text |
| --- | --- |
| R60 | Carbon assets loaded from a CDN with an exact pinned version, the same way Mermaid is loaded today. |
| R61 | No SRI hashes: written rationale kept with the design (Carbon loads via module imports; subresource integrity does not cover module-graph payloads; exact pin is the hardening). |
| R62 | Server runtime stays stdlib-only; Node tooling is build/test-time only (NFR-9.1). |

## R70: Repo ASCII rule (NFR-9.5)

Shipped PAGE assets are ASCII: prototype glyphs (LED squares, copy and
chevron icons) are replaced by CSS-drawn elements, Carbon icons, or ASCII
equivalents. The em dash checker (tests/test_repo_ascii.py) remains green.

## Acceptance rules (spec section 12)

A1 visual match prototype for four scenarios x two themes; A2 row counts
(1 row >=1336px, 2 rows ~1100px, 3 rows ~700px) with no horizontal scroll
and no crossings; A3 short topology = one centred natural-size row; A4 live
reflow on resize, no reload; A5 all section 10 behaviours pass updated
contract tests; A6 page works with Carbon CDN blocked; A7 /topology has
flow and edges, mermaid unchanged.
