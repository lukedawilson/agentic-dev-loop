# 033-brief-step.md

> **Status: COMPLETED**

Assumes 032 implemented first (not strictly required; the brief step is
independent of the stop-when-green signal).

## Scope

Engine (config surface + context assembly for one new agent step, plus a
run-scoped artifact lifetime), a `brief.agent.md` prompt template, and
consumer pipeline wiring (tooling paths). No engine persistence: fresh
conversations stay fresh.

## Context

Consumer run 5367f0c488d8 had ~55 dev entries, each a fresh conversation
that re-read the repo rules (AGENTS.md, ~29KB), the unit plan, and
re-discovered environment facts (the export has no .git; package cache
paths; offline constraints). The lane allowlist dilemma was re-derived at
least twice. Fresh contexts are a deliberate feature (no persistence, no
context rot) and stay; the waste is the re-digestion, not the reset.

User decision: the brief is created once at run start and re-used by
every attempt, regenerated only when its inputs change. What changes
between attempts (prior verdicts, open feedback) already reaches dev and
review through the notes channel every attempt, so it stays out of the
brief.

## Decisions

| # | Decision | Proposal |
| --- | --- | --- |
| D1 | The brief is an ordinary agent step (`agent: brief`, `produces: attempt-brief.md`, `max_iterations: 12`), not a new step kind; capture and injection reuse existing machinery (capture-first policy, 027 artifact injection). Its artifact gets run-scoped lifetime (exempt from per-attempt hygiene) | AGREED |
| D2 | Inputs are a closed list, not free exploration: AGENTS.md, CLAUDE.md when present, README.md, coding standards, the resolved unit plan docs, and an environment-facts block | AGREED |
| D3 | Output follows a strict template (repo rules digest, unit scope and lane digest, environment facts, open risks) with a one-pass anti-churn rule in the brief prompt. Prior verdicts and feedback are NOT in the brief: the notes channel delivers them per attempt already | AGREED |
| D4 | Created once at run start and reused by every attempt. A host-side hash check over the resolved inputs regenerates it only when the inputs change (e.g. a mid-run edit to AGENTS.md or the unit plan); no LLM run otherwise | AGREED |

## Changes

### 1. Engine: brief input config + context assembly

- New optional `StepConfig.brief_inputs`: `{ include: [<paths/globs>],
  env_facts: [<strings>] }`. The engine resolves the include list (unit
  doc resolution already exists in the aidlc extension) and hands it to
  the brief agent as its task message. `env_facts` carries the stable
  environment facts so the brief agent never discovers them by running
  commands. Each entry is one line pairing a fact with the behavior it
  implies; worked example: `"the workspace is an export with no .git;
  verify with find/ls, never git status"`.
- Capture and injection are existing machinery: the brief's final
  response is captured to `attempt-brief.md` (capture-first) and 027
  injects it into the steps that declare it (dev and review in the
  consumer pipeline).

### 2. Run-scoped artifact lifetime (engine)

Per-attempt hygiene deletes stale artifacts at each attempt start; the
brief artifact is exempt. It is deleted at run start instead, so one
brief survives the whole run. This is the only hygiene change: a
run-scoped exemption for `produces` artifacts of steps marked as such
(or, simplest, for the step whose artifact the run graph declares
run-scoped).

### 3. Regeneration gate (engine, host-side)

The engine hashes the resolved include list contents + `env_facts` when
the brief is written. Before each attempt it re-checks the hash; on a
change it re-runs the brief step (which also refreshes the hash). No LLM
call when nothing changed. This covers mid-run edits to AGENTS.md or the
unit plan, which do happen.

### 4. Brief prompt template (tooling path)

New `agentic-dev-loop/sdk_agents/brief.agent.md`: tools `[terminal]`,
strict section template (D3), one-pass rule ("read the listed inputs,
emit the brief, finish - do not explore, do not verify, do not read
anything not listed"), bounded by the step's `max_iterations: 12`. The
template's environment-facts section carries each `env_facts` entry
verbatim, fact and implied behavior together (worked example: "the
workspace is an export with no .git; verify with find/ls, never git
status").

### 5. Consumer pipeline (tooling path)

A brief step placed first (`on_pass: dev`), with `produces:
attempt-brief.md`, `max_iterations: 12`, and `brief_inputs` listing the
consumer's context files. dev and review declare the artifact for
injection.

### 6. README.md documentation (tooling path)

Update the `pipeline.yaml` reference (README.md) to document
`brief_inputs` (one YAML line, one table row) and add a short note that a
produces-only agent step such as `brief` is the documented pattern for
per-run context digestion, regenerated only when its inputs change.

## Tooling-path deliverables

`brief.agent.md`, the consumer pipeline.yaml wiring, and README.md. Human
commits required; the loop commit will not sweep tooling paths.

## Verification

- TDD: context-assembly tests (include list resolution, `env_facts`
  passthrough), config validation (unknown include paths fail loudly per
  the no-silent-fallback rule), hash-gate tests (unchanged inputs skip
  the brief step; a changed file re-runs it), hygiene tests (the brief
  artifact survives attempt starts, dies at run start).
- Backward compatibility: steps without `brief_inputs` behave identically.
- ASCII only; no consumer names in this repo.
- Post-deploy observation: exactly one brief per run (or per input
  change); dev and review read the brief instead of re-deriving; the
  environment-fumbling turns disappear.
