# Plan 043: Brief coverage flap fix

> **Status: COMPLETED**

## Scope

Wording only, in two files: the coverage retry note in the engine
(`src/agentic_dev_loop/engine/stages.py`, `build_brief_context`) and the
brief agent prompt (`agentic-dev-loop/sdk_agents/brief.agent.md`). The
coverage gate itself (`src/agentic_dev_loop/engine/brief_coverage.py`) is
unchanged. Tests in `tests/test_041_brief_coverage.py` are extended to pin
the new note wording.

## Context

Run 5367f0c488d8 (plan 004-portal-to-root-move.md) hard-failed at the brief
step after 4 attempts: 1 initial run plus the 3 retries the 041 gate allows,
with last-error.md naming 27 persistent drops. The gate itself worked as
designed. The brief agent's digest was genuinely non-compliant, and the
correction note it received on each retry was too weak to change its output
format.

Evidence, checked against the dead run's own artifacts:

- Replaying the captured `attempt-brief.md` through
  `brief_coverage.missing_anchors` with the anchors extracted from the run's
  include files (the export copy of the consumer AGENTS.md and
  coding-standards-ddd.yaml) returns exactly the 27 items in last-error.md.
- Failure mode 1: the digest writes `## Unit scope digest`. The contract
  heading is `## Unit scope and lane digest`, one of the four verbatim
  section headings checked by the gate.
- Failure mode 2: every include-file anchor is inlined as
  `- anchor: content` on one line (for example
  `- general_principles: Simplest thing that works; ...`). The gate matches
  whole lines (041 D2, deliberately): the anchor must occupy its own line
  after stripping `**`, `##`, and bullet decoration. A trailing colon plus
  content breaks the match, so all 26 anchors fail.
- The retry loop consumed its cap of 3 re-runs (041 D3). Each retry rebuilt
  the message with a `## Coverage retry` section whose instruction reads
  "Regenerate the digest so every listed item appears on its own line". The
  model read that as "the item name starts its own line", not "the item is
  the only text on its line", and kept emitting `anchor: content` lines.
  The retry output regressed: attempt 1 dropped 10 items, the retry dropped
  27 (the inline format spread to anchors that had been compliant, and the
  unit section heading got renamed).
- Dash characters are significant: normalization case-folds and collapses
  whitespace only. The consumer AGENTS.md heading ends with the U+2014 em
  dash followed by `ASK BEFORE CODING`. The note lists normalized anchors
  verbatim, so copying an item from the note reproduces the right dash; the
  gate is correct to require the exact character.

## Decisions

| # | Decision | Proposal |
| --- | --- | --- |
| D1 | The gate is not relaxed. Whole-line anchor matching stays: it is the deliberate anti-fake mechanism from 041 D2, and the captured digest is genuinely non-compliant, not a false negative | AGREED |
| D2 | Fix the correction signal instead. The coverage note states the mechanical rule, shows a two-line compliant example, and names the failing one-line form | AGREED |
| D3 | The brief agent prompt gains the same concrete example in its RULES section and a warning not to shorten or rename the four section headings | AGREED |
| D4 | Dead run 5367f0c488d8 stays dead. After this fix lands, the user re-invokes the loop for plan 004; the new run gets a fresh retry budget and a fresh hash | AGREED |

## Changes

### 1. Engine: coverage note wording (`stages.py`, `build_brief_context`)

Replace the instruction sentence of the `## Coverage retry` section with the
strict rule plus a compliant example:

```text
Regenerate the digest so every listed item occupies its own line, exactly
as listed: the item alone on the line, no trailing colon and no content.
Put the compressed content on the following line:

- general_principles
  Simplest thing that works; defer decisions to the last responsible moment.

A line like `general_principles: Simplest thing that works; ...` does not
satisfy coverage; the item must be the only text on its line.
```

Notes: the example anchor is ASCII, so this repo's em-dash ban holds for
the code. Items with U+2014 in the real anchor set are generated from the
include file, not written into this repo's code. The note remains a
correction request against the digest contract, not prior verdicts, so the
033 rule ("prior verdicts stay out of the brief message") is unaffected.

### 2. Brief agent prompt (`agentic-dev-loop/sdk_agents/brief.agent.md`)

- Extend the repo-rules-digest coverage bullet in RULES with the same
  example shape: anchor alone on its line, compressed rules on the
  following line, and the sentence that `anchor: rules` on one line does
  not satisfy coverage.
- Add one sentence under OUTPUT: the four section headings must appear
  exactly as written in the template, including
  `## Unit scope and lane digest`; do not shorten or rename them.

### 3. Tests (`tests/test_041_brief_coverage.py`)

Red first:

- Pin the note text: `build_brief_context(..., coverage_note=...)` output
  contains the strict-rule sentence, the compliant example, and the
  failing-form sentence.
- Existing tests stay green untouched: no checker behavior changes.

## Verification

- TDD red-green-refactor on the note-text test, then the engine and prompt
  edits. Full suite locally (`uv run pytest --filter Category!=Database`).
- Replay check: the captured `attempt-brief.md` still returns 27 missing
  items (gate unchanged); a mechanically compliant rewrite of it (anchor
  lines split from content, `## Unit scope and lane digest` restored)
  returns an empty list.
- ASCII only; no consumer names.

## Out of scope

- Relaxing the whole-line match to accept `anchor: content` lines.
- A mechanical last-ditch repair of a non-compliant digest on cap
  exhaustion.
- Rescuing or resuming run 5367f0c488d8.
- Any change to `brief_coverage.py` or to the anchor extraction rules.

## Open risks

- The model may still emit the inline format despite the stricter note. If
  that recurs on a future run, the fallback options are accepting the
  `anchor: content` line form in the gate, or mechanically splitting such
  lines when the cap is exhausted. Both are deliberately deferred until
  evidence shows the stricter note is not enough.
