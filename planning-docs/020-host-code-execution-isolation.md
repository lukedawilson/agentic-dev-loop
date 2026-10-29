# 020 - Host-side code execution: shrink the host to the engine

> **Status: COMPLETED**

**Origin:** security review of the agentic-dev-loop sandbox boundary (2026-10-23). Of the three mapped surfaces (secret exfiltration → 019, typosquat → 018 mechanism + lockfile dial follow-up, host-side code execution → this plan), host execution has the highest severity ceiling: code running in the host process has the full user account - SSH keys, keychain, persistence, survives the run.

**Agreed decisions (user, 2026-10-23):**
1. **Action contract redesign:** consumer actions execute inside the sandbox, command-shaped; the engine ships only its trusted built-ins (commit, document loaders).
2. **Trusted extensions dir:** extensions load from a directory *outside* the workspace; the engine refuses workspace-resident extensions by default.
3. **Orchestrator install path:** the engine is installed non-editable, in a venv *outside* the workspace; a startup self-check refuses to run if the engine's own code resolves under the workspace path.
4. **The built-in `commit` action stays host-side, deliberately:** it holds push credentials; moving it into a container would trade host-exec-of-trusted-code for credential-in-sandbox. The standalone principle - *the sandbox must never hold push credentials* - is the same one 019's secrets design encodes; it stands whether or not 019 is implemented. The action's host-side blast radius is already constrained (argv-style, scrubbed env, `-c core.hooksPath=~/.agentic-dev-loop/state/hooks`).
5. **Tooling integrity check (fold-in):** at run start, tooling files must be git-clean relative to HEAD; the commit action excludes tooling paths from its sweep.

## Problem, precisely

What executes in the host process today:

- **Action steps** - `make_action_node` runs `fn(state, params)` as plain Python host-side. Built-in `commit` runs host `git`; the consumer's `ConformanceExtension` runs `subprocess.run(["bash", script])` on the host (dead-but-loaded: the consumer's conformance is already a command step).
- **Extension/tool module imports** - `load_extensions` importlib-imports `module:Class` refs resolving to workspace files, at run start, in the host process. Import *is* execution.
- **SDK tool handlers** - e.g. the Figma executor's host-side httpx GET - run in the agent runtime process (host-side by definition; the runtime is the host process).
- **The orchestration itself** - config load, graph build, routers, notes, artifacts, `state_dir` writes, sandbox provisioning, LLM calls. Irreducible: the engine creates the sandbox; it cannot live inside it.

The amplifier: **agent-writable workspace files become host-executed next run.** the consumer's `extensions/*.py` sit in the mounted workspace; both repos install the engine non-editable *inside* the workspace (`.venv` in repo root), so site-packages is agent-writable regardless of install mode (verified: agent-engine's `.venv` holds a real `agentic_dev_loop/` package dir, no `__editable__` pth - but the mount set is exactly `{workspace}`, so the sandbox can edit it).

Why "move everything to containers" is the wrong shape: the orchestrator and tool layer are host-side by definition, and the one thing that *looks* movable (commit) is deliberately not moved.

## Design

### 1. Action contract redesign

- Consumer actions become **sandbox-executed, command-shaped steps**: script + params, same network/verdict semantics as command steps, executed via the existing sandbox run path. `make_action_node`'s host-side `fn(state, params)` survives only for trusted built-ins.
- Engine ships: `commit` (host-side, decision 4), document loaders (read-only).
- the consumer consumer change: **stop registering `ConformanceExtension`** - its job is already a command step (`bash tools/run-conformance.sh`). No host-side bash path at all.
- Contract rule to enforce mechanically: an action that is not a built-in *cannot* register a host-side `fn` - the extension API only accepts the sandbox-executed form.

### 2. Trusted extensions dir

- New config: extensions resolve from a directory **outside the workspace** (e.g. `~/.agentic-dev-loop/extensions/`). `load_extensions` resolves `module:Class` refs there only.
- **Refusal-by-default:** at load time, if a resolved extension module's file lives under the workspace mount, fail fast naming the file. Same rule for tool handlers (Figma): consumer-trusted code must be agent-uneditable.
- This closes the import seam: no workspace file is ever imported by the host.

### 3. Orchestrator install path

- **Precondition:** non-editable install (both repos already are - an editable install means the executed code *is* workspace `src/`, and no location rule helps).
- **Location:** the engine venv lives outside the workspace - pipx (`~/.local/pipx/venvs/agentic-dev-loop`) as the canonical consumer path, or `~/.agentic-dev-loop/venvs/<project>/`. The sandbox mount set is exactly `{workspace}`, so an outside install is *unmounted* - no access at all, not even read.
- **Mechanical enforcement:** startup self-check - refuse to run if `agentic_dev_loop.__file__` resolves under the workspace root. Same fail-fast pattern as 019's `.env*` ban.
- Layer comparison, documented for the record: unmounted > read-only submount (possible; an `ro_paths` engine feature as defense-in-depth if a venv must stay in-workspace) > file permissions (**do not work** - the sandbox exec runs as host uid:gid, so user-owned files are container-writable) ; editable installs bypass everything.

### 4. Built-in `commit` stays host-side (decision record)

Push credentials stay out of the sandbox. Existing constraints stand: argv-style subprocess (no shell), scrubbed child env, `-c core.hooksPath=~/.agentic-dev-loop/state/hooks` so repo hooks never execute, dirty-worktree loud skip.

### 5. Tooling integrity check

- At run start: `pipeline.yaml`, `sdk_agents/`, `additional_files`, `aidlc-docs/`, `tools/` must be git-clean relative to HEAD - otherwise refuse, naming the dirty files.
- Closes two holes: the **self-modifying loop** (an agent rewriting the pipeline/prompts for the next run) and the **self-gate** (`tools/run-conformance.sh` is currently an agent-writable, network-on gate script).
- Commit action: **exclude tooling paths from the `git add -A` sweep** (explicit pathspec), so the loop never commits tooling changes. The integrity check runs against HEAD, so the exclusion must be consistent with it.

## Dependencies / sequencing

**Self-contained:** no work item or verification step in this plan requires 019's code. The only overlap is shared principles (secrets out of the sandbox) and the cross-ref map below. The two plans can land independently.

## Workspace-narrowing map (cross-refs, not this unit's scope)

- `.git/` exclusion from sandbox mounts (worktree export; sandbox needs source + tests, not history) → **IP-exfiltration unit**.
- `.env*` ban → **019** (planned; the ban lands with that plan, not this one).
- Caches (`.nuget-packages/`, `.venv-sandbox/`, `qa/.browsers/`, `node_modules/`) **stay in the workspace** - relocation doesn't remove sandbox access, it only splits the mount set; the control for what's *inside* caches is 018's lockfile dial.

## Net picture after this plan

- **Host executes nothing from the workspace**: venv + extensions outside; consumer actions sandboxed; commit is the sole host-side exec and is built-in.
- **Host reads only git-clean control files.**
- **Sandbox sees the worktree minus `.git` and secrets** (future units).
- The only agent-writable, host-consumed thing left is the code the loop is building - which is the point.

## Work items

1. **Engine - action API:** sandbox-executed consumer-action form (script + params, command-step semantics); `make_action_node` restricted to trusted built-ins; validation rejects host-side `fn` registrations from non-built-ins.
2. **Engine - extensions dir:** config field; resolution outside the workspace; fail-fast on workspace-resident extension/tool module files.
3. **Engine - orchestrator self-check:** startup refusal when `agentic_dev_loop.__file__` resolves under the workspace root.
4. **Engine - tooling integrity check:** git-clean check on tooling paths at run start (fail fast naming files) + commit sweep excluding tooling paths.
5. **Consumer repo:** stop registering `conformance_action`; reinstall the engine via pipx (venv outside the workspace); move/remove `extensions/` accordingly.
6. **Consumer - agent-engine:** host runs the loop from an out-of-workspace install (pipx or moved venv); `.venv-sandbox` stays the throwaway.
7. **Tests:** unit (action contract validation, extensions-dir resolution/refusal, self-check, integrity check, commit pathspec) + docker-marked integration (a consumer action's code cannot touch the host - host-side sentinel probe; workspace-resident extension refused; dirty tooling refused; commit skips tooling).
8. **Docs:** README security model section (host boundary), pipx install path, consumer migration notes.

## Verification checklist

- [ ] A consumer action cannot create a file outside the sandbox (host sentinel probe clean).
- [ ] Workspace-resident extension file → run refused, naming the file.
- [ ] Engine installed in-workspace (or editable) → run refused, naming the path.
- [ ] Tooling file modified vs HEAD → run refused, naming the file; the loop never commits tooling paths.
- [ ] `commit` action still works with tooling paths excluded.
- [ ] the consumer full run green with the conformance extension unregistered and the engine installed outside the workspace.

## Out of scope (separate units)

- Workspace IP exfiltration: mount granularity (`.git` exclusion), egress policy.
- Secrets store + `.env*` ban → 019.
- Typosquat/supply-chain lockfile dial → 018 follow-up.
