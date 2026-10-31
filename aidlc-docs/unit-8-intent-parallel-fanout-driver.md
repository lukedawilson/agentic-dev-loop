# Unit 8 Intent (draft) - Parallel Fan-out Driver (deterministic multi-run orchestration)

> **Status: INTENT ONLY** - draft, not started. Written 2026-10-31 following the agent-parallelism brainstorming session. No code written, no AI-DLC workflow started, no decisions committed. Revisit before proceeding.

**Assumption:** this unit builds on units 1-5 (complete) and the plan work 001-042. It does not depend on unit-6 (cloud deployment) or unit-7 (Linux-native sandboxing); ordering relative to them is open.

## Raw intent (user, 2026-10-31, verbatim)

- "I want to brainstorm adding agent parallelism to the agentic dev loop"
- Initial thoughts: "Would be most useful for dev"
- "Could tie into aidlc if aidlc supports the concept of unit dependencies or can be easily extended to do so (but don't want to tie it exclusively to aidlc flow)"
- "Claude Code Dynamic Workflows support agent fan-out"

## Steering decisions (user, 2026-10-31, this session)

- **Shape:** parallelism lives ACROSS runs (a fan-out driver around the unchanged engine), not inside one run. Engine-native parallel branches were considered and rejected for this unit (shared workspace, resume determinism, viz/checkpoint rework; also fails the data, see Findings).
- **Driver form:** "The fan-out driver has to be deterministic. It will not be driven by claude-code, that was just an example." Pure Python, no LLM in the driver loop.
- **Scope:** fan-out driver only. Within-run wins (review parallel qa, brief precomputation) are noted future work, out of scope.
- **Conflict policy:** option A (user pick) - rebase-first; on rebase conflict or integration-gate failure, re-attempt the losing unit run from its dev step against the new base.

## Intent

The loop today constructs one unit per run, strictly sequentially: 33 successful runs in 10 days (Oct 20-29), operator-initiated, often hourly. Measured step time across that window is ~36.6h, of which dev is 30.2h (83%). Independent units do not need to wait for each other. This unit adds a deterministic fan-out driver that runs independent unit pipelines concurrently - each run unchanged, on its own worktree and branch - and lands each finished unit onto the base branch with a fixed, bounded conflict-recovery policy. The aidlc unit-of-work dependency matrix serves as the static prerequisite gate; file-level conflicts (which the data shows are the real axis) are absorbed by the landing policy, not predicted up front.

### What the user gets (the load-bearing pillars)

1. **A standalone fanout manifest** (`fanout.yaml`): units, `depends_on`, `concurrency`, per-unit pipeline path and args, integration command. Not aidlc-bound; an aidlc generator can produce it later.
2. **A deterministic driver** (pure Python, no LLM): topological scheduling with a concurrency cap; serial landings; fixed conflict-recovery policy; persisted state for crash-resume of the fanout itself.
3. **Per-run guarantees intact**: every unit run is the complete unchanged pipeline (brief -> dev -> restore -> test -> review -> qa -> commit) with its own sandbox, egress policies, review, qa, artifacts, and thread-id resume. One small engine extension (resume at a named step) is the only core change.
4. **Per-unit commits preserved**: one clean commit per unit on the base branch, matching the aidlc "each unit ships independently" criterion.
5. **Bounded, deterministic conflict recovery**: clean rebase + integration-gate pass -> land; conflict or gate failure -> re-attempt from dev on the new base, within fixed budgets; budget exhausted -> unit fails fast, dependents blocked, independent units continue.

## Evidence base (historical run-data analysis, Oct 20-29)

Sources: checkpoint sqlite under `~/.agentic-dev-loop/runs/<hash>/` (22 attempts in 5367f0c488d8, 13 completed runs in 6cb529022644), `state/sandbox-audit.jsonl` (1469 agentic-dev-loop-construction events), construction commit log.

- **Where the time went** (~36.6h measured step time): dev 30.2h (83%), review 3.3h (9%), qa 3.1h (8%), restore + test <15 min, brief unmeasured in the audit (15-60 min per run from checkpoint timestamps).
- **Retry structure** (22 run clusters): median 2 dev executions per run; heavy tail of 4 runs with >=5 (worst 10-11); marathons: Oct-26 08:19-13:29 (5.2h, ~11 dev turns) and Oct-25/26 overnight (10 consecutive dev->restore cycles, ~4.5h, no test/review between).
- **File overlap is the norm, not the exception**: each of the last 6 construction commits edited shared engine core (isolation.py by 3 of 6, graph.py by 2, stages.py by 3). Even logically independent work (plan 031, done out of order) overlapped files. Static dependency declarations cannot prevent concurrent-run conflicts; agents choose their file sets dynamically.
- **What parallelism would NOT have fixed**: single-unit retry spirals (the marathons above) are convergence problems on one unit; within-run parallelism cannot help them.

## Relationship to existing units

- **unit-1/2/3** - the foundation (output passing, SDK hardening, kernel isolation); each fan-out unit run keeps all of it.
- **unit-4 (WONTSHIP)** - CC dynamic workflows rejected as the driver form (its no-fs/no-shell sandbox forced subagent mediation at every seam). The surviving insight is the unit-4 doc's noted workflow strength: parallel fan-out over whole runs, which this driver implements deterministically without a workflow. This unit does NOT revive the withdrawn adapter surface.
- **unit-5** - egress isolation unchanged; each concurrent run keeps its own egress policies and container profiles.
- **aidlc unit-of-work-dependency.md** - the dependency matrix is the static prerequisite gate (start-time gate only); insufficient alone (see file-overlap finding).
- **plan 029 (sync-back absorb-and-merge)** - the merge-conflict philosophy, lifted from workspace sync-back to branch landing.
- **unit-6/7** - ordering open; this unit's driver would equally orchestrate cloud or Linux-native runs later.

## Recommended architecture (direction only - Functional Design resolves)

Three components, all in the existing `agentic_dev_loop` package:

1. **`fanout.yaml` manifest** - Pydantic-validated, `extra="forbid"`, same style as StepConfig. Fields: `name`, `base_ref` (optional, default: branch where fanout starts), `concurrency` (simultaneous-run cap), `units` (slug, pipeline path, pipeline_args e.g. aidlc_unit, depends_on, optional conflict_attempts override), `integration: command` (one sandboxed command run after each clean rebase; covers the cross-unit validation gap each run's qa cannot see).
2. **Fanout driver** (`engine/fanout.py` + a `fanout` CLI subcommand) - pure Python, no LLM. Scheduler (start units when their deps land, up to concurrency), landing controller (rebase -> gate -> re-attempt policy), state file (`~/.agentic-dev-loop/fanouts/<id>/state.json`) for `fanout --resume <id>`.
3. **One engine extension** - `agentic-dev-loop <pipeline> --resume <thread-id> --at-step dev`: re-export the workspace from the worktree's current HEAD, re-enter the graph at dev, preserve brief/artifacts/notes, increment the attempt counter. Guarded: agent steps only, otherwise fail fast.

**Branching and landing:** the driver creates one git worktree per unit run off `base_ref`, branches named `loop/fanout/<id>/<slug>`; each run's existing commit action lands on its own branch (verification: confirm the commit action commits in the invocation worktree). Landings are processed serially by the driver: rebase the unit branch onto the current base tip, then run the integration gate; pass -> fast-forward merge (linear history, one clean commit per unit); conflict or gate failure -> recovery. Recovery: reset the unit branch to the current base tip and re-attempt the unit run from its dev step on the new base (the existing resume machinery, re-entering at dev). Budgets: per-unit conflict_attempts (default 2), plus the run's own max_attempts. Dependencies are gates at start time, so a dependent never lands before its dependency; independent units may interleave on the base branch (harmless).

## Considered and rejected (do not re-litigate without new evidence)

- **Engine-native parallel branches in one run**: rejected - shared workspace exports break sync-back, every LoopState merge reducer becomes a concurrency problem, resume determinism (the README headline) gets hard, and the data shows the win is between units, not within one.
- **File-claim registry (agents pre-declare files)**: rejected - agents discover their file sets mid-run; pre-declaration is unreliable (the overlap data proves drift into shared core files).
- **Conservative static scheduling (only overlap declared-disjoint units)**: rejected - declared independence was not file independence in the data; would serialize most plan-construction work.
- **CC dynamic workflow as the driver**: rejected by user directive (determinism); its fan-out-over-runs insight is inherited by the Python driver instead.

## Findings (live-sourced during this session)

- The engine's StepConfig has no dependency/parallel declaration; steps are a linear list compiled into a sequential cyclic LangGraph with merge-reducer state (artifacts, notes, brief_hashes). This is why engine-native branches were rejected.
- The sandbox audit log does not record the brief step; brief durations came from checkpoint timestamps (UUID1 checkpoint ids decode to creation time).
- Concurrent runs would append to shared state files (sandbox-audit.jsonl, container-registry.jsonl); append-only safety under concurrency is unverified (open question).
- The aidlc dependency concept already exists: `aidlc-docs/inception/application-design/unit-of-work-dependency.md` mandates a dependency matrix (Unit | Depends on | Depended on by | Coupling type) plus a mermaid graph; validation criterion: "each unit compiles, tests, and ships independently".
- The engine's resume model already supports re-entering at dev (`on_fail: { goto: dev, retry: true }`); a merge conflict just needs to be injectable as a resume-at-dev signal.

## Open questions (revisit at Functional Design)

1. **Commit-action target**: does the commit action commit in the invocation worktree (driver-owned per-run worktrees work as-is) or does it need a branch parameter?
2. **Engine extension semantics**: exact resume-at-step contract - workspace re-export, attempt accounting, interaction with max_attempts and the brief artifact (reused, not regenerated), guard list (which steps are legal re-entry points).
3. **Integration gate**: one manifest-level command (default: the repo's full non-docker test suite) vs per-unit; which container profile/mounts/egress the gate runs with (test-step-like semantics; reuse the isolation pool).
4. **Recovery git mechanics**: exact reset sequence after a failed rebase (rebase --abort, branch reset to base tip) and its failure modes (dirty worktree).
5. **Concurrent shared-state writes**: verify audit/registry JSONL appends are safe under concurrent runs; serialize if not.
6. **Observability**: units run with --no-viz; status table + state file only? Attach-to-unit-viz path? Aggregate fanout viz is noted future work.
7. **Concurrency default and LLM rate limits**: sensible default cap (2?) and documented operator guidance; not enforced by the driver.
8. **Fanout resume semantics**: re-deriving unit status from the state file (thread ids captured from child output; landed units verified via commit messages on the base branch).
9. **Fail-fast semantics**: budget-exhausted unit -> dependents blocked, independent units continue, non-zero exit, report lists blocked units. Confirm.
10. **aidlc tie-in**: future `fanout generate` from unit-of-work-dependency.md; keep the manifest format compatible; out of scope here.
11. **Determinism definition to document**: driver policy is a fixed function of (manifest, base ref, unit outcomes, merge results); base-branch history order varies with unit completion order (external LLM timing) - accepted, to be stated in the product docs.

## Sources

- Brainstorming session (this session, 2026-10-31)
- Historical run data: `~/.agentic-dev-loop/runs/5367f0c488d8/loop-checkpoints.sqlite`, `~/.agentic-dev-loop/runs/6cb529022644/`, `~/.agentic-dev-loop/state/sandbox-audit.jsonl`, construction commit log Oct 20-31
- planning-docs/029 (sync-back conflict absorb-and-merge)
- `aidlc-docs/inception/application-design/unit-of-work-dependency.md`
- `aidlc-docs/unit-4-intent-claude-code-backend.md` (withdrawn unit; parallel-fan-out insight)
- `src/agentic_dev_loop/engine/graph.py`, `engine/stages.py`, `engine/isolation.py`, `core/config.py`
- `agentic-dev-loop/pipeline.yaml` (dogfood pipeline)
