# 033-brief-step.md

> **Status: PLANNED**

Assumes 032 implemented first (not strictly required; the brief step is
independent of the stop-when-green signal).

## Scope

Engine (config surface + context assembly for one new agent step), a
`brief.agent.md` prompt template, and consumer pipeline wiring (tooling
paths). No engine persistence: fresh conversations stay fresh.

## Context

Consumer run 5367f0c488d8 had ~55 dev entries, each a fresh conversation
that re-read the repo rules (AGENTS.md, ~29KB), the unit plan, and
re-discovered environment facts (the export has no .git; package cache
paths; offline constraints). The lane allowlist dilemma was re-derived at
least twice. Fresh contexts are a deliberate feature (no persistence, no
context rot) and stay; the waste is the re-digestion, not the reset.

User decision: a single bounded step reads the repo context once per
attempt and its output is reused, instead of persistence or conversation
resume.

## Decisions

| # | Decision | Proposal |
| --- | --- | --- |
| D1 | The brief is an ordinary agent step (`agent: brief`, `produces: attempt-brief.md`, `max_iterations: 12`), not a new step kind; capture, hygiene, and injection reuse existing machinery (capture-first policy, per-attempt artifact hygiene, 027 artifact injection) | AGREED |
| D2 | Inputs are a closed list, not free exploration: AGENTS.md, CLAUDE.md when present, README.md, coding standards, the resolved unit plan docs, an environment-facts block, and the notes/verdicts already flowing into step context | AGREED |
| D3 | Output follows a strict template (repo rules digest, unit scope and lane digest, environment facts, prior verdicts and open feedback, open risks) with a one-pass anti-churn rule in the brief prompt | AGREED |
| D4 | Regenerated every attempt (per-attempt hygiene deletes it); optional v2 skips regeneration when the gather hashes and the notes channel are unchanged | AGREED (v2 optional) |

## Changes

### 1. Engine: brief input config + context assembly

- New optional `StepConfig.brief_inputs`: `{ include: [<paths/globs>],
  env_facts: [<strings>] }`. The engine resolves the include list (unit
  doc resolution already exists in the aidlc extension), appends the
  accumulated notes/verdicts, and hands the whole set to the brief agent
  as its task message. `env_facts` carries the stable environment facts
  (no .git in the export, package cache paths, offline rules) so the
  brief agent never discovers them by running commands.
- Everything downstream is existing machinery: the brief's final response
  is captured to `attempt-brief.md` (capture-first), per-attempt hygiene
  deletes it at attempt start, and 027 injects it into the steps that
  declare it (dev and review in the consumer pipeline).

### 2. Brief prompt template (tooling path)

New `agentic-dev-loop/sdk_agents/brief.agent.md`: tools `[terminal]`,
strict section template (D3), one-pass rule ("read the listed inputs,
emit the brief, finish - do not explore, do not verify, do not read
anything not listed"), bounded by the step's `max_iterations: 12`.

### 3. Consumer pipeline (tooling path)

A brief step placed first (`on_pass: dev`), with `produces:
attempt-brief.md`, `max_iterations: 12`, and `brief_inputs` listing the
consumer's context files. dev and review declare the artifact for
injection.

### 4. Optional v2 skip

Skip regeneration when the include-list content hashes and the notes
channel are unchanged since the last brief. Deferred; v1 regenerates
every attempt.

### 5. README.md documentation (tooling path)

Update the `pipeline.yaml` reference (README.md) to document
`brief_inputs` (one YAML line, one table row) and add a short note that a
produces-only agent step such as `brief` is the documented pattern for
per-attempt context digestion.

## Tooling-path deliverables

`brief.agent.md`, the consumer pipeline.yaml wiring, and README.md. Human
commits required; the loop commit will not sweep tooling paths.

## Verification

- TDD: context-assembly tests (include list resolution, `env_facts`
  passthrough, notes appended), config validation (unknown include paths
  fail loudly per the no-silent-fallback rule).
- Backward compatibility: steps without `brief_inputs` behave identically.
- ASCII only; no consumer names in this repo.
- Post-deploy observation: exactly one brief per attempt; dev and review
  read the brief instead of re-deriving; the environment-fumbling turns
  disappear.
