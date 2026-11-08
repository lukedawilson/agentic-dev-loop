"""Two-phase CLI: phase 1 finds the pipeline YAML; phase 2 builds the full
parser - core flags plus every document loader's contributed args (the
built-in `plan` loader plus whatever the pipeline's `extensions:` register)
and runs the pipeline. A loader's flag therefore exists only when its
extension is listed in that pipeline's YAML."""

import argparse
import importlib.metadata
import json
import os
import signal
import sys
import threading
from pathlib import Path

from agentic_dev_loop.core.config import PipelineConfig, load_config
from .copilot_auth import (TOKEN_PATH, CopilotAuthError, list_copilot_models,
                           login, read_token)
from .containers import containers_report
from .isolation import isolation_state_dir, reap_orphaned_containers
from .loaders import mark_plan_completed
from .registry import Registry, default_registry, load_extensions


_sigterm_in_progress = False


def _handle_sigterm(signum, frame) -> None:
    """First SIGTERM raises SystemExit(143) so the graph ``finally`` runs
    close(); a second SIGTERM while already terminating hard-exits (D4)."""
    global _sigterm_in_progress
    if _sigterm_in_progress:
        os._exit(143)
    _sigterm_in_progress = True
    raise SystemExit(143)


def _install_sigterm_handler():
    """Install the graceful-shutdown handler; returns the prior handler."""
    global _sigterm_in_progress
    _sigterm_in_progress = False
    return signal.signal(signal.SIGTERM, _handle_sigterm)


def _restore_sigterm_handler(previous) -> None:
    """Restore the prior SIGTERM handler and reset the in-progress flag."""
    global _sigterm_in_progress
    _sigterm_in_progress = False
    signal.signal(signal.SIGTERM, previous)


def _wait_for_interrupt() -> None:
    """Block until the user presses ctrl+c; a bounded wait keeps the loop
    responsive to signals without requiring a tty."""
    idle = threading.Event()
    while not idle.wait(1.0):
        pass


def _version() -> str:
    """The installed distribution version - the single source of truth."""
    try:
        return importlib.metadata.version("agentic-dev-loop")
    except importlib.metadata.PackageNotFoundError:
        print("agentic-dev-loop is not installed - version unavailable; "
              "run `pip install -e .`", file=sys.stderr)
        raise SystemExit(1)


def _add_version_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--version", action="version",
                        version=f"agentic-dev-loop {_version()}")


def _core_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-dev-loop",
        description="Run an OpenHands agent pipeline defined in YAML. "
                    "Document-loader flags contributed by the pipeline's "
                    "extensions appear once PIPELINE is known - see "
                    "`agentic-dev-loop PIPELINE --help`.")
    parser.add_argument("pipeline", type=Path,
                        help="path to the pipeline YAML")
    parser.add_argument("--resume", metavar="THREAD_ID", default=None,
                        help="resume a checkpointed run by thread id")
    parser.add_argument("--max-attempts", type=int, default=None,
                        help="override the pipeline's max_attempts")
    parser.add_argument("--no-viz", action="store_true",
                        help="suppress the live graph view (served by "
                             "default)")
    parser.add_argument("--viz-port", type=int, default=8321,
                        help="preferred port for the live graph view "
                             "(default: 8321; falls back to a free port when "
                             "busy)")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate the pipeline and print a summary plan "
                             "without executing anything (no LLM, no "
                             "sandbox, no state writes)")
    _add_version_flag(parser)
    return parser


def _registry_for(cfg_path: Path) -> tuple[Registry, PipelineConfig]:
    """Load the pipeline config and build ONE registry with its extensions.
    The CLI loads extensions exactly once per process: the SDK tool registry
    is process-global and BR-3 rejects a second registration of any tool
    name, so the registry built here is passed down to the dry run and the
    run path instead of being rebuilt."""
    cfg = load_config(cfg_path)
    registry = default_registry()
    load_extensions(cfg.extensions, registry,
                    extensions_dir=cfg.extensions_dir, workspace=Path.cwd())
    return registry, cfg


def _containers_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-dev-loop containers",
        description="Inspect engine sandbox containers and networks.")
    parser.add_argument("--reap", action="store_true",
                        help="reap orphaned registry-owned containers and "
                             "networks and print their names")
    parser.add_argument("--json", action="store_true",
                        help="emit the report or reap result as JSON")
    parser.add_argument("--state-dir", default="~/.agentic-dev-loop/state",
                        help="isolation state dir "
                             "(default: ~/.agentic-dev-loop/state)")
    return parser


def _run_containers(argv: list[str]) -> int:
    args = _containers_parser().parse_args(argv)
    state_dir = Path(args.state_dir).expanduser()
    if args.reap:
        names = reap_orphaned_containers(state_dir, announce=False)
        if args.json:
            print(json.dumps({"reaped": names}, sort_keys=True))
        else:
            for name in names:
                print(name)
        return 0
    report = containers_report(state_dir)
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_containers_report(report)
    return 0


def _print_containers_report(report) -> None:
    rows = report["containers"] + report["unregistered"]
    if not rows:
        print("no engine containers or networks found")
        return
    for item in rows:
        run = item.get("run_id") or "-"
        pid = item.get("pid")
        pid_s = str(pid) if pid is not None else "-"
        print(f"{item['verdict']:12} {item['kind']:9} {item['name']} "
              f"run={run} pid={pid_s}")


def _token_path(value: str | None) -> Path:
    return Path(value).expanduser() if value else TOKEN_PATH.expanduser()


def _run_copilot_login(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agentic-dev-loop copilot-login",
        description="Run the GitHub device flow and store the Copilot OAuth "
                    "token (~/.agentic-dev-loop/auth/copilot.json, mode 600).")
    parser.add_argument("--token-file", default=None,
                        help="token path (default: "
                             "~/.agentic-dev-loop/auth/copilot.json)")
    args = parser.parse_args(argv)
    path = _token_path(args.token_file)
    try:
        login(token_path=path)
    except CopilotAuthError as e:
        print(f"copilot-login: {e}", file=sys.stderr)
        return 1
    print(f"Copilot token written to {path}")
    return 0


def _run_copilot_models(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agentic-dev-loop copilot-models",
        description="List the Copilot models available to this token's tier.")
    parser.add_argument("--token-file", default=None,
                        help="token path (default: "
                             "~/.agentic-dev-loop/auth/copilot.json)")
    parser.add_argument("--json", action="store_true",
                        help="emit the model list as JSON")
    args = parser.parse_args(argv)
    try:
        models = list_copilot_models(read_token(_token_path(args.token_file)))
    except CopilotAuthError as e:
        print(f"copilot-models: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"models": models}, sort_keys=True))
    else:
        for model in models:
            print(model)
    return 0


def _report_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-dev-loop report",
        description="Report accumulated agentic-dev-loop run history as "
                    "read-only markdown.")
    parser.add_argument("run_id", nargs="?", default=None,
                        help="12-hex run dir name for a deep dive "
                             "(default: aggregate report)")
    parser.add_argument("--since", default=None,
                        help="only threads starting at or after this ISO "
                             "datetime")
    parser.add_argument("--until", default=None,
                        help="only threads starting before this ISO datetime")
    parser.add_argument("--status", action="append", default=None,
                        help="filter by status (success/failed/interrupted/"
                             "running); repeatable or comma-separated")
    parser.add_argument("--runs-root", default=None,
                        help="runs root (default: $AGENTIC_DEV_LOOP_RUNS_ROOT "
                             "or ~/.agentic-dev-loop/runs)")
    parser.add_argument("--audit-dir", "--state-dir", dest="audit_dir",
                        default=None,
                        help="sandbox audit dir (default: "
                             "$AGENTIC_DEV_LOOP_STATE_DIR or "
                             "~/.agentic-dev-loop/state)")
    return parser


def _run_report(argv: list[str]) -> int:
    from agentic_dev_loop.reporting import (
        apply_filters, build_run_records, discover, find_run_dir,
        pure_metrics, read_audit, read_dir, render_markdown, runs_root)

    args = _report_parser().parse_args(argv)
    root = runs_root(args.runs_root)
    notice = None
    if args.run_id is not None:
        run_dir = find_run_dir(args.run_id, root)
        if run_dir is None:
            print(f"report: unknown run id: {args.run_id}", file=sys.stderr)
            return 1
        dirs = [run_dir]
    else:
        dirs = discover(root)
        if not root.is_dir():
            notice = f"runs root not found: {root}"
    audit_pool = read_audit(args.audit_dir)
    records = build_run_records([read_dir(path) for path in dirs], audit_pool)
    filters = {key: getattr(args, key)
               for key in ("since", "until", "status") if getattr(args, key)}
    try:
        records = apply_filters(
            records, since=args.since, until=args.until,
            statuses=args.status)
    except ValueError as exc:
        print(f"report: {exc}", file=sys.stderr)
        return 1
    if not records and notice is None:
        notice = "no runs recorded" if not dirs else "no runs matched filters"
    metrics = pure_metrics(records)
    sys.stdout.write(render_markdown(
        records, metrics, deep_dive=args.run_id is not None,
        filters=filters, notice=notice))
    return 0


def build_parser(cfg_path) -> argparse.ArgumentParser:
    """The full parser for one pipeline: core flags + loader-contributed."""
    registry, _ = _registry_for(cfg_path)
    parser = _core_parser()
    for loader in registry.document_loaders:
        loader.add_cli_args(parser)
    return parser


def main(argv: list[str] | None = None, *, keep_alive: bool = True) -> int:
    if argv is None:
        argv = sys.argv[1:]
    else:
        argv = list(argv)
    if argv and argv[0] == "containers":
        return _run_containers(argv[1:])
    if argv and argv[0] == "copilot-login":
        return _run_copilot_login(argv[1:])
    if argv and argv[0] == "copilot-models":
        return _run_copilot_models(argv[1:])
    if argv and argv[0] == "report":
        return _run_report(argv[1:])
    if argv and argv[0] == "run-status":
        from .run_status import _run_status
        return _run_status(argv[1:])
    if argv and argv[0] == "init":
        from .onboarding.init import run_init
        return run_init(argv[1:])
    if argv and argv[0] == "doctor":
        from .onboarding.doctor import run_doctor
        return run_doctor(argv[1:])
    if argv and argv[0] == "upgrade":
        from .onboarding.upgrade import run_upgrade
        return run_upgrade(argv[1:])
    probe = argparse.ArgumentParser(add_help=False)
    probe.add_argument("pipeline", type=Path, nargs="?")
    _add_version_flag(probe)
    known, _ = probe.parse_known_args(argv)
    if known.pipeline is None:
        # No pipeline → the extension surface is unknowable; the core parser
        # handles --help and the missing-positional error itself (both exit).
        _core_parser().parse_args(argv)
        return 2  # unreachable - parse_args exits first
    registry, cfg = _registry_for(known.pipeline)
    parser = _core_parser()
    for loader in registry.document_loaders:
        loader.add_cli_args(parser)
    args = parser.parse_args(argv)
    if args.dry_run:
        from .dryrun import dry_run
        return dry_run(args.pipeline, args, registry)
    # The run path alone needs the heavy graph/SDK imports, and --dry-run
    # needs the dryrun import chain in-branch; defer both so `containers`,
    # --help, and --version stay side-effect free. 042 D4: a
    # SIGTERM here raises SystemExit(143) so graph.run_pipeline's finally
    # still closes the sandbox; a second SIGTERM hard-exits.
    previous = _install_sigterm_handler()
    try:
        from .graph import run_pipeline
        success, _attempts = run_pipeline(args.pipeline, args, registry=registry)
        if success and args.plan is not None:
            # Only a completed run earns the stamp - failed runs leave the plan
            # as-is. AI-DLC runs never pass --plan, so aidlc docs stay untouched.
            mark_plan_completed(args.plan)
        if not success:
            print("agentic-dev-loop: pipeline failed - exiting with code 1",
                  file=sys.stderr)
        if keep_alive and not args.no_viz:
            print("Pipeline finished: press ctrl+c to exit")
            try:
                _wait_for_interrupt()
            except KeyboardInterrupt:
                pass
        return 0 if success else 1
    finally:
        _restore_sigterm_handler(previous)
