"""Manual smoke harness for the viz page: Storybook-style, minus the pipeline.

Serves the real embedded page + the real self-loop topology (built from
agentic-dev-loop/pipeline.yaml, no LLM construction), then publishes a scripted
run to the bus so every visual state can be eyeballed in the browser: pending
→ running → passed/failed nodes, verdict badges (fail on attempt 1, reset on
bump, pass on attempt 2), the attempt counter, the scrolling event log, and
the run_finished banner.

Usage:
    .venv-sandbox/bin/python viz_demo.py [--port 8321] [--scenario success|failure]
                                 [--no-browser] [--flood N]

--flood N makes the attempt-1 dev node publish N console lines while it
"runs", so the sidebar's tailing can be eyeballed: the stage panel should
stay pinned to the bottom as lines stream in, and past 500 lines a
"…N lines omitted" counter should appear at the top while the last lines
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

from agentic_dev_loop import viz
from agentic_dev_loop.core.config import load_config

EXAMPLE = Path(__file__).parent / "agentic-dev-loop" / "pipeline.yaml"
SUBJECT = "plan demo.md"

NODE_GAP = 1.0    # between nodes - the pace a watcher sees
EVENT_GAP = 0.35  # between the started/state/finished bursts of one node


def real_topology() -> dict:
    """The shipped self-loop example's topology DTO - solid happy-path
    chain, labeled loop-backs, and the success terminal - exactly as a live
    run serves it."""
    return viz.topology_graph(load_config(EXAMPLE))


def emit_node(bus: viz.VizBus, name: str, state: dict | None = None,
              ok: bool = True, flood: int = 0, kind: str = "agent") -> None:
    """One node's real event cadence: started → heartbeat → state →
    heartbeat → finished, so the elapsed line is eyeballable without an LLM.

    ``kind="command"`` makes the node look like the pipeline's mechanical
    test gate (a shell command + its exit output) instead of an agent
    conversation. ``flood`` publishes that many extra console lines between
    the two heartbeats (slow enough to watch the panel tail live), for
    exercising the tail/eviction paths with a single node."""
    started = time.monotonic()
    bus.publish({"type": "node_started", "node": name})
    bus.publish({"type": "console", "node": name, "stream": "stdout",
                 "text": f"[demo] Running {name} step..."})
    if kind == "command":
        bus.publish({"type": "console", "node": name, "stream": "stdout",
                     "text": "$ .venv-sandbox/bin/python -m pytest tests/ -q"})
        bus.publish({"type": "console", "node": name, "stream": "stdout",
                     "text": "388 passed, 10 skipped in 242.86s"})
    else:
        bus.publish({"type": "console", "node": name, "stream": "stderr",
                     "text": "\x1b[33m[SDK] step 1 \u2014 thinking\x1b[0m"})
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


def run_scenario(bus: viz.VizBus, scenario: str, flood: int = 0) -> None:
    """Fabricate one run. `success`: qa fails its verdict on attempt 1,
    bump retries to dev, everything passes on attempt 2. `failure`: the
    qa node itself hard-fails on attempt 1 (red node, failure banner).
    The mechanical `test` command gate runs between `dev` and `review` on
    every attempt. `flood` lines are emitted by the attempt-1 dev node
    (see emit_node)."""
    scenario_start = time.monotonic()
    bus.publish({"type": "run_started", "subject": SUBJECT,
                 "max_attempts": 3, "thread_id": "viz-demo", "attempt": 1,
                 "started_at": time.time()})
    bus.publish({"type": "console", "node": None, "stream": "stdout",
                 "text": "[demo] pipeline started"})
    time.sleep(NODE_GAP)

    emit_node(bus, "dev", {"attempt": 1}, flood=flood)
    emit_node(bus, "test", {"step_verdicts": {"test": "pass"}},
              kind="command")
    emit_node(bus, "review", {"step_verdicts": {"test": "pass",
                                                "review": "pass"}})
    if scenario == "failure":
        emit_node(bus, "qa", ok=False)
        bus.publish({"type": "run_finished", "success": False,
                     "attempts": 1,
                     "elapsed_seconds": int(time.monotonic() - scenario_start)})
        return

    emit_node(bus, "qa", {"step_verdicts": {"test": "pass",
                                            "review": "pass",
                                            "qa": "fail"}})
    emit_node(bus, "bump", {"attempt": 2, "step_verdicts": {}})
    emit_node(bus, "dev")
    emit_node(bus, "test", {"step_verdicts": {"test": "pass"}},
              kind="command")
    emit_node(bus, "review", {"step_verdicts": {"test": "pass",
                                                "review": "pass"}})
    emit_node(bus, "qa", {"step_verdicts": {"test": "pass",
                                            "review": "pass",
                                            "qa": "pass"}})
    emit_node(bus, "commit")
    emit_node(bus, "success", {"outcome": "success"})
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
