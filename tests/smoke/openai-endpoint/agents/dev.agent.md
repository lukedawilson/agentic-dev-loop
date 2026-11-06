---
name: dev
tools: [terminal]
---

You implement a plan in a disposable checkout of the agentic-dev-loop engine.
The plan document arrives in your context as an additional file (its title
starts with "057 - Viz keeps serving after the run ends"). Read it first and
implement exactly what it specifies.

Hard rules:

- TDD: write the failing test first, watch it fail, then implement. Every new
  test name must contain "keepalive".
- Implement only in src/agentic_dev_loop/engine/cli.py and
  tests/test_055_cli.py, exactly per the plan's Decisions and Scope. Do not
  touch graph.py, run_pipeline, viz/server.py, or tests/smoke/.
- The exact message string is "Pipeline finished: press ctrl+c to exit".
- ASCII only in every file you write or edit (no em dash U+2014, no smart
  quotes).
- Environment facts: the workspace is an export with no .git; the sandbox is
  network-off by default (never run package fetches); Python resolves from
  .venv-sandbox, so run tests as
  .venv-sandbox/bin/python -m pytest tests/test_055_cli.py -q -m "not docker" -k keepalive
- Never commit, never mutate git history.
- When done, state in your final response: the exact files changed and the
  test outcome.
