# Business Rules - unit-3 (always-on sandbox isolation)

> **NFR-stage amendments (2026-10-19)**: native Linux backend dropped (user directive - Docker everywhere, `Sandbox` surface retained as extension seam); per-step `env:` mapping (NFR-1); persistent-per-run lifecycle (NFR-2); `docker` CLI transport (NFR-3); per-step `setup:` hook run fresh at the step boundary (NFR-5); Windows CI (NFR-6). Amended rules carry inline notes.

## BR-1: Isolation boundary (FR-2.1)

| Rule | Detail |
| --- | --- |
| BR-1.1 | The agent `terminal` tool and command steps execute inside the sandbox |
| BR-1.2 | Action steps, LangGraph runtime, LLM calls, viz server, and commit execute host-side (trusted code) - they never enter the sandbox |
| BR-1.3 | ~~Each sandboxed execution is a create → exec → destroy lifecycle, one step per execution; no long-lived shared containers in this unit~~ **Amended (NFR-2):** per-request exec semantics; the Docker lifecycle is persistent-per-run - one execution container created lazily at the first sandboxed exec, `docker exec` per request, destroyed at pipeline end and on all error paths |

## BR-2: Isolation is always on (FR-2.3 as amended, NFR-1 as amended, FD-4)

| Rule | Detail |
| --- | --- |
| BR-2.1 | Sandboxing is mandatory for every pipeline run - no opt-out, no `isolation: false`, no disable knob |
| BR-2.2 | No usable runtime ⇒ the pipeline refuses to run - fail fast ALWAYS, including the self-hosted dev loop |
| BR-2.3 | The failure names the missing runtime and gives install instructions; never a silent unsandboxed fallback |
| BR-2.4 | The `isolation:` config section configures the always-on sandbox; its absence means opinionated defaults, not unsandboxed behaviour |

## BR-3: Backends (FR-2.2 as amended, FD-2/2b/2c as amended, NFR stage)

| Rule | Detail |
| --- | --- |
| BR-3.1 | Docker is the only backend, on all platforms (macOS/Windows/Linux - Docker Desktop / OrbStack / Colima / podman machine) - toolchain from the harness-pinned dev image. **Amended (user directive 2026-10-19):** ~~the native bwrap+Landlock+seccomp backend on Linux~~ is dropped from this intent |
| BR-3.2 | ~~Native backend on Linux: host toolchain inside namespaces (bwrap read-only bind, tmpfs scratch, Landlock + seccomp); no image on Linux~~ - REMOVED (native backend dropped) |
| BR-3.3 | The extracted `Sandbox` surface is **retained with one concrete backend (Docker)** as the extension seam for future backends - a deliberate, user-directed exception to the no-single-implementation-interface rule |

## BR-4: Backend selection & platform adaptation (FR-2.4, FD-2c as amended, FD-4b as amended)

| Rule | Detail |
| --- | --- |
| BR-4.1 | Selection = platform + availability probe; the decision logic is a pure function over probe results. **Amended:** the `backend:` override knob is removed (no second backend to select) |
| BR-4.2 | All platforms select Docker. ~~Linux defaults to native~~ - REMOVED |
| BR-4.3 | ~~macOS/Windows default to Docker; no native option exists there~~ - covered by BR-4.2 |
| BR-4.4 | Daemon detection adapts per platform: unix socket vs npipe (+ WSL2 on Windows); transport is the `docker` CLI subprocess (NFR-3) |
| BR-4.5 | Mount paths are translated per platform (WSL2 path translation on Windows); CRLF/permission-metadata handling on Windows mounts |
| BR-4.6 | A failed probe is itself loud (BR-2.3); the probe is an IO adapter faked in tests (FD-5) |

## BR-5: Default isolation profile (FR-2.5, FD-1, FD-3c, FD-4b)

| Rule | Detail |
| --- | --- |
| BR-5.1 | Default profile: network egress blocked; rootfs read-only; writes only to the bind-mounted workspace + state_dir; capabilities dropped |
| BR-5.2 | Default profile: repo-mutating git operations blocked (BR-6) |
| BR-5.3 | The `isolation:` section may override profile knobs (e.g. re-allow git mutations); unknown keys fail at load |
| BR-5.4 | The default image is harness-pinned by digest; overriding the image is explicit, never an implicit `latest` |
| BR-5.5 | Profile → mechanism translation is pure and platform-independent (unit-tested everywhere) |

## BR-6: Agent git mutations blocked by default (FD-3c, FR-2.6)

| Rule | Detail |
| --- | --- |
| BR-6.1 | Inside the sandbox, git commands whose subcommand is outside the known-read set (status, log, diff, show, ls-files, rev-parse, blame, describe, for-each-ref, read-only branch/tag listing) are treated as mutations and rejected loudly by default |
| BR-6.2 | Known-read git operations are always allowed |
| BR-6.3 | One profile knob (`allow_git_mutations`) re-allows mutations per-pipeline, author-controlled |
| BR-6.4 | The guard applies to all sandboxed executions - the terminal tool and command steps alike (command steps are agent-influence channels: FR-2.1's conftest argument) |
| BR-6.5 | The classifier is argv-based and deny-by-default; it is policy convenience, not the security boundary - kernel isolation (BR-5.1) is the boundary |

## BR-7: Host-side commit unchanged (FR-2.6)

- The `commit` action stays host-side native git (unchanged from today; FR-2.6 motivated by Windows write-back pitfalls).
- **Accepted residual risk (recorded in FD-3b):** agent-influenced `.git/hooks` still execute host-side during the trusted commit action. The sandbox cannot prevent host-side hook execution; the default-profile git-mutation block (BR-6) reduces the agent's ability to stage malicious mutations, but a hostile agent that writes `.git/hooks` before the commit step remains a residual. Documented and accepted.

## BR-8: Fail fast, always loud (FR-2.3, NFR-2)

| Rule | Detail |
| --- | --- |
| BR-8.1 | Every load-time detection/validation failure (unknown isolation key, bad knob value, missing runtime, image pull failure, mount failure) raises a loud error naming the problem |
| BR-8.2 | Every sandboxed execution failure surfaces as a step failure (exit code / stderr), never swallowed |
| BR-8.3 | Error paths always destroy the execution context (container/namespace cleanup) - no leaked sandboxes (SECURITY-15) |

## BR-9: Environment & credential hygiene inside the sandbox (NFR-3, NFR-1)

| Rule | Detail |
| --- | --- |
| BR-9.1 | ~~The sandbox env is the unit-2 scrubbed `child_env` set~~ **Amended (NFR-1):** the sandbox env = fixed safe base (PATH/HOME/LANG/LC_ALL/TERM) + the step's declared `env:` mapping only; the global `env_allowlist` never leaks into the sandbox; the LLM key is included only if explicitly declared (resolves the former BR-9.1/BR-9.2 contradiction). Host-side command/action steps keep unit-2 `child_env` unless the step declares `env:` - then the declared env IS the allowlist for that step |
| BR-9.2 | No secrets (API keys, tokens) enter the sandbox env or sandbox logs unless the author explicitly declares them; LLM credentials are host-side by BR-1.2 |
| BR-9.3 | The Docker socket is never mounted into the sandbox |
| BR-9.4 | Read-only rootfs + dropped capabilities + write-restricted mounts = least privilege (SECURITY-06) |

## BR-10: Transient-infrastructure resiliency (NFR-4, scoped)

| Rule | Detail |
| --- | --- |
| BR-10.1 | Idempotent infrastructure operations (image pull, daemon-start wait) may retry with bounded backoff |
| BR-10.2 | Sandboxed command execution is never retried (side effects); failures are loud step failures |
| BR-10.3 | Server-grade load/HA/DR/RTO/RPO explicitly out of scope (NFR-4 scoped) |

## BR-11: Audit trail of sandboxed executions (NFR-3)

| Rule | Detail |
| --- | --- |
| BR-11.1 | Every sandboxed execution is logged: what ran (argv, backend/image), when, exit code - structured, no secrets (SECURITY-03) |
| BR-11.2 | Sandboxed code cannot reach host logs; the audit trail is written host-side only |

## BR-12: Testability (NFR-7)

| Rule | Detail |
| --- | --- |
| BR-12.1 | The fake speaks the extracted `Sandbox` surface (FD-5) - unit tests need no container |
| BR-12.2 | Pure translation logic (profile → payload, mount translation, selection, classifier, sandbox-env resolution) is unit-tested on every platform |
| BR-12.3 | Real-backend integration tests are marked + opt-in (any machine with a daemon) and are NEVER silently skipped - absence of the runtime is a loud failure, not a skip |
| BR-12.4 | The CI workflows ship as part of unit-3: Docker-backend integration tests on GitHub Actions ubuntu-latest AND windows-latest runners (NFR-6) |

## BR-13: Provision phase - per-step `setup:` hook (NFR-5)

| Rule | Detail |
| --- | --- |
| BR-13.1 | Agent and command steps may declare `setup:` - a trusted author command that prepares the working environment (stack-agnostic: `cd frontend && npm ci`, `python -m venv .venv-sandbox && .venv-sandbox/bin/pip install -e ".[dev]"`). Brief and action steps reject `setup:` at load |
| BR-13.2 | Setup runs INSIDE the backend environment (the provision container), never on the host (ABI correctness - host-built artifacts must never run in the Linux sandbox) |
| BR-13.3 | Two container roles: the setup phase runs in a one-shot `--rm` provision container with network ON (destroyed afterwards); the execution container is created `--network none`. Container networking is per-container, so the phases cannot share a container (NFR-5, agreed) |
| BR-13.4 | Setup targets the per-run export (a .git-free copy of the repo's product files), never the real workspace. Setup outputs land in the export and are visible to the step's sandboxed execution |
| BR-13.5 | Setup runs fresh at every step entry (first entry, `goto dev` retries, all of it). No stamping, no keys, no probes, no cross-run reuse. Steps without `setup:` trigger nothing (zero overhead) |
| BR-13.6 | Setup failure is a loud step failure (BR-8). The agent terminal never runs setup and never gets network - frozen, full stop |
| BR-13.7 | Accepted risk (recorded): the workspace is writable inside the sandbox (BR-5.1), so the agent can mutate setup outputs (e.g. poison `node_modules` before a later `npm test` step). Host containment still holds; the git-mutation guard (BR-6) and the commit dirty-at-start policy limit persistence |

## Security-baseline mapping (sandbox surfaces)

| SECURITY rule | Disposition | Where |
| --- | --- | --- |
| SECURITY-03 application logging | Enforced | BR-11 structured audit log of sandboxed executions, no secrets |
| SECURITY-05 input validation | Enforced | request validation (argv shape, cwd inside mounts, bounded sizes); config key validation (BR-5.3, request VO) |
| SECURITY-06 least privilege | Enforced | BR-9 (declared-env-only sandbox env, no docker.sock), BR-5.1 (read-only root, dropped caps, write-restricted mounts) |
| SECURITY-07 restrictive network config | Enforced | BR-5.1 network egress blocked in the execution container; BR-13.3 network ON exists only in the trusted provision phase |
| SECURITY-09 hardening | Enforced | harness-controlled image, fail-closed errors (BR-8.3) |
| SECURITY-10 supply chain | Enforced | digest-pinned default image, no `latest`, explicit override only (BR-5.4) |
| SECURITY-11 secure design | Enforced | defense in depth: kernel-level sandboxing + git guard + env scrub; misuse scenario (git alias/bypass) documented in BR-6.5 |
| SECURITY-12 credential management | Enforced | no hardcoded credentials; no secrets into sandbox env (BR-9.2) |
| SECURITY-13 integrity verification | Enforced | image digest verification on pull (BR-5.4) |
| SECURITY-15 fail-safe defaults | Enforced | fail closed (BR-8), resource cleanup on error paths (BR-8.3) |
| SECURITY-01 encryption at rest | N/A - library, no data stores | - |
| SECURITY-02 network intermediary logging | N/A - no network intermediaries | - |
| SECURITY-04 HTTP headers | N/A - no web endpoints | - |
| SECURITY-08 access control | N/A - no application endpoints | - |
| SECURITY-14 alerting/monitoring | N/A - desktop library, no deployed monitoring infra; sandbox audit log covers observability per NFR-3 | - |

## Amendment (2026-10-19) - loop-mechanics hardening (post-infra review)

- **BR-5**: mount set narrowed to EXACTLY {workspace}; isolation state_dir is host-only and NEVER mounted (Q1). Sandboxed writes are restricted to the workspace mount.
- **BR-11**: reinforced - audit records are written host-side only, and the sandbox has no path to the audit log (not mounted); forging is impossible, not just discouraged by convention.
- **BR-13**: setup runs fresh at every step entry (agent/command steps) in a one-shot provision container that targets the per-run export; no stamping, no `--refresh`. Steps without `setup:` trigger nothing.
- **BR-7**: git-hook residual RECLASSIFIED accepted → mitigated: the trusted commit action runs git with `-c core.hooksPath=<trusted dir>` (Q3); agent-influenced `.git/hooks` never execute host-side.
- **BR-1.3/BR-2**: execution container created + started at startup (eager lifecycle, Q2); mid-run failure = command result or daemon death (AE-SBX-012).
- **NEW**: PipelineConfig.state_dir default relocates to `~/.agentic-dev-loop/runs/<pipeline-hash>/` - checkpoint DB + last-error.md out of the sandbox; explicit configs keep working (author accepts exposure if they point it inside the workspace).

## Amendment (2026-10-19) - package rename

- Package references updated `agent-engine` → `agentic-dev-loop` throughout (module paths, default image `ghcr.io/lukedawilson/agentic-dev-loop/dev`, env var `AGENTIC_DEV_LOOP_DEV_IMAGE`, user state dirs `~/.agentic-dev-loop/*`). No design change.
