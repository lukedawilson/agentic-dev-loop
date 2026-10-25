# Functional Design Plan - unit-5 (workspace egress isolation)

## Unit Context (from unit-5-intent-workspace-egress-isolation.md)

Unit 5 closes the last structural exfil channel in the containment model. Today every container mounts the entire workspace read-write, and `network: true` means unrestricted egress. Anything executing inside a container can (a) read everything the workspace holds, and (b) send anything it can touch anywhere on the internet. Plans 019 and 020 removed secrets and the host from the target list; this unit removes the two remaining paths: **reading what a step does not need** and **sending to addresses a step does not need**.

Two mechanisms:
1. **Per-step mount granularity** - steps declare their mount scope with rw/ro/absent distinctions. Each step mounts only what its job requires. The `.git` blindness plan 020 deferred lands here.
2. **Egress policy** - `network: true` stops meaning unrestricted. Each network-on step gets an allowlist of targets (domains/IPs/ports): restore -> package registries; qa -> host app + Kinde + R2 + registries; conformance -> npm registry.

Five load-bearing pillars: least-access mounts, egress allowlists, read-only where possible, `.git` blindness, loud failure modes.

Scope: new unit scope defined by the intent doc (no story-map rows exist yet). Relationship to prior work: plan 023 follow-up 5 (read-only workspace) is closed here; the rw workspace mount is the residual write/exfil channel after the browser sandbox and is the direct target of this unit. Extends unit-3's `engine/isolation.py` (mount assembly + container networking).

## FD Steps

- [x] Analyze unit-5 context (intent doc, live-sourced findings, current `isolation.py` mount/network assembly)
- [x] Domain exposition (DOMAIN-01 - six probe dimensions, answered)
- [x] Resolve FD open questions (Q1-Q9 + F1/F2 below - all resolved)
- [x] Generate `aidlc-docs/construction/unit-5/functional-design/business-logic-model.md`
- [x] Generate `aidlc-docs/construction/unit-5/functional-design/business-rules.md`
- [x] Generate `aidlc-docs/construction/unit-5/functional-design/domain-entities.md`
- [x] Validate against DDD rules (descent audit in domain-entities.md; no protocols; no new single-impl interfaces)
- [x] Security-baseline compliance check (least privilege, egress allowlist integrity, audit, loud failures - mapping in business-rules.md)
- [x] Identify PBT properties for the new pure functions (mount-plan serialisation, egress-policy serialisation, profile-key stability, export-path invariants)

## Domain Exposition (DOMAIN-01)

### Dimension 1 - Concepts named

- **Source**: intent "per-step mount scope", "egress allowlists", ".git blindness"; live findings (rw workspace mount = the residual write/exfil channel after the browser sandbox; ro mounts feasible for render/QA steps).
- **Proposed**: the **Mount Set** (what a step can see and how - path x mode), the **Egress Policy** (what a network-on step can reach - an allowlist, or the broad-but-logged browser class), the **Workspace Export** (the .git-free derivative the sandbox mounts), the **Sync-Back** (post-step write-back to the real repo), the **Container Profile** (mount set + egress policy + seccomp/cap policy - the identity that keys a container). Trusted complement: the provision phase, the engine host side (policy assembly, proxy management, sync-back), the commit action.

### Dimension 2 - State ownership

- **Source**: `StepConfig` owns step declarations today (network, env, setup); caches live in the real workspace (plan 020 analysis said STAY, confirmed live).
- **Proposed**: `StepConfig` owns the new declarations (`mounts`, `egress`). The engine owns the resolved policies, the per-policy networks, the export directory, the container pool, and the sync-back. The real workspace is the source of truth; the export is an engine-owned derivative; caches are real-workspace state bind-mounted into the export (F2).

### Dimension 3 - Runtime contexts

- **Proposed**: **load time** - config validation, policy assembly (proxy config generation), export materialisation, pool warm-up; all loud on failure. **run time** - per-step request routed to the profile's container; sync-back after rw-declared steps; cleanup at pipeline end + error paths.

### Dimension 4 - Change-togetherness

- **Proposed**: mount-set resolution, egress assembly, export planning, sync-back planning, and profile keying are pure functions over config + platform facts - they change together with config semantics. Proxy management and container management are the Docker backend's transport concerns. Presets and per-toolchain cache declarations are data: supporting pypi/npm/NuGet/crates is preset data, not mechanism.

### Dimension 5 - Invariants & failure modes

- **Proposed** invariants: the sandbox never sees `.git`; a step reads only what its mount set names; a network-on step reaches only its allowlist (except the declared broad-but-logged class); rw exists only on declared paths; the export is a derivative and sync-back is its only path into the repo; the pool and networks are always destroyed. Failure modes: unresolvable domain, allowlist miss, missing cache path at export, sync-back conflict, network/rule application failure - all loud, none silent.

### Dimension 6 - Actors

- **Proposed**: the **pipeline author** (trusted - declares mounts, egress, presets), the **agent** (untrusted - runs inside), the **engine** (trusted mediator - assembles policies, runs the proxy, syncs back), the **Docker daemon** (executes), the **registries/CDNs/app endpoints** (external, the allowlist targets).

## Open Functional Questions

### Question Q1: egress enforcement mechanism under `--cap-drop ALL`

`--cap-drop ALL` means the engine cannot hand `NET_ADMIN` to a container for in-container iptables. Docker has no native per-container egress firewall for bridge networks. Enforcement must live outside the container. Live fact: `DockerBackend` already shapes per-container syscall/cap policy, so the egress knob is a neighbour, not a new mechanism.

A) **Engine-created per-policy network + host-applied nftables rules before container start** - the engine creates a named network per egress policy (or per run), applies nftables rules to that network's namespace from the host, then starts the container inside it. No NET_ADMIN needed in-container; true network-level allowlisting; needs root (or equivalent) on the host side and Linux netfilter. Recommended, with the proxy (B) as the portable fallback where nftables is unavailable.

B) **Egress proxy the container must use** - engine runs a local proxy per policy; container HTTP(S)_PROXY points at it; proxy enforces the allowlist in userspace. Portable, no root; only covers proxy-respecting HTTP(S) traffic; needs a kernel-level backstop for non-HTTP traffic.

C) **Docker network-policy features** - native per-container egress policy if the installed Docker version supports it. Version support TBD, not portable across engines.

X) Other

~~[Answer]: A - engine-created per-policy network + host-applied nftables rules before container start, with the egress proxy (B) as the portable fallback where nftables is unavailable.~~

[Answer]: SUPERSEDED (2026-10-25, user directive): "Drop the separate linux enforcement altogether for now. We will eventually build a native linux path (without docker), so that hardening will live there. For now, all three OSs get the same treatment". B becomes the only mechanism: the engine-managed per-policy egress proxy is THE enforcement point on all three OSs. Kernel-level enforcement is deferred to the future native Linux backend (unit-3 FD-2 scope).

### Question Q2: domain allowlists and DNS control

Domain-based allowlists need DNS control (resolve-and-allow, or DNS interception). Is IP-based acceptable where hosts are stable (`host.docker.internal`, registry IPs)?

A) **Domain entries resolved to IP sets at policy-assembly time** - config carries domains; the engine resolves them (with re-resolution on network/container rebuild) into nftables IP/CIDR sets. Handles registry/CDN drift; resolution and TTL semantics add complexity.

B) **IP/CIDR entries only, accepted where hosts are stable** - simplest, kernel-friendly; brittle when CDNs rotate or registries move.

X) Other

~~[Answer]: A - domain entries resolved to IP sets at policy-assembly time, with re-resolution on network/container rebuild.~~

[Answer]: SUPERSEDED (2026-10-25, follows Q1): the proxy matches the request hostname (CONNECT target) per connection - no IP resolution at assembly, no DNS interception. The DNS-control problem dissolves: the proxy never sees IPs as policy inputs.

### Question Q3: which paths stay rw per step

`ro` mounts vs copy-on-write: build outputs and caches need rw. Live fact: the browser probe writes results to `/tmp` tmpfs, never the workspace, so render/QA steps work with a `ro` workspace mount.

A) **Declared-writes model** - every mount entry carries a mode; source/docs default `ro`; only declared outputs and caches are `rw` per step. Least privilege, matches the unit intent; requires precise per-step declarations.

B) **`{workspace}` rw everywhere** - today's behaviour preserved; simplest, but leaves the write channel open.

X) Other

[Answer]: A - declared-writes model: every mount entry carries a mode; source/docs default ro; only declared outputs and caches are rw per step.

### Question Q4: default posture

What happens when a step omits the new `mounts` key?

A) **Strict-by-default** - empty or missing `mounts` = empty mount set (mount nothing). Secure and loud; existing pipelines (this repo's dogfood pipeline, the reference consumer repo) must add explicit declarations or their steps fail with clear messages. Forces every mount to be justified.

B) **Compat-by-default** - missing `mounts` = today's `{workspace}` rw single mount. Preserves today's behaviour for existing pipelines; the structural channel stays open by default and only closes for pipelines that opt in.

X) Other

[Answer]: A - strict-by-default: empty or missing `mounts` = empty mount set (mount nothing). Dogfood pipeline and the reference consumer repo gain explicit declarations as part of this unit.

### Question Q5: `.git` blindness mechanism

The sandbox must never see repository history.

A) **Worktree-export** - the engine materialises a fresh export of the repo (git worktree-add or archive) into a temp dir and mounts that instead of the workspace; `.git` is absent by construction. Simple, no submount gymnastics; adds an export step per run; write-back semantics for dev-step outputs need definition (produced artifacts copy back).

B) **Mount-exclusion** - mount the real workspace rw as today, then submount each other top-level entry so `.git` is hidden. No copy step, work stays live in the real repo; assembling per-entry submounts is brittle when entries change.

X) Other

[Answer]: A - worktree-export: engine materialises a fresh repo export (git worktree-add or archive) into a temp dir and mounts that; `.git` absent by construction. Write-back semantics TBD via follow-up F1.

### Question Q6: caches stay rw in-workspace (confirm)

Caches (`.nuget-packages`, `.venv-sandbox`, `node_modules`, `qa/.browsers`): stay rw in-workspace at their current paths (declared `rw` mounts under the new `mounts` field), or relocate to a host-owned cache dir?

A) **Stay in-workspace, declared rw** - consistent with the existing model and the plan-020 analysis (STAY); live-confirmed: `qa/.browsers` (chromium binary + syslibs) and `qa/node_modules` are large and must stay readable/rw for browser steps.

B) **Relocate to a host-owned cache dir** - mirror `sandbox_venv_dir`; moves large caches out of the workspace mount.

X) Other

[Answer]: A - stay in-workspace at current paths, declared rw under the new `mounts` field. Interaction with Q5 (export) resolved via follow-up F2.

### Question Q7: testability of docker-marked mount/egress tests

How much of the mount/egress test surface runs without root on the host (nftables needs root; CI implications)?

A) **Tiered split** - payload construction + config validation + policy serialisation = pure functions, unit-tested everywhere; a fake Sandbox backend drives run-loop logic; real-Docker integration tests marked and opt-in where a daemon exists; enforcement-behaviour tests (nftables, proxy allow/deny) marked and opt-in, requiring root/Linux, running fully in a dedicated privileged CI job or VM, never silently skipped. Mirrors the unit-3 FD-5 pattern.

B) **Docker-in-Docker on CI** - run enforcement tests inside a privileged DinD job so CI covers them without host root. Heavier, still needs privilege.

X) Other

[Answer]: A - tiered split: pure functions unit-tested everywhere; fake Sandbox backend for run-loop logic; real-Docker tests marked and opt-in. Enforcement-behaviour tests (proxy deny/allow) need no root and run in normal CI, never silently skipped. (Amended 2026-10-25: nftables dropped, so no privileged job or root requirement exists.)

### Question Q8: browser steps and arbitrary third-party assets

The page under test fetches live third-party assets (theme CSS, Google Fonts) from arbitrary domains; a render-check step's egress target set is effectively "any page dependency". How do allowlists apply to that step class?

A) **Broad-but-logged for browser steps** - browser steps get an explicit "any egress" policy class with connection logging (auditable); command/agent steps keep strict allowlists. Matches the post-intent finding; the browser's job is to render a live page.

B) **Egress proxy for browser steps** - proxy logs all browser-step traffic and can filter, though the filter set is unknown ahead of time. Same observability as A with more machinery.

X) Other

[Answer]: A - broad-but-logged egress for browser steps (explicit "any egress" policy class with connection logging); command/agent steps keep strict allowlists.

### Question Q9 (user-raised): allowlist ownership - who decides what's on the allowlist?

A) **Engine-shipped presets + pipeline-author override** - engine ships curated target sets (package-registries preset: pypi.org, files.pythonhosted.org, api.osv.dev, registry.npmjs.org, nodejs.org, crates.io, static.crates.io, api.nuget.org, github.com + codeload.github.com; browser class = any-egress-logged per Q8). Steps reference a preset or declare explicit targets; the author can extend. Undeclared targets fail loudly. (recommended)

B) **Pipeline-author declares everything** - no presets; every network-on step lists its targets; misses fail loudly. Max transparency; every pipeline re-implements registry lists and gets CDN mirrors wrong.

C) **Engine-only curated lists** - no per-project extension. QA steps (Kinde/R2/app URL) become impossible without falling back to broad-but-logged everywhere.

X) Other

[Answer]: A - engine-shipped presets + pipeline-author override

Feasibility note (user question 2): a common allowlist across toolchains is feasible for toolchain-driven steps (restore/conformance/test-install): all package registries are a small stable set served over CDNs with rotating IPs, which the proxy's per-connection hostname matching handles (CDN rotation is invisible to hostname matching). Per-project additions are account-specific hosts (R2 endpoint, Kinde domain, host app URL): typically 2-4 entries, concentrated in the qa step. Browser steps bypass the allowlist via Q8's broad-but-logged class.

## Follow-up Questions (from answer interactions)

### Question F1 (Q3 x Q5 interaction): worktree-export write-back semantics

With worktree-export (Q5), the sandbox works on a fresh export, not the real workspace. Dev steps write code there. How do sandbox writes get back to the real repo?

A) **Sync-back after each step with declared rw mounts** - after a step that declared rw paths, the engine syncs the export's changes back to the real repo (git-aware, no `.git` involved). Sandbox stays detached during the step; edits land afterwards. Simple, least surprise.

B) **Produced-artifacts only** - the export is mounted ro; agents get files into the repo only through the produces/artifact mechanism. Real repo is never touched by the sandbox; dev agents cannot edit code in place (breaks today's dev-step workflow).

X) Other

[Answer]: A - sync-back after each step with declared rw mounts (git-aware, no `.git` involved; sandbox detached during step; edits land afterwards)

### Question F2 (Q5 x Q6 interaction): caches under worktree-export

Caches stay rw in-workspace (Q6), but the sandbox mounts an export (Q5), not the real workspace. Where do cache paths (`qa/.browsers`, `qa/node_modules`, `.nuget-packages`) point inside the sandbox?

A) **Real-workspace cache paths bind-mounted into the export** - at the same relative paths, rw. Caches stay shared, real, and persistent across runs; the sandbox touches real cache state only. Requires cache paths to exist at export time and keeps a small deliberate rw window into the real workspace.

B) **Caches copied into the export** - export carries a copy of the cache dirs. No real-workspace rw window at all, but cache copies are stale/fresh-per-run and large (chromium binary + syslibs); defeats cache persistence.

X) Other

[Answer]: A - real-workspace cache paths bind-mounted into the export at the same relative paths, rw (shared, persistent; small deliberate rw window limited to declared cache paths)

## Resolved design decisions (recorded in FD, flagged for review)

### Design decision D-1: container model under per-step profiles

Unit-3's NFR-2 established persistent-per-run containers (two roles: network-none execution + provision). Per-step mount sets (Q4 strict) and per-step egress policies (Q1) make the mount/network surface per-step, and Docker fixes mounts at container creation. **Decision: profile-keyed container pool** - the engine derives each step's ContainerProfile = (resolved mount set, resolved egress policy, seccomp/cap policy); containers are created lazily per distinct profile and destroyed at pipeline end + error paths. Rationale: distinct profiles are few (typically 2-5 per pipeline), preserving NFR-2's performance posture; per-step create/destroy would regress the unit-3 agreed lifecycle. The provision container keeps its own profile (broad egress, trust phase, stamp-cached).

### Design decision D-2: multi-toolchain support is preset data

Per the user requirement (2026-10-25): the package-registries preset covers pypi, npm, NuGet (.NET), and crates (Rust) plus VCS/source-install hosts, so plan 018's toolchain-agnostic restore step stays agnostic; per-toolchain caches (.venv-sandbox, node_modules, .nuget-packages, target/) are cache declarations, not mechanism. New toolchains add data, never code.
