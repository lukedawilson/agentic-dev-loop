# Unit 10 (multi-provider runtime) - Requirement Verification Questions

Unit intent: `aidlc-docs/unit-10-intent-provider-abstraction.md` (approved
brainstorm design, 2026-11-05).

Please answer the following questions to resolve the requirements decisions.

## Question 1
How should the unit be decomposed?

A) One unit with staged Code Generation: (1) abstraction + OpenHandsRuntime
refactor landing green alone, (2) claude driver, (3) copilot driver

B) Three separate units, one per stage above

C) Two units: abstraction refactor, then both drivers together

X) Other (please describe after [Answer]: tag below)

[Answer]: A - One unit with staged Code Generation: (1) abstraction + OpenHandsRuntime refactor landing green alone, (2) claude driver, (3) copilot driver. (user answer 2026-11-05)

## Question 2
How does the user authenticate with Claude (claude-agent provider)?

A) User runs `claude login` themselves (Anthropic's own flow); the engine
detects the session and fails fast with setup guidance if absent

B) Engine-assisted: the engine opens the login URL/flow and waits for completion

X) Other (please describe after [Answer]: tag below)

[Answer]: A - User runs `claude login` themselves (Anthropic's own flow); the engine detects the session and fails fast with setup guidance if absent. (user answer 2026-11-05)

## Question 3
How does the user authenticate with Copilot (copilot provider)?

A) Engine hosts GitHub's device flow (prints code + URL, user completes in
browser, no credentials intermediated)

B) Require an existing Codex/Copilot CLI login (e.g. ~/.codex/auth.json)
detected at startup, same pattern as claude

X) Other (please describe after [Answer]: tag below)

[Answer]: A - Engine hosts GitHub's device flow (prints code + URL, user completes in browser, no credentials intermediated). (user answer 2026-11-05)

## Question 4
What is the policy-risk stance for the claude provider?

A) Ship the claude provider with a documented risk note (Anthropic policy gray
area; OpenCode ships the same pattern; distinct from the banned spoofing tools)

B) Hold the claude provider until Anthropic clarifies; ship the abstraction and
the copilot provider first

X) Other (please describe after [Answer]: tag below)

[Answer]: A - Ship the claude provider with a documented risk note (Anthropic policy gray area; OpenCode ships the same pattern; distinct from the banned spoofing tools). (user answer 2026-11-05)

## Question 5
Where do the missing design sections go? The brainstorm approved the design
before the per-provider data flow (message in, events out, final-response
extraction, nudge loop mechanics) and the full status-mapping walkthrough were
presented.

A) Cover them in Requirements Analysis (requirements.md) and deepen in
Functional Design

B) Re-open the brainstorm to present those sections before proceeding

X) Other (please describe after [Answer]: tag below)

[Answer]: A - Cover them in Requirements Analysis (requirements.md) and deepen in Functional Design. (user answer 2026-11-05)

## Question 6
Should the User Stories stage run for this unit?

A) Skip (internal tooling, consistent with all prior units)

B) Run user stories (multi-provider developer-facing feature)

X) Other (please describe after [Answer]: tag below)

[Answer]: A - Skip (internal tooling, consistent with all prior units). (user answer 2026-11-05)

---

## Supersession note (2026-11-05)

Q1's staged answer (one unit, 3-part Code Generation) is SUPERSEDED by a user decision at Code Generation entry (2026-11-05): "Why is the code generation split into 3 parts like this? Each part is useless" -> the parts collapse into ONE continuous Code Generation stage. The de-risking value of the part-1 green gate is preserved as commit-level green checkpoints inside that single stage (pure move green first, then claude driver, then copilot driver; one Build and Test gate at the end). Audit entry: "Code Generation restructure (unit-10)" 2026-11-05T21:30:00Z.

