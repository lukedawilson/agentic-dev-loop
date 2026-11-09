"""Manual smoke harness for the viz page: Storybook-style, minus the pipeline.

Serves the real embedded page + the real self-loop topology (built from
agentic-dev-loop/pipeline.yaml, no LLM construction), then publishes a scripted
run to the bus so every visual state can be eyeballed in the browser: pending
-> running -> passed/failed nodes across the FULL chain (brief, deps, dev,
restore, test, review, qa, commit, success), verdict badges (qa fails its
verdict on attempt 1, the retry resets verdicts and re-runs dev through qa on
attempt 2), the attempt counter, the scrolling event log, and the
run_finished banner.

Usage:
    .venv-sandbox/bin/python tests/smoke/viz_demo.py [--port 8321] [--scenario success|failure]
                                 [--no-browser] [--flood N]

--flood N makes the attempt-1 dev node publish N console lines while it
"runs", so the sidebar's tailing can be eyeballed: the stage panel should
stay pinned to the bottom as lines stream in, and past 500 lines a
"...N lines omitted" counter should appear at the top while the last lines
stay visible. N must exceed ~13 to overflow the 240px panel and 500 to hit
the eviction path (600 is a good default).

The process exits when the scripted run finishes. No agents run, no LLM
calls, no checkpoints - the events are fabricated here; only viz.py's
server/page code is exercised.
"""

from __future__ import annotations

import argparse
import sys
import time
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_dev_loop import viz  # noqa: E402 - src/ inserted first
from agentic_dev_loop.core.config import load_config  # noqa: E402

EXAMPLE = REPO_ROOT / "agentic-dev-loop" / "pipeline.yaml"
SUBJECT = "plan demo.md"

NODE_GAP = 1.0    # between nodes - the pace a watcher sees
EVENT_GAP = 0.35  # between the started/state/finished bursts of one node


def real_topology() -> dict:
    """The shipped self-loop example's topology DTO - solid happy-path
    chain, labeled loop-backs, and the success terminal - exactly as a live
    run serves it."""
    return viz.topology_graph(load_config(EXAMPLE))


# Console scripts for the mechanical steps, verbatim from pipeline.yaml:
# deps bootstraps the sandbox venv, restore upgrades pip + reinstalls +
# audits the pins, test is the exit-code gate. commit is the loop's action
# step; success is the terminal.
DEPS_LINES = (
    '$ python -m venv .venv-sandbox && .venv-sandbox/bin/pip install -e ".[dev]"',
    "Successfully installed agentic-dev-loop-0.10.5",
)
RESTORE_LINES = (
    '$ .venv-sandbox/bin/python -m pip install --upgrade pip && '
    '.venv-sandbox/bin/python -m pip install -e ".[dev]" && '
    ".venv-sandbox/bin/python -m pip_audit --skip-editable",
    "No known vulnerabilities found",
)
TEST_LINES = (
    '$ .venv-sandbox/bin/python -m pytest tests/ -q -m "not docker"',
    "388 passed, 10 skipped in 242.86s",
)
BRIEF_LINES = (
    "[brief] digest headings: Repo rules digest, Unit scope digest, "
    "Environment facts, Open risks",
    "[brief] environment facts captured - digest written",
)
COMMIT_LINES = (
    '$ git commit -m "feat: construct plan demo.md (agent dev loop)"',
    "[main 3f2a1c0] feat: construct plan demo.md (agent dev loop)",
)
SUCCESS_LINES = ("[demo] run complete - all steps passed",)


def emit_node(bus: viz.VizBus, name: str, state: dict | None = None,
              ok: bool = True, flood: int = 0, kind: str = "agent",
              lines: tuple[str, ...] = ()) -> None:
    """One node's real event cadence: started -> console -> heartbeat -> state ->
    heartbeat -> finished, so the elapsed line is eyeballable without an LLM.

    ``kind="agent"`` (default) makes the node look like an SDK conversation
    (one yellow "[SDK] step 1 - thinking" stderr line). ``kind="command"``
    is the pipeline's mechanical gate and ``kind="brief"`` the engine-native
    digest step: both print ``lines`` (the shell command + its output, or
    digest progress) instead. ``flood`` publishes that many extra console
    lines between the two heartbeats (slow enough to watch the panel tail
    live), for exercising the tail/eviction paths with a single node."""
    started = time.monotonic()
    bus.publish({"type": "node_started", "node": name})
    bus.publish({"type": "console", "node": name, "stream": "stdout",
                 "text": f"[demo] Running {name} step..."})
    if kind == "agent":
        bus.publish({"type": "console", "node": name, "stream": "stderr",
                     "text": "\x1b[33m[SDK] step 1 \u2014 thinking\x1b[0m"})
    for text in lines:
        bus.publish({"type": "console", "node": name, "stream": "stdout",
                     "text": text})
    bus.publish({"type": "heartbeat", "node": name,
                 "elapsed_seconds": int(time.monotonic() - started)})
    if flood:
        bus.publish({"type": "console", "node": name, "stream": "stdout",
                     "text": f"\x1b[33m[flood] {name} emitting {flood} lines\x1b[0m"})
        for i in range(1, flood + 1):
            bus.publish({"type": "console", "node": name, "stream": "stdout",
                         "text": f"[flood] {i:04d} \u2500 lorem ipsum dolor sit amet"})
            time.sleep(0.003)
    time.sleep(EVENT_GAP)
    bus.publish({"type": "state", **(state or {})})
    time.sleep(EVENT_GAP)
    bus.publish({"type": "heartbeat", "node": name,
                 "elapsed_seconds": int(time.monotonic() - started)})
    bus.publish({"type": "node_finished", "node": name, "ok": ok,
                 "elapsed_seconds": int(time.monotonic() - started)})
    time.sleep(NODE_GAP)


def run_scenario(bus: viz.VizBus, scenario: str, flood: int = 0,
                 max_attempts: int | None = None) -> None:
    """Fabricate one run over the REAL pipeline topology: brief -> deps -> dev
    -> restore -> test -> review -> qa -> commit -> success.

    `success`: brief/deps/dev/restore/test/review all pass; qa fails its
    verdict on attempt 1; the retry resets verdicts and re-runs the dev ->
    restore -> test -> review -> qa chain (dev re-enters per the pipeline's
    `on_fail: goto dev`; restore re-audits the pins dev bumped); everything
    passes on attempt 2 and commit runs. `failure`: the same chain through
    review, then the qa node itself hard-fails (red node, failure banner).
    `flood` lines are emitted by the attempt-1 dev node (see emit_node).
    `max_attempts` defaults to the shipped pipeline's value."""
    if max_attempts is None:
        max_attempts = load_config(EXAMPLE).max_attempts
    scenario_start = time.monotonic()
    bus.publish({"type": "run_started", "subject": SUBJECT,
                 "max_attempts": max_attempts, "thread_id": "viz-demo",
                 "attempt": 1, "started_at": time.time()})
    bus.publish({"type": "console", "node": None, "stream": "stdout",
                 "text": "[demo] pipeline started"})
    time.sleep(NODE_GAP)

    # Attempt 1: the full chain. step_verdicts snapshots mirror LoopState:
    # each step appends its verdict and the whole dict rides every state
    # event (010).
    verdicts = {"brief": "pass"}
    emit_node(bus, "brief", {"step_verdicts": verdicts}, kind="brief",
              lines=BRIEF_LINES)
    verdicts = {**verdicts, "deps": "pass"}
    emit_node(bus, "deps", {"step_verdicts": verdicts}, kind="command",
              lines=DEPS_LINES)
    verdicts = {**verdicts, "dev": "pass"}
    emit_node(bus, "dev", {"step_verdicts": verdicts}, flood=flood)
    verdicts = {**verdicts, "restore": "pass"}
    emit_node(bus, "restore", {"step_verdicts": verdicts}, kind="command",
              lines=RESTORE_LINES)
    verdicts = {**verdicts, "test": "pass"}
    emit_node(bus, "test", {"step_verdicts": verdicts}, kind="command",
              lines=TEST_LINES)
    verdicts = {**verdicts, "review": "pass"}
    emit_node(bus, "review", {"step_verdicts": verdicts})

    if scenario == "failure":
        emit_node(bus, "qa", ok=False)
        bus.publish({"type": "run_finished", "success": False,
                     "attempts": 1,
                     "elapsed_seconds": int(time.monotonic() - scenario_start)})
        return

    verdicts = {**verdicts, "qa": "fail"}
    emit_node(bus, "qa", {"step_verdicts": verdicts})

    # Retry: the engine's bump node consumes an attempt, resets verdicts and
    # re-enters dev. It is not a pipeline step, so it is not drawn; publish
    # its state update bare (plus a log line) instead of as a phantom node.
    bus.publish({"type": "console", "node": None, "stream": "stdout",
                 "text": "[demo] qa verdict: FAIL - retrying from dev "
                         "(attempt 2)"})
    bus.publish({"type": "state", "attempt": 2, "step_verdicts": {}})

    # Attempt 2: dev re-enters clean (deps/brief ran once per run), restore
    # re-audits, and verdicts accumulate fresh from the reset.
    verdicts = {"dev": "pass"}
    emit_node(bus, "dev", {"step_verdicts": verdicts})
    verdicts = {**verdicts, "restore": "pass"}
    emit_node(bus, "restore", {"step_verdicts": verdicts}, kind="command",
              lines=RESTORE_LINES)
    verdicts = {**verdicts, "test": "pass"}
    emit_node(bus, "test", {"step_verdicts": verdicts}, kind="command",
              lines=TEST_LINES)
    verdicts = {**verdicts, "review": "pass"}
    emit_node(bus, "review", {"step_verdicts": verdicts})
    verdicts = {**verdicts, "qa": "pass"}
    emit_node(bus, "qa", {"step_verdicts": verdicts})
    verdicts = {**verdicts, "commit": "pass"}
    emit_node(bus, "commit", {"step_verdicts": verdicts}, kind="command",
              lines=COMMIT_LINES)
    emit_node(bus, "success", {"outcome": "success"}, kind="command",
              lines=SUCCESS_LINES)
    bus.publish({"type": "run_finished", "success": True, "attempts": 2,
                 "elapsed_seconds": int(time.monotonic() - scenario_start)})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8321,
                        help="port for the demo server (default: 8321)")
    parser.add_argument("--scenario", choices=["success", "failure"],
                        default="success")
    parser.add_argument("--no-browser", action="store_true",
                        help="print the URL but don't open a browser tab")
    parser.add_argument("--flood", type=int, default=0, metavar="N",
                        help="publish N console lines for the attempt-1 dev "
                             "node (0 = off; 600 exercises tailing/eviction)")
    args = parser.parse_args()

    bus = viz.VizBus()
    topology = real_topology()
    try:
        httpd, _thread = viz.serve_viz(bus, topology["mermaid"], SUBJECT,
                                       args.port, topology["nodes"],
                                       topology["flow"], topology["edges"])
    except OSError as exc:
        print(f"{exc}\nPick another port with --port.", file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    print(f"Viz demo ({args.scenario}): {url}", flush=True)
    if not args.no_browser:
        webbrowser.open(url)

    run_scenario(bus, args.scenario, flood=args.flood)
    print("Run finished - process exiting.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
