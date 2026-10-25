# Unit 5 Intent (draft) - Sandbox Mount Granularity + Egress Policy

> **Status: INTENT ONLY** - draft, not started. Written 2026-10-23 following the security-area scoping discussion. No code written, no AI-DLC workflow started, no decisions committed. Revisit before proceeding.

**Assumption:** this unit starts after plans 018 (restore step + dependency mechanism), 019 (secrets store + `.env*` ban), 020 (host executes nothing from the workspace; tooling integrity check), and 021 (`deps_policy`, locked default) are all implemented. Both 020 and this unit touch `engine/isolation.py` - the plan-020 work lands first.

## Intent

Close the last structural channel in the containment model. Today every container mounts the entire workspace read-write, and `network: true` means *unrestricted* egress. Anything that executes inside a container - agent-written code, a squatted package, an install script - can (a) read everything the workspace holds, and (b) send anything it can touch anywhere on the internet. Plan 019 took secrets out of the workspace and plan 020 took the host off the target list; this unit removes the two remaining exfil paths: **reading what a step doesn't need**, and **sending to addresses a step doesn't need**.

Two mechanisms:

1. **Per-step mount granularity.** Steps declare their mount scope with read-write / read-only / absent distinctions. Each step mounts only what its job requires: dev gets source + tests + docs + its cache (rw), not `.git` or unrelated dirs; restore gets manifests + caches; qa gets built app artifacts + its caches. The `.git` blindness 020 deferred lands here.
2. **Egress policy.** `network: true` stops meaning unrestricted. Each network-on step gets an allowlist of targets (domains/IPs/ports): restore → package registries; qa → host app + Kinde + R2 + registries; conformance → npm registry. This bounds 019's residual: a rogue secret-holder can only send a value to a declared target.

### What the user gets (the load-bearing pillars)

1. **Least-access mounts** - config expresses mount scope per step; `DockerBackend` assembles per-request mounts; a step physically cannot read what it does not mount.
2. **Egress allowlists** - config expresses target sets per step; enforcement is network-level, not a prompt rule.
3. **Read-only where possible** - read-only paths get `ro` mounts, so even a compromised step cannot tamper with them.
4. **`.git` blindness** - the sandbox never sees repository history.
5. **Loud failure modes** - a missing path or an allowlist miss fails the run with a clear message, never a silent degradation.

## Relationship to existing units

- **unit-1/2/3** - the containment foundation (output passing, SDK hardening, kernel isolation). This unit extends unit-3's `isolation.py` (mount assembly + container networking).
- **unit-4** - WONTSHIP (2026-10-22); no adapter ships. Only its module split matters here (viz lives in `src/agentic_dev_loop/viz/`).
- **plan 018/021** - the restore step's egress allowlist target set is exactly the package registries; coordinated with 021's locked dial.
- **plan 019** - egress allowlists bound the rogue-secret-holder residual; mount granularity removes the workspace-wide read a rogue secret-holder would otherwise have.
- **plan 020** - the run-start git-clean integrity check stays valid; narrower mounts reduce what an agent can modify at all.
- **plan 023** - shipped the bundled chromium seccomp profile + `--cap-add SYS_CHROOT`; its follow-up 5 (read-only workspace) is closed by this unit's mount granularity.

## Findings (exploratory; live-sourced)

- Mount set today: **exactly `{workspace}`, rw**, in every container (`DockerBackend`, `isolation.py`).
- `network: true` = bridge container, **no egress filtering of any kind**.
- The provision container (setup hooks) also mounts `{workspace}` rw with full network at startup.
- `--cap-drop ALL` means the engine **cannot hand `NET_ADMIN` to a container** for in-container iptables - egress enforcement must live outside the container.
- Docker has no native per-container egress firewall for bridge networks; enforcement options are engine-managed netns + nftables before container start, an egress proxy, or Docker's newer network-policy features (version support TBD).

## Findings (post-intent, live-sourced during plan 023, 2026-10-24)

The Chromium sandbox work (plans 023-025) exercised exactly the surfaces this
unit touches. Live-verified facts:

- **ro mounts are feasible for render/QA steps.** The browser probe writes
  its results to a tmpfs path (`/tmp`), never into the workspace; a
  read-only workspace mount would not have blocked the render-check flow.
  Supports pillar 3 and favours strict defaults (open question 4).
- **Browser steps carry hard-to-allowlist egress.** The page under test
  fetched live third-party assets (theme CSS, Google Fonts) from arbitrary
  domains while rendering; a render-check step's egress target set is
  effectively "any page dependency". The egress-policy design must handle
  browser steps explicitly (proxy-based, or broad-but-logged egress for
  that step class) rather than assume registries-style allowlists cover
  everything.
- **Custom seccomp profiles work under `--cap-drop ALL`, with selective
  cap re-adds.** Plan 023 shipped a bundled chromium profile (userns
  syscalls allowed) plus `--cap-add SYS_CHROOT`; Chromium's namespace
  sandbox then engages fully (renderers show `Seccomp: 2`,
  `NoNewPrivs: 1`). The same `DockerBackend` that assembles mounts already
  shapes per-container syscall/cap policy, so the two knobs are neighbours,
  not separate mechanisms.
- **Workspace caches stay rw and large.** The consumer's `qa/.browsers`
  (chromium binary + syslibs) and `qa/node_modules` live inside the
  workspace mount and must remain readable/rw for browser steps; live
  confirmation of open question 6's STAY conclusion.
- **The rw workspace mount is the residual write/exfil channel after the
  browser sandbox engages.** Plan 023's review called it out explicitly:
  with renderers sandboxed, the last vector for a compromised page is
  writing into the repo through the rw mount. This unit's mount-granularity
  work is the direct close of that vector (plan 023 follow-up 5 points
  here).

## Recommended architecture (direction only - Functional Design resolves)

- **Config:** `mounts:` per step - list of `{path (repo-relative), mode: rw | ro}` - default preserves today's `{workspace}` rw (compatibility), plus `exclude: [".git"]`; FD decides strict-vs-compat defaults.
- **Mount enforcement:** multiple `-v` flags per request; `ro` submounts for read-only paths.
- **Egress enforcement:** engine-created per-policy network; the host applies nftables rules to the network namespace *before* the container starts inside it (no NET_ADMIN needed in-container) - or an HTTP(S) proxy the container must use, with a kernel-level block as backstop. Domain-based allowlists need DNS control (resolve-and-allow, or DNS interception) - an FD problem.
- **FD resolution (2026-10-25):** the engine-managed per-policy egress proxy became THE enforcement point on all platforms; nftables-in-netns dropped. Kernel-level enforcement is deferred to the future native Linux backend (unit-3 FD-2 scope). Product posture for now: "best we can do with Docker". Hostname matching at the proxy per connection (CONNECT target); no IP resolution, no DNS interception.
- **Provision container:** startup keeps broad egress (stamp-cached, runs before any agent input exists) - FD confirms.

## Open questions (revisit later - Functional Design)

1. Egress enforcement mechanism that works with `--cap-drop ALL` and unprivileged containers: nftables-in-netns vs egress proxy vs Docker network-policy features (which Docker versions?).
2. Domain-based allowlists need DNS control; is IP-based allowlisting acceptable where hosts are stable (`host.docker.internal`, registry IPs)?
3. `ro` mounts vs copy-on-write: build outputs and caches need rw - which paths stay rw per step?
4. Default posture: strict-by-default (empty mount list = mount nothing) vs compat-by-default (`{workspace}` rw) - ergonomics vs security.
5. `.git` removal: mount-exclusion (multiple submounts of repo-minus-`.git`) vs worktree-export (`git archive` to a temp dir) - freshness vs simplicity.
6. Caches (`.nuget-packages`, `.venv-sandbox`, `node_modules`, `qa/.browsers`): stay rw in-workspace (already part of the model - 020 analysis said STAY); confirm no relocation needed here.
7. Testability: docker-marked tests asserting mount/egress behavior - how much runs without root on the host (nftables needs root; CI implications)?
8. Browser steps: how do egress allowlists apply when the page under test may fetch arbitrary third-party assets (plan 023 finding)? Proxy-based vs broad-but-logged for that step class.

## Sources

- planning-docs/018, 019, 020, 021, 023, 024, 025
- Security-surface + severity discussion (this session, 2026-10-23)
- `src/agentic_dev_loop/engine/isolation.py` (DockerBackend, mount assembly)
- unit-3 functional design (kernel-level sandboxing)
