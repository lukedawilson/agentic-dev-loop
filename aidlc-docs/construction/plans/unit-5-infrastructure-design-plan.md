# Infrastructure Design Plan - unit-5 (workspace egress isolation)

> **Status: in progress.** Next stage after Infrastructure Design = Code Generation.

## Stage Context

Design inputs: the unit-5 Functional Design (plans/unit-5-functional-design-plan.md; artifacts at aidlc-docs/construction/unit-5/functional-design/: business-logic-model.md, business-rules.md BR-1..12, domain-entities.md) and NFR Requirements (plans/unit-5-nfr-requirements-plan.md; artifacts at aidlc-docs/construction/unit-5/nfr-requirements/: nfr-requirements.md NFR-5.1..5.8, tech-stack-decisions.md TS-1..8).

Infrastructure surface after the 2026-10-25 enforcement-point amendment (engine-managed per-policy egress proxy on ALL platforms; no nftables, no NET_ADMIN, no privileged CI): the proxy itself (implementation, container reachability, lifecycle), step-workspace materialisation, sync-back mechanics, and CI wiring for portable enforcement tests. Kernel-level egress and the native Linux backend are deferred to unit-7 (intent recorded 2026-10-25); the cloud box that will host them is unit-6.

## Steps

- [x] Analyze unit-5 context (FD + NFR artifacts, enforcement-point amendment)
- [x] Resolve ID open questions (ID-1..ID-6, all A - recommended options selected)
- [x] Generate `aidlc-docs/construction/unit-5/infrastructure-design/infrastructure-design.md`
- [x] Generate `aidlc-docs/construction/unit-5/infrastructure-design/deployment-architecture.md` (shared-infrastructure.md: N/A - justified in the category-applicability table)
- [x] Completion message + approval (approved 2026-10-25, user: "continue")

## Open Questions

### Question ID-1 (proxy implementation): hand-rolled vs library

The per-policy egress proxy needs: HTTP(S) forward proxying with CONNECT tunneling, hostname allowlist matching (BR-6.1), deny + logging (BR-6.2), and it must be engine-managed on all three OSs without extra daemons. TS-1 says "engine-managed"; it does not yet say how it is built.

A) **Hand-rolled minimal asyncio forward proxy in the engine** - Python stdlib only (asyncio, sockets); CONNECT + GET/absolute-URI handling + hostname allowlist + connection logging; small surface, unit-testable without Docker, no new dependencies (plan 021's dependency policy stays untouched) (recommended)

B) **Third-party proxy library dependency** - embed a battle-tested forward proxy (e.g. a Python proxy library) for protocol edge cases (CONNECT variants, HTTP/1.0 clients, chunking); costs a new dependency against plan 021's locked deps_policy and a larger audit surface

X) Other

[Answer]: A - hand-rolled minimal asyncio forward proxy in the engine (user selected the recommended option)

### Question ID-2 (container-to-proxy reachability): host gateway

Containers must reach the engine-run proxy on the host. Docker 20.10+ supports `host.docker.internal` via `--add-host=host.docker.internal:host-gateway` on all platforms (incl. Linux); the proxy env vars (HTTP_PROXY/HTTPS_PROXY/NO_PROXY) then point at it.

A) **`--add-host=host.docker.internal:host-gateway` + proxy env vars** - works identically on Docker Desktop (macOS/Windows) and Linux Docker hosts; no IP discovery, no per-platform branching; proxy bound to the host loopback with the gateway mapping doing the reach-in (recommended)

B) **Bridge gateway IP discovery** - inspect the container's bridge gateway IP per run and inject it into env; works only on Linux bridge networks, brittle on Docker Desktop VM networking

X) Other

[Answer]: A - --add-host=host.docker.internal:host-gateway + proxy env vars (user selected the recommended option)

### Question ID-3 (proxy lifecycle and ports): ephemeral per policy

Each distinct egress policy needs a running proxy for the duration of the run.

A) **Ephemeral, loopback-bound port per policy per run** - engine binds 127.0.0.1:0 (OS-assigned port) per policy, spawns and owns the proxy process, kills it at pipeline end and on error paths; idempotent startup with bounded retry (NFR-5.4); no port collisions, no long-lived daemons, no cleanup debt (recommended)

B) **Fixed well-known port per policy, long-lived daemon** - one proxy process persists across runs; faster startup but port conflicts, stale-process management, and host-level state outside state_dir

X) Other

[Answer]: A - ephemeral, loopback-bound OS-assigned port per policy per run, engine-spawned and engine-killed (user selected the recommended option)

### Question ID-4 (step-workspace materialisation): copy vs git worktree vs archive

The export must preserve the working tree as-is (including uncommitted changes - the dev step works on dirty state), exclude `.git` by construction (BR-2), and exclude host-only dirs.

A) **Copy-based export** - copytree/rsync-style copy of the workspace into `state_dir/exports/<run-id>/` excluding `.git` + host-only dirs; preserves dirty state exactly; `.git` absent by construction; cost = copy time, bounded by NFR-5.1 (seconds for typical repos) (recommended)

B) **`git worktree add`** - fast (object-store sharing) but leaves a `.git` FILE in the export (worktree metadata pointing at the real repo) and starts from HEAD, losing dirty state

C) **`git archive`** - cleanest (no `.git` at all) but only committed content; the dev step's uncommitted work would vanish from the sandbox

X) Other

[Answer]: A - copy-based export (user selected the recommended option)

### Question ID-5 (sync-back mechanics): in-process vs rsync binary

Sync-back copies declared-rw changes from the export back into the real workspace (BR-4), git-aware, never touching `.git`.

A) **In-process Python diff-copy (shutil-based)** - walk the export, copy changed files back, delete files removed by the step where declared-rw; portable, no rsync binary dependency (Windows hosts have no rsync by default), unit-testable; cost = slower on huge trees, acceptable per NFR-5.1 (recommended)

B) **Shell out to `rsync` with a Python fallback** - fastest when rsync exists (macOS/Linux dev boxes), but a second code path and a host-tool dependency on Windows

X) Other

[Answer]: A - in-process Python shutil-based diff-copy (user selected the recommended option)

### Question ID-6 (CI wiring for proxy enforcement tests): portable, no privileged job

NFR-5.7/TS-8: proxy deny/allow behaviour must be proven in normal CI without root, never silently skipped.

A) **Loopback test servers in normal CI** - enforcement tests spin up local test HTTP servers (127.0.0.1) and assert the proxy allows listed hostnames and denies + logs unlisted ones; runs in the existing ubuntu+windows matrix jobs; real-Docker tests stay marked/opt-in as today; no privileged job, no new workflow (recommended)

B) **New dedicated workflow for proxy tests** - a separate job with its own trigger; isolation from the existing matrix but more CI surface to maintain

X) Other

[Answer]: A - loopback test servers in normal CI (user selected the recommended option)
