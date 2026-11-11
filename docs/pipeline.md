# pipeline.yaml reference

One file declares the whole loop: the stage graph, the LLM connection,
per-step isolation, secrets, and the commit policy. `agentic-dev-loop init`
scaffolds a working copy; this page is the complete reference.

## Full example

```yaml
name: my-construction-loop
extensions:
  - agentic_dev_loop.engine.extensions.aidlc:AidlcExtension   # bundled AI-DLC doc loader
additional_files:
  - ../README.md                                       # shared context for every agent step

llm:
  model: openai/deepseek-v4-pro
  base_url: https://api.deepseek.com
  api_key_env: OPENAI_API_KEY
  # reasoning_effort: medium                        # optional: low | medium | high | xhigh | none

# Named LLM profiles: a step names one with `llm: <name>` to move the whole
# profile (model, endpoint, key, effort default) together. The `llm:` block
# above remains the default.
# llms:
#   review:
#     model: openai/deepseek-v4-pro
#     base_url: https://api.deepseek.com
#     api_key_env: REVIEW_API_KEY
#     reasoning_effort: xhigh

max_attempts: 10                                        # CLI --max-attempts overrides
agents_dir: sdk_agents                                  # your <name>.agent.md files

steps:
  - name: brief
    reasoning_effort: medium
    brief_inputs:
      include: [AGENTS.md, coding-standards-ddd.yaml]
      sections: [Repo rules digest, Unit scope digest, Environment facts, Open risks]
      env_facts: ["the workspace is an export with no .git; verify with find/ls, never git status"]
    on_pass: dev

  - name: dev
    agent: dev
    produces: implementation-summary.md
    terminal_timeout: 900
    # forbidden_commands: ["npm install", "npm ci"]      # per-step tool deny-list; engine defaults are git mutations + docker
    on_pass: test

  - name: test
    command: .venv-sandbox/bin/python -m pytest tests/ -q -m "not docker"
    setup: python -m venv .venv-sandbox && .venv-sandbox/bin/pip install -e ".[dev]"
    timeout: 600
    on_pass: review
    on_fail: { goto: dev, retry: true }

  - name: review
    agent: review
    artifact: review-findings.md
    verdicts: { pass: APPROVED, fail: NEEDS CHANGES }
    # reasoning_effort: xhigh                        # per-step override of the effective profile's effort
    # llm: review                                    # select a named `llms:` profile for this step
    on_pass: qa
    on_fail: { goto: dev, retry: true }

  - name: qa
    agent: qa
    artifact: qa-report.md
    verdicts: { pass: PASS, fail: FAIL }
    max_iterations: 100
    finalize_nudge_turns: 10
    on_pass: commit
    on_fail: { goto: dev, retry: true }

  - name: commit
    action: commit
    params: { message: "feat: construct {subject} (agent dev loop)" }
```

## Field reference

| Key | Notes |
| --- | --- |
| `agent:` / `command:` / `action:` / `brief_inputs:` | Exactly one per step. Agents live in `agents_dir` as `<name>.agent.md`; commands run sandboxed (mechanical, never an LLM call). `commit` is the sole host-side built-in action; consumer actions are sandbox-executed scripts (see [host boundary](architecture.md#host-boundary-security-model)). |
| `produces:` / `artifact:` + `verdicts:` | Hygiene-tracked outputs vs structured-verdict routing. The verdict comes from a validated `record_verdict` tool call, never from prose; stale artifacts are deleted at each attempt start. |
| `brief_inputs:` | A native brief step: `{ include: [<paths/globs>], env_facts: [<strings>], sections: [<headings>] }`. It digests the included docs into an engine-owned `attempt-brief.md` that later agents read, and re-runs only when its inputs change. |
| `on_pass:` / `on_fail:` | A step name, terminal `success`/`failure`, or `{goto: X, retry: true}`. No loops happen unless you write them. |
| `env:` / `setup:` / `network:` | `env:` declares step vars (fixed safe base + declared vars only); `setup:` runs fresh at every step boundary in a one-shot provision container targeting the export (command or agent steps; network ON); `network: true` grants egress scoped by `egress:` (default off) - see [per-stage networking](#per-stage-networking). |
| `mounts:` / `egress:` | Per-step workspace isolation. `mounts:` lists `{path, mode}` entries (`rw`/`ro`, default `ro`), strict-by-default: empty/missing mounts nothing, and only declared-rw paths sync back to the repo. `egress:` names the hosts a networked step may reach (`{class, presets, targets}`). See [mounts and egress](#mounts-and-egress). |
| `secrets:` (top-level) / step `secrets:` | Declares secret **refs only - never values**; each step opts in to the refs it needs. Resolved host-side and injected into that step's exec env only. See [secrets](#secrets). |
| `profile:` (top-level) | Names a bundled toolchain profile (`python`, `dotnet-node`) or a consumer profile file path, merged at load time: isolation key-wise with the pipeline winning, caches unioned profile-first. |
| `isolation:` (optional) | Configures the sandbox - image override, resource caps, `allow_git_mutations`, `seccomp_profile`. It can never disable it. See [isolation](#isolation). |
| `terminal_timeout:` | Optional agent-step per-action timeout in seconds; overrides the SDK tool default. |
| `finalize_nudge_turns:` | Optional agent-step final-response nudge; requires `max_iterations:` and must be less than it. |
| `deps_policy:` / `lockfiles:` | `locked` (default) or `open`. The human-owned lockfile is the source of truth under `locked`; see [dependency policy](#dependency-policy-locked-by-default). |
| `caches:` / `sync_excludes:` | Top-level run-wide lists. `caches:` names toolchain cache dirs bind-mounted rw from host-side stores (a `setup:` hook can warm them); `sync_excludes:` names non-gitignored scratch paths skipped from export and sync-back. |
| `merge_on_conflict:` | Top-level list of append-style logs. A concurrent host edit to a listed path is union-merged instead of raising; see [sync-back conflicts](#sync-back-conflicts-merge_on_conflict). |
| `llms:` (top-level) / step `llm:` / `reasoning_effort:` | Named LLM profiles, each a full `LlmConfig`; a step selects one whole with `llm: <name>`. `reasoning_effort` (`low`/`medium`/`high`/`xhigh`/`none`) is opt-in on the `llm` block, a profile, or a step. See [reasoning effort](#reasoning-effort-opt-in). |

## Isolation

The `isolation:` section is optional (opinionated defaults apply when
absent):

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
  branch/tag create/delete, rebase); reads (`status`/`log`/`diff`) always
  work. `allow_git_mutations: true` re-allows them per-pipeline.
- **`state_dir` (run state)** defaults to
  `~/.agentic-dev-loop/runs/<pipeline-hash>/` - the checkpoint DB and
  `last-error.md` live there, **outside** the workspace mount, unreachable by
  the sandbox. An explicit `state_dir:` keeps working unchanged; pointing it
  inside the workspace is an author-accepted exposure.

## Mounts and egress

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

## Per-stage networking

The sandbox is network-off by default. A step that legitimately needs egress
declares `network: true` and names exactly what it may reach:

```yaml
steps:
  - name: fetch-docs
    command: curl -fsSL https://example.com/spec.json -o spec.json
    mounts: [{ path: ".", mode: rw }]      # the cwd + a place to write spec.json
    network: true                          # egress for this step only
    egress: { targets: [example.com] }     # ...and only these hosts

  - name: restore
    command: .venv-sandbox/bin/pip install -e ".[dev]"
    mounts: [{ path: ".", mode: rw }]
    network: true
    egress: { presets: [package-registries] }   # pypi/npm/NuGet/cargo + VCS
```

- `network: true` is scoped by the `egress:` allowlist, structurally: the
  step's traffic is forced through a containerized egress proxy on a per-run
  internal docker network with no route to the host, and a request to an
  undeclared host is denied and logged. `egress: { class: browser }` is the
  explicit broad-but-logged class (every connection logged, nothing
  filtered); otherwise the allowlist is strict.
- A networked step that writes output must also declare a writable mount:
  with no `mounts:` a step mounts nothing, so `curl ... -o spec.json` has no
  writable cwd.
- Host services are reachable through the proxy's `host.docker.internal`
  host-gateway alias, so an S3 SDK against a local MinIO works through the
  enforced, logged path. Sandbox containers have no host route.
- Command, agent, and sandbox-action steps may declare it; the host-side
  `commit` action never takes it. Author opt-in is the control - reserve it
  for stages that need it (QA, integration).

## Caches and sync-excludes

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

## Sync-back conflicts (`merge_on_conflict`)

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

## Dependency policy (locked by default)

The supply-chain dial is a pipeline-level `deps_policy:` field, **locked by
default**. The engine injects the policy into every agent step's context:

```yaml
deps_policy: locked    # or: open    (default: locked)
lockfiles: [uv.lock]   # or: [packages.lock.json, package-lock.json]
```

- **`locked`** - the human-owned lockfile is the source of truth; the agent
  may not add dependencies. If the design requires a missing dependency, the
  agent declares it in its final response under `## Missing dependencies`,
  which fails the run fast with one human lockfile edit - not a retry loop.
- **`open`** - the agent may add exact-pinned manifest entries directly
  (never a package-manager `add` command); the network-on restore step
  fetches them.

A `locked` pipeline with no `lockfiles:` entry fails fast at load. The
**restore step** runs the toolchain's locked mode and fails when the
manifest and lockfile disagree:

| Toolchain | Locked restore | Lockfile |
| --- | --- | --- |
| .NET | `dotnet restore --locked-mode` | `packages.lock.json` |
| node | `npm ci` | `package-lock.json` |
| python | `uv sync --locked` | `uv.lock` |

The restore step also fails on known-CVE findings (per-toolchain thresholds:
NuGetAudit at `error`, `npm audit --audit-level=high`, `pip-audit`). A human
generates the lockfile once, on a network machine, and commits it - the
engine never fetches on the agent's behalf.

## Banned commands (`forbidden_commands:`)

`forbidden_commands:` is a per-step list of regex patterns checked against
every agent command before any shell spawn; a match returns a structured
`[forbidden: <label>]` observation instead of running. The engine defaults
(git mutations and docker) always apply and have no disable switch; a
toolchain profile may add tool-specific rules (for example the `python`
recipe denies `pip install`, `uv add`, and `pip-compile` on agent steps).
Command steps bypass the filter by design.

## Reasoning effort (opt-in)

`reasoning_effort` is an opt-in latency dial on the pipeline `llm` block, a
named `llms:` profile, and individual agent steps. Unset means reasoning is
off - the field is forwarded as none and reasoning is stripped from requests.
A thinking model without an effort level is invalid config and fails fast
with an actionable engine error rather than a provider 400. A step's value
overrides the effective profile's value.

Recommended lane defaults (guidance only, not enforced):

| Lane | reasoning_effort | Why |
| --- | --- | --- |
| brief | medium | digest and classify, no design work |
| dev | high | the most demanding construction work |
| qa | medium | classification discipline must stay reliable |
| review | xhigh | highest stakes: a missed detection sails past the last gate into commit |

## Secrets

Secret values never belong in the pipeline yaml, the workspace, or
`state_dir`. Declare the refs at the top level and opt each step into the
refs it needs:

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

- **Resolution is host-side, once, at run start.** The `env` provider reads
  the named host environment variable. A missing variable fails fast naming
  the ref, the declaring step(s), and the provider error. There is no silent
  degradation.
- **Injection is per step.** A resolved value reaches a step's exec env only
  when that step lists the ref - the same path step `env:` uses. It never
  reaches the provision container, `state_dir`, an agent's context, or any
  other step.
- **Providers.** `env` and `file` ship. The `file` provider reads a host
  file: `key` is the file path and `json_key` selects one string field (the
  whole file content when absent). A missing file fails fast naming
  `agentic-dev-loop copilot-login`.
- **LLM keys.** The `llm:` block takes exactly one of `api_key_env` (the
  existing shape) or `api_key_secret` (a `SecretSpec`, file provider usable).
  Neither or both fails at load. The resolved LLM key joins the run's
  redaction set.
- **`.env*` ban.** Before anything else runs, the workspace root is scanned
  for `.env*` files. Any hit fails fast, naming the files, with the remedy
  text ("move these values into `secrets:` refs / host env and delete the
  files"). The convention fixtures `.env.example`, `.env.template`,
  `.env.sample` are whitelisted. The ban applies regardless of `network:`
  flags - the workspace must simply not contain secrets.
- **Output scrubbing.** Every resolved value is scrubbed from captured step
  output, retry notes, artifacts, and `state_dir` files before they are
  written. This is hygiene, not a control: the declaring step's agent has
  already seen the value in its own env.
- Prefer a mechanical `command` step for secret-consuming work so the LLM and
  the secret never meet; an `agent` step holds a secret only when its job
  genuinely requires it. See the [migration
  checklist](setup.md#7-secrets-migration-checklist) when converting an
  existing repo.

## LLM profiles

Named profiles let one pipeline carry several LLM lanes (for example a flash
model for dev and a stronger model for review). Each profile is a full
`LlmConfig`; a step opts in with `llm: <name>`, moving model, endpoint, key,
and effort default together. The top-level `llm:` block remains the default
for steps that name none. See [LLM providers](llm-providers.md) for the
provider-side setup.
