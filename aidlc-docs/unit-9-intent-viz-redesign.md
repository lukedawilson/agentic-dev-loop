# Unit 9 Intent (elaboration) - Viz redesign (Carbon re-skin, horizontal pipeline)

> **Status: INTENT / ELABORATION** - started 2026-11-03. Implements the normative
> design in `planning-docs/design/`. No code written. This elaboration includes
> the user-requested validation of the design against the imported
> frontend-development rules (`.opencode/skills/frontend-development/SKILL.md`);
> findings and open decisions are below and in
> `aidlc-docs/inception/requirements/unit-9-requirement-verification-questions.md`.

## Raw intent (user, 2026-11-03, verbatim)

- "Fine, now we want a new aidlc unit, implementing the design for the viz specified in planning-docs/design"
- "As part of the elaboration phase, review the design and validate it against the frontend rules you just imported"

## Intent

Replace the viz page - the embedded `PAGE` string in
`src/agentic_dev_loop/viz/server.py`, plus a small additive change to
`GET /topology` - with the design normatively specified in
`planning-docs/design/viz-redesign-spec.md`:

- Carbon Web Components (v2 line, exact pinned CDN version, loaded the way
  Mermaid is today), themed from Carbon g100/g10 via `tokens.css`.
- A horizontal station-and-rail pipeline drawn by `pipeline-layout.js`
  (fewest-rows-that-fit reflow, wrap connectors, crossing-free back-edge
  routing). Mermaid rendering is removed.
- Dark/light themes (system default, persisted toggle), console always dark.
- Rule zero (spec): the UI changes, the UX does not. Every behaviour in spec
  section 10 and the `/events` event contract are unchanged.

### What the user gets

1. A pipeline view that reads as a reliable machine: fixed-size numbered
   stations on a rail; every status shown by colour + 8px LED + uppercase mono
   label (never colour alone).
2. A re-flowing layout: fewest rows that fit, recomputed live on resize, no
   horizontal scroll at the spec's reference widths, no edge crossings.
3. Dark and light themes; the console stays dark in both.
4. Graceful degradation: with the Carbon CDN unreachable the page still works,
   styled by the same tokens (the prototype is exactly that baseline).
5. Backend: `/topology` gains `flow` and `edges` (additive; `mermaid` retained
   for compatibility, removable in a later plan).

## Design vs frontend-development rules - validation report

Rules source: `.opencode/skills/frontend-development/SKILL.md` (imported
2026-11-03 from the other repo's DESIGN.md set). Design source:
`planning-docs/design/` (spec normative; prototype informative).

### V-0. Structural scope finding (frames everything below)

The skill's own description binds "ANY change under **frontend/**". The viz
lives at `src/agentic_dev_loop/viz/server.py` as an embedded HTML/CSS/JS string
served by a stdlib-only Python server (spec TS-4). The enforcement linter
(`frontend/scripts/lint-figma-primitives.mjs`) requires a live `frontend/`
app (`frontend/src`, `index.html`, `node_modules/@carbon/*`) that this repo
does not have. So: the rules cannot be *mechanically enforced* on the viz, and
their premise (a Figma-driven frontend with import/export round-trip) has no
counterpart here - there is no Figma file for the viz; the prototype + spec
are the design. Note also the rules postdate the viz: the current `PAGE`
already ships custom embedded CSS (incl. `@keyframes pulse`), so the status
quo is equally non-compliant under a literal reading.

### V-1. Rule-by-rule assessment (rules applied in spirit)

| # | Design element | Rule | Verdict |
|---|---|---|---|
| 1 | Carbon WC from pinned CDN (cds-header, cds-tile x4, cds-code-snippet, cds-tag, cds-accordion, cds-inline-notification, cds-actionable-notification, cds-copy-button) | "ALWAYS use native Carbon web components" | PASS (in spirit) |
| 2 | `tokens.css` `--cds-*` overrides on g100/g10 | "CSS custom property overrides" table exists for exactly this | PASS-able; every override needs a table row with an honest Figma fill mapping |
| 3 | `--adl-*` product tokens (5 status tints, unverifiable purple, console surface, radius, 2 font stacks) | Custom CSS forbidden; genuine mapping test; closed Figma vocabulary covers Carbon color tokens only | FAIL strict - rgba tints and the purple are not Carbon tokens (the purple is Carbon's purple-40 *value* but not a named support token). Decision needed (Q4) |
| 4 | Pipeline drawing (`.node` stations, `.ln` rail/route lines, `.arr` arrowheads, `.elabel` chips, `.pipe`) | Genuine mapping test; sanctioned vocabulary includes Position X/Y and Frame width/height for absolute children | BORDERLINE-passable, but every class must be registered somewhere |
| 5 | Motion: `@keyframes blink` (LED) + `sweep` (progress bar), `prefers-reduced-motion` handling | Closed Figma vocabulary has NO motion affordance | FAIL strict - cannot pass the genuine mapping test; must be documented component-class behaviour or dropped (Q4) |
| 6 | Scrollbar styling (Firefox `scrollbar-*`, WebKit `::-webkit-scrollbar*`) | No Figma scrollbar control exists | FAIL strict - justify as component CSS or drop (Q4) |
| 7 | 16 static `style="..."` attributes in the prototype; runtime `style.left/top` set per node/line/arrow/label by the layout engine | "Inline styles are FORBIDDEN" | FAIL. Static ones convert to classes. Runtime positioning is unavoidable for a computed layout - the Position vocabulary covers the concept but not the mechanism. Decision needed (Q3) |
| 8 | Element selectors (`*`, `html, body`, `button`, `main`, headings) | Sanctioned element rules register with written justification | PASS-able via register entries (escape hatch 1) |
| 9 | Graceful degradation: full function with Carbon CDN blocked, plain HTML + tokens | "PREFER web components over utility classes" | STRUCTURAL TENSION - the fallback re-implements every Carbon component in custom markup by design (Carbon as enhancement, not dependency). Decision needed (Q4) |
| 10 | Responsive `@media (max-width: 760px)` stacking | "ALWAYS make your designs responsive" | PASS in intent (Figma has no breakpoint affordance; CSS media queries are the only mechanism - same class of mechanics the rules tolerate for resets) |
| 11 | Semantic HTML, real buttons, aria-labels, role="img"/"status", 44px targets | "Semantic HTML is REQUIRED" | PASS (spec section 11 is thorough) |
| 12 | The imported SKILL.md tables currently carry the other repo's classes (`.portal-*`, `.panel-row-*`, `.asset-preview-*`, 4 `l-` layout classes) | "zero-usage classes MUST be deleted"; lint enforces SCSS<->table sync | FINDING - that register is foreign to this repo (no call sites here, no FigmaPrimitives.scss build). Adding viz classes into it mixes two applications' registers. Decision needed (Q1/Q2) |

### V-2. Summary verdict

The design **cannot** comply with a literal application of the imported rules:
its core visual system (station/LED/rail drawing, motion, product tokens) fails
the genuine mapping test because Figma has no controls for those concepts, and
the rules' enforcement machinery cannot run on a Python-embedded page. The
design **does** comply with the rules' *intent* where that intent is
checkable: Carbon components first, token-driven theming, no shadow one-off
styling (all styling is centralised, named, and spec'd), semantic HTML,
responsive, accessible. The open decisions (Q1-Q5 in the questions file)
determine how much of the letter of the rules binds this unit and how
compliance is enforced.

## Extension compliance preview (elaboration; per aidlc-state.md Extension Configuration)

- **Security Baseline** (enabled): CDN assets pinned to exact versions (spec
  section 3) - SRI hashes worth adding at Functional Design.
- **Resiliency Baseline** (scoped, enabled): CDN-unreachable fallback and the
  stream watchdog/reload behaviour are spec'd (sections 3, 9) - compliant.
- **Property-Based Testing** (partial: pure functions): `pipeline-layout.js`
  is a pure function - layout invariants (stations never scaled, fewest rows,
  no U/C crossings, deterministic ordering) are natural PBT targets.
- **Domain Standards** (enabled): N/A beyond existing domain docs - UI restyle,
  no domain-model change (the `/topology` addition is a DTO extension).

## Relationship to existing units

- **units 1-3, 5** (engine hardening, sandboxing, egress): the viz server is
  host-side trusted code (FR-2.1); untouched by this unit.
- **unit-4** (WONTSHIP): the kept core/engine/viz module split is what this
  unit restyles; no adapter surface is revived.
- **unit-6** (cloud deployment): the viz becomes remotely reachable there;
  no dependency either way.
- **unit-7, unit-8**: no interaction.

## Open decisions (recorded in unit-9-requirement-verification-questions.md)

1. Q1 - Do the frontend rules bind the viz, and at what strength?
2. Q2 - What enforces compliance (no frontend/ toolchain exists)?
3. Q3 - Runtime inline positioning for the layout engine?
4. Q4 - Non-Carbon visuals (animations, scrollbars, `--adl-*` tokens, fallback duplication)?
5. Q5 - Sequencing of unit-9 relative to units 6-8?

## Sources

- `planning-docs/design/README.md`, `viz-redesign-spec.md` (normative), `tokens.css` (normative values), `pipeline-layout.js` (normative algorithm), `prototype/index.html` (visual oracle)
- `.opencode/skills/frontend-development/SKILL.md`; `frontend/scripts/lint-figma-primitives.mjs`
- `src/agentic_dev_loop/viz/server.py` (current PAGE; 1273 lines), `tests/test_viz.py` (1519 lines)
