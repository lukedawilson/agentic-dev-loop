# Unit 9 (viz redesign) - Requirement Verification Questions

Unit intent: `aidlc-docs/unit-9-intent-viz-redesign.md` (includes the
design-vs-frontend-rules validation report referenced below as V-0..V-2).

Please answer the following questions to resolve the elaboration decisions.

## Question 1
How strongly do the imported frontend-development rules bind the viz redesign? (Validation finding V-0: the rules are scoped to `frontend/`; the viz is an embedded page in `src/agentic_dev_loop/viz/server.py`, and the Figma round-trip premise does not exist here.)

A) Apply the rules in full - every class passes the genuine mapping test and is registered in the shared SKILL.md tables; design elements that cannot pass (animations, scrollbar styling, `--adl-*` tokens, runtime inline positioning) are dropped or reworked; the normative spec gets amended accordingly.

B) Apply the rules as principles, with a viz-local register - Carbon components first, token-driven, no hidden one-off CSS; every custom class documented with an honest mapping-or-justification in a unit-owned register (kept with the unit's construction artifacts), and SKILL.md gains a scope note pointing at it. The normative design stays as written. (Recommended)

C) Exempt the viz entirely - the rules bind `frontend/` only; the unit implements the spec unchanged with no register.

D) Other (please describe after [Answer]: tag below)

[Answer]: B - Apply the rules as principles, with a viz-local register - Carbon components first, token-driven, no hidden one-off CSS; every custom class documented with an honest mapping-or-justification in a unit-owned register (kept with the unit's construction artifacts), and SKILL.md gains a scope note pointing at it. The normative design stays as written. (user answer 2026-11-03)

## Question 2
How is compliance enforced for the viz, given the imported JS linter cannot run here (it needs `frontend/src` + `node_modules/@carbon/*`; this repo is Python/pytest, server stdlib-only)?

A) A pytest contract check that parses the shipped PAGE CSS/markup and asserts every class/selector is in the unit's register (repo-idiomatic, runs in the existing CI suite). (Recommended)

B) No automated gate - compliance is review-only at Functional Design / Code Generation.

C) Port/extract the viz assets so the existing JS linter can walk them (adds a Node build step the spec's stdlib-only constraint argues against).

D) Other (please describe after [Answer]: tag below)

[Answer]: C - Port/extract the viz assets so the existing JS linter can walk them (adds a Node build step the spec's stdlib-only constraint argues against). (user answer 2026-11-03)

## Question 3
The layout engine computes positions and must set `left/top` per station, line, arrow and label at runtime (spec section 7). "Inline styles are FORBIDDEN" - how is runtime positioning handled?

A) Sanction dynamic inline positioning for the pipeline drawing only, documented in the register (the sanctioned Figma vocabulary's "Position -> X/Y offset (absolute-positioned children only)" covers the concept; the JS style write is the only viable mechanism). Static inline styles remain forbidden and are converted to classes. (Recommended)

B) Forbid it - switch the drawing to SVG or another mechanism. (Contradicts the normative spec, which specifies HTML stations; would require re-elaboration.)

C) Other (please describe after [Answer]: tag below)

[Answer]: A - Sanction dynamic inline positioning for the pipeline drawing only, documented in the register (the sanctioned Figma vocabulary's "Position -> X/Y offset (absolute-positioned children only)" covers the concept; the JS style write is the only viable mechanism). Static inline styles remain forbidden and are converted to classes. (user answer 2026-11-03)

## Question 4
Non-Carbon visuals that fail the strict genuine mapping test (validation V-1 rows 3, 5, 6, 9): the blink/sweep running animations, custom scrollbars, `--adl-*` product tokens (status tints, unverifiable purple, console surface), and the no-Carbon fallback that duplicates components in plain markup. Keep or drop?

A) Keep all, documented as component-class behaviours and product tokens with written justifications in the register (preserves the approved design; the fallback is a resiliency requirement). (Recommended)

B) Drop/replace with stock Carbon tokens and no animation - the spec gets reworked (weakens the three-cue status system and the station-fill design).

C) Case-by-case at Functional Design - each element individually justified or dropped.

D) Other (please describe after [Answer]: tag below)

[Answer]: A - Keep all, documented as component-class behaviours and product tokens with written justifications in the register (preserves the approved design; the fallback is a resiliency requirement). (user answer 2026-11-03)

## Question 5
Sequencing: record this as unit-9. Units 6 (cloud deployment), 7 (Linux-native sandboxing) and 8 (parallel fan-out driver) are intent-only. Where does unit-9 sit?

A) Ordering open - unit-9 has no dependency on 6/7/8; construction can start as soon as inception stages for it complete. (Recommended)

B) After unit-8 - keep strict numeric ordering.

C) Other (please describe after [Answer]: tag below)

[Answer]: A - Ordering open - unit-9 has no dependency on 6/7/8; construction can start as soon as inception stages for it complete. (user answer 2026-11-03)
