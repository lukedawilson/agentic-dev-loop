"""Standalone viz launcher: `python -m agentic_dev_loop.viz --pipeline
<pipeline.yaml>` serves the live graph on localhost."""

from __future__ import annotations

import argparse
import sys

from agentic_dev_loop.core.config import load_config

from . import VizBus, serve_viz, topology_graph


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentic-dev-loop-viz",
        description="Serve the agentic-dev-loop live graph for a pipeline.")
    parser.add_argument("--pipeline", default="pipeline.yaml",
                        help="pipeline YAML for the flowchart topology")
    parser.add_argument("--port", type=int, default=8321,
                        help="preferred port (default 8321; free-port "
                             "fallback when busy)")
    args = parser.parse_args(argv)

    cfg = load_config(args.pipeline)
    topology = topology_graph(cfg)
    bus = VizBus()
    httpd, _thread = serve_viz(bus, topology["mermaid"], cfg.name, args.port,
                               topology["nodes"], topology["flow"],
                               topology["edges"])
    bound = httpd.server_address[1]
    url = f"http://127.0.0.1:{bound}"
    print(f"Live graph viz: {url}", flush=True)
    if args.port and bound != args.port:
        print(f"viz port {args.port} busy - serving on {url}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
