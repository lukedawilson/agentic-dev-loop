# 030: Infra-failure classification and orphaned-container reaper

> **Status: PROPOSED (2026-10-27)**

## Context

The the consumer repo run 5367f0c488d8 (pipeline `sitebuilder-construction`) flapped for
17+ minutes: every terminal tool call failed with exit code 126 and stderr
`openat etc/group: input/output error`, rendered to the agent as
`[An error occurred during execution.]` plus the stderr text. The cause was
environmental, not the engine: the Docker Desktop VM disk (Docker.raw) is in a
broken ext4 error state that returns EIO on reads and writes inside the VM,
almost certainly after host free space hit zero and sparse-file growth failed.
The engine behaved as designed throughout, and the pipeline config is
orthogonal. Remediation (free disk, restart Docker Desktop, re-run) is
separate from this plan.

Two engine gaps surfaced while diagnosing, both real and both in scope here:

1. **No infra-failure classifier.** The engine raises AE-SBX-012 only when the
   docker CLI itself dies mid-run (`exit_code is None`). A daemon-reported I/O
   failure surfaces as a routine `SandboxExecutionResult` with exit 126, which
   becomes an `is_error` observation. The agent then treats a broken sandbox
   as a failed command and retries, burning tokens at roughly 2 minutes per
   retry (10 exit-126 execs in the audit, 11:10:10Z to 11:26:49Z on
   2026-10-27, plus one earlier episode on 2026-10-21).
2. **Orphaned sandbox containers accumulate.** Exec containers run
   `docker run -d --rm ... sleep infinity`; `--rm` fires only when the
   container exits. When the CLI is SIGKILLed, `close()` never runs and the
   container keeps sleeping forever. Six leaked `*-exec` containers from
   earlier runs were observed still Up (oldest 2 days), and their json.log
   writes were part of the VM error spew. Plan 015 D3 deferred a startup
   sweep pending evidence; the evidence has arrived.

## Decisions

| # | Decision | Proposal | Answer (2026-10-27) |
|---|---|---|---|
| D1 | Classify daemon-side exec failures and raise a new `SandboxError` code AE-SBX-019, sticky for the rest of the run | Classified: exit 126 + EIO text; docker CLI exit 125; `Error response from daemon:` with exit 1/125/126. Rejected: exit 127, exit 1 + EIO text, exit 126 alone, read-only fs, permission denied, 137, 124 (reasons in Design) | APPROVED as proposed |
| D2 | Make AE-SBX-012 (daemon lost mid-run) sticky too | Same failure mode as 019: one daemon loss proves the exec path is dead; every subsequent exec should fail instantly, not after another 30 s runner timeout | APPROVED as proposed |
| D3 | Fast-stop inside the agent step: the terminal executor writes `ConversationExecutionStatus.STUCK` on the conversation so the SDK run loop breaks within one iteration | Without it the step still ends correctly (sticky raise + post-step check), but the agent can keep burning iterations up to `max_iterations` within the current step | APPROVED, gated on a COMPAT-SHIMS canary test proving the SDK loop-break behaviour (see Tests) |
| D4 | Orphan reaper mechanism | A state-dir registry of created container names + host pid liveness check at startup (exact, no heuristics), plus `--label` on create for human cleanup. Name/image heuristics rejected as the primary (previous runs' run_ids are unknown after a crash) | APPROVED as proposed |
| D5 | No new config surface | Classifier and reaper are always on; no `pipeline.yaml` keys. Add an opt-out only if a false positive is ever observed | APPROVED as proposed |

## Current state (evidence)

Engine (this repo, `/Users/luke/dev/work/agent-engine`):

- `isolation.py:1023` `DockerBackend._exec`: builds `docker exec` argv, runs it
  via `SubprocessRunner`, audits, raises AE-SBX-012 only when
  `res.exit_code is None` (line 1042). Any docker CLI nonzero exit is returned
  as a normal result, no classification.
- `isolation.py:596` `docker_create_payload` builds the
  `docker run -d --rm --name {run_id}-exec[-...] --network ... --read-only
  --cap-drop ALL ... sleep infinity` payload. Container names:
  `{run_id}-exec`, pool profiles `{run_id}-exec-{key[:16]}`, network suffix
  `-net`.
- `isolation.py:93` `SandboxError(RuntimeError)`; `isolation.py:758`
  `class Sandbox(ABC)` with `run(request) -> SandboxExecutionResult`.
  `isolation.py:135` `SandboxExecutionResult` (exit_code, stdout, stderr,
  duration_seconds, backend, timed_out).
- `isolation.py:1381` `startup_sandbox`, called from `graph.py:153` before the
  pipeline graph builds.
- `agents.py:78` `SandboxTerminalExecutor.__call__` receives the SDK
  `conversation`; it calls `sandbox.run(request)` and renders a
  `TerminalObservation` with `is_error=not succeeded and not timed_out`.
- `stages.py:73` `Runtime.run_agent`: `conv.run()` inside a try that catches
  all exceptions (line 127) and returns `AgentResult(ok=False)`;
  `make_agent_node` (line 356) maps `ok=False` to a run failure with no
  retry_target. `stages.py:454` command steps call `_run_command` inside a try
  (line 459) whose except branch also fails the run without retry.

SDK behaviour (openhands-sdk 1.17.0, verified in the pipx venv):

- Tool-executor exceptions are caught and converted to `AgentErrorEvent`:
  `agent.py:953` catches ValueError only, but
  `parallel_executor.py:_run_action_with_locks` catches all Exceptions and
  returns an `AgentErrorEvent` ("Error executing tool 'terminal': ..."). A
  raise inside `_exec` therefore does NOT crash the conversation; the agent
  sees it as a retryable tool error. This is why plan 029's AE-SBX-016 raise
  "bricked even echo hello" while the agent kept trying.
- The conversation run loop (`local_conversation.py:run`) breaks only on
  PAUSED, STUCK, FINISHED, or WAITING_FOR_CONFIRMATION status. ERROR does not
  break the loop. STUCK is the only loop-breaking status the engine can set
  from a tool without user interaction. This is the D3 mechanism.
- The SDK docstring warns tools must not mutate shared conversation state
  (execution_status transitions are caller-managed); D3 therefore ships as a
  pinned compatibility reliance, see Tests.

Evidence from the incident (for test fixtures, built from real dumps):

- Audit JSONL `~/.agentic-dev-loop/state/sandbox-audit.jsonl`, pipeline
  `sitebuilder-construction`: 5,751 exit-0 execs; 11 exit-126 execs, each
  followed by a successful host-side sync_back (host I/O healthy, VM disk
  broken). Exact observed stderr for the classifier fixture:
  `openat etc/group: input/output error`.
- Docker CLI exit-code contract: 125 = docker itself failed (daemon-side),
  126 = contained command cannot be invoked, 127 = contained command not
  found. The engine's `_run_subprocess` also maps host FileNotFoundError to
  127, so 127 is ambiguous and must not classify.
- Leaked containers observed Up: `0f1ebcaf...-exec-7b5f04c9a8ecc3f1-net`
  (24 h), `c2931802e755` (25 h), `dad388a6d59d` (27 h), `4d4bae143053`
  (27 h), `f9500eefa69d` (2 days), `ead084628cb8` (2 days), all
  `sleep infinity` on the engine's pinned image.

## Design A: infra-failure classifier

### Classification rules (pure function, no I/O)

New pure function in `engine/isolation.py`:

```python
def classify_infra_failure(exit_code: int | None, stderr: str, stdout: str) -> str | None:
```

Returns a short reason string when the failure is daemon/infra-side, None when
it is a routine command failure. Rules:

| Condition | Verdict | Reason |
|---|---|---|
| exit_code == 125 | infra | docker CLI contract: daemon-side error |
| exit_code == 126 and EIO pattern in stderr+stdout | infra | the guest could not even load the command (observed: `openat etc/group: input/output error`) |
| `Error response from daemon:` in stderr and exit_code in (1, 125, 126) | infra | the host docker CLI printed a daemon error (prefix cannot come from the container's command) |

EIO pattern: `input/output error` or `I/O error`, case-insensitive.

Explicitly NOT classified (with reasons, so future edits do not re-litigate):

- exit 127: ambiguous. Missing command inside the image is a genuine pipeline
  error, and the engine maps host FileNotFoundError to 127 as well.
- exit 1 + EIO text: a test suite that prints `input/output error` and exits 1
  is a genuine failure. The exit-126 requirement filters it: exit 126 means
  the command never started.
- exit 126 alone (no EIO): can be a genuine cannot-invoke (bad shebang,
  missing shared library).
- `read-only file system`: genuine under `--read-only` rootfs (writes outside
  mounts).
- `permission denied`: genuine under `--cap-drop ALL`.
- exit 137 (SIGKILL, e.g. OOM) and exit 124 (timeout): routine, already
  surfaced as timed_out for 124.

### Where it hooks in

`DockerBackend` gains `self._infra_error: SandboxError | None = None`.

`_exec` (isolation.py:1023), after `self._audit(...)`:

1. `res.exit_code is None` branch: build the AE-SBX-012 error, store it in
   `self._infra_error`, raise it (D2: unchanged message, new stickiness).
2. `reason = classify_infra_failure(res.exit_code, res.stderr, res.stdout)`;
   if not None: build
   `AE-SBX-019: sandbox infrastructure failure: <reason>. The exec container
   cannot run commands; this is a Docker engine/VM problem, not a command
   failure. Check the engine health (docker info, docker system df) and
   restart Docker Desktop if the VM reports I/O errors (docs/setup.md).`
   with the stderr tail, store it in `self._infra_error`, raise it.
3. Otherwise return the result as today (behaviour preserved for all routine
   failures).

Sticky fast path: `run()` raises `self._infra_error` immediately when set, and
`_exec` guards the same way, so no docker process is spawned after the first
classification. The classified exec is audited before the raise (the failure
stays visible in the audit trail); the fast path does not audit (no exec
happened). The classified audit record gains an `infra_reason` field.

Protocol: `Sandbox` (ABC) gains
`@property def infra_error(self) -> SandboxError | None: return None`;
`DockerBackend` overrides it to return the stored error. stages.py can query
the protocol without importing DockerBackend.

### Run-level failure (no retry)

- `stages.py` `Runtime.run_agent`: immediately after `conv.run()` returns
  (before any capture/verdict logic, line 108), if
  `self.sandbox is not None and self.sandbox.infra_error is not None`, raise
  the stored error. The existing except (line 127) turns it into
  `AgentResult(ok=False)`, and `make_agent_node` fails the run with no
  retry_target. Ordering matters: the check must precede the truncated/capture
  paths, which would otherwise schedule an unclear-retry.
- Command steps need no change: the raise from `sandbox.run` inside
  `_run_command` propagates to `make_command_node`'s except branch (line
  459), which fails the run without retry.

### D3 fast-stop inside the step (approved, gated on canary)

In `SandboxTerminalExecutor.__call__`, wrap `self._sandbox.run(request)`:

- catch `SandboxError as exc`; if `exc is self._sandbox.infra_error` and
  `conversation is not None`: set
  `conversation.state.execution_status = ConversationExecutionStatus.STUCK`;
  re-raise. The SDK run loop observes STUCK at the next iteration and breaks,
  so the current step stops after one more LLM round trip instead of flapping
  to `max_iterations`. Correctness does not depend on D3: the sticky raise and
  the post-step check hold regardless; D3 only cuts token burn.
- This is a reliance on undocumented SDK loop behaviour, so it ships with a
  COMPAT-SHIMS.md entry and a canary test in `tests/test_sdk_compat.py`
  (same pattern as the existing SDK shims), stating the removal criterion
  (SDK grows a supported stop mechanism, or the run loop stops checking
  STUCK).

## Design B: orphaned-container reaper

### Registry

New host-side file `~/.agentic-dev-loop/state/container-registry.jsonl`, one
record per successfully created exec container:

```json
{"run_id": "...", "name": "...-exec...", "pid": 1234,
 "process_start": "Mon Oct 26 12:00:00 2026", "created_at": "..."}
```

- Written after `docker run -d --rm` returns successfully (both the legacy
  create path at isolation.py:884/897 and the profile pool at 1061 funnel
  through the same helper).
- `process_start` is `ps -o lstart= -p <pid>` of the CLI process at create
  time, to defeat pid recycling at reap time.
- `close()` filters out the current run's entries after its `docker rm -f`
  (tolerating a missing file and a missing container, per 015 D2).
- Registry write/read is best-effort and must never fail a run (a broken
  registry only disables reaping, it cannot break execution).

### Reaper (startup)

New function `reap_orphaned_containers(state_dir, runner)`, called at the top
of `startup_sandbox` (isolation.py:1381) after the docker health checks
(001-004) and before any container creation:

- Read the registry. For each entry:
  - Name must match `^[0-9a-f]{32}-exec(-[0-9a-f]{16})?(-net)?$` (defense in
    depth against a corrupted registry; engine names only).
  - pid alive (`os.kill(pid, 0)`) AND `ps -o lstart= -p <pid>` matches the
    recorded start: a live CLI owns the container, leave it (covers
    concurrent loops).
  - pid dead, recycled, or name mismatch: `docker rm -f <name>`, tolerating
    "no such container" (a Docker Desktop restart already removed VM-side
    containers), remove the registry entry, announce
    `[isolation] reaped orphaned sandbox container <name>`.
- Never touches containers outside the registry, so user stacks (outline,
  docmost, postgres, minio) are untouched by construction.

### Labels (identification aid, not the reaper)

`docker_create_payload` gains
`--label agentic-dev-loop=true --label agentic-dev-loop.run={run_id}` so any
container created by the engine is identifiable regardless of the registry
(e.g. `docker ps --filter label=agentic-dev-loop` for humans, and a future
heuristic reaper if the registry ever proves insufficient).

## Engine changes (file by file)

### `src/agentic_dev_loop/engine/isolation.py`

- `classify_infra_failure(...)` pure function + `_EIO_PATTERN`.
- `DockerBackend.__init__`: `self._infra_error = None`; expose `infra_error`
  property.
- `_exec`: sticky fast path at the top; AE-SBX-012 store-then-raise (D2);
  classify, store, raise AE-SBX-019 after audit (D1); audit dict gains
  `infra_reason` on the classified record.
- `run`: fast path raise when `_infra_error` is set.
- `docker_create_payload`: add the two labels (D4).
- Container-create helper (legacy + profile): append registry records (D4).
- `close()`: filter registry entries for this run_id after rm (D4).
- `reap_orphaned_containers(state_dir, runner)` (D4).
- `startup_sandbox`: call the reaper before backend creation (D4).
- `Sandbox` ABC: `infra_error` property default None.

### `src/agentic_dev_loop/engine/agents.py`

- `SandboxTerminalExecutor.__call__`: catch infra SandboxError, set STUCK on
  the conversation, re-raise (D3, approved).

### `src/agentic_dev_loop/engine/stages.py`

- `Runtime.run_agent`: post-`conv.run()` infra_error check that raises into
  the existing except path (D1/D2 correctness layer).

### Rules amended

- `aidlc-docs/construction/unit-5/functional-design/business-rules.md`:
  BR-11 gains BR-11.4 (daemon-side exec failures are classified, raise
  AE-SBX-019, are sticky for the run, and fail the run without retry); the
  BR-11.3 cleanup row gains a note that the startup reaper extends cleanup to
  crash paths.

### Docs

- `docs/setup.md`: AE-SBX-019 row in the fail-fast table (line 253 area); a
  short paragraph on the registry + reaper under the audit/state note (line
  270 area).
- `README.md`: mention the classifier (fail fast on broken sandboxes, no
  agent retry loop) and the startup reaper.
- `CHANGELOG.md`: Added (infra-failure classification AE-SBX-019, orphaned
  container reaper), Changed (AE-SBX-012 now sticky).
- `COMPAT-SHIMS.md`: entry for the D3 STUCK reliance, with
  removal criterion and canary pointer.

## Tests (TDD red/green/refactor)

All RED first; fixtures use the recorded incident stderr/exit code (real
dump, not invented).

- **RED (unit, `tests/test_isolation.py` TestClassifier):** table-driven
  `classify_infra_failure`: 126+`openat etc/group: input/output error` ->
  reason; 125 -> reason; `Error response from daemon:` + exit 1 -> reason;
  127, 1+EIO text, 126 alone, read-only fs, permission denied, 137, 124,
  0+EIO text -> None.
- **RED (unit, `tests/test_isolation.py` TestExec):** fake runner returns
  (126, "", EIO stderr): AE-SBX-019 raises, audit record written with
  `infra_reason`, runner invoked exactly once; the next `run()` raises
  without invoking the runner (stickiness). Fake runner exit 1: result
  returned, no raise, no stickiness (behaviour preserved). Fake runner exit
  None: AE-SBX-012 raises and is sticky (D2).
- **RED (unit, `tests/test_stages.py`):** fake sandbox with `infra_error`
  set: `run_agent` returns `AgentResult(ok=False)` (Conversation mocked at
  the SDK boundary, allowed by the mock policy); command node with a raising
  sandbox returns failed with no retry_target.
- **RED (unit, `tests/test_isolation.py` TestReaper):** tmp state dir
  registry: dead pid (a subprocess that just exited) -> fake runner receives
  `docker rm -f <name>`, entry removed; live pid (os.getpid()) -> untouched;
  malformed name -> skipped; "no such container" tolerated; concurrent-run
  entry untouched.
- **RED (unit, TestPayloads):** create payload now includes the two labels
  (invariants update).
- **RED (canary, `tests/test_sdk_compat.py`):** D3 only: assert the installed
  SDK run loop still breaks on STUCK (source-level check, same pattern as the
  existing shim canaries).
- **GREEN:** implementation, then refactor.
- **Real-daemon proof:** docker-marked integration test in
  `tests/test_isolation_integration.py`: create a real exec container via the
  backend, append a registry entry with a dead pid, run the reaper, assert
  `docker ps -a` no longer lists the name. Existing docker-marked lifecycle
  tests remain green (labels are additive).

## Acceptance criteria

1. Classifier: every table row above passes; routine failures (exit 1/2/127)
   still return results and never raise (no behaviour change for healthy
   sandboxes).
2. A classified exec raises AE-SBX-019 once, is audited with `infra_reason`,
   and every subsequent run() raises instantly without spawning docker.
3. AE-SBX-012 message unchanged; it also becomes sticky.
4. Agent step: after an infra failure the run fails with no retry
   (retry_target unset), last-error.md written, and (D3) the step ends within
   one extra iteration.
5. Command step: infra failure fails the run without retry.
6. Reaper: startup reaps dead-pid entries (missing containers tolerated),
   leaves live-pid entries, prunes the registry, and never touches
   non-registry containers; create payloads carry the labels; close() prunes
   its own entries.
7. ASCII/no-em-dash hook passes; full suites green (non-docker and
   docker-marked); COMPAT-SHIMS + canary present (D3 approved).
8. README.md, docs/setup.md, CHANGELOG.md, and the aidlc BR-11 amendment
   document the classifier and reaper.

## Dogfood

Executed by the loop itself from the published wheel (same shape as 015/029):

1. Launch: `./trigger-agentic-dev-loop.sh --plan planning-docs/030-...md`.
2. The dev agent implements D1, D2, D3, D4 TDD; test step
   passes inside the sandbox; review -> qa -> commit.
3. After the loop: manual one-off remediation of the six already-leaked
   containers (`docker rm -f 0f1ebcaf... dad388a6d59d ...`), review the diff,
   push, publish the next prerelease.
4. Honest coverage note: the live EIO trigger cannot be reproduced without
   breaking the Docker VM again; the classification logic rides on unit tests
   replaying the recorded incident stderr, and the reaper rides on the
   docker-marked integration test. The next real infra failure is the live
   proof, and it should now fail the run in under a minute instead of
   17+ minutes.

## Out of scope

- Healing or detecting the broken VM disk itself (docker engine health
  probing, `docker system df` checks at startup): raised as a possible
  follow-up, not here.
- A heuristic name/image reaper (registry + pid liveness is exact; revisit
  only if the registry proves lossy).
- Opt-out config surface for the classifier (D5: none until a false positive
  is observed).
- Reaping containers leaked before this plan ships (manual one-off only,
  listed under Dogfood).
