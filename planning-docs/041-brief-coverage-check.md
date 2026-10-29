# 041-brief-coverage-check.md

> **Status: PLANNED**

Independent of 038 and 039. This is Part B of the brief coverage fix;
Part A (the section-anchored digest contract in the brief agent prompt,
commit 75b545e) is already merged and can land on its own. Part B
enforces Part A mechanically.

## Scope

Engine only: anchor extraction from the brief step's include files, a
coverage gate on the captured brief, a bounded coverage retry, and one
new paragraph in README.md (tooling path). No new pipeline config keys.

## Context

The brief step (033) digests a closed input set into four sections and
injects the result into every subsequent agent step. In the 036 dogfood
run the brief silently dropped whole top-level sections of its rule
inputs: AGENTS.md's "Reverse engineering undocumented systems" section
and the error_handling and coding_style categories of
coding-standards-ddd.yaml. The rules were load-bearing for that unit
(036 modifies SandboxTerminalExecutor and sync_back, the machinery on
every agent write path), and the loss was invisible: the brief step
passed, and dev/review never saw the missing rules because the brief is
the only carrier of those files.

Part A changes the digest contract from free-form selection to
structure-preserving compression: one line per top-level section,
heading or category name kept verbatim, no section may be dropped. Part
A is prompt-only. LLM compliance with a prompt contract is not a
guarantee; the same silent drop can recur, and a regression that never
fails a step is a regression nobody fixes. Part B makes the coverage
guarantee mechanical: the engine derives the required anchors from the
include files themselves and fails the step when the captured brief does
not cover them.

## Decisions

| # | Decision | Proposal |
| --- | --- | --- |
| D1 | The anchor set is derived mechanically from each resolved `brief_inputs.include` file: for markdown, every `##` heading; for YAML, each `- name:` entry under `categories:` plus the `enforcement_levels` key. Files with no extractable anchors contribute none (vacuous pass for that file) | AGREED |
| D2 | A coverage gate runs in the brief node after capture (and on a reused digest): the four required section headings must appear; every anchor must appear on its own line, case-insensitive, whitespace-collapsed, bold decoration allowed; every env fact must appear verbatim. Missing items fail coverage and name exactly what was dropped | AGREED |
| D3 | A coverage failure re-runs the brief step with a "Coverage retry" note listing the missing anchors, bounded by a per-run counter (cap 3); when the cap is exhausted the run fails via last-error.md naming the persistent drops. The counter resets when the input hash changes (a new input set gets a fresh budget) | AGREED |
| D4 | The coverage note is appended by `build_brief_context` (new optional parameter), and the brief agent prompt gains one bullet telling the agent how to respond to a Coverage retry section | AGREED |

## Changes

### 1. Engine: anchor extraction (new module)

New `src/agentic_dev_loop/engine/brief_coverage.py`:

- `extract_anchors(content: str) -> list[str]`: markdown `## ` headings
  (line starts), YAML `- name:` values under a `categories:` block plus
  the `enforcement_levels` key. Anchors are normalized (case-folded,
  whitespace-collapsed) once, at extraction.
- `required_sections()`: the four template headings the brief must emit.
- `missing_anchors(brief_text: str, anchors: list[str],
  env_facts: list[str]) -> list[str]`: every required section heading,
  every anchor (line-anchored match: the normalized anchor must occupy
  its own line, `**` decoration stripped first; a prose mention mid-line
  does not satisfy), and every env fact (exact verbatim match). Returns
  the missing list; empty list means pass.

Line-anchoring is deliberate: Part A already requires headings on their
own lines, so a compliant digest always matches, and a mention-in-prose
cannot fake coverage.

### 2. Engine: coverage gate in the brief node

In `stages.py` `make_brief_node`, after a successful capture AND on the
reuse path (the digest handed to dev/review must be checked whoever
produced it):

- Compute the anchor set from the resolved include files (the same
  contents that feed `brief_inputs_hash`).
- Run the coverage check. On pass, proceed exactly as today.
- On miss, consume one unit of the per-run coverage retry budget and
  re-run the brief agent with the coverage note appended. Re-entry
  reuses the existing retry-target machinery; the budget is tracked in
  run state next to `brief_hashes`.
- Budget exhausted: write last-error.md naming the persistent drops and
  fail the run, mirroring the existing brief-step failure path.

### 3. Engine: coverage note in the brief message

`build_brief_context` gains an optional `coverage_note: str | None`
parameter. When set, the message ends with a `## Coverage retry`
section: the list of missing anchors, and the instruction to regenerate
the digest so every listed item appears on its own line. The note is not
prior verdicts or feedback; it is a correction request against the
digest contract, so the 033 "prior verdicts stay out of the brief" rule
is unaffected.

### 4. Agent prompt: coverage retry bullet

One bullet in `agentic-dev-loop/sdk_agents/brief.agent.md` (the Part A
file): if a `## Coverage retry` section is present, regenerate the brief
making every listed item appear, verbatim where the contract requires
verbatim. No exploration; the listed items are the only correction
signal.

### 5. README.md documentation (tooling path)

One paragraph in the brief-step reference: the coverage gate, what it
checks (four sections, include-file anchors, verbatim env facts), the
bounded retry, and the run-failure behavior. Human commit required.

## Tooling-path deliverables

README.md paragraph (change 5). Human commits required.

## Verification

- TDD, extractor first: `extract_anchors` on fixtures cut from real
  files (AGENTS.md and coding-standards-ddd.yaml, not synthesized) -
  markdown headings, YAML categories plus enforcement_levels, a plain
  file with no anchors yields an empty list.
- `missing_anchors`: all-present passes; each missing anchor, missing
  section heading, and altered env fact is named in the result;
  line-anchoring rejects a mid-line mention; case and whitespace
  variants pass; `**`-wrapped headings pass.
- Brief node: coverage miss returns the retry note and re-runs the
  agent (fake agent, no LLM); the cap is consumed per miss; exhausted
  budget writes last-error.md and fails the run; a reused digest that
  fails coverage regenerates instead of shipping; a passing digest is
  reused without an LLM call.
- Backward compatibility: pipelines without a brief step are untouched;
  include files with no extractable anchors pass vacuously; no new
  config keys, existing pipelines load unchanged.
- ASCII only; no consumer names in this repo.

## Out of scope

- Part C: a verbatim (uncompressed) class of brief inputs.
- Judging digest quality beyond coverage: the compressed lines are the
  agent's to write; only the guarantee that every section survives is
  mechanical.
- Unit digest coverage: plan docs reach every agent verbatim through the
  loader channel, so their sections need no anchor enforcement.
- Changes to `brief_inputs_hash` itself.

## Open risks

- The line-anchored match can false-negative a digest that inlines a
  heading in prose. Accepted: Part A requires headings on their own
  lines, so the check enforces the contract, and the retry note names
  exactly what to fix.
- Duplicate headings across include files merge into one anchor; one
  line satisfies both files. Coverage is the goal, not per-file
  attribution; noted and accepted.
- YAML extraction handles `- name:` under `categories:` only. A
  consumer rule file with an unusual shape yields few or no anchors and
  is therefore under-checked, never over-checked. Documented in README.
- Coverage retries consume graph cycles; the cap of 3 plus the agent's
  own turn limit bounds the cost.
- A mid-run edit to an include file changes both the hash and the
  anchor set: the existing regeneration gate re-runs the brief, and the
  gate checks the new set. Combined behavior covered by tests.
