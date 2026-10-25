# 026 - Commercial-name scrub

> **Status: DONE (2026-10-25).** User directive 2026-10-25: this repo's
> committed files must not name the commercial consumer product. Docs scrub
> executed earlier (aidlc-docs/, planning-docs/, docs/); the code-mode
> inventory (repo root, src/, tests/) was executed on 2026-10-25 per the
> user's step-0 instruction.

## What changed

- README.md secrets-store example: the product-prefixed R2 key name is now
  the neutral `R2_KEYS`.
- Dockerfile.dotnet header comment: neutral phrasing ("the consumer repo").
- src/agentic_dev_loop/engine: actions.py, graph.py, hostboundary.py,
  stages.py (4 spots) - comments and docstrings only, zero behaviour change.
- tests/: test_actions.py, test_agents.py, test_config.py (3), test_graph.py
  (4, incl. one test method name), test_stages.py (2), test_secrets.py (4) -
  docstrings, fixture YAML names, and assertion-matched strings, all
  mechanical.
- planning-docs/019 secrets-store doc: example key name neutralised.
- The execution inventory in the first version of this doc missed
  test_secrets.py (4 spots) and the 019 example line; both were caught by
  the post-edit verification grep and scrubbed in the same change-set.

## Phrasing

Deliberate and consistent: "the consumer repo" for repo references, "the
consumer loop" for loop provenance, "the consumer's" for possession. It
matches the phrasing already used in the scrubbed planning docs, several of
which quote these docstrings (e.g. planning-docs/011 quotes a
stages.py:243 docstring).

## Acceptance (verified 2026-10-25)

- Full `pytest` suite green.
- a case-insensitive grep for the former commercial name returns nothing on tracked files.

audit.md keeps its append-only record; the scrub is logged there without
naming the product.
