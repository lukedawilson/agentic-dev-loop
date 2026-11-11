# Setup and troubleshooting

The agentic-dev-loop sandbox is **always on**: the agent `terminal`
tool and command steps run inside a Docker-based, kernel-enforced boundary.
There is no opt-out - the `isolation:` section configures the sandbox, it never
disables it. This guide covers the host-side setup: the Docker engine, the
dev image, caching, troubleshooting, and the security model. The
`pipeline.yaml` contract lives in the
[pipeline reference](pipeline.md).

## 1. Prerequisites

Install a compliant Docker engine with **Engine API >= 1.43** (Engine 24):

- **macOS** - [Docker Desktop](https://www.docker.com/products/docker-desktop/).
- **Windows** - WSL2 with an Ubuntu distro. Enable Docker Desktop's WSL
  integration (or run Docker Engine inside the distro) and keep the repo on
  the WSL filesystem (`~/dev/...`), not `/mnt/c`: bind-mounting a 9p path
  into containers breaks permissions and is slow. Everything else is the
  plain Linux setup.
- **Linux** - Docker Engine, [OrbStack](https://orbstack.dev/),
  [Colima](https://github.com/abiosoft/colima), or a podman docker-compat
  machine.

Verify the engine:

```bash
docker run --rm hello-world
docker version --format '{{.Server.APIVersion}}'   # must be >= 1.43
docker info --format '{{.OSType}}'                 # must be "linux"
```

If the API is older, or `OSType` is not `linux`, the pipeline refuses to run
with a fail-fast message pointing here.

## 2. First run

On first run the pipeline probes the daemon, then pulls the **digest-pinned**
dev image (`ghcr.io/lukedawilson/agentic-dev-loop/dev`) and verifies its
digest - the supply-chain check refuses to continue on a mismatch. Later runs
reuse the local image cache; the pull is retried (bounded) only on
connection/timeout errors, never on auth/not-found.

If the pinned digest constant is empty, startup fails fast with `AE-SBX-005`.
For a pre-publish dev loop, point the
engine at any image (tag or digest) via an environment override:

```bash
export AGENTIC_DEV_LOOP_DEV_IMAGE=ghcr.io/lukedawilson/agentic-dev-loop/dev:latest
```

The override wins over the pinned digest and over any per-pipeline
`isolation.image:` - it is the explicit, never-silent escape hatch.

## 3. Onboarding: init and doctor

A new consumer repo starts with one command:

```bash
agentic-dev-loop init --profile python        # or: --profile dotnet-node
agentic-dev-loop init --profile ./my-profile.yaml --recipe python
```

`init` detects solution files, `package.json` directories, lockfiles, README,
AGENTS.md, and a conformance script; asks only what it cannot detect; and
writes `agentic-dev-loop/pipeline.yaml`, the agent files,
`agentic-dev-loop/.engine-version`, missing premise files, gitignore
additions, and an env-file template. It refuses to overwrite an existing
`agentic-dev-loop/` directory. See [Authoring a loop](authoring.md) for what
the generated layout looks like.

`doctor` probes the host and repo before every run. Run it yourself: the CLI
never runs doctor or upgrade for you.

```bash
agentic-dev-loop doctor --fast      # per-run gate: hard failures abort
agentic-dev-loop doctor --json      # machine-readable results
```

Hard checks: engine import/version, installed version equals the
`.engine-version` pin, the pin is not behind the latest release, the engine
runtime is available with API >= 1.43 and a Linux OSType, the workspace is a
git repo, and no `.env*` files exist. Warnings: profile-contributed probes,
premise files, the LLM key env var, declared secret env vars, and
compose-service reachability. `--fast` skips checks needing more than one
quick network round trip; the pin-vs-latest check stays in.

`agentic-dev-loop upgrade` installs the latest release with pipx and rewrites
`.engine-version` to the installed version, so the host install and the
committed pin converge in one explicit action. Run it when doctor reports a
pin mismatch.

Secrets live in the host environment. A run reads the process environment by
default; pass `--env FILE` to load a host env file outside the workspace. A
named-but-missing file fails loudly, and values already set in the process
environment win over the file.

## 4. Caching

There is no stamp cache: every `setup:` hook re-runs fresh at the step
boundary (first entry, `goto dev` retries, all of it). To keep per-run warm-up
to minutes, declare toolchain stores in the top-level `caches:` list (see
[caches and sync-excludes](pipeline.md#caches-and-sync-excludes)) - the
engine resolves each to a host-side dir under
`~/.agentic-dev-loop/state/caches/<name>` and bind-mounts it rw into the
export, so a fetch re-uses already-downloaded packages instead of re-fetching
them.

Declare a neutral store per toolchain (e.g. `.nuget-packages`, `qa/.browsers`,
a node cache dir) and point the toolchain at it with its cache env var (e.g.
`NUGET_PACKAGES`, `PIP_CACHE_DIR`, `npm_config_cache`) in the declaring step's
`env:` block - the provision container runs with that step's env.

Stores are content-addressed by design, so sharing them across repos is correct
and is the point. Cache pruning is manual for now: delete the dirs under
`~/.agentic-dev-loop/state/caches/`.

## 5. Troubleshooting

Run `agentic-dev-loop doctor --fast` first: it names the likeliest host-side cause and the fix command.

Every fail-fast error points here (`AE-SBX-<NNN>`):

| Code | Cause | Fix |
| --- | --- | --- |
| AE-SBX-001 | `docker` not on PATH | install an engine (section 1) |
| AE-SBX-002 | daemon unreachable | start your engine (section 1) |
| AE-SBX-003 | engine API < 1.43 | upgrade Docker (section 1) |
| AE-SBX-004 | `OSType` != linux | switch Docker Desktop to Linux containers (section 1) |
| AE-SBX-005 | no pinned digest, no env override | set `AGENTIC_DEV_LOOP_DEV_IMAGE` (section 2) |
| AE-SBX-006 | image pull failed after retries | check connectivity/credentials (section 2) |
| AE-SBX-007 | post-pull digest mismatch | supply-chain check failed; re-pull |
| AE-SBX-008 | git mutation blocked | set `isolation.allow_git_mutations: true` (see the [pipeline reference](pipeline.md#isolation)) |
| AE-SBX-009 | `setup:` command failed | inspect its output tail; the run halts (no stamp concept) |
| AE-SBX-010 | container create/start/exec failure | inspect the docker error |
| AE-SBX-012 | daemon lost mid-run | restart the engine; cleanup is best-effort |
| AE-SBX-013 | networked container create/route failure | inspect the docker error; a step declared `network: true` but no bridge container started (see [per-stage networking](pipeline.md#per-stage-networking)) |
| AE-SBX-016 | sync-back conflict | a host edit raced a step's declared-rw path; see [sync-back conflicts](pipeline.md#sync-back-conflicts-merge_on_conflict) |
| AE-SBX-019 | sandbox infrastructure failure (daemon-side) | check `docker info` / `docker system df`; restart Docker Desktop if the VM reports I/O errors |
| AE-SBX-020 | egress proxy start/attach failed | inspect the daemon stderr tail in the message (it names the real cause, e.g. "Address already in use") |

The audit trail of every sandboxed execution is at
`~/.agentic-dev-loop/state/sandbox-audit.jsonl` (rotates at 5 MB); run state
is under `~/.agentic-dev-loop/runs/`.

The orphan-container reaper keeps a host-side registry at
`~/.agentic-dev-loop/state/container-registry.jsonl` and, at startup, removes
engine exec containers whose owning process is gone (a crashed run can no
longer leak `sleep infinity` containers).

**Linux-host note**: containers run as root inside the container, so
bind-mounted writes can land root-owned in your workspace. Recover with
`sudo chown -R $USER <workspace>`.

## 6. Security notes

This is **kernel-level sandboxing** - the host kernel enforces the boundary via
namespaces, cgroups, capability checks and seccomp-BPF syscall filters. The
execution container runs `--network none` (no egress) by default, `--read-only`
rootfs, `--cap-drop ALL`, and mounts a fresh `.git`-free export of the
workspace - writes land only in the step's declared-`rw` `mounts:`. A step that
declares `network: true` runs in a bridge-network container whose traffic is
forced through an engine-run per-policy egress proxy and reaches only its
`egress:` allowlist; network access otherwise exists only in the trusted
provision phase. The Docker socket is never mounted. The host-side `commit`
action, the LangGraph runtime, LLM calls, and the viz server stay host-side;
consumer actions run inside the sandbox like command steps.

Git hooks during the commit action are **mitigated**, not a residual risk: the
commit action runs `git -c core.hooksPath=<trusted dir>`, so repo hooks in
`.git/hooks` never execute. Residual risks: an agent can mutate setup outputs
inside the writable workspace (the git-mutation guard and the dirty-at-start
commit gate limit persistence), and an explicit in-workspace `state_dir`
choice is the author's accepted exposure.

A `network: true` command step is an agent-influence channel: an agent can
plant files (e.g. tests) that the networked command step then executes,
granting the agent indirect egress. A `network: true` agent step grants direct
egress for that stage. The networked container stays `--read-only` +
`--cap-drop ALL`, so the blast radius is data exfiltration and reachability,
not host compromise. Pipeline authors should reserve networked stages for
those that need them (QA, integration).

The full host-boundary model is in
[Architecture](architecture.md#host-boundary-security-model).

## 7. Secrets migration checklist

The `secrets:` contract lives in the
[pipeline reference](pipeline.md#secrets). When converting an existing repo:

1. Replace secret-bearing workspace files with `secrets:` refs and host env.
2. Read the values from environment variables in scripts (`qa/common.sh`), not
   from a committed `.env`.
3. Delete the `.env` file - the run refuses to start while it exists.
4. Add the `secrets:` block and the per-step `secrets: [...]` opt-in.
5. Run `--dry-run` (validates schema/refs) then a full run against the real
   service with no `.env` in the workspace.
6. Prefer a mechanical `command` step for secret-consuming work:
   the LLM and the secret never meet. Reserve `agent` + `secrets` for steps
   whose job genuinely requires the agent to exercise them.
