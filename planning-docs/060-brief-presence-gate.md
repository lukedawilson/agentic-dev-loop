# 060 - Brief presence gate

> **Status: COMPLETED**

## Scope

The brief step's coverage gate (`src/agentic_dev_loop/engine/brief_coverage.py`),
the brief prompt contract (`src/agentic_dev_loop/engine/brief_prompt.md`), and
the coverage retry note (`build_brief_context` in
`src/agentic_dev_loop/engine/stages.py`). Three coordinated edits, no change
to the brief node's retry budget, reuse path, or hashing (beyond the prompt
version bump that the hash already consumes).

This is Alternative 1 of the three discussed directions for the brief
flapping problem: keep the mechanical gate, keep its "nothing silently
disappears" invariant, but stop demanding an output shape the model cannot
reliably produce.

## The problem

The coverage gate exists for one reason: the brief is the only rules channel
for the dev and review agents on every attempt, and a real run
(5367f0c488d8, planning-docs/043) dropped 27 rule sections from its digest
before the gate existed. The invariant worth keeping is that no repo-rule
section, env fact, or configured section heading silently disappears between
input and digest.

Today the gate validates *form* as a proxy for that invariant: each anchor
must occupy its own whole line, character-exact after casefold and whitespace
collapse. Form is what LLMs flap on. Two observed failure classes:

1. **The em dash class.** `brief_prompt.md` demands "keep each heading
   verbatim" and "ASCII only. Do not use em dash characters" in the same
   RULES list. When a consumer's repo-rule heading contains a non-ASCII
   character (the consumer AGENTS.md heading ending in U+2014 already
   produced this in run 5367f0c488d8, per 043), the agent cannot satisfy
   both rules. It transliterates, the gate compares character-exact and
   reports a miss, the retry note lists the item verbatim, and the agent
   flaps between the two instructions until the retry cap (3) exhausts and
   the run fails. One em dash kills the run.
2. **The inline class.** 043's own evidence: the model persistently emits
   `anchor: content` on one line, which the whole-line match rejects. 043
   fixed the correction note's wording and deliberately kept the strict
   match, with the inline form listed as an accepted fallback if evidence
   showed the stricter note was not enough (043 "Open risks"). That evidence
   has now arrived in the form of the em dash class and the general
   brittleness report.

## Verified facts

- `brief_coverage.py` `_normalize` (lines 16-18) case-folds and collapses
  whitespace only; `_normalize_line` (21-31) strips `**`, leading `#`, and
  bullet decoration; `missing_anchors` (65-85) matches sections and anchors
  against the set of normalized whole lines (75-81) and env facts as exact
  substrings (82-84).
- Unicode normalization alone does not fold the em dash: U+2014 has no
  compatibility decomposition, so NFKC leaves it unchanged. An explicit
  translate table is required for U+2010 through U+2015 (the dash family)
  and for curly quotes U+2018/2019/201C/201D (also untouched by NFKC).
- `brief_prompt.md` lines 27-40 carry the strict shape contract ("The anchor
  line holds the heading and nothing else", the two-line example, and the
  "does not satisfy coverage" sentence); line 43 carries the ASCII-only
  rule that contradicts the verbatim-heading rule.
- `brief_prompt.py` line 18: `BRIEF_PROMPT_VERSION = "1"`; the version joins
  the brief digest hash (046 D6, `brief_inputs_hash` in stages.py line 641),
  so a bump invalidates cached digests and forces one fresh capture per
  input set.
- The retry note (`build_brief_context`, stages.py lines 677-691) currently
  teaches the strict form: "the item alone on the line, no trailing colon
  and no content". Pinned by `TestCoverageNoteWording` in
  tests/test_041_brief_coverage.py (lines 145-169).
- `COVERAGE_RETRY_CAP = 3` (stages.py line 66); the brief node's miss path
  (stages.py 1003-1027) and reuse path (965-984) are form-agnostic and need
  no change.
- `test_mid_line_mention_does_not_satisfy` (test_041 line 126) already
  encodes the anti-fake case: a heading buried in prose must not satisfy
  coverage. Line-prefix matching keeps this test green unchanged.

## Design

**D1 - Line-prefix matching replaces whole-line matching.** A required item
(a configured section heading or an include-file anchor) is covered when its
normalized text starts one line of the brief, after the existing decoration
stripping. Content may follow on that line (`general_principles: Simplest
thing that works...` now passes) or on following lines. A mid-line mention
still fails, because the item must be the line's prefix, not merely present.
This deliberately accepts the `anchor: content` inline form 043 rejected,
and deliberately keeps the 041 D2 anti-fake property against buried prose.

**D2 - Glyph-tolerant normalization.** `_normalize` gains, before casefold
and whitespace collapse: NFKC normalization, then an explicit translate
table mapping U+2010-U+2015 to U+002D (hyphen) and U+2018/2019 to U+0027
and U+201C/201D to U+0022 (straight quotes). Applied at extraction and at
comparison, so a digest that transliterates an em dash to a hyphen matches
an anchor extracted from a heading that contains the em dash, and vice
versa. The dash table is the load-bearing part for the reported failure
class; NFKC alone does not fold U+2014 (see Verified facts).

**D3 - Env facts: glyph-tolerant, case-sensitive.** Facts are matched as
normalized substrings: `_fold_glyphs(fact) not in _fold_glyphs(brief_text)`,
where `_fold_glyphs` is NFKC plus the dash/quote table plus whitespace
collapse, case preserved. A fact reproduced with a hyphen instead of an em
dash passes; a genuinely altered fact is still named (the existing
`test_altered_env_fact_is_named` stays green); case still matters, so a
short fact like `CI` cannot be satisfied by the word "efficient". Facts
stay byte-verbatim in the prompt contract; the gate merely stops punishing
glyph drift.

**D4 - Prompt re-contract and version bump.** `brief_prompt.md`:

- The repo-rules digest rule (lines 27-40) becomes: keep each heading
  verbatim at the start of its own line; the compressed rules follow on
  that line (after a colon) or on the following line. The two-line example
  stays as the recommended form; the "does not satisfy coverage" sentence
  is replaced by "the heading must start its line" wording. No top-level
  section or category may be dropped; merging is still allowed only between
  a section and its own subsections.
- The ASCII rule (line 43) becomes: ASCII only, except that a required
  heading, env fact, or configured section heading must be reproduced
  character-exact; when such an item contains a non-ASCII character, keep
  that exact character and never transliterate it. Do not introduce em
  dashes or other non-ASCII characters of your own.
- The OUTPUT block sentence about section headings ("must appear exactly as
  written") is reworded to "must appear at the start of their own lines",
  matching the gate.
- `BRIEF_PROMPT_VERSION` bumps "1" to "2" in `brief_prompt.py`, which
  invalidates cached digests through the existing hash join.

**D5 - Retry note reworded to the relaxed contract.** `build_brief_context`
(stages.py lines 677-691) replaces "the item alone on the line, no trailing
colon and no content" with "each listed item must start its own line;
compressed content may follow on that line or the next". The note keeps
listing missing items verbatim and keeps the 033 rule intact (it is a
correction request against the digest contract, not prior verdicts).

**D6 - Side effect on the note's item text.** Because anchors are now
glyph-folded at extraction, the retry note lists the folded form (hyphen
instead of em dash). Copying a listed item verbatim therefore satisfies the
gate as written. The prompt still prefers original-case headings; the gate
remains case-insensitive as today, so no new failure mode.

**D7 - Nothing else moves.** Retry budget, reuse path, `verified_state`,
env-fact prompt wording, and the hashing scheme are untouched beyond D4's
version bump. The judge (Alternative 3) and the engine scaffold
(Alternative 2) are out of scope.

## Changes (file by file)

1. `src/agentic_dev_loop/engine/brief_coverage.py`: add `_fold_glyphs`;
   rework `_normalize` on top of it; rework `missing_anchors` to
   prefix matching (lines become a list, membership becomes
   `any(line.startswith(item) for line in lines)`); rework the env-fact
   check to the D3 form.
2. `src/agentic_dev_loop/engine/brief_prompt.md`: the D4 wording edits.
3. `src/agentic_dev_loop/engine/brief_prompt.py`: version bump only.
4. `src/agentic_dev_loop/engine/stages.py`: the D5 note wording only.
5. Tests (below), red first.

All new strings are ASCII (repo em dash ban): tests use `\u2014`-style
escapes, the translate table uses integer codepoints or escaped literals.
No consumer names.

## Tests (red first)

In `tests/test_041_brief_coverage.py`:

- `test_inline_anchor_content_line_passes`: a digest line
  `**Error handling**: Never fall back silently.` covers the anchor.
- `test_em_dash_heading_transliterated_line_passes`: anchor extracted from
  a heading ending in U+2014; a digest line using a hyphen passes.
- `test_curly_quote_heading_folded`: anchor with U+2019; a digest line with
  a straight apostrophe passes.
- `test_env_fact_em_dash_tolerated`: fact `offline \u2014 never fetch`
  covered by a digest line with a hyphen.
- `test_env_fact_case_still_matters`: fact `CI` not covered by the word
  `efficient` (guards D3's case sensitivity).
- `test_mid_line_mention_does_not_satisfy` (line 126): unchanged and still
  green, now the anti-fake pin for prefix matching.
- `TestCoverageNoteWording.test_note_states_line_rule_and_example`:
  re-pinned to the D5 wording; the verbatim-kept-items test is unchanged.

In `tests/test_046_brief_prompt.py`:

- `test_relaxed_contract_is_stated`: the rendered prompt contains the
  "start its own line" rule and the "reproduce character-exact, never
  transliterate" ASCII exception, and no longer demands "the only text on
  its line". This is the anti-divergence pin for the 043 class: prompt,
  gate, and note must agree on one contract.

## Verification

- Full suite green locally (`uv run pytest`), including
  `tests/test_repo_ascii.py` over the touched files.
- Replay check: a digest in the 043 inline form (`anchor: content` on one
  line) now returns no missing items; a digest with a genuinely dropped
  anchor still names it.
- Dogfood: this repo's own pipeline runs the brief step cleanly after the
  version bump forces one fresh capture.

## Non-goals

- Substring-anywhere matching (the loosest form of Alternative 1): prefix
  matching keeps the digest navigable, every heading still starts a line.
- Any change to the retry budget, the reuse path, or last-error.md.
- Env-fact case-insensitivity.
- The judge-verified brief (Alternative 3) and the engine-owned scaffold
  (Alternative 2).
- Mechanical repair of a non-compliant digest on cap exhaustion.

## Open risks

- **The 043 divergence class, three sites again.** Prompt, gate, and note
  must now agree on the relaxed contract. Mitigated by the
  `test_relaxed_contract_is_stated` pin and the note-wording pins.
- **Content quality remains unverified.** The gate checks presence, never
  quality of the compressed rules; a lazy one-liner per anchor passes.
  This is unchanged from today's gate and is the stated cost of staying
  mechanical and deterministic. Alternative 3 is the quality path if this
  ever becomes the observed failure.
- **Prefix semantics at the edges.** A heading that is a prefix of another
  section's text (`error handling` vs a line starting `error handling in
  python`) is covered by either reading; this is acceptable coverage, not
  a miss, and the prompt still directs one section per line.
- **Dash folding merges anchors** that differ only by dash, extending the
  existing dedup at extraction. Two headings differing only in dash type
  would share one anchor; the digest is expected to keep both verbatim
  anyway, and the gate cannot distinguish them. Accepted.

## Decisions

| # | Decision | Proposal | Answer |
| --- | --- | --- | --- |
| D1 | Line-prefix matching replaces whole-line matching: the item must start its own line, content may follow on that line or the next | Line-prefix | AGREED |
| D2 | Glyph-tolerant normalization: NFKC plus an explicit dash-family fold (U+2010-U+2015 to hyphen) and curly-quote fold | NFKC + dash family + curly quotes | AGREED |
| D3 | Env facts: glyph-tolerant, whitespace-collapsed, case-sensitive substring match | Glyph-tolerant, case-sensitive | AGREED |
| D4 | Prompt re-contract plus retry-note rewording, with BRIEF_PROMPT_VERSION 1 to 2 | Reword both, bump version | AGREED |

## Approved wording (D4/D5)

The user approved these exact sentences (2026-11-08), no changes:

- `brief_prompt.md` repo-rules digest rule:

  > The repo-rules digest is structural: one line per top-level section of
  > each repo-rule input. Markdown anchors are the `##` headings: keep each
  > heading verbatim at the start of its own line, then compress that
  > section's rules into the text that follows on that line (after a colon)
  > or on the next line. YAML anchors are each `- name:` entry under
  > `categories:` plus the `enforcement_levels` preamble: keep each name
  > verbatim. No top-level section or category may be dropped; merging is
  > allowed only between a section and its own subsections.

- `brief_prompt.md` ASCII rule:

  > ASCII only, except that a required heading, env fact, or configured
  > section heading must be reproduced character-exact: when such an item
  > contains a non-ASCII character, keep that exact character, never
  > transliterate it. Do not introduce em dashes or other non-ASCII
  > characters of your own.

- `brief_prompt.py` OUTPUT block sentence: "The section headings must
  appear at the start of their own lines, as written above"; and
  `BRIEF_PROMPT_VERSION` "1" to "2".

- `stages.py` retry note sentence:

  > Regenerate the digest so every listed item starts its own line;
  > compressed content may follow on that line or the next line.

## User decisions

All four decision points are approved as proposed (D1 line-prefix, D2 full
fold, D3 glyph-tolerant case-sensitive facts, D4/D5 wording approved
verbatim above). Implementation remains a separate, explicit step per the
repo's ask-before-coding rule: approval does not schedule the work.
