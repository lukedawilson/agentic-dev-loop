# 039-step-custom-prompt.md

> **Status: PLANNED**

## Scope

Engine: a new per-step config key that appends operator-supplied text
(or the contents of a file the operator names) to an agent step's
prompt. No consumer changes required for the engine half. README
documentation is the only tooling-path deliverable and needs a human
commit.

## Context

Consumer run 5367f0c488d8, unit-11 QA: the QA agent builds its own test
plan, but the user wants to augment it with custom guidance or a whole
test plan for a specific run. The user's counterproposal: a custom
prompt key on a step that takes either a file path or text, and appends
it to the agent prompt.

The same mechanism serves any agent step: a dev step can carry one-line
scope notes, a review step extra findings, a brief step domain facts.
The engine domain has no concept of a specific step such as "qa", so
the feature is step-agnostic: any agent step can declare the key.

Append-only in v1. Override mode (replacing the step's own
instructions) is a non-goal until a run shows it is needed.

Worked example: a pipeline's qa step can carry
`custom_prompt: tests/qa-plan.md` to inject a hand-written test plan
for that run, or `custom_prompt: "verify the export tab first, then
the asset upload flow"` as inline guidance.

## Decisions

| # | Decision | Proposal |
| --- | --- | --- |
| D1 | New StepConfig field `custom_prompt` (string), agent steps only | AGREED |
| D2 | The value is either literal text or a path to a file whose contents are read at load time. Resolution rule: a value that names an existing file (relative to the pipeline's directory, or absolute) is read as a file; any other value is literal text. A value that looks like a path (has a file extension or a path separator) but names no file is a hard validation error, never silent text | AGREED |
| D3 | The resolved contents are appended to the agent prompt at context assembly as their own section, after the step's prompt and before the task/artifacts block. The engine appends the text verbatim, no rewording | AGREED |
| D4 | Shape rule: `custom_prompt` requires `agent` (mirrors the `terminal_timeout` rule) | AGREED |
| D5 | Append-only v1. Override mode is a non-goal | AGREED |

## Changes

### 1. StepConfig.custom_prompt (engine)

`core/config.py`: `custom_prompt: str | None = None`, plus the shape
rule "'custom_prompt' requires 'agent'". File resolution per D2 happens
at pipeline load time; a missing file that looks like a path raises a
config error naming the path (repo rule: no silent fallbacks).

### 2. Context assembly injection (engine)

Where an agent step's prompt is assembled (sdk prompt file plus
injected resources), the resolved `custom_prompt` text is appended as
its own section ("Run-specific instructions", verbatim). Command and
action steps never see it.

### 3. README (tooling path)

pipeline.yaml reference table row for `custom_prompt` plus one sentence
in the agent steps docs. Needs a human commit.

## Verification

- Config tests: literal text round-trips; an existing-file path reads
  the file contents; a missing path with an extension fails validation
  with a named error; a non-agent step with the key fails validation.
- Context assembly test: an agent step with `custom_prompt` has the
  text appended exactly once, after the prompt and before the
  artifacts; a step without it is unchanged.
- Full pytest suite green.
- ASCII only, no em dash, no consumer name (repo rules).

## Non-goals

- Override mode (replacing the step's own instructions).
- Per-attempt regeneration or re-reading of the file.
- Consumer-specific text in engine code (the value comes from the
  pipeline; the engine stores and appends it verbatim).
