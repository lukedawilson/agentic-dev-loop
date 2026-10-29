# 039-egress-proxy-runtime-hardening.md

> **Status: PLANNED**

Follow-up to 038 (commit 5ee7936). Findings from the post-implementation
code review of the 038 commit. 038's decisions and payload-level
conformance stand; this plan hardens the proxy container's runtime
posture and closes the gap between argv-level pinning and verified
daemon behavior. It does not reopen any 038 decision.

## Scope

Engine: `isolation.py` (proxy payload, lifecycle, readiness),
`egress_proxy.py` (container-mode entrypoint), tests. One tooling-path
deliverable: a `qa.agent.md` rule change (Change 7), committed
host-side with this plan.

## Context

038 containerized the egress proxy and made sandbox egress structural.
The implementation is payload-correct and well-pinned at the argv level,
but the review found:

- The proxy container runs with Docker default privileges: root,
  default capabilities (including NET_ADMIN), writable rootfs, no
  memory/CPU limits. It is the enforcement point that parses
  attacker-influenced bytes from sandbox containers. If a sandbox
  container finds a bug in the proxy, the proxy becomes a jump host
  with Docker-default caps, which is precisely the property D2 assumes
  away.
- No readiness wait: `docker run -d` returns before python binds 3128,
  and `_proxy_alive` only checks `State.Running`. The first networked
  exec (for example the restore step's pip) can race the bind.
- `dial_rewrites` is hardcoded to `{}` in `_start_proxy_container`.
  The platform override on the EgressProxy class (D8) is unreachable
  through the starter.
- The proxy binds `0.0.0.0:3128` on both attachments, so other bridge
  containers (provision container, unrelated bridge containers) can
  route through it as a policy-checked open proxy.
- One dead branch remains: `if request.network and not prof.started`
  in `_run_profile` is unreachable because `started` is always True
  after `_start_profile`.
- Tests prove argv shape only (FakeRunner seam). No test verifies that
  the daemon binds, enforces a live dial, or emits an enforcement
  event.

## Decisions

| # | Decision | Proposal |
| --- | --- | --- |
| D1 | Harden the proxy container's runtime posture: `--cap-drop ALL`, `--read-only`, non-root `--user`, and memory/CPU limits | AGREED |
| D2 | Add a proxy readiness wait: after container start and network connect, the engine probes the proxy's bound address from inside the proxy container (bounded retry) before starting the first sandbox container | AGREED |
| D3 | Wire the `dial_rewrites` override: `_start_proxy_container` passes the EgressProxy's `dial_rewrites` field through to the container argv instead of hardcoding `{}`. No config key; the override stays a code-level platform hook per D8 | AGREED |
| D4 | The proxy binds its internal-network interface address only, not `0.0.0.0`; the bridge attachment carries no listener | AGREED |
| D5 | Remove the unreachable `if request.network and not prof.started` branch in `_run_profile` | AGREED |
| D6 | Add daemon-behavior tests: bind, a live relayed dial, enforcement-event emission, and the readiness wait | AGREED |

## Changes

### 1. Proxy payload hardening (D1)

`docker_proxy_payload` gains `--cap-drop ALL`, `--read-only`, a
non-root `--user` (the `nobody` UID or a dedicated user added to the
isolation image), and memory/CPU limits. Notes:

- The events file stays writable under `--read-only`: it is a
  bind-mounted file, which overrides the read-only rootfs for that
  path. The engine creates the file and sets permissions so the proxy
  user can append.
- Python skips `.pyc` caching when the source directory is not
  writable (the `.py` mounts are `:ro`), so nothing needs a writable
  location.

### 2. Readiness wait (D2)

After `docker run -d` and the internal-network connect, before the
first sandbox container starts, the engine probes the proxy's bound
address with a bounded retry loop executed inside the proxy container
(`docker exec` TCP connect, short timeout, small retry budget). The
host cannot reach the internal network directly, so the probe must run
inside the proxy container. On timeout the run fails through the
existing internal proxy-failure error (AE-SBX-021); the error-guide
row is updated if its wording changes. `_proxy_alive` stays as the
per-exec liveness check.

### 3. dial_rewrites wiring (D3)

`_start_proxy_container` accepts the run's `dial_rewrites` (from the
EgressProxy field, default `{}`) and passes it as the `--dial-rewrites`
JSON argument. The default topology keeps an empty map; the D8
platform override becomes reachable by code, not by pipeline config.

### 4. Interface binding (D4)

The engine pins the proxy's internal-network IP at connect time
(`--ip` on `docker network connect`) and passes that address to the
entrypoint; the proxy binds it instead of `0.0.0.0`. The bridge
attachment remains for host access only and carries no listener.
Ready-to-write alternative if IP pinning is impractical: resolve the
address after connect and pass it the same way, but pinning keeps the
engine deterministic.

### 5. Dead branch removal (D5)

Delete the unreachable branch in `_run_profile`; keep the
`prof.proxy_name is None` guard (AE-SBX-018), which is the reachable
failure path.

### 6. Daemon-behavior tests (D6)

- Update payload tests for the new flags (`--cap-drop`, `--read-only`,
  `--user`, limits), the bind address argument, and the non-empty
  `dial_rewrites` path (the existing D8 override case).
- Subprocess-run the container-mode entrypoint against a real local
  upstream: assert the bind, relay a proxied dial end-to-end, and
  assert an enforcement event line appears in the events file.
- Engine-side readiness tests with a fake `docker exec` seam: the probe
  runs between container start and sandbox payload creation; probe
  success proceeds; probe exhaustion raises the proxy error.

### 7. qa.agent.md carve-out (tooling path, committed host-side)

`agentic-dev-loop/sdk_agents/qa.agent.md` gains a plan-scoped
test-execution carve-out, committed host-side with this plan (the
loop's commit action excludes the file by design, plan 020). The qa
stage loads the file from the workspace at stage start, so the
committed prompt governs this run's QA; no in-run dev edit is needed.
Exact rules added:

- Replace the absolute pytest ban with: never run pytest or any test
  command EXCEPT commands the plan's QA section explicitly lists under
  "QA executes first-hand". Run exactly those, once, and report the
  observed output, labelled "plan-mandated execution". Every other
  criterion stays owned by the test gate.
- A criterion the plan marks "QA executes first-hand" may NOT be
  satisfied by a gate-record or dev-summary citation; it passes only
  on QA's own executed output or a first-hand file:line read.
- No live-run or audit-log inspection is evidence: the run's own
  orchestration executes the previous engine version, so run artifacts
  prove nothing about this change. All evidence is generated in the QA
  sandbox.

## QA stage contract

This section reaches the QA agent verbatim through the loader channel.
It is the acceptance surface for this plan; the checks below are the
only ones that matter for the verdict, and every row must carry
first-hand evidence, not a citation of the dev summary or test gate.

- **QA executes first-hand** (plan-mandated, overrides the no-pytest
  rule): `.venv-sandbox/bin/python -m pytest
  tests/test_egress_proxy.py tests/test_isolation.py
  tests/test_isolation_pbt.py tests/test_isolation_integration.py -m
  "not docker" -q -p no:cacheprovider`. Report the observed pass/skip
  counts and any failure text. These tests are docker-free and
  loopback-only, so the sandbox can run them.
- **Static evidence per decision**, each row a first-hand file:line
  read from the workspace (mount is ro, greps allowed):
  - D1: `docker_proxy_payload` carries `--cap-drop ALL`, `--read-only`,
    `--user`, and memory/CPU limits.
  - D2: the readiness probe runs between container start and sandbox
    payload creation, with a bounded retry and the AE-SBX-021 timeout.
  - D3: `_start_proxy_container` passes the EgressProxy `dial_rewrites`
    field, not a hardcoded `{}`.
  - D4: the entrypoint binds the internal-net address, not `0.0.0.0`.
  - D5: the dead `if request.network and not prof.started` branch is
    gone.
  - D6: the new daemon-behavior and readiness tests exist and were in
    the executed set above.
- **Change 7 self-check**: the qa.agent.md carve-out text is present in
  the workspace file (grep it). QA is reviewing the prompt it runs
  under, so it must confirm the edit landed.
- **The only legitimate UNVERIFIABLE**: the manual docker probe matrix
  from 038's Verification (docker is forbidden in this sandbox; it is a
  human runbook item). No other check may be reported UNVERIFIABLE, and
  no unrelated generic probe may substitute for a listed check.
- The verdict is PASS only when every row above carries QA's own
  evidence. A report whose rows are gate-record citations is a FAIL of
  this contract, whatever the verdict line says.

## Tooling-path deliverables

`agentic-dev-loop/sdk_agents/qa.agent.md` (Change 7 plus 038 Change
4's persona-scope section, same file), committed host-side with this
plan. The consumer repo copy of the qa prompt (038 Change 4, both
repos) remains a human commit in that repo.

## Verification

- TDD: the daemon-behavior test red first, then the readiness tests,
  then payload assertions.
- Full pytest suite green.
- ASCII only, no em dash, no consumer name (repo rules).

## Non-goals

- Policy logic, topology, or NO_PROXY changes (038 decisions stand).
- Host-side filtering, seccomp, or iptables.
- 038's Change 6 (README paragraph) remains a human commit, untouched
  by this plan.
- Real docker behavior verification: unchanged, lives in the manual
  probe matrix of 038's Verification (runbook).

## Open risks

- Binding a pinned IP couples the engine to the internal subnet's
  allocation; `--ip` makes it deterministic, and the entrypoint takes
  the address as an argument so the engine stays the single source.
- The daemon-behavior test exercises the entrypoint over loopback
  only, not docker networking; accepted, matching the suite's existing
  no-docker constraint.
- The consumer repo copy of the qa prompt is outside this repo's
  reach; landing it there is a human action (038 Change 4), and until
  it lands the consumer runs keep the old prompt.
- The QA contract hard-requires first-hand execution, which costs QA
  turn budget; the scoped four-file pytest run plus greps is bounded,
  and the contract's stop rule keeps it from sprawling.
