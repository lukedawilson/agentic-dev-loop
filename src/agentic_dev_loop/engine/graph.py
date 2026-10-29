"""Config-driven LangGraph builder + the run_pipeline public API.

The pipeline is a cyclic state graph: one node per configured step, routers
steer the retry cycle, and a SQLite checkpointer makes runs crash-resumable
(--resume <thread-id> re-enters at the first stage that didn't complete).
"""

from __future__ import annotations

import operator
import uuid
import webbrowser
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from agentic_dev_loop import viz
from .actions import git_worktree_clean, kill_listeners_on_port
from .allowlist import resolve_engine_allowlist
from agentic_dev_loop.core.config import load_config
from .hostboundary import (check_orchestrator_install,
                           check_tooling_integrity, tooling_rel_paths)
from .isolation import startup_sandbox
from .llm import build_llm
from .loaders import resolve_docs
from .secrets import resolve_secrets, scan_env_files
from .registry import (Registry, default_registry, load_extensions,
                       validate_action_mounts_egress, validate_action_network)
from .stages import (Runtime, clear_attempt_artifacts,
                     make_action_node, make_agent_node, make_brief_node,
                     make_bump_node, make_command_node, make_router,
                     success_node)

CHECKPOINT_DB = "loop-checkpoints.sqlite"  # under state_dir, one DB per workspace


def _merge_artifacts(a: dict[str, str], b: dict[str, str]) -> dict[str, str]:
    """Accumulate per-step artifact text across nodes. LangGraph gives
    un-annotated fields last-write-wins, so without this reducer one node's
    entry would silently replace the accumulated dict."""
    return {**a, **b}


def _merge_counts(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    """Accumulate the per-step brief coverage retry counts (041)."""
    return {**a, **b}


class LoopState(TypedDict):
    """State threaded through the graph. `notes` accumulates across attempts
    (append reducer), `artifacts` accumulates across stages (merge reducer);
    every other key is last-write-wins."""
    subject: str
    docs: dict[str, str]
    doc_sources: dict[str, str]        # doc key -> source path (033 regeneration gate)
    artifacts: Annotated[dict[str, str], _merge_artifacts]  # step name -> artifact text
    attempt: int
    max_attempts: int
    notes: Annotated[list[str], operator.add]
    failed: bool                       # hard stage failure - ends the run
    failed_step: str | None            # the step that hard-failed (resume target)
    resume_target: str | None          # where a resumed run re-enters (START edge)
    outcome: str | None                # "success" once routed to the success terminal
    commit_allowed: bool               # worktree was clean at run start
    state_dir: str                     # resolved pipeline state dir (for the commit action)
    tooling_paths: list[str]          # workspace-relative paths the commit never sweeps
    step_verdicts: dict[str, str | None]  # per-step pass/fail/None(unclear)
    retry_target: str | None           # where the bump node loops back to
    thread_id: str
    brief_hashes: Annotated[dict[str, str], _merge_artifacts]  # step name -> input hash (033)
    brief_retries: Annotated[dict[str, int], _merge_counts]  # step name -> coverage retries used (041)


def build_graph(runtime: Runtime, checkpointer=None):
    """Compile the pipeline's state graph: one node per step (agent, command,
    or action), plus the bump retry node and the success terminal. Unknown
    action names fail here, at build time, not mid-run."""
    known_actions = (set(runtime.registry.actions)
                     | set(runtime.registry.sandbox_actions))
    for step in runtime.cfg.steps:
        if step.action is None:
            continue
        if step.action not in known_actions:
            raise ValueError(
                f"step {step.name!r}: unknown action {step.action!r} "
                f"(registered: {sorted(known_actions)})")
        validate_action_network(step.name, step.action, step.network,
                                runtime.registry)
        validate_action_mounts_egress(step.name, step.action, step.mounts,
                                      step.egress, runtime.registry)
    graph = StateGraph(LoopState)
    for step in runtime.cfg.steps:
        if step.kind == "agent":
            if step.brief_inputs is not None:
                node = make_brief_node(runtime, step)
            else:
                node = make_agent_node(runtime, step)
        elif step.kind == "command":
            node = make_command_node(runtime, step)
        else:
            node = make_action_node(runtime, step)
        graph.add_node(step.name, node)
        graph.add_conditional_edges(step.name, make_router(runtime, step))
    graph.add_node("bump", make_bump_node(runtime))
    graph.add_conditional_edges("bump", lambda state: state["retry_target"])
    graph.add_node("success", success_node)
    graph.add_conditional_edges(
        START,
        lambda state: state.get("resume_target") or runtime.cfg.steps[0].name)
    graph.add_edge("success", END)
    return graph.compile(checkpointer=checkpointer)



def run_pipeline(cfg_path, args, *, viz_bus=None,
                 registry: Registry | None = None) -> tuple[bool, int]:
    """Load a pipeline YAML, resolve docs via the selected document loader,
    and run the graph to completion. Returns (success, attempts) - the
    public API, the consumer's `construct`/`resume_loop` equivalent.

    `args` carries the parsed CLI namespace: loader flags, --resume,
    --max-attempts (which overrides the YAML value when set), and --no-viz.

    The live viz is on by default for CLI runs (suppressed with ``--no-viz``).
    With ``viz_bus=None`` and viz suppressed, the graph runs via
    ``graph.invoke`` exactly as before. A passed-in ``viz_bus`` (programmatic)
    or a default-on CLI run switches to ``graph.stream`` and publishes live
    events; the CLI path additionally serves the HTTP page and opens the
    browser, and the process exits as soon as the loop completes.
    Programmatic Namespaces without a ``no_viz`` attribute get no server -
    default-on is a CLI-layer concern.

    ``registry`` carries extensions already loaded by the CLI (it loads them
    once, for loader-contributed flags; a second load would collide with the
    SDK's process-global tool registry). When absent - programmatic calls -
    the registry is built here."""
    cfg_path = Path(cfg_path)
    cfg = load_config(cfg_path)
    base = cfg_path.resolve().parent
    max_attempts = getattr(args, "max_attempts", None) or cfg.max_attempts

    # 020: the host must execute nothing from the workspace - refuse an
    # in-workspace engine install, then the 019 env-file ban, before any
    # sandbox/LLM work. Tooling integrity is checked once the config is known.
    check_orchestrator_install(Path.cwd())
    scan_env_files(Path.cwd())
    resolved_secrets = resolve_secrets(cfg)
    check_tooling_integrity(cfg, cfg_path, base, Path.cwd())
    tooling_paths = tooling_rel_paths(cfg, cfg_path, base, Path.cwd())

    if registry is None:
        registry = default_registry()
        load_extensions(cfg.extensions, registry,
                        extensions_dir=cfg.extensions_dir,
                        workspace=Path.cwd())

    state_dir = Path(cfg.state_dir).expanduser()
    if not state_dir.is_absolute():
        state_dir = base / state_dir

    # Eager startup (Q2): probe -> image -> create+start exec container ->
    # pre-provision all declared setups, BEFORE the graph builds. Zero infra
    # operations mid-run; the only sandbox call during steps is run(request).
    sandbox = startup_sandbox(cfg, workspace=Path.cwd(),
                               refresh=getattr(args, "refresh", False),
                               state_dir=state_dir)
    try:
        return _run_pipeline(cfg, base, registry, state_dir, max_attempts,
                             args, viz_bus, sandbox, resolved_secrets,
                             tooling_paths)
    finally:
        sandbox.close()


def _run_pipeline(cfg, base, registry, state_dir, max_attempts, args, viz_bus,
                  sandbox, resolved_secrets, tooling_paths) -> tuple[bool, int]:
    export_root = getattr(sandbox, "export_root", None)
    engine_allowlist = resolve_engine_allowlist(registry, args)
    runtime = Runtime(cfg=cfg, registry=registry, llm=build_llm(cfg.llm),
                      agents_dir=base / cfg.agents_dir, state_dir=state_dir,
                      workspace=Path.cwd(), sandbox=sandbox,
                      sandbox_cwd=Path(export_root) if export_root else None,
                      secrets=resolved_secrets,
                      engine_allowlist=engine_allowlist)
    state_dir.mkdir(parents=True, exist_ok=True)

    resume = getattr(args, "resume", None)
    thread_id = resume or uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}
    db = str(state_dir / CHECKPOINT_DB)
    if resume:
        initial = None  # state (docs/subject/artifacts) comes from the checkpoint
    else:
        _loader, docs, subject, doc_sources = resolve_docs(cfg, base, registry, args)
        # A fresh run owns state_dir: drop cross-run leftovers (a previous
        # failure's last-error.md, stale artifacts) so nothing mistakes
        # them for this run's output. --resume keeps them.
        clear_attempt_artifacts(runtime, exempt_run_scoped=False)
        initial: LoopState = {
            "subject": subject,
            "docs": docs,
            "doc_sources": doc_sources,
            "artifacts": {},
            "attempt": 1,
            "max_attempts": max_attempts,
            "notes": [],
            "failed": False,
            "failed_step": None,
            "resume_target": None,
            "outcome": None,
            # Only a clean-at-start worktree gets an auto-commit - otherwise
            # the commit could swallow pre-existing changes. Computed BEFORE
            # the checkpointer creates its db under state_dir, which would
            # itself dirty the worktree.
            "commit_allowed": git_worktree_clean(Path.cwd()),
            "state_dir": str(state_dir),
            "tooling_paths": tooling_paths,
            "step_verdicts": {},
            "retry_target": None,
            "thread_id": thread_id,
            "brief_hashes": {},
            "brief_retries": {},
        }
    bus = viz_bus
    with SqliteSaver.from_conn_string(db) as saver:
        if resume and saver.get_tuple(config) is None:
            raise ValueError(
                f"No checkpoint found for thread {thread_id!r} in {db} - "
                f"nothing to resume.")
        graph = build_graph(runtime, saver)

        if resume and getattr(args, "max_attempts", None):
            # A resumed run takes its state from the checkpoint, so the CLI
            # override computed above would never reach it. Re-apply it to the
            # checkpoint state before the run re-enters.
            graph.update_state(config, {"max_attempts": max_attempts})

        if resume and initial is None:
            values = graph.get_state(config).values
            if values.get("failed"):
                # A hard-failed run's checkpoint has no pending work - a
                # plain resume would return it untouched and exit silently.
                # Re-enter at the step that failed so the run can complete.
                failed_step = values.get("failed_step")
                target = (failed_step or values.get("retry_target")
                          or cfg.steps[0].name)
                print(
                    f"[{values.get('subject', '?')}] Resuming hard-failed run "
                    f"(thread {thread_id}) - re-entering at step {target!r}. "
                    f"Failure details: {state_dir}/last-error.md",
                    flush=True)
                initial = {**values, "failed": False,
                           "resume_target": target, "failed_step": None,
                           "artifacts": values.get("artifacts", {})}

        if bus is None and not getattr(args, "no_viz", True):
            start_state = initial if initial is not None \
                else graph.get_state(config).values
            topology, nodes = viz.topology_mermaid(cfg)
            bus = viz.VizBus()
            if args.viz_port:
                kill_listeners_on_port(args.viz_port, match="agentic-dev-loop",
                                       cwd=runtime.workspace)
            httpd, _thread = viz.serve_viz(
                bus, topology, start_state["subject"], args.viz_port, nodes)
            bound = httpd.server_address[1]
            url = f"http://127.0.0.1:{bound}"
            if args.viz_port and bound != args.viz_port:
                print(f"viz port {args.viz_port} busy - serving on {url}",
                      flush=True)
            print(f"Live graph viz: {url}", flush=True)
            webbrowser.open(url)

        if bus is not None:
            start_state = initial if initial is not None \
                else graph.get_state(config).values
            bus.publish({
                "type": "run_started",
                "subject": start_state["subject"],
                "max_attempts": start_state["max_attempts"],
                "thread_id": thread_id,
                "attempt": start_state["attempt"],
            })
            watch = viz.NodeWatch()
            capture = viz.ConsoleCapture(bus, watch)
            capture.attach()
            heartbeat = viz.HeartbeatThread(watch, bus)
            heartbeat.start()
            final = None
            try:
                for part in graph.stream(initial, config,
                                         stream_mode=["tasks", "updates"],
                                         version="v2"):
                    for event in viz.translate(part):
                        if event["type"] == "node_started":
                            watch.start(event["node"])
                        elif event["type"] == "node_finished":
                            watch.finish(event["node"])
                        bus.publish(event)
                final = graph.get_state(config).values
            finally:
                heartbeat.stop()
                heartbeat.join()
                if final is None:
                    try:
                        final = graph.get_state(config).values
                    except Exception:
                        final = None
                if final is not None:
                    success = final.get("outcome") == "success"
                    attempts = final.get("attempt", 0)
                else:
                    success = False
                    attempts = 0
                bus.publish({"type": "run_finished",
                             "success": success, "attempts": attempts})
                capture.detach()
        else:
            final = graph.invoke(initial, config)
            success = final["outcome"] == "success"
            attempts = final["attempt"]

    if not success and final is not None:
        if final.get("failed"):
            print(
                f"[{final['subject']}] FAILED after {final['attempt']} "
                f"attempt(s) - hard failure at step "
                f"{final.get('failed_step', 'unknown')!r}; see "
                f"{state_dir}/last-error.md",
                flush=True)
        else:
            print(f"[{final['subject']}] FAILED after {final['attempt']} "
                  f"attempt(s).", flush=True)
    return success, attempts
