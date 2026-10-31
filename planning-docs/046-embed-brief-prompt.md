# Plan 046: Embed the brief prompt in the engine, config-driven sections

> **Status: PROPOSED** (flips to COMPLETED after implementation lands)

## Scope

Two changes, one root problem. The brief step's contract today lives in
three places that must agree by hand: the engine's gate constant
(`_REQUIRED_SECTIONS` in `brief_coverage.py`), the engine's vendored
prompt file (`sdk_agents/brief.agent.md`), and every consumer's vendored
copy of that file. Plan 043 fired because copies diverged; the Oct 31
failure ("lanes" in a heading) fired because the vocabulary itself is an
engine-invented fiction no repo file ever defined. This plan makes the
section headings single-sourced (config) and the prompt text
engine-embedded, so the 043 divergence class is structurally impossible
and the vocabulary belongs to the consumer that owns it.

## Context

Verified facts:

- `_REQUIRED_SECTIONS` (src/agentic_dev_loop/engine/brief_coverage.py
  lines 15-18) = ("Repo rules digest", "Unit scope and lane digest",
  "Environment facts", "Open risks"). `missing_anchors` requires each on
  its own whole line (line 87). The gate is a universal engine constant
  containing repo-specific domain language ("lane").
- Neither repo's AGENTS.md contains "lane" or "Unit scope"
  (yosk-cms/AGENTS.md, 29130 bytes, zero hits; the Oct 31 export's
  AGENTS.md, zero hits). "Unit scope" appears only in yosk-cms
  aidlc-docs/construction unit docs and in the brief prompt itself. The
  vocabulary entered the engine at commit 57c83fc ("Add brief agent").
- The engine embeds zero prompt text. `load_agent` (engine/agents.py
  line 28) loads `<agents_dir>/<name>.agent.md` for every agent step
  (stages.py line 176); `agents_dir` comes from pipeline config, so every
  consumer is forced to vendor all four prompt files.
- The gate call sites are stages.py 796 (hash-reuse check), 805
  (reuse-miss logging), 827 (fresh capture check). No prompt text is
  passed to the gate today; it reads the module constant.
- After 043 the consumer copy of brief.agent.md is byte-identical to the
  engine's (cp + diff verified). It carries zero consumer-specific
  content: pure duplication.
- The 034 error guide is precedent for engine-embedded, engine-owned
  agent-facing text: a versioned engine resource injected into agent
  context, updated via reinstall, never consumer-edited.
- The real consumer flexibility today is config-level and untouched by
  this plan: `brief_inputs.include` and `brief_inputs.env_facts`
  (pipeline.yaml). Prompt-file editing is cosmetic (prose) or illusory
  (renaming a section breaks the gate).
- Tests that reference `sdk_agents` paths and will need review:
  test_dry_run.py (28, 53, 182), test_viz.py:143, test_033_brief_step.py
  (532), test_config.py (31, 72, 94), test_secrets.py:25,
  test_hostboundary.py:18, test_outputs.py:19, test_cli.py (25, 41, 63,
  384).

## Decisions

| # | Decision | Proposal | Answer |
|---|---|---|---|
| D1 | Embed the brief prompt in the engine package as a versioned resource (same mechanism as the 034 error guide): shipped in the wheel, read at runtime, updated via reinstall. The brief step loads the embedded prompt unconditionally; `agents_dir` no longer serves the brief prompt. dev/qa/review keep loading from `agents_dir` (they are genuinely toolchain-bound), and custom agents keep the same loader | Embedded prompt, brief excluded from agents_dir lookup | PENDING |
| D2 | Config-driven sections: add `brief_inputs.sections: [str]` (ordered list of heading strings). Engine default is domain-neutral: ["Repo rules digest", "Unit scope digest", "Environment facts", "Open risks"]. The gate derives its required sections from this config at pipeline parse time instead of the module constant; `_REQUIRED_SECTIONS` disappears as a hardcoded source of truth | sections from config, neutral engine default | PENDING |
| D3 | The embedded prompt's OUTPUT block is rendered from the configured sections: each section becomes its own `## <section>` heading line, and the prompt's own "do not shorten or rename" warning lists the configured names. One source feeds prompt AND gate, so the 043 divergence class cannot recur: there is no second copy to edit | Render OUTPUT from sections | PENDING |
| D4 | Validation, fail loud: `brief_inputs.sections` empty, missing, or containing duplicates raises the config ValidationError at load. Sections are normalized by the existing `_normalize` when the gate compares, so configured whitespace is harmless, but exact duplicates are rejected | Validate non-empty, no dup | PENDING |
| D5 | Consumer migration: yosk-cms deletes its vendored `agentic-dev-loop/sdk_agents/brief.agent.md` in the same commit that adds `brief_inputs.sections` (with "Unit scope and lane digest" if it wants the lane vocabulary, or the neutral "Unit scope digest"). If a pipeline still has a vendored brief.agent.md after this lands, the engine logs a warning that it is ignored (embedded prompt is authoritative); deletion is not silently required but is the documented cleanup | Delete vendored file + config sections in yosk-cms | PENDING |
| D6 | Digest hashing: the brief digest hash already covers the step inputs; the embedded prompt version and the configured sections join the hash inputs, so an engine upgrade or a sections edit busts the cached digest and forces a fresh capture (hash-reset budget behavior unchanged) | Prompt version + sections in digest | PENDING |
| D7 | Tests (TDD, red first): (a) config: sections default, override, empty -> ValidationError, duplicate -> ValidationError; (b) gate: `missing_anchors` required set comes from config (adapt the 041 tests, which currently construct against the constant); (c) prompt render: embedded prompt contains each configured section as its own whole `## ` line (this test is the anti-043 pin); (d) brief step: embedded prompt is used even when agents_dir contains no brief.agent.md, and a vendored copy is ignored with the warning; (e) 033 brief-step tests updated for the embedded loader | Red first, then green | PENDING |

## Changes (file by file)

1. `src/agentic_dev_loop/engine/brief_prompt.py` (new): the embedded
   brief prompt template, rendered from configured sections.
2. `src/agentic_dev_loop/engine/brief_coverage.py`: delete the
   `_REQUIRED_SECTIONS` constant; `required_sections` becomes
   `required_sections(sections_config)` or the call sites read config.
3. `src/agentic_dev_loop/core/config.py`: `brief_inputs.sections`
   field + validators (D2, D4).
4. `src/agentic_dev_loop/engine/stages.py`: brief node reads the
   embedded prompt (D1), threads configured sections into the gate
   calls (796/805/827), adds prompt version + sections to the digest
   (D6), warns on a vendored brief.agent.md (D5).
5. `src/agentic_dev_loop/engine/agents.py`: `load_agent` untouched for
   dev/qa/review/custom; brief no longer routes through it.
6. `agentic-dev-loop/sdk_agents/brief.agent.md`: deleted from the repo
   (the embedded template replaces it).
7. Tests: test_config.py, test_041_brief_coverage.py, test_033_brief_step.py,
   plus any `sdk_agents` path fixture that assumed a vendored brief file.
8. Consumer (after this lands, separate commit): yosk-cms deletes its
   vendored brief.agent.md and adds `brief_inputs.sections`.

## Non-goals

- No change to the whole-line matching semantics or the anti-fake
  rationale (041 D2 stays).
- No embedding of dev/qa/review prompts; they stay consumer-vendored
  and genuinely toolchain-specific.
- No prompt-prose override field. The config surface for consumers is
  the sections list, include list, and env facts; anything prose-level
  stays engine-owned and versioned.
- No change to the coverage retry budget, last-error.md behavior, or
  hash-reset budget.

## Verification

- Full suite green, including adapted 041/033 tests and the new
  anti-043 render test.
- Engine dogfood: this repo's own pipeline.yaml gains
  `brief_inputs.sections` (neutral defaults) and its vendored
  brief.agent.md is deleted; a dry-run brief step renders the embedded
  prompt with the configured headings.
- Consumer follow-up: yosk-cms commit deleting the vendored copy and
  declaring its sections; a fresh plan-004 attempt then briefs with the
  consumer's own vocabulary, gate and prompt sourced from one list.

## User Decision Required

Approve or adjust each of D1-D7. Points worth an explicit call:
(1) D1 removes any consumer ability to edit the brief prompt file -
every edit available today was either cosmetic or gate-breaking, and the
legitimate want (section vocabulary) moves to config, but say so if you
want a last-resort prompt override path kept. (2) D5's warning-vs-fatal
choice for a stale vendored copy. (3) D2's neutral default dropping
"lane" from the engine forever.
