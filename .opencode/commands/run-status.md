---
description: Show the latest agentic-dev-loop run in this workspace as a mermaid graph with the current step highlighted, plus a bullet summary. Usage: /run-status
---

Report the status of the latest agentic-dev-loop run in this workspace. Render a mermaid flowchart with the current step highlighted, and a brief bullet summary below it.

## Step 1: collect the run state

Run the engine's run-status subcommand with no argument. It discovers the latest run across the whole run-state directory, so it finds the most recent run regardless of which pipeline path (including a smoke test's disposable temp pipeline) produced it:

!`agentic-dev-loop run-status`

The command prints two sections: `===MERMAID===` then `===SUMMARY===`.

## Step 2: render the result

- If the command printed `NO_RUN` (or "No run has been recorded for this workspace yet"), report "No run has been recorded for this workspace yet" and stop.
- Otherwise, present the `===MERMAID===` section verbatim inside a ```mermaid fenced block (strip the `===MERMAID===` marker line).
- Below it, present the `===SUMMARY===` section verbatim as a bulleted list (strip the marker line). Keep the `Steps:` line as a single bullet; do not reformat it.

## Errors

- If `agentic-dev-loop` is not on PATH, report that the engine is not installed (`pipx install agentic-dev-loop`) and stop.
