---
description: Report accumulated agentic-dev-loop run history. Aggregate by default, or pass a 12-hex run id for a deep dive. Optional --since/--until/--status filters. Usage: /loop-report [run-id] [--since ISO] [--until ISO] [--status STATUS]
---

Report the run history recorded by the engine's read-only reporting command.

## Step 1: run the report

Pass the user's arguments through to the engine:

!`agentic-dev-loop report $ARGUMENTS`

## Step 2: present the result

- Present the markdown output verbatim.
- Keep every provenance label (`[mechanical]`, `[parsed]`, `[claimed]`) and
  every `unavailable (<reason>)` entry exactly as printed - do not infer,
  fill in, or summarise away missing data.
- If the command exits nonzero, report the error line verbatim and stop.

## Errors

- If `agentic-dev-loop` is not on PATH, report that the engine is not
  installed (`pipx install --pip-args="--pre" agentic-dev-loop`) and stop.
