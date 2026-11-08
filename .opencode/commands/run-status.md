---
description: Show the current (latest) pipeline run in this workspace as a mermaid graph with the current step highlighted, plus a bullet summary. Usage: /run-status
---

Report the status of the latest agentic-dev-loop run in this workspace. Render a mermaid flowchart of the pipeline graph with the current step highlighted, and list a brief bullet summary below it.

## Step 1: collect the run state

Run the engine's run-status subcommand for this workspace's pipeline (normally `agentic-dev-loop/pipeline.yaml`; adjust the path if this repo's pipeline lives elsewhere):

!`agentic-dev-loop run-status agentic-dev-loop/pipeline.yaml`

The command prints two sections: `===MERMAID===` then `===SUMMARY===`.

## Step 2: render the result

- If the command printed `NO_RUN`: report "No run has been recorded for this workspace yet" and stop.
- Otherwise, present the `===MERMAID===` section verbatim inside a ` ```mermaid ` fenced block (strip the `===MERMAID===` marker line).
- Below it, present the `===SUMMARY===` section verbatim as a bulleted list (strip the marker line). The `Steps:` line is a single bullet; do not reformat it.

## Errors

- If `agentic-dev-loop` is not on PATH, report that the engine is not installed (`pipx install agentic-dev-loop`) and stop.
- If the pipeline file is missing, report that and stop.
