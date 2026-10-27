# Dev on a cheap Hetzner VPS (agent-engine + the consumer repo, post units 5-7)

**Type**: feasibility assessment (research only; no code). Informs unit-6 Functional Design.

**Trigger**: User - "Once we build units 5-7, how feasible will it be to do dev on this repo and ../the consumer repo on a cheap Hetzner VPS?" Follow-up: evaluate four rungs (infeasible on any Hetzner / bigger box / same-sized box / this box).

## 1. The existing CAX11 (wiki box; not the dev target)

The user owns a Hetzner CAX11, created 2026-10-21:

| Item | Value (live-verified) |
|---|---|
| Plan | CAX11, name `consumer-ubuntu-4gb-nbg1`, 91.99.18.150, Nuremberg (eu-central) |
| CPU | 2 shared vCPU, Ampere Neoverse-N1 @ 2.0 GHz, aarch64 |
| RAM | 3.7 GiB usable, **no swap configured** |
| Disk | 38 GB usable, 6.5 GB used, 30 GB free |
| Docker | 29.9.2 native (Linux daemon, no Docker Desktop needed) |
| Resident stack | Docmost (439 MB), Outline (539 MB), 2x Postgres (73 MB), 2x Redis (14 MB), 2x Mailpit (24 MB), Caddy (14 MB) = **~1.1 GiB resident at idle** |
| Toolchain | docker, git, python3 present. **dotnet, node, uv, make, jq absent** |
| Swap | 0 B |

So this is not a fresh box: the wiki stack (Docmost + Outline + mail + Caddy)
already consumes roughly a third of RAM and ~7 GB of disk. Per the user's
decision (2026-10-25), this box stays the wiki box; dev gets a NEW dedicated
box because isolation is the point. It appears here only as the evidence base
for sizing.

## 2. What units 5-7 change for this question

- **Unit 5** (mount granularity + egress proxy): no resource impact of note.
  The per-step egress proxy is a small resident process per run. Mount
  granularity changes what the sandbox sees, not how much the host needs.
- **Unit 6** (cloud deployment): this IS the question. The intent is verbatim
  "deploy this all (e.g. to a Hetzner VPS) ... model would be build the plans
  locally, run the loop in the cloud". Unit 6 makes remote loop + remote viz a
  first-class path instead of hand-rolled SSH.
- **Unit 7** (Linux-native sandboxing, no Docker): developed ON this box ("we
  need it to test the linux native stuff since I have a mac"). On Linux the
  native path (bubblewrap/Landlock/seccomp) drops the Docker daemon and the
  sandbox-image pull/disk costs from the loop's runtime footprint. Every
  Linux-kernel facility it needs (namespaces, Landlock, seccomp) is available
  on Ubuntu arm64. Net effect: units 5-7 make the box MORE viable, not less.
  Unit 6 legitimizes the workflow; unit 7 shrinks the loop's footprint.

## 3. Resource budget on the CAX11 as it stands

### 3.1 RAM (the binding constraint)

Measured/derived figures (resident, not limits):

| Consumer | RAM |
|---|---|
| Existing wiki stack (resident today) | ~1.1 GiB |
| Docker daemon + OS + SSH + page cache floor | ~0.6-0.8 GiB |
| agent-engine loop: engine process (Python + OpenHands SDK + LangGraph) | 0.4-0.8 GiB |
| Sandbox container during pytest/pip steps (limit is `4g`, actual usage) | 0.3-1.0 GiB |
| the consumer repo: `dotnet restore`/`build` spikes (MSBuild workers) | 0.5-1.5 GiB |
| the consumer repo dev compose: Postgres + MinIO (pgsty/silo) | 0.3-0.5 GiB |
| Vite dev server / Kestrel | 0.2-0.5 GiB |
| Chromium render probe (qa stage, qa/.browsers) | +0.5-0.9 GiB transient |

Steady dev (loop running + the consumer repo stack up): ~3.2-4.5 GiB demand on a
3.7 GiB box. Peak (dotnet build + chromium probe + loop restore concurrently)
reaches ~5 GiB demand.

**Verdict**: 4 GB is workable only with a swap file (4-8 GB on the NVMe) and
sequencing discipline (do not run the dev loop, a dotnet build, and a
chromium probe at the same moment). Without swap, the OOM killer will land on
random processes during peaks. With swap, peaks degrade to slow instead of
dead. The loop's own timeouts are generous enough for this (restore 600-1800s,
test 600s) and the loop is sequential by design (one stage, one container at a
time), which is exactly the right shape for a small box.

### 3.2 Disk (tight but manageable)

| Item | Size (measured where possible) |
|---|---|
| agent-engine repo incl. both venvs (today) | 1.6 GB |
| the consumer repo repo incl. node_modules + qa + .nuget-packages | 4.8 GB |
| Sandbox images on box: `dev` + `dev-dotnet` (arm64 variants) | ~2.4 GB |
| .NET 10 SDK (linux-arm64) | ~1.5-2 GB |
| NuGet global cache | 2-5 GB (shareable via NUGET_PACKAGES) |
| Chromium arm64 build in qa/.browsers (fetched per repo) | ~0.4 GB |
| the consumer repo Postgres + MinIO volumes | ~1 GB |
| Swap file (see 3.1) | 4-8 GB |

Total addition: ~18-25 GB against 30 GB free. Fits, but the box needs
discipline: one .venv-sandbox per repo (the engine already bind-mounts it from
`~/.agentic-dev-loop/venvs/...` outside the workspace), `docker image prune`
after image refreshes, no duplicated node_modules. A second chromium cache
(one per consumer repo) is a known cost of the current model.

### 3.3 CPU (slow but sufficient)

2 shared Neoverse-N1 cores at 2.0 GHz is roughly a 2019-era ARM server core.
Expect single-threaded builds (dotnet restore/build, pytest) to run 1.5-3x
slower than the author's Mac. The loop is sequential, so vCPU count matters
less than vCPU speed; wall-clock per loop iteration will grow, not correctness.
Shared-vCPU steal is a Hetzner reality but tolerable for this workload.

### 3.4 Multi-loop concurrency (designed ceiling: 4 loops; typical: 2)

User decision (2026-10-25): the dev box is a GENERIC loop-runner host. The
author runs at most 2 loops in practice and wants headroom, so the box is
sized for a designed ceiling of 4 concurrent loops (different repos;
same-repo concurrency is a workspace-contention problem and is assumed out of
scope). Budget at 4 vCPU / 8 GB / 80 GB, per loop:

- **Engine process** (Python + OpenHands SDK + LangGraph + viz server):
  0.4-0.8 GiB resident per loop.
- **Sandbox container**: created as `sleep infinity` and kept per run
  (~30-80 MB idle); real consumption only during command execution: pip
  install 0.3-0.8 GiB, pytest 0.3-0.6 GiB, dotnet restore/build 0.5-1.5 GiB,
  chromium probe +0.5-0.9 GiB transient. The per-container memory LIMIT
  defaults to 4g (provision container 8g), so four loops nominally carry 16g
  of potential limit on an 8 GB box. Limits are per-pipeline overridable via
  `isolation.memory` (verified in `engine/isolation.py`); that knob is the
  load-bearing one for concurrency.
- **CPU**: per-container default quota is 2.0 cpus. Four loops on four vCPUs
  is 1:1 at the ceiling, and typical use (2 loops) gives each loop the full
  quota with no contention. Docker shares gracefully when the ceiling is hit,
  but the fixed per-step timeouts (e.g. the 600s test gate) can then be hit
  from contention alone, not slow code. A solo-slow suite is fine; a 4-way
  CPU crunch risks a spurious gate failure and an expensive LLM retry.

RAM arithmetic on 8 GB:

| Scenario | Demand |
|---|---|
| Typical: 2 loops, both mid-heavy (dotnet builds) | ~5.4 GiB |
| Steady ceiling: 4 loops mid-run, 2 executing heavy commands | 5.4-8.0 GiB |
| Worst: all 4 in dotnet builds, or 2-3 chromium probes | ~10 GiB |

Typical use never stresses the box: two loops each get the full 2.0 CPU quota
(4 vCPU / 2 loops) and demand stays under 6 GiB. The designed ceiling fits in
steady state; the worst case slightly exceeds 8 GiB and is absorbed by a 4 GB
swapfile (degrades to slow, not dead; the loop's own timeouts are generous
except the 600s gates under CPU contention, see below).

Verdict: 4 vCPU / 8 GB / 80 GB is the right size, not a squeeze. Three habits
keep it honest: (1) per-pipeline memory caps (2g for dotnet repos, 1-1.5g for
python repos) so total potential stays bounded (4 × 2g = 8g worst); (2)
stagger loop starts (they are author-triggered anyway) so the 600s gates never
collide with a CPU crunch; (3) a 4 GB swapfile as the OOM backstop. With a
typical load of 2 loops, none of these bind; they exist for the ceiling. The
16 GB tier is unnecessary at this workload.

Multi-loop facts that already work in the engine:

- **Viz ports**: plan 009 made startup multi-instance-safe (cwd-scoped port
  sweep, OS-assigned fallback). Four concurrent viz servers coexist. Unit 6
  should make per-run viz URLs first-class, since the author will juggle
  several.
- **State/checkpoints**: per-run dirs, no shared mutable state between loops.
- **LLM traffic**: remote deepseek API per loop; 20 TB egress is a non-issue
  at four loops.

Disk at four repos: 4 × ~5 GB workspaces + 4 × 0.7 GiB sandbox venvs + images
3-5 GB + dotnet SDK/NuGet 5-7 GB + per-repo chromium caches ~2 GB + run
artifacts/checkpoints. Total ~35-45 GB on 80 GB: fits with room, but run
artifacts and checkpoint sqlite files accumulate per run and need a retention
policy (a cron that prunes run dirs older than N days).

## 4. ARM64 readiness (verified where possible)

| Component | arm64 status |
|---|---|
| Sandbox image `dev` (digest-pinned) | multi-arch, arm64 manifest verified |
| Sandbox image `dev-dotnet` (digest-pinned) | multi-arch, arm64 manifest verified |
| pgsty/silo (MinIO stand-in in the consumer repo compose) | multi-arch, arm64 manifest verified |
| .NET 10 SDK | official linux-arm64 builds |
| Node / Vite / Vitest | official arm64 builds |
| Postgres 16 alpine, Redis, Caddy | multi-arch |
| OpenHands SDK, LangGraph (loop runtime) | pure Python |
| Playwright chromium headless-shell for qa probes | arm64 build exists (fetched at first qa run) |
| Kernel facilities for unit 7 (namespaces, Landlock, seccomp) | all on Ubuntu arm64 |

No architecture blockers found. Unknowns: none material for dev.

## 5. Portability gaps found while assessing (unit-6 relevant)

1. **Hardcoded Mac path in the the consumer repo pipeline**: `dev` step env sets
   `NUGET_PACKAGES: /Users/luke/dev/work/the consumer repo/.nuget-packages`. On the box
   this path does not exist; the dev agent's network-off NuGet restores would
   fall back to an empty global cache and fail. Needs a workspace-relative or
   env-expanded form before the the consumer repo loop can run on the box.
2. **Secrets bootstrap**: `trigger-agentic-dev-loop.sh` reads
   `~/.config/consumer/agentic-dev-loop.env` (Kinde + API keys). The box needs its
   own copy of that env file (or a unit-6 secrets story) before any loop run.
3. **Kinde dev flows**: local dev callbacks are localhost-bound; on the box
   they work through an SSH tunnel (`ssh -L`) or a dev subdomain on the
   existing Caddy. Minor, but must be set up once.
4. **Repo/state transfer** (unit 6 open question 4): today it would be git
   push + pull on the box, or rsync. Unit 6 FD decides the blessed mechanism.
5. **Viz remote access** (unit 6 open question 2): until unit 6 lands, an SSH
   tunnel (`ssh -L 127.0.0.1:<vizport>:...`) or Caddy + basic auth on the box
   closes this today. The box already runs Caddy.

## 6. Feasibility verdict (four-rung ladder)

The question re-asked as a spectrum: infeasible on any Hetzner, feasible on a
bigger box, feasible on a same-sized box, feasible on this box. Answers in
order, with the evidence that lands each rung.

### 6.1 Infeasible on any Hetzner: NO

Nothing in the workload needs hardware a Hetzner VPS lacks. No GPU (LLM calls
go to the deepseek API over the network), no macOS-only tooling, no unusual
kernel features (unit 7's namespaces/Landlock/seccomp are all standard Ubuntu
arm64 facilities), and every critical image is arm64-capable (verified in
section 4). This rung is ruled out by the live probes alone.

### 6.2 Feasible on a bigger box: YES, comfortably

A 4 vCPU / 8 GB / 80 GB box (CAX21-class, about 10.50 EUR/mo at June 2026
pricing; orderability must be checked, see below) runs everything at once:
wiki stack (if migrated there) or dev stack, loop, chromium probes, dotnet
builds, no swap required, no pruning discipline, 2-4x faster builds than the
2-vCPU box. This is the "just works" rung. The next tier up (8 vCPU / 16 GB /
160 GB, about 21 EUR/mo) adds nothing but margin; nothing measured needs it.

### 6.3 Feasible on a same-sized (fresh 2 vCPU / 4 GB / 40 GB) box: YES

The same plan, bought new and dedicated to dev. The binding facts from
section 3: the wiki stack consumes ~1.1 GiB resident and ~7 GB disk, which on
a fresh box go to the dev workload instead. Remaining budget: ~2.5 GiB base
available, ~35 GB disk. The loop is sequential by design (one stage, one
container at a time), so peak = one heavy operation + small resident dev
compose. Add a 4-8 GB swapfile and the same sequencing habit, and this is an
ordinary small-box dev experience: slower than a bigger box, but never
blocked. Disk stays comfortable (roughly 20 GB used of 40 GB, see 3.2).

### 6.4 Feasible on this box (wikis included): YES, with discipline

Everything in 6.3, minus the freed wiki footprint, plus two real risks:

1. **RAM**: peaks (dotnet build + chromium probe + loop restore overlapping)
   reach ~5 GiB demand on 3.7 GiB usable. Swap is mandatory, not optional, and
   the OOM killer is the alternative. Worst case it takes down a wiki
   container, not the dev work.
2. **Disk**: 30 GB free against an ~18-25 GB addition. Fits, but only with
   pruning habits (single .venv-sandbox per repo, image prune after
   refreshes, no duplicate node_modules). The disk budget, not RAM, is what
   eventually forces a move.

The honest characterization: feasible, but the wiki stack is the reason this
rung needs discipline at all. The dev workload alone is a fine fit for 4 GB;
the co-tenancy is what makes it tight.

### 6.5 Decision

Settled (2026-10-25):

1. **Separate box, full stop.** Isolation is the point. The CAX11 stays the
   wiki box; dev gets a NEW dedicated box.
2. **Target: 4 vCPU / 8 GB / 80 GB** (CAX21-class, about 10.49 EUR/mo at June
   2026 pricing; check orderability, see below). The user's stated 10 EUR/mo
   budget covers it.
3. **4 concurrent loops fit on that box with room to spare** (typical use is
   2), with the three habits from section 3.4: per-pipeline `isolation.memory`
   caps, staggered starts, and a 4 GB swapfile backstop. The 16 GB tier is
   unnecessary at this workload.

Caveat on pricing/availability: Hetzner's June 2026 repricing moved the
cost-optimized line substantially and third-party reports in 2026 flag the
CX/CAX line as intermittently unorderable. Treat every price and plan name
here as "check at purchase time", not a quote.

## 7. Recommended working split (fits both the box and unit 6's model)

1. **Author machine (Mac)**: opencode/editor, plan authoring, git history,
   Kinde login flow (local). Triggers and watches loops remotely.
2. **Wiki box (existing CAX11)**: stays as-is; no dev workload ever lands
   there.
3. **Dev box (new, dedicated, 4 vCPU / 8 GB / 80 GB)**: a generic loop-runner
   host, not a repo-specific one:
   - Designed ceiling of 4 concurrent AI-DLC loops across different repos
     (agent-engine, the consumer repo, future consumers), typical load 2, one
     loop per repo at a time.
   - Generic toolchain: dotnet SDK 10, node LTS, uv, python3, jq, make, plus
     the Docker daemon for unit-5-era sandboxing (unit 7 later replaces it on
     Linux).
   - Per-pipeline `isolation.memory` caps (2g dotnet, 1-1.5g python), swapfile
     (4 GB), staggered loop starts.
   - Per-consumer secrets env files (the `~/.config/consumer/agentic-dev-loop.env`
     pattern), sandbox venvs under `~/.agentic-dev-loop/venvs/`.
   - Viz per run behind an SSH tunnel now; unit 6 auth/TLS + per-run URLs
     later.
   - Artifact retention cron: prune run dirs/checkpoints older than N days so
     80 GB stays 80 GB.
4. **LLM calls** originate from the dev box (deepseek API); 20 TB traffic is a
   non-issue even at four loops.
5. Setup checklist on the dev box: swapfile, toolchain (dotnet/node/uv/jq/
   make), pull the digest-pinned sandbox images once, per-pipeline memory
   caps, fix the NUGET_PACKAGES path gap, seed the secrets env files, image
   prune after refreshes, retention cron.

## 8. Questions this raises for unit-6 Functional Design

1. Does unit 6's "simple deployment" target a fresh dedicated dev box
   (provisioning script for a Hetzner Ubuntu, per section 6.5) or the
   existing wiki box, or a portable story across both?
2. Where does the the consumer repo loop's NuGet cache live on Linux, and does the
   hardcoded-path gap in section 5 fall into unit 6 scope or a the consumer repo fix?
3. Viz auth: tunnel-only for the dev phase, or does the first unit-6 slice
   need TLS + auth (Caddy on the box makes HTTPS cheap)?
4. State transfer: git push/pull (works today, keeps history local) versus
   rsync/archive upload (unit 6 open question 4) - the git model is already
   proven on this box and should be the default unless FD finds otherwise.

## Sources

- Live probes: SSH to 91.99.18.150 (uname, free, df, docker stats, docker
  system df, lscpu), 2026-10-25
- `docker manifest inspect` of the two digest-pinned sandbox images and
  pgsty/silo (local daemon)
- `du` of agent-engine, the consumer repo, .venv-sandbox, qa/.browsers,
  .nuget-packages
- aidlc-docs unit-5/6/7 intent documents
- the consumer repo `agentic-dev-loop/pipeline.yaml` (NUGET_PACKAGES path, dev-dotnet
  image, secrets block)
- Hetzner Cloud pricing/availability reports (June 2026 adjustments; CX/CAX
  availability flagged)
