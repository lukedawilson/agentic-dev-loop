# Startup Port Sweep + Viz Port Fallback - Implementation Plan

> **Status: COMPLETED**

> **For agentic workers:** Use bite-sized task execution to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bake the viz-server port sweep into the engine's startup (instead of a `port_sweep` pipeline step), and make `--viz-port` a **preferred** port that falls back to an OS-assigned port when busy - matching the user intent:

> "Instead of a port_sweep step, can we bake the port sweep into the agent engine run (before starting the viz server)?"

plus the two earlier design directions: (3) "instead of failing loudly, find a free port", and multi-instance safety - two agent-engine runs from **different** workspaces must coexist on one machine.

**Architecture:** Three pieces, all in-repo:

1. **cwd-scoped sweep** in `actions.py`: `_listening_pids` / `kill_listeners_on_port` gain an optional `cwd=` filter - a listener is only swept when its working directory resolves to the caller's workspace (positive-only attribution: an unreadable cwd spares the pid, never kills it).
2. **Startup sweep call** in `graph.run_pipeline`'s CLI-viz branch (graph.py:164-173), before `serve_viz`: best-effort `kill_listeners_on_port(args.viz_port, match="agent-engine", cwd=runtime.workspace)`.
3. **Busy-port fallback** in `viz.serve_viz`: on bind failure with `errno.EADDRINUSE` only, rebind port 0 (OS-assigned); any other bind error still fails loud naming `--viz-port`. `run_pipeline` builds the URL from `httpd.server_address[1]` and prints a notice when the preferred port wasn't available.

The `port_sweep` step leaves `examples/self/pipeline.yaml` (the `kill_listeners` **action** stays registered - its agent-orphaned-app-server use case is unrelated to viz); `--viz-port` default 8321 is unchanged.

**Tech Stack:** unchanged - Python ≥3.12, pytest, stdlib `socket`/`lsof`/`http.server`, no new dependencies.

## Verified facts

- **The hazard is live on this machine (2026-10-08):** pid 81855 is an agent-engine viz server holding 8321, launched from the consumer repo workspace (`ps` command contains `agent-engine`; `lsof -a -p 81855 -d cwd -Fn` reports `fcwd`/`n` for the consumer repo workspace). Consequences, all three verified: (a) any run from this repo today dies loudly on the port; (b) today's pipeline-step sweep would SIGTERM/SIGKILL that live consumer-repo run (its command line matches `agent-engine`); (c) a cwd-scoped sweep spares it - cwd differs - and the fallback hands this repo's run a free port.
- `lsof -a -p PID -d cwd -Fn` output format (macOS): `p<PID>` / `fcwd` / `n<path>` - parse the `n` line after `fcwd`.
- Console entry point is `agent-engine = "agent_engine.cli:main"` (pyproject.toml:19-20), so a listener's `ps` command line contains the literal `agent-engine` (a `python -m agent_engine` run would show `agent_engine` - same limitation as today's pipeline step; accepted).
- `serve_viz` raises `OSError` naming `--viz-port` on any bind failure (viz.py:265-270); locked in plan 001 line 17 ("Fail fast, never silently degrade") and enforced by `tests/test_viz.py::test_bind_failure_is_loud` (line 424).
- `run_pipeline`'s viz branch: graph.py:164 (`if bus is None and not getattr(args, "no_viz", True):`), :169 (`serve_viz`), :171 (URL built from `args.viz_port`). The sweep slots in before :169.
- `_listening_pids` (actions.py:32-52) always excludes the caller's own PID; `kill_listeners_on_port` (actions.py:55-81) does TERM→KILL escalation with lsof-based survivor re-checks - both reusable as-is once `cwd` is threaded through.
- Test exposure, verified: `tests/test_graph.py`'s `args()` Namespace has no `no_viz` attribute → `getattr(args, "no_viz", True)` is True → no server, no sweep in those tests. `tests/test_e2e_fixtures.py` passes `--no-viz` everywhere and already patches `agent_engine.actions._listening_pids` → `[]`. `tests/test_cli.py::TestViz::test_lifecycle_serves_and_exits` monkeypatches `agent_engine.viz.serve_viz` with a fake returning `(object(), object())` - it will need an `httpd` stand-in carrying `server_address`, plus a stubbed sweep. `tests/test_viz.py::TestTopologyMermaid::test_shipped_self_loop_topology` (lines 131, 145) pins the `port_sweep` node in the example's chain - updated in Task 5. README references: diagram at lines 53-56, bullet 3 at 67-68, YAML walkthrough at 119-125.

## Behaviour matrix

| Situation on `--viz-port` at startup | Sweep | Bind |
|---|---|---|
| Orphaned viz from a crashed run of **this** workspace | cwd matches + cmd matches → reaped | preferred port binds |
| Live viz from a **different** workspace (the consumer repo case) | cwd differs → spared | `EADDRINUSE` → OS-assigned port + printed notice |
| Live viz from the **same** workspace | cwd matches → killed (same-repo concurrency is already unsupported - shared checkpoint DB, `clear_attempt_artifacts`) | preferred port binds |
| Port free | no-op | preferred port binds |
| `--viz-port 0` | sweep skipped (port 0 means "any port"; nothing to reap) | OS-assigned |
| `--no-viz`, programmatic `viz_bus=`, `args` without `no_viz` | no server, no sweep | - |

## Global Constraints

- TDD red→green→refactor for every task
- Mock policy (repo rule, extended here): `run_agent` remains the only stubbed *agent* seam; process/socket tests stay real (`lsof_required` marker). The CLI-lifecycle tests **must** stub the sweep - a real sweep in a test could kill a contributor's live server on 8321 (the consumer repo pid on this machine is exactly that case). The seam is `agent_engine.graph.kill_listeners_on_port` (the name `graph` imports), monkeypatched in `main()` lifecycle tests only - the same justification plan 001 gave `serve_viz`/`webbrowser.open`
- Never silent: the fallback prints a notice naming the busy preferred port and the actual port
- No new dependencies; `lsof`/`socket`/`errno` only
- Full suite green at the end of every task: `.venv-sandbox/bin/python -m pytest tests/ -q`

## Locked decisions (from user / research)

| Decision | Choice |
|---|---|
| Sweep location | `run_pipeline`'s CLI-viz branch, **before** `serve_viz` - no pipeline step, no sentinel plumbing, no registry contract change |
| Sweep safety | cwd-scoped: kill only listeners whose cwd resolves to `runtime.workspace` (the process cwd), AND whose command line contains `agent-engine` (defense in depth). Positive-only attribution: if a pid's cwd can't be determined (lsof fails, process vanished, permissions), the pid is **spared** - fallback handles the busy port |
| Sweep failure mode | Best-effort, never fatal; an lsof-less machine keeps today's WARNING print |
| Fallback | `serve_viz` rebinds port 0 **only** on `errno.EADDRINUSE`; other bind errors (e.g. EACCES on a privileged port) still raise loud naming `--viz-port` |
| `--viz-port` semantics | "Preferred port"; default 8321 unchanged; help text updated |
| URL source of truth | `httpd.server_address[1]` - the printed URL and `webbrowser.open` always carry the real bound port |
| Pipeline step | Removed from `examples/self/pipeline.yaml` (`review.on_pass` → `qa`). The `kill_listeners` action and its node wrapper stay registered (agent-orphaned app servers on arbitrary ports remain a legitimate pipeline use) |
| Plan 001 | Its locked "fail fast, no fallback" decision is **amended** (see Amendments below) - the new rule is "sweep, then bind preferred, then fall back loudly on EADDRINUSE" |
| Same-workspace concurrency | Still unsupported - a same-repo live instance's viz is reaped at startup, consistent with checkpoint-DB/artifact exclusivity |

---

### Task 1: cwd filter in `_listening_pids` / `kill_listeners_on_port`

**Files:**
- Modify: `src/agent_engine/actions.py`
- Test: `tests/test_actions.py`

**Interfaces:**
- `_listening_pids(port, match=None, cwd=None) -> list[str]`
- `kill_listeners_on_port(port, match=None, term_timeout=3.0, cwd=None) -> None`
- New pure helper `_pid_cwd(pid) -> str | None`: runs `lsof -a -p PID -d cwd -Fn`, returns the `n` line after `fcwd`, or `None` on any failure (including no output). `cwd` filtering compares `Path(p).resolve() == Path(cwd).resolve()`.

- [x] **Step 1: Failing tests** - parser unit test: `_pid_cwd` returns the path from a fabricated `p…/fcwd/n/path` payload and `None` for garbage/missing `n` line (pure function, no subprocess). Real-process tests (`lsof_required`, `spawn_listener` launched with `Popen(cwd=…)`): sweep with matching cwd kills; sweep with `cwd=<other dir>` spares (process survives); sweep with cwd and non-matching `match` spares. Keep all existing `TestKillListenersOnPort` tests green (they don't pass `cwd`).
- [x] **Step 2: Implement; green + refactor.**

### Task 2: startup sweep in `run_pipeline`

**Files:**
- Modify: `src/agent_engine/graph.py` (`run_pipeline`)
- Test: `tests/test_cli.py`, `tests/test_graph.py`

**Interfaces:** inside the existing `if bus is None and not getattr(args, "no_viz", True):` branch, before `serve_viz`:

```python
if args.viz_port:
    kill_listeners_on_port(args.viz_port, match="agent-engine",
                           cwd=runtime.workspace)
```

(`kill_listeners_on_port` is imported from `.actions` - `graph` already imports `git_worktree_clean` from there.)

- [x] **Step 1: Failing tests** - `test_cli.py::TestViz::test_lifecycle_serves_and_exits`: additionally monkeypatch `agent_engine.graph.kill_listeners_on_port`, record calls, assert it was called once with `(8321, match="agent-engine", cwd=<the tmp repo, resolved>)` **before** the fake `serve_viz` ran (assert via a shared call-order list). New `test_no_viz_suppresses_server` extension: sweep call list is empty. `test_graph.py`: one new test - `run_pipeline(..., viz_bus=VizBus())` (programmatic) never calls the sweep.
- [x] **Step 2: Implement; green + refactor.**

### Task 3: busy-port fallback in `serve_viz`

**Files:**
- Modify: `src/agent_engine/viz.py` (`serve_viz`)
- Test: `tests/test_viz.py`

**Interfaces:** `serve_viz(bus, topology, subject, port, nodes=()) -> (httpd, thread)` - signature and return type unchanged. Implementation: try `ThreadingHTTPServer(("127.0.0.1", port), handler)`; on `OSError` with `exc.errno == errno.EADDRINUSE` and `port != 0`, rebind with port 0; any other `OSError` re-raises the existing loud `--viz-port` message.

- [x] **Step 1: Failing tests** - replace `test_bind_failure_is_loud` with three: (a) `test_preferred_port_honored_when_free` - `serve_viz` on a free port binds exactly that port; (b) `test_busy_port_falls_back_to_os_assigned` - occupy a port with a real listener (the existing `server` fixture), call `serve_viz` on the same port → no raise, `httpd.server_address[1]` differs from the busy port, and `GET /` still returns 200; (c) `test_privileged_port_still_loud` - `serve_viz(..., port=1)` raises `OSError` matching `--viz-port` (EACCES, not EADDRINUSE - assume non-root dev machine; mark/skip if `os.geteuid() == 0`).
- [x] **Step 2: Implement; green + refactor.**

### Task 4: URL from the actual bound port + fallback notice

**Files:**
- Modify: `src/agent_engine/graph.py` (`run_pipeline`)
- Test: `tests/test_cli.py`

**Interfaces:** capture `httpd, _thread = viz.serve_viz(...)`; `bound = httpd.server_address[1]`; `url = f"http://127.0.0.1:{bound}"`; when `bound != args.viz_port`, print `f"viz port {args.viz_port} busy - serving on {url}"` before the URL print.

- [x] **Step 1: Failing tests** - update the lifecycle test's fake `serve_viz` to return `(types.SimpleNamespace(server_address=("127.0.0.1", 8321)), object())`; existing assertions (`captured["port"] == 8321`, `opened == ["http://127.0.0.1:8321"]`) stay green. New test: fake returns `server_address=("127.0.0.1", 45123)` → `opened == ["http://127.0.0.1:45123"]` and the busy-port notice is printed (capsys).
- [x] **Step 2: Implement; green + refactor.**

### Task 5: remove the pipeline step + docs + demo

**Files:**
- Modify: `examples/self/pipeline.yaml`, `viz_demo.py`, `README.md`, `tests/test_viz.py`, `tests/test_e2e_fixtures.py`

- [x] `examples/self/pipeline.yaml`: delete the `port_sweep` step; `review.on_pass: qa`.
- [x] `viz_demo.py`: drop `emit_node(bus, "port_sweep")` (line 81); build the URL from `httpd.server_address[1]` instead of `args.port` (line 107); the `try/except OSError` stays for non-EADDRINUSE failures.
- [x] `README.md`: diagram (lines 53-56) drops `port_sweep`; delete bullet 3 and renumber (67-72); YAML walkthrough (119-125) drops the step; the viz paragraph (35-40) gains: "a stale viz server from a crashed run of the same repo is swept automatically at startup; if another repo's run holds the port, the engine prints a notice and serves on a free port".
- [x] `tests/test_viz.py::test_shipped_self_loop_topology`: chain becomes `["dev", "review", "qa", "commit", "success"]`; assert `review --> qa;`.
- [x] `tests/test_e2e_fixtures.py`: update the module docstring's "example's verbatim port sweep" note (the example no longer has the step); the `_listening_pids` → `[]` patch stays as defense-in-depth. Assertions are otherwise unaffected (`port_sweep` was an action, not an agent).
- [x] Full suite green.

### Task 6: plan 001 amendment

- [x] Append to plan 001's **Amendments** section a dated entry: the locked "fail fast, never silently degrade" port decision (line 17) is superseded by plan 009 - sweep best-effort at startup, bind the preferred port, fall back to an OS-assigned port on EADDRINUSE with a printed notice (never silent); `test_bind_failure_is_loud` replaced by the fallback/preferred-honored tests.
- [x] Full suite green.

## Verification

1. `.venv-sandbox/bin/python -m pytest tests/ -q` green.
2. Manual, staged, on this machine (pid 81855 still holds 8321 - ideal fixture):
   - Run any pipeline (or `viz_demo.py` with `--port 8321`) → notice printed, server on a free port, the consumer repo's server (pid 81855) untouched.
   - Terminate the consumer repo server, re-run → binds 8321 (preferred honored).
   - Launch `viz_demo.py --port <free>`, kill it with SIGKILL (simulated crash orphan), re-run `viz_demo.py` on the same port from the same cwd → orphan reaped, preferred binds.
3. Real `agent-engine examples/self/pipeline.yaml --plan <doc>` run - **user triggers** (makes LLM calls).

## Amendments

- 2026-10-09: Task 5's target chain listed a non-existent `test` node - the
  shipped example's steps are `dev → review → qa → commit`, so the corrected
  chain (and the test) is `["dev", "review", "qa", "commit", "success"]`.
