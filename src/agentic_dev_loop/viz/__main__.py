"""Standalone viz launcher.

``python -m agentic_dev_loop.viz --pipeline <pipeline.yaml>`` serves the
live graph on localhost; ``--history`` serves the historical runs view with
no pipeline file.
"""

from __future__ import annotations

import argparse
import sys

from agentic_dev_loop.core.config import load_config

from . import VizBus, serve_history, serve_viz, topology_graph


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentic-dev-loop-viz",
        description="Serve the agentic-dev-loop live graph or run history.")
    parser.add_argument("--pipeline",
                        help="pipeline YAML for the flowchart topology "
                             "(required unless --history)")
    parser.add_argument("--history", action="store_true",
                        help="serve the historical runs view without a "
                             "pipeline file")
    parser.add_argument("--port", type=int, default=8321,
                        help="preferred port (default 8321; free-port "
                             "fallback when busy)")
    args = parser.parse_args(argv)

    if args.history:
        httpd, _thread = serve_history(args.port)
        label = "Run history viz"
    else:
        pipeline = args.pipeline or "pipeline.yaml"
        cfg = load_config(pipeline)
        topology = topology_graph(cfg)
        bus = VizBus()
        httpd, _thread = serve_viz(bus, topology["mermaid"], cfg.name,
                                   args.port, topology["nodes"],
                                   topology["flow"], topology["edges"])
        label = "Live graph viz"

    bound = httpd.server_address[1]
    url = f"http://127.0.0.1:{bound}"
    print(f"{label}: {url}", flush=True)
    if args.port and bound != args.port:
        print(f"viz port {args.port} busy - serving on {url}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
