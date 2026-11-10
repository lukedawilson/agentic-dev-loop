# Sandboxed execution setup

The agentic-dev-loop sandbox is **always on**: the agent `terminal`
tool and command steps run inside a Docker-based, kernel-enforced boundary.
There is no opt-out - the `isolation:` section configures the sandbox, it never
disables it. This guide covers the three moving parts: the Docker engine, the
dev image, and the `pipeline.yaml` contract.

## 1. Prerequisites

Install a compliant Docker engine with **Engine API ≥ 1.43** (Engine 24):

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

## Onboarding: init and doctor

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
`agentic-dev-loop/` directory.

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

## 3. Writing pipeline.yaml

The `isolation:` section is optional (opinionated defaults apply when absent):

```yaml
isolation:
  image: ghcr.io/org/custom-dev@sha256:...  # override (digest required)
  memory: 4g              # execution-container memory cap
  cpus: 2                 # execution-container cpu cap
  allow_git_mutations: false
  state_dir: ~/.agentic-dev-loop/state   # host-side caches + audit (never mounted)
```

- **`env:` (per step)** - `VAR: value` sets it in the sandbox; `VAR: null`
  inherits the host value. The sandbox env is the fixed safe base
  (`PATH`/`HOME`/`LANG`/`LC_ALL`/`TERM`) **plus declared vars only** - the
  global `env_allowlist` never leaks in and the LLM key is never auto-added.
- **`setup:` (command and agent steps)** - a trusted author command (e.g.
  `python -m venv .venv-sandbox && .venv-sandbox/bin/pip install -e ".[dev]"`)
  that runs fresh at the step boundary in a **one-shot provision container
  with network ON**, before the step's first turn, targeting the per-run
  export (never the real workspace). Setup outputs land in the export and are
  visible to the step's exec container immediately. Steps without `setup:`
  trigger nothing; brief and action steps reject `setup:` at load.
- **`network:` (command, agent, and sandbox-action steps)** - `network: true`
  runs the step in a bridge-network container whose traffic is forced through
  the engine-run per-policy egress proxy and reaches only the step's `egress:`
  allowlist (`--read-only`, `--cap-drop ALL`). Default is `false` - every step
  stays network-off unless it opts in. The host-side built-in `commit` action
  rejects `network:` (refused once the registry is known).
- **Git mutations** are blocked by default inside the sandbox (commit, push,
  branch/tag create/delete, rebase, …); reads (`status`/`log`/`diff`) always
  work. `allow_git_mutations: true` re-allows them per-pipeline.
- **`state_dir` (run state)** defaults to
  `~/.agentic-dev-loop/runs/<pipeline-hash>/` - the checkpoint DB and
  `last-error.md` live there, **outside** the workspace mount, unreachable by
  the sandbox. An explicit `state_dir:` keeps working unchanged; pointing it
  inside the workspace is an author-accepted exposure.
- **`deps_policy` / `lockfiles`** - `deps_policy: locked` (default) makes the
  human-owned lockfile the source of truth and injects the canonical
  dependency rule into every agent step; a non-empty `## Missing dependencies`
  declaration in an agent's final response fails the run fast. `deps_policy:
  open` injects the bootstrap protocol instead. `lockfiles:` names the
  lockfile(s) (workspace-relative) the rule and fail-fast reference - e.g.
  `uv.lock` (python), `package-lock.json` (node), `packages.lock.json` (.NET).
  A `locked` pipeline with no `lockfiles:` entry fails fast at load. The
  restore step runs the toolchain's locked mode (`uv sync --locked`, `npm
  ci`, or `dotnet restore --locked-mode`) and fails on known-CVE findings
  at a per-toolchain threshold (`pip-audit`, `npm audit --audit-level=high`,
  NuGetAudit at `error`).

### Mounts and egress

Per-step workspace isolation: a step mounts only what its `mounts:` declares
(strict-by-default - empty/missing `mounts:` mounts nothing), and a
`network: true` step reaches only the hosts its `egress:` allowlist names.

```yaml
steps:
  - name: dev
    agent: dev
    mounts: [{path: ".", mode: rw}]          # source + tests rw for this step

  - name: restore
    command: .venv-sandbox/bin/pip install -e ".[dev]"
    network: true
    mounts: [{path: ".venv-sandbox", mode: rw}]
    egress: { presets: [package-registries] }   # pypi/npm/NuGet/cargo + VCS

  - name: review
    agent: review
    mounts: [{path: ".", mode: ro}]          # read-only review
```

- **`mounts:`** - a list of `{path, mode}` entries. `path` is repo-relative
  (no absolute paths, no `..` traversal, never `.git` or a host-only dir, and
  never `state_dir`); `mode` is `rw` or `ro` (default `ro`). Strict-by-default:
  an empty/missing `mounts:` mounts nothing. A `.` entry mounts the whole
  export with its mode; subpath entries mount the export root read-only and add
  a writable submount for each declared-`rw` path (declared-writes). Only
  declared-rw paths sync back to the real repo after the step. A malformed
  declaration fails loudly at load, naming the step.
- **`egress:`** - `{class, presets, targets}`. `class: browser` is the
  explicit broad-but-logged browser class (every connection logged, nothing
  filtered); the default is a strict allowlist. `presets:` references an
  engine-shipped preset (`package-registries` today); `targets:` adds explicit
  `host`/`host:port` entries. An allowlist miss denies the connection at the
  engine-run egress proxy and logs an enforcement event. The engine never adds
  an undeclared target, and an unknown preset or malformed target fails load.
- **`network:`** routes through a per-policy egress proxy (the container
  is pointed at it via `HTTP_PROXY`/`HTTPS_PROXY` and reaches it through the
  `host.docker.internal` host-gateway mapping); it does not mean
  unrestricted egress. The host-side built-in `commit` action rejects
  `mounts:`/`egress:`/`network:` (refused once the registry is known).

### Caches and sync-excludes

Two top-level, run-wide lists declare which host-side stores are toolchain
caches and which paths are scratch (never copied into the export, never synced
back). Both default to `[]`.

```yaml
caches: [.nuget-packages, qa/node_modules, qa/.browsers]
sync_excludes: ["**/bin/**", "**/obj/**"]
```

- **`caches:`** - repo-relative toolchain cache dirs resolved host-side to
  `{state_dir}/caches/<name>` (content-addressed, shared across runs and
  repos) and bind-mounted rw into the export at the same relative paths. The
  host dir is created at startup (a missing workspace path is fine) and
  becomes the mount source; caches are never synced back. Validation rejects
  traversal, absolute paths, `.git`, and a step mount under a cache (the rw
  cache submount would defeat a declared `ro` mount there). A whole-tree `.`
  mount stays fine - the cache submount layers on top. Declaring a cache on a
  gitignored path (e.g. `node_modules`) is the normal, correct case.
- **`sync_excludes:`** - scratch paths. A literal entry (no wildcard) excludes
  the dir and everything under it (`qa/.artifacts`). A wildcard entry is
  `fnmatch` on the full posix relpath, and `*` spans path separators, so
  `**/bin/**` matches a literal `/bin/` segment at any depth and `*.dll`
  matches at any depth. Excluded paths are skipped in both snapshots and never
  classify as added/modified/deleted/conflict - no copy-back, no deletion
  mirroring, and no conflict from host-side build-artifact drift. They
  are also skipped at export materialisation (no bind-mount), so a step cannot
  read pre-existing excluded content: in-sandbox builds are clean builds.
  Put the reusable part (restore inputs) in `caches:` instead.
- **Artifact shadowing footgun:** a step's `artifact:`/`produces:` path that
  matches a `sync_excludes:` entry will never sync back. Load warns
  (non-fatal) naming the step and path - wildcard overlap is fuzzy, so it is
  a warning, not a hard failure.

### Sync-back conflicts and merge_on_conflict

Only declared-`rw` paths sync back to the real repo. If the host edits a
declared-`rw` path while a step is running, sync-back is a loud failure
(`AE-SBX-016`), never an overwrite. Two fixes land on top of that guard:

- **Absorb, not wedge.** The conflict is printed to the console
  (`[isolation] sync-back conflict: ...`), the host's version wins, and the
  baseline is re-snapshotted before the raise. The conflicted step's edits to
  those paths never land; the next step - even `echo hello` - syncs clean
  instead of re-raising the same conflict forever.
- **Append-style logs can opt into a union merge.** Declare them in the
  top-level `merge_on_conflict:` list:

```yaml
merge_on_conflict: [aidlc-docs/audit.md]
```

For a concurrent edit on a listed path the engine does a three-way union
merge: the host file keeps its lines, the sandbox's export-only lines are
appended in export order, and nothing is ever deleted. Both sides converge
(host and export are written identically), the baseline is re-snapshotted,
the run continues, and the console prints
`[isolation] merged concurrent edits: <path> (<n> sandbox lines appended)`.

Entries use the same vocabulary as `sync_excludes:` (literal = component
prefix; wildcard = `fnmatch` on the full posix relpath; `*` spans separators).
Single strings coerce to a one-item list. Validation rejects traversal,
absolute paths, `.git`, host-only dirs, and a literal entry under a literal
`sync_excludes:` or `caches:` entry (those paths never sync, so they can never
conflict). Declaring a non-append file here is the pipeline author's
responsibility - the merge only ever appends, it never reconciles in-place
edits.


## 4. Caching

There is no stamp cache: every `setup:` hook re-runs fresh at the step
boundary (first entry, `goto dev` retries, all of it). To keep per-run warm-up
to minutes, declare toolchain stores in the top-level `caches:` list - the
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
| AE-SBX-001 | `docker` not on PATH | install an engine (§1) |
| AE-SBX-002 | daemon unreachable | start your engine (§1) |
| AE-SBX-003 | engine API < 1.43 | upgrade Docker (§1) |
| AE-SBX-004 | `OSType` ≠ linux | switch Docker Desktop to Linux containers (§1) |
| AE-SBX-005 | no pinned digest, no env override | set `AGENTIC_DEV_LOOP_DEV_IMAGE` (§2) |
| AE-SBX-006 | image pull failed after retries | check connectivity/credentials (§2) |
| AE-SBX-007 | post-pull digest mismatch | supply-chain check failed; re-pull |
| AE-SBX-008 | git mutation blocked | set `isolation.allow_git_mutations: true` (§3) |
| AE-SBX-009 | `setup:` command failed | inspect its output tail; the run halts (no stamp concept) |
| AE-SBX-010 | container create/start/exec failure | inspect the docker error |
| AE-SBX-012 | daemon lost mid-run | restart the engine; cleanup is best-effort |
| AE-SBX-013 | networked container create/route failure | inspect the docker error; a step declared `network: true` but no bridge container started (§3) |
| AE-SBX-019 | sandbox infrastructure failure (daemon-side) | check `docker info` / `docker system df`; restart Docker Desktop if the VM reports I/O errors |
| AE-SBX-020 | egress proxy start/attach failed | inspect the daemon stderr tail in the message (it names the real cause, e.g. "Address already in use") (§3) |

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

## 7. Secrets (`secrets:`) and the `.env*` ban

Secret values are **refs in config, values only in the host**. Declare the refs
at the top of `pipeline.yaml` and opt each step into the refs it needs:

```yaml
secrets:                              # refs only - never values
  kinde-client-secret:
    from: env                         # provider name (env ships first)
    key: KINDIE_CLIENT_SECRET         # host env var to read + step env name

steps:
  - name: qa
    command: ./qa/run-against-kinde.sh
    secrets: [kinde-client-secret]
```

- **Resolution is host-side, once, at run start.** The `env` provider reads the
  named host environment variable. A missing variable fails fast naming the
  ref, the declaring step(s), and the provider error. There is no silent
  degradation.
- **Injection is per step.** A resolved value reaches a step's exec env only
  when that step lists the ref - the same `resolve_sandbox_env` path step
  `env:` uses today. It never reaches the provision container, `state_dir`, an
  agent's context, or any other step.
- **Providers.** `env` and `file` ship. The `file` provider reads a host file:
  `key` is the file path and `json_key` selects one string field (the whole
  file content when absent). A missing file fails fast naming
  `agentic-dev-loop copilot-login`.
- **LLM keys.** The `llm:` block takes exactly one of `api_key_env` (the
  existing shape) or `api_key_secret` (a `SecretSpec`, file provider usable).
  Neither or both fails at load. The resolved LLM key joins the run's
  redaction set.
- **`.env*` ban.** Before anything else runs, the workspace root is scanned for
  `.env*` files. Any hit fails fast, naming the files, with the remedy text
  ("move these values into `secrets:` refs / host env and delete the files").
  The convention fixtures `.env.example`, `.env.template`, `.env.sample` are
  whitelisted. The ban applies regardless of `network:` flags - the workspace
  must simply not contain secrets.
- **Output scrubbing.** Every resolved value is scrubbed from captured step
  output, retry notes, artifacts, and `state_dir` files before they are written.
  This is hygiene, not a control: the declaring step's agent has already seen
  the value in its own env.

### Copilot subscription endpoint

Copilot runs as an OpenAI-compatible endpoint on the existing runtime - no new
driver, no MCP, no subprocess. Log in once, then point an `llm:` block at the
endpoint:

```bash
agentic-dev-loop copilot-login    # device flow; writes the token (mode 600)
agentic-dev-loop copilot-models   # the models this token's tier can use
```

```yaml
llm:
  model: openai/claude-opus-5.5    # pro default
  base_url: https://api.githubcopilot.com
  api_key_secret:
    from: file
    key: ~/.agentic-dev-loop/auth/copilot.json
    json_key: access_token
  headers:
    Copilot-Integration-Id: vscode-chat
    Editor-Version: vscode/1.94.0
    Openai-Intent: conversation-panel
llms:
  flash:
    model: openai/claude-sonnet-5.5  # flash profile; no step effort
    base_url: https://api.githubcopilot.com
    api_key_secret:
      from: file
      key: ~/.agentic-dev-loop/auth/copilot.json
      json_key: access_token
    headers:
      Copilot-Integration-Id: vscode-chat
      Editor-Version: vscode/1.94.0
      Openai-Intent: conversation-panel
```

- Working models: `claude-opus-5.5` (pro) and `claude-sonnet-5.5` (flash),
  smoke-verified end-to-end 2026-11-08 (run bdbd5597cbd6: brief, dev, test,
  and review all pass, with real review verdicts). The gateway splits an
  Anthropic model's non-streamed chat response into multiple choices and
  the SDK once read only the narration choice, which collapsed agent steps
  to tool-less turns (planning-docs/059); the engine now merges the choices
  before the SDK reads them, so the claude pins work. Effort: opus-5.5
  accepts `medium`/`xhigh` (probe-validated); sonnet-5.5 accepts no effort
  or `medium`/`high` (probe-validated), so the dev step runs the flash
  profile with no step effort. The brief step runs the pro model at
  `medium`; the review step runs pro at `xhigh`. Also usable, previously
  probe-validated: `gpt-5.6-luna` (routes through the Responses API, which
  the SDK switches to automatically; accepts `xhigh`) and `gpt-4.1` (chat
  path, clean tool calls, but hard-400s on ANY reasoning_effort). Avoid:
  `gpt-4o-mini` (Copilot caps its chat context at 12288 tokens),
  `claude-opus-4.8` (its two-choice shape is now merged engine-side but the
  model was never re-pinned or re-probed end-to-end), and `kimi-k3`
  (the Copilot gateway silently drops the ARRAY-encoded tool results the SDK
  sends for it, leaving the agent blind).
- The `openai/` prefix on the model names is required. litellm routes a
  bare `claude-*` name through its Anthropic provider path, which speaks
  Anthropic's wire format and dies at the gateway with "missing required
  Authorization header"; a bare `kimi-k3` makes litellm refuse outright
  ("LLM Provider NOT provided"). The prefix forces the OpenAI-compatible
  route: Bearer auth, chat/completions, bare model name in the body.
- The three headers above are accepted at `/models` and
  `/chat/completions`. Do not add `X-GitHub-Api-Version: 2022-11-28`; the
  endpoint rejects it with 400 `invalid apiVersion` at both endpoints.

- The token is the Copilot subscription credential; the pinned OAuth client id
  belongs to OpenCode's app and is overridable with
  `AGENTIC_DEV_LOOP_COPILOT_CLIENT_ID`.
- A token with the old `read:user` scope is refused (401/403): delete it and
  re-run `agentic-dev-loop copilot-login`. The fail-fast names this fix.
- `model:` stays required. Confirm the models with `copilot-models` before a
  run.

### Secrets migration checklist

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
