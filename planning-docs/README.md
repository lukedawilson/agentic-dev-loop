# Planning conventions

Conventions for authoring and reviewing plans in this directory.

## Tooling-path deliverables

If a plan changes a tooling path, the loop commit will NOT sweep it
(tooling paths are excluded from the commit action by design, plan 020).
State it in the plan: "deliverable X is a tooling path and will need a
human commit."

Known tooling paths: `agentic-dev-loop/pipeline.yaml`,
`agentic-dev-loop/sdk_agents/`, README.md (via `additional_files:
- ../README.md` in the pipeline), and everything under the tooling
directory names in `hostboundary.py` (`tooling_rel_paths`).

Why this exists: three plans in a row (023, 024, 025) each left one
deliverable outside the loop's commit, and in each case the plan never
said so, so the completion review had no trigger to check for it. Plans
that touch tooling paths must say so explicitly.

## Plan status

A plan's `Status` line flips to COMPLETED only when all its deliverables
are on main, including any human-committed tooling files. If a tooling
deliverable is outstanding, the plan is not complete yet.
