# Unit 3 Implementation Spec - kernel-level sandboxing (always-on)

Handoff document for the implementing agent. **The code generation plan is
`aidlc-docs/construction/plans/unit-3-code-generation-plan.md`** - this spec supplies the
exact technical detail for each step. Design authority lives in the approved artifacts
(`functional-design/`, `nfr-requirements/`, `infrastructure-design/`); where this spec and a
design artifact conflict, the design artifact wins and this spec is wrong - flag it, don't
silently pick one.

Work through the plan's 16 steps in order, TDD (red → green → refactor): write the failing
test first, watch it fail, implement, watch it pass.

## 1. Scope

- **Create**: `src/agentic_dev_loop/isolation.py`, `tests/test_isolation.py`,
  `tests/test_isolation_pbt.py`, `tests/test_isolation_integration.py`, `Dockerfile`,
  `.github/workflows/ci.yml`, `.github/workflows/publish-image.yml`, `docs/setup.md`,
  `aidlc-docs/construction/unit-3/code/` summaries (this dir).
- **Modify**: `src/agentic_dev_loop/{config,stages,agents,graph,cli,actions}.py` - `actions.py`
  gets exactly ONE change: the commit action gains the trusted hooksPath (§7). All other
  actions stay host-side, in-process, untouched. Also `tests/{test_config,test_stages,
  test_agents,test_cli,test_e2e_fixtures,test_graph,conftest}.py`, `COMPAT-SHIMS.md`,
  `tests/test_sdk_compat.py`, `README.md`, `agentic-dev-loop/pipeline.yaml` (this repo's own
  pipeline migrated to the sandboxed world, §7.1), `.gitignore` (add `.venv-sandbox/`).
- **Never touch**: action-step routing (`stages.make_action_node` and the action registry
  stay in-process host-side); `src/agentic_dev_loop/proc.py` unit-2 behavior.

## 2. `isolation.py` - exact API surface

Single module, ordered sections: VOs → pure functions → Sandbox ABC → DockerBackend → probe
→ audit helpers. Public names below; private helpers free.

### 2.1 Value objects (dataclasses, frozen where noted)

```python
@dataclass(frozen=True)
class SandboxExecutionRequest:
    argv: list[str]              # full command, argv[0] = program
    cwd: str                     # absolute container-side path (== host mount path)
    env: dict[str, str]          # resolved sandbox env (already scrubbed)
    timeout: float | None = None # seconds; None = no limit

    def __post_init__(self) -> None:
        # invariant behaviour: argv non-empty; cwd absolute (PosixPath(cwd).is_absolute());
        # timeout None or > 0. Raise ValueError otherwise. No environment access here.

@dataclass(frozen=True)
class SandboxExecutionResult:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    backend: str                # "docker"
    timed_out: bool = False

    @property
    def succeeded(self) -> bool: return self.exit_code == 0
    @property
    def is_timeout(self) -> bool: return self.timed_out
```

### 2.2 Pure functions (no I/O, no env access - all PBT targets)

```python
def resolve_effective_profile(isolation: IsolationConfig | None) -> EffectiveProfile
    # BR-5 defaults: network_off=True, read_only_rootfs=True, writes_only_on_mounts=True,
    # drop_all_capabilities=True, git_mutations_blocked=not isolation.allow_git_mutations,
    # image=isolation.image or DEFAULT_DEV_IMAGE_REF, caps from isolation or defaults.
    # EffectiveProfile = frozen dataclass.

def resolve_sandbox_env(step_env: dict[str, str | None] | None, *, host_env=None) -> dict[str, str]
    # Fixed base ONLY: PATH=/usr/local/bin:/usr/bin:/bin, HOME=/root,
    # LANG=C.UTF-8, LC_ALL=C.UTF-8, TERM=xterm.
    # Then declared entries: value None → inherit from host_env (os.environ if omitted);
    # non-None → use as-is. Global env_allowlist NEVER consulted; LLM key NEVER auto-added.

def is_git_mutation(argv: list[str]) -> bool
    # Only classifies git invocations; returns False for non-git programs.
    # argv[0] basename in {"git", "git.exe"} → inspect argv[1:], skipping global flags
    # (-C <dir>, -c <k=v>, --git-dir=<d>, --work-tree=<d>, --no-pager, --version).
    # KNOWN-READ subcommands (always allowed): status, log, diff, show, ls-files,
    # rev-parse, blame, describe, for-each-ref, branch/tag WITHOUT create/delete/-d/-D/-m,
    # remote -v/show only, --help, help. EVERYTHING ELSE - including unknown
    # subcommands - is a mutation (deny-by-default, BR-6). Mutating set per FD-3c:
    # commit, push, reset, branch/tag create/delete/rename, stash, merge, rebase,
    # cherry-pick, revert, worktree add, clean, gc, fetch, pull, am, apply, checkout/switch
    # (worktree-mutating), rm, mv, init, config --local.
    # NOTE: classifier is policy convenience, NOT the security boundary (the container is).

def translate_mount_path(path: str) -> str
    # expanduser + absolute resolution (no symlink chasing). Identity on macOS/Windows/
    # WSL2-with-Docker-Desktop (verbatim passthrough - Docker Desktop translates).
    # Idempotent: translate(translate(p)) == translate(p). WSL2 edge cases deferred.

def docker_create_payload(*, run_id, image, profile, workspace) -> list[str]
    # Exact list for `docker create` (template §3.3) - execution container.
    # Invariants (PBT-03): contains --network none; contains --cap-drop ALL; the mount
    # set is EXACTLY {workspace} (single mount - iso state dir is host-only, Q1); caps
    # from profile (4g/2cpu defaults).

def docker_provision_payload(*, run_id, image, workspace, setup_cmd) -> list[str]
    # Exact list for `docker run --rm` provision container (template §3.4).
    # Network ON (default bridge), rw rootfs, --cap-drop ALL, 8g/4cpu defaults.
    # Mount set EXACTLY {workspace} (Q1).

def select_backend(platform: str, probe_result: ProbeResult) -> type[Sandbox]
    # Totality: exactly one backend for every (platform, probe_result) input - DockerBackend.
    # Retained as the seam where a future native/gVisor backend plugs in.
```

### 2.3 Sandbox ABC + DockerBackend

```python
class Sandbox(ABC):
    @abstractmethod
    def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult: ...
    @abstractmethod
    def close(self) -> None: ...   # idempotent; destroys execution container if any

class DockerBackend(Sandbox):
    def __init__(self, *, image: str, workspace: str, iso_state_dir: str,
                 profile: EffectiveProfile, runner: CommandRunner | None = None): ...
    # runner: injected callable (default = subprocess-based real runner). This is the
    # seam unit tests use - NOT monkeypatching. CommandRunner = Protocol:
    #   run(argv: list[str], *, timeout: float | None, check: bool = False) -> CmdResult
    # CmdResult = dataclass(exit_code, stdout, stderr).

    def ensure_image(self) -> None        # template §3.2, bounded retry + digest verify
    def provision(self, setup_cmd: str, *, env: dict[str, str] | None = None) -> None
    def run(self, request) -> SandboxExecutionResult   # git guard → lazy create → exec
    def close(self) -> None               # stop -t 5 then rm -f, best-effort both

def probe_docker(runner: CommandRunner | None = None) -> ProbeResult
    # shutil.which("docker") → AE-SBX-001 if missing.
    # `docker version --format {{.Server.APIVersion}}` → AE-SBX-002 if unreachable,
    # AE-SBX-003 if < 1.43 (Engine 24).
    # `docker info --format {{.OSType}}` → must be "linux"; AE-SBX-004 otherwise.
    # ProbeResult = dataclass(api_version, ostype).
```

### 2.4 Audit

```python
def append_audit_record(state_dir: Path, record: dict) -> None
    # JSONL, one line per record, appended to sandbox-audit.jsonl.
    # Record keys: timestamp (ISO-8601 UTC), pipeline, step, request_digest
    # (sha256 of json [argv, cwd, sorted env KEYS - never values]), backend,
    # container_id, exit_code, timed_out, duration_seconds.
    # Rotation: if size > 5 MB → os.replace to sandbox-audit.jsonl.1 (overwrite), start fresh.
    # Host-side only; never called from inside the sandbox.
```

052: the stamp helpers (`read_stamp`/`write_stamp`/`clean_stamps`) and `setup_stamp_key`
were deleted - setup runs fresh at the step boundary with no stamping.

### 2.5 Constants

```python
DEFAULT_DEV_IMAGE = "ghcr.io/lukedawilson/agentic-dev-loop/dev"
DEFAULT_DEV_IMAGE_REF = ""   # digest-pinned; filled after first publish (KD-2), e.g.
                             # "ghcr.io/lukedawilson/agentic-dev-loop/dev@sha256:..."
DEFAULT_EXEC_MEMORY, DEFAULT_EXEC_CPUS = "4g", 2.0
DEFAULT_PROVISION_MEMORY, DEFAULT_PROVISION_CPUS = "8g", 4.0
ENGINE_API_FLOOR = "1.43"
SANDBOX_ENV_BASE = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/root",
                    "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TERM": "xterm"}
```

Image ref resolution at runtime: `AGENTIC_DEV_LOOP_DEV_IMAGE` env override (tag OR digest) →
else `DEFAULT_DEV_IMAGE_REF` → if empty string, **fail fast AE-SBX-005** (no silent fallback).
Once pinned, pull is `docker pull <image>@<digest>`; an env-override TAG also pulls by tag.
Timeout exit code convention: `timeout(1)` inside the container exits **124**.

## 3. Docker command templates (exact argv)

### 3.1 Probe
```
docker version --format {{.Server.APIVersion}}
docker info --format {{.OSType}}
```

### 3.2 Image ensure (bounded retry, idempotent only)
```
docker image inspect <ref> --format {{.Id}}          # present?
docker pull <ref>                                     # retry ≤3, backoff 2/4/8s,
                                                     # ONLY on connection/timeout errors -
                                                     # never on auth/not-found. Never retry exec.
docker image inspect <ref> --format {{.RepoDigests}}  # post-pull digest verify (AE-SBX-007)
```

### 3.3 Execution container (`docker create`, KD-8)
```
docker create --name <run_id>-exec \
  --network none --read-only --cap-drop ALL \
  --memory <profile.memory> --cpus <profile.cpus> \
  --tmpfs /tmp --tmpfs /root \
  -v <workspace_abs>:<workspace_abs> \
  --workdir <workspace_abs> \
  <image_ref> sleep infinity
```
EAGER (Q2): created AND started at pipeline startup (`Runtime.__init__`), before the
graph builds - never lazily. `run_id` = uuid4 hex. Mount set is exactly `{workspace}`:
audit JSONL, and the runs/checkpoint state dir are host-only, unreachable from
inside (Q1).

### 3.4 Exec (per request)
```
docker exec -e K=V ... -w <workspace_abs> <container> \
  /usr/bin/timeout <timeout_s> <argv...>
```
- Timeout present → prefix `/usr/bin/timeout <ceil>` (dev image MUST ship coreutils `timeout`; Dockerfile §6).
- No timeout → argv verbatim. Exit code 124 → `timed_out=True`.
- stdout/stderr captured separately; no `-i`/`-t`.

### 3.5 Provision (`docker run --rm`)
```
docker run --rm --name <run_id>-provision --network bridge --cap-drop ALL \
  --memory 8g --cpus 4 \
  -v <workspace_abs>:<workspace_abs> \
  --workdir <workspace_abs> \
  <image_ref> sh -c "<setup_cmd>"
```
Network is the container default bridge (no `--network none`). Rootfs rw. `--rm` = destroy.
Mount set = export root rw + host-side cache mounts (052); workdir = export root; env = the
declaring step's `env:` block. Setup output echoed via host logger; nonzero exit → AE-SBX-009.
STEP BOUNDARY (052): a step's `setup:` runs fresh, once per step entry, in this one-shot
container - never at startup, no stamping.

### 3.6 Destroy (close / error paths)
```
docker stop -t 5 <container>   # best-effort, ignore "No such container"
docker rm -f <container>       # best-effort
```

## 4. Config schema (`config.py`)

```python
class IsolationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")     # unknown keys FAIL load (BR-5)
    image: str | None = None                      # per-pipeline override (KD-2 ref rules)
    memory: str | None = None                     # override exec container memory
    cpus: float | None = None                     # override exec container cpus
    allow_git_mutations: bool = False             # BR-6 knob
    state_dir: str = "~/.agentic-dev-loop/state"      # host-side caches + audit. HOST-ONLY:
    # this dir is NEVER mounted into the sandbox (Q1). Not run state.
    # validators: memory matches ^\d+[kmg]$; cpus > 0; state_dir expanduser at use.
    # 052: `refresh` was removed (setup runs fresh at the step boundary) - loading it fails
    # loud with a migration message.

class PipelineConfig(BaseModel):                  # line 117 - ADD:
    isolation: IsolationConfig | None = None      # None ⇒ defaults (always-on regardless)

    # Q1 - state_dir default RELOCATION (existing field, line 125): default changes from
    # ".pr" to "~/.agentic-dev-loop/runs/<pipeline-hash>/" where pipeline-hash = sha256 hex
    # (first 12 chars) of the absolute pipeline-file path. Checkpoint DB + last-error.md
    # therefore live OUTSIDE the workspace mount - unreachable by the sandbox.
    # Explicit `state_dir:` configs keep working unchanged (backward compatible); authors
    # who explicitly point state_dir inside their workspace accept the exposure (noted in
    # docs/setup.md §5). This repo's own pipeline drops its explicit `.pr` (§7.1).

class StepConfig(BaseModel):                      # line 35 - ADD:
    env: dict[str, str | None] | None = None      # NFR-1; null = inherit host value
    setup: str | None = None                      # BR-13; model_validator: allowed ONLY on
                                                  # command and agent steps (brief/action ⇒
                                                  # ValueError at load). Also reject on command
                                                  # steps combined with on_pass/on_fail verdicts? NO -
                                                  # verdicts allowed; setup failure = loud step
                                                  # failure (AE-SBX-009), retries per existing loop.
```
Serialisation round-trip (NFR-6): `IsolationConfig` + `StepConfig` must survive
`model_dump()` → `model_validate()` (PBT-02 target).

## 5. Error message catalog

Every message is `AE-SBX-<NNN>: <what happened>. <action>` - action always points at
`docs/setup.md` (ID-7).

| Code | Trigger | Message core |
| --- | --- | --- |
| AE-SBX-001 | `docker` not on PATH | install a Docker engine - docs/setup.md §1 |
| AE-SBX-002 | daemon unreachable | start your Docker engine - docs/setup.md §1 |
| AE-SBX-003 | API < 1.43 | found X, require ≥ 1.43 (Engine 24) - upgrade - docs/setup.md §1 |
| AE-SBX-004 | OSType ≠ linux | switch Docker Desktop to Linux containers - docs/setup.md §1 |
| AE-SBX-005 | no digest pinned AND no env override | pinned digest not yet published; set AGENTIC_DEV_LOOP_DEV_IMAGE - docs/setup.md §2 |
| AE-SBX-006 | pull failed after retries | last pull error + docs/setup.md §5 |
| AE-SBX-007 | post-pull digest mismatch | supply-chain check failed; refuse to continue |
| AE-SBX-008 | git mutation blocked | named command + `isolation.allow_git_mutations: true` - docs/setup.md §3 |
| AE-SBX-009 | setup command failed | exit code + output tail; run halts (no stamp concept) |
| AE-SBX-010 | container create/start/exec failure | docker error verbatim |
| AE-SBX-012 | daemon lost mid-run | loud failure; best-effort cleanup |

AE-SBX-011 reserved for timeouts (not an exception: `timed_out=True`, exit 124).

## 6. SDK terminal seam (`agents.py`) - VERIFY at implementation

Findings (openhands-sdk 1.17.0, site-packages): `TerminalTool.create(conv_state, ...,
executor: ToolExecutor | None = None)` accepts a pluggable executor; `ToolExecutor` ABC
(`__call__(action, conversation) -> Observation`, optional `close()`); `register_tool(name,
factory)` in `openhands.sdk.tool.registry` accepts a **callable factory returning
Sequence[ToolDefinition]**.

Implementation:

```python
from openhands.sdk.tool import ToolExecutor, TerminalTool
from openhands.sdk.tool.registry import register_tool          # VERIFY export path

class SandboxTerminalExecutor(ToolExecutor):
    def __init__(self, sandbox: Sandbox): self._sandbox = sandbox
    def __call__(self, action, conversation):
        # TerminalAction: command, is_input, timeout
        # is_input=True → unsupported under stateless exec: return TerminalObservation
        #   (command, exit_code=None, timeout=False, output="[input unsupported - stateless sandbox]")
        # Build SandboxExecutionRequest(argv=split_command(command), cwd=workspace,
        #   env=resolve_sandbox_env(step_env), timeout=action.timeout)
        # → sandbox.run → TerminalObservation(command, exit_code, timeout=result.timed_out,
        #   output=stdout+stderr). git-mutation refusals → AE-SBX-008 raised/loud.
        ...
    def close(self): pass  # sandbox closed by Runtime; idempotent

def _terminal_factory_for(sandbox) -> Callable:
    # Per-run re-registration INSIDE run_agent, BEFORE agent build/load_agent.
    # returns [TerminalTool.create(conv_state, executor=SandboxTerminalExecutor(sandbox))]
    #  - VERIFY: exact factory signature the registry invokes (params, conv_state)? and
    #    whether returning create()'d instances vs the subclass is required.
```

- Pipelines are sequential ⇒ per-run re-registration of the `terminal` name is safe.
- `COMPAT-SHIMS.md`: new entry "TerminalTool.create(executor=) + registry callable factory"
  - version range `openhands-sdk==1.17.0` (tested), removal criterion = SDK drops the kwarg
  or changes the factory convention → replace with agentic-dev-loop-owned terminal tool;
  pinning test in `test_sdk_compat.py` (inspect.signature canary per existing procedure).
- Consequence (accepted): no persistent shell session - each action is a fresh exec;
  C-c/soft-timeout semantics degrade to hard timeouts (recorded in FD).

## 7. Runtime wiring (`stages.py`, `graph.py`, `cli.py`, `actions.py`)

- `Runtime.__init__` (graph.py line ~129): **STARTUP SEQUENCE** - in order, before the
  graph builds:
  1. `probe_docker()` - fail fast AE-SBX-00x, pointing at docs/setup.md
  2. resolve image ref (KD-2 / AE-SBX-005)
  3. `DockerBackend` → `self.sandbox`; `ensure_image()` (pull at startup, retried §3.2)
  4. create + start the execution container (never lazy)
  5. materialise the export + create host-side cache dirs
  6. only then build the graph and run steps.
  052: setup no longer runs at startup. A step's `setup:` runs fresh, once per step entry,
  in a one-shot provision container (the one sanctioned mid-run infra call - §3.5); mid-run
  failure is either a step result, a setup halt (AE-SBX-009), or daemon death (AE-SBX-012).
- Destroy: `try/finally` around the pipeline run entry - `runtime.sandbox.close()` on
  completion AND every error path (BR-8).
- `stages.py _run_command(command, workspace, timeout, env=None)` (line 137): signature
  kept; body replaced for sandboxed command steps: git guard (`is_git_mutation` → AE-SBX-008
  raised, retry loop NOT consulted for it - blocked is blocked), build
  `SandboxExecutionRequest(argv=split_command(command), cwd=workspace, env=env,
  timeout=timeout)` → `runtime.sandbox.run(...)` → `(exit_code, output, timed_out)`.
- `make_command_node(runtime, step)` (line 308): env = `resolve_sandbox_env(step.env)`;
  provision runs here at the step boundary via `_provision_step` (052). Retry loop unchanged.
- `make_action_node` (line 347): routing UNCHANGED - actions (incl. commit) run
  in-process, host-side, never routed through the sandbox (guard test required).
- `actions.py` commit action - THE ONLY actions.py CHANGE (Q3): run
  `git add -A` and `git commit` with `-c core.hooksPath=<trusted>` where trusted =
  an agentic-dev-loop-managed dir (`~/.agentic-dev-loop/state/hooks` created empty, or
  `<state>/hooks`) - repo hooks in `.git/hooks` never execute. `git_worktree_clean`
  and `kill_listeners_on_port` untouched.
- `cli.py`: `_core_parser()` gains no setup-related flags (052 removed `--refresh` and
  `--clean-stamps` - setup runs fresh at the step boundary with no stamping);
  `main(argv)` probe-then-build pattern extended with the startup sequence before
  graph build.

### 7.1 This repo's own pipeline.yaml migration (user requirement, 2026-10-19)

`agentic-dev-loop/pipeline.yaml` must keep working under always-on sandboxing. Changes:

| Item | Today | Migrated |
| --- | --- | --- |
| `state_dir: .pr` (line 16) | explicit, INSIDE workspace | drop the line → new default `~/.agentic-dev-loop/runs/<hash>/` (out of mount) |
| test step command | `.venv/bin/python -m pytest tests/ -q` (HOST venv - unexecutable in the Linux container) | `.venv-sandbox/bin/python -m pytest tests/ -q -m "not docker"` (docker-marked tests run in CI, and the sandbox has no docker CLI) |
| test step `setup:` | none | `python -m venv .venv-sandbox && .venv-sandbox/bin/pip install -e ".[dev]"` (provision container: network ON, runs fresh at the step boundary targeting the export; dev/review/qa agents can also use `.venv-sandbox/bin/python` because the venv is a gitignored dir in the export) |
| `.gitignore` | has `.pr/` | keep `.pr/` (belt-and-braces if someone re-declares it) + add `.venv-sandbox/` |
| agent steps (dev/review/qa) | host terminal | sandboxed terminal automatically; git reads (status/log/diff) allowed, mutations blocked - they never commit (commit is the action) |

Fixture pipelines in tests (`tests/test_e2e_fixtures.py`, `test_graph.py`, others that
build tmp pipelines): declare an EXPLICIT `state_dir:` under `tmp_path` - tests must
never write to the real user home via the new default. Unit tests asserting the default
(`test_config.py` line 90/123, etc.) update to the new default expression.

## 8. CI + Dockerfile

`ci.yml` (on: push, pull_request):
- job matrix `os: [ubuntu-latest, windows-latest]`; setup-python 3.12; `pip install -e ".[dev]"`;
  `pytest tests/ -m "not docker"` (unit + PBT everywhere); then `pytest tests/ -m docker`:
  ubuntu - Docker preinstalled, run directly; windows - start Docker Desktop first
  (service start step), verify `docker version` before the marked tests.

`publish-image.yml` (on: workflow_dispatch, push to main when Dockerfile changed):
- docker/build-push-action → `ghcr.io/lukedawilson/agentic-dev-loop/dev:latest` + digest
  manifest; step prints the digest for pinning into `DEFAULT_DEV_IMAGE_REF` (KD-2: paste the
  digest into `isolation.py` in the same PR as the first publish).

`Dockerfile`:
```
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    git nodejs npm coreutils ca-certificates curl && rm -rf /var/lib/apt/lists/*
CMD ["sleep", "infinity"]
```
(bookworm nodejs ≈ 18; coreutils provides `timeout` required by §3.4.)

## 9. docs/setup.md - ID-7 outline (six sections)

1. **Prerequisites** - engine install per OS (Docker Desktop macOS/Windows; Docker Engine/OrbStack/Colima/podman Linux); Windows: Linux-containers mode.
2. **First run** - digest-verified pull on first use; `AGENTIC_DEV_LOOP_DEV_IMAGE` override for pre-publish dev loop; what the pinned digest means.
3. **pipeline.yaml** - walkthrough: `isolation:` (image, memory/cpus, allow_git_mutations, state_dir = host-side caches + audit), per-step `env:` (null = inherit), per-step `setup:` (command and agent steps), git-mutation default block and the knob, the new `state_dir` default (`~/.agentic-dev-loop/runs/<pipeline-hash>/` - checkpoint DB + last-error.md live there, out of the sandbox; explicit `state_dir` inside a workspace is author's accepted risk).
4. **Caching** - host-side caches (each declared `caches:` name resolves to `~/.agentic-dev-loop/state/caches/<name>`); no stamping; removed `--refresh` / `isolation.refresh` / `--clean-stamps`.
5. **Troubleshooting** - every AE-SBX message with cause + fix; audit log location (`~/.agentic-dev-loop/state/sandbox-audit.jsonl`); run-state location (`~/.agentic-dev-loop/runs/`); Linux-host note: bind-mounted writes land root-owned (KD-7), `sudo chown -R $USER` recovery.
6. **Security notes** - kernel-level sandboxing terminology (ID-8), threat model summary, residual risks (setup-output mutation inside sandbox; explicit in-workspace state_dir choice). Git hooks during commit are MITIGATED (trusted hooksPath, Q3) - not a residual.

README: add a short "Sandboxed execution" section linking `docs/setup.md`.

## 10. Test plan

| File | Scope |
| --- | --- |
| `tests/test_config.py` | IsolationConfig/StepConfig schema: accept/reject/validators; serialisation round-trip |
| `tests/test_isolation.py` | Pure functions (steps 2-3): table-driven + temp-dir fixtures; VOs (step 4); DockerBackend with fake runner (step 5): ensure_image retry/verify, EAGER create at init, exec argv assembly, exit-124 timeout, close idempotence; provision/audit incl. rotation (step 6) - real temp dirs, NO file mocking |
| `tests/test_isolation_pbt.py` | Hypothesis properties (step 10, PBT-03): `--network none` always present; the mount set is EXACTLY {workspace} (single-mount invariant, Q1); `--cap-drop ALL`; resolve_sandbox_env ⊆ base∪declared; request/result round-trip; mount idempotence; git deny-by-default (random subcommands); select_backend totality |
| `tests/test_isolation_integration.py` | `@pytest.mark.docker`: probe passes; echo round-trip; no-network (e.g. `curl`/socket fails); read-only rootfs + writable mounts; git mutation block e2e; provision at the step boundary (fresh per entry). Skip with explicit reason when daemon absent; NEVER silently skipped in CI |
| `tests/test_stages.py` | Fake Sandbox: `_run_command` routing, git-guard refusal, setup provisioned at the step boundary (fresh per entry), destroy on success/error paths, commit NEVER routed; commit action uses trusted hooksPath (`git -c core.hooksPath`) |
| `tests/test_agents.py` | SandboxTerminalExecutor against fake Sandbox; per-run re-registration; is_input degradation |
| `tests/test_cli.py` | removed `--refresh`/`--clean-stamps` flags rejected; fail-fast probe messages (faked runner) |
| `tests/test_e2e_fixtures.py` + `conftest.py` | Fake-Sandbox fixture by default; marked docker variant for real backend; fixture pipelines declare explicit `state_dir:` under tmp_path (never real home) |
| `tests/test_sdk_compat.py` | TerminalTool.create(executor=) canary + version-range pinning |
| Guard tests (Q1/Q2/Q3) | (1) recorded-runner sequence test: startup performs exactly [probe, pull/inspect, create, start] and each step entry performs at most one provision call - the one sanctioned mid-run infra call (052); (2) Runtime passes ONLY the export + cache mounts to DockerBackend (state_dir/audit paths never in any docker argv - Q1); (3) audit writes happen host-side (audit record exists even when exec fails; sandbox has no path to state dir - Q1); (4) pipeline default state_dir resolves outside the workspace (Q1); (5) commit argv contains `core.hooksPath` (Q3) |

Markers: register `docker` in pyproject (`[tool.pytest.ini_options] markers`).

## 11. Guardrails

- **Mock policy**: mocks ONLY at external boundaries - SDK classes, and the injected
  `CommandRunner` for docker CLI. Never mock file I/O, pure functions, or local utilities
  (real temp dirs).
- **No silent fallbacks**: every unreachable/missing runtime state raises AE-SBX-00x.
- **No hardcoded logic** beyond the plan; blockers get reported, not improvised.
- **TDD**: red first, every step; plan checkboxes updated same-interaction.
- **Brownfield**: modify in place; never create `*_new.py`; verify no duplicates at the end.
- **COMPAT-SHIMS**: follow `COMPAT-SHIMS.md` upgrade procedure; add the terminal-seam entry.

## Amendment (2026-10-19) - package rename

- Repo/package renamed `agent-engine` → `agentic-dev-loop` (GitHub repo `lukedawilson/agentic-dev-loop`, PyPI `agentic-dev-loop`, wheel v0.2.0).
- Applied throughout this spec: module paths `src/agent_engine/` → `src/agentic_dev_loop/`; env override `AGENT_ENGINE_DEV_IMAGE` → `AGENTIC_DEV_LOOP_DEV_IMAGE`; default image `ghcr.io/lukedawilson/agentic-dev-loop/dev`; user state dirs `~/.agent-engine/*` → `~/.agentic-dev-loop/*`; pipeline dir reference `agent-engine/pipeline.yaml` → `agentic-dev-loop/pipeline.yaml`. No design change.
