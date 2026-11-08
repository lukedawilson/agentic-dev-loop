# Unit-12 reporting fixture provenance

These databases are a sanitized slice of runs produced by the real engine, not
hand-built rows. `regen_fixture.py` drives `engine.graph.run_pipeline` with the
real LangGraph `SqliteSaver`, the real graph nodes, real files, and a real git
repo. The only stub is `Runtime.run_agent` (the SDK network boundary, matching
the repo's FakeAgents policy). The audit rows are written with the real
`isolation.append_audit_record` writer shape (V12).

## Scenarios

| Dir | Threads | Shape |
| --- | --- | --- |
| `abcdef123456` | 2 | A crashed run (interrupted, older) then a successful run with a review-fail retry (success, latest, owns the prose artifacts). |
| `111111111111` | 1 | A crashed run that is the directory's latest thread (running). |
| `333333333333` | 1 | An agent hard failure (`failed=True`, `failed_step=qa`). |
| `222222222222` | 1 | Synthetic edge: one corrupt checkpoint blob (decode-failure path). |
| `444444444444` | 1 | Synthetic edge: pre-unit-11 legacy prose verdicts in the `artifacts` channel (V9), a shape the current engine no longer writes. |

The synthetic entries are the only hand-built rows, per the fixture policy's
edge-case carve-out.

## Sanitization

Every msgpack checkpoint and write blob and every JSON metadata blob is decoded,
absolute paths under the generation temp root are replaced with `/fixture`,
re-encoded, and the database is `VACUUM`ed so no freed page keeps the original
path. No machine-specific path or consumer name remains.

## Audit fixture

`state/sandbox-audit.jsonl.1` holds one rotated command row; the current file
holds one egress event without `exit_code` (must be filtered, U4) and three
command rows (two `dev`, one `test`) whose timestamps overlap the latest thread
of `abcdef123456`. The two `dev` rows are exit 1 then exit 0, pinning the
dev-lane red-then-green signal.

## Regenerating

    .venv/bin/python tests/fixtures/unit12/regen_fixture.py

Regeneration is not part of the test suite; the committed artifact is what the
tests read.
