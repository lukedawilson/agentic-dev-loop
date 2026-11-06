#!/usr/bin/env python3
"""Smoke-test the agentic-dev-loop engine FROM SOURCE against a smoke plan.

Runs the engine straight from this repository's source tree (PYTHONPATH=src,
no wheel, no pip install) in a DISPOSABLE GIT WORKTREE of this repo, so the
workspace is never the source tree (the host-boundary check stays green and
the main checkout is never touched). The run is driven by a config directory:

    <config-dir>/
        pipeline.yaml          pipeline template (deps -> dev -> test)
        agents/dev.agent.md    the agent definition used by the dev step
        plan.md                optional plan document (fed as an additional
                               file when present)

The plan this repo ships (tests/smoke/viz-keepalive/) implements the keep-
alive change: the viz server keeps serving after the run ends and the engine
prints "Pipeline finished: press ctrl+c to exit". This script streams the
engine output, waits for that line, probes the viz endpoint, sends ctrl+c,
and reports PASS/FAIL plus the files the agent changed in the worktree.

On-demand local tooling: never run in CI.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = REPO_ROOT / "src"

KEEPALIVE_MARKER = "Pipeline finished: press ctrl+c to exit"


def free_port() -> int:
    """A port that is free right now (best effort, race-free enough)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fail(message: str, code: int = 2) -> None:
    print(f"run_smoke: {message}", file=sys.stderr)
    sys.exit(code)


def check_assets_clean(config_dir: Path) -> None:
    """The smoke assets must be committed (tooling-integrity refuses dirty
    tooling paths; a fresh worktree only carries committed files)."""
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", str(config_dir)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        fail(f"git status failed: {result.stderr.strip()}", 1)
    if result.stdout.strip():
        fail(
            f"smoke assets are dirty relative to HEAD - commit them first "
            f"(the engine refuses dirty tooling paths and a worktree only "
            f"carries committed files):\n" + result.stdout, 1)


def create_worktree(worktree_dir: Path) -> None:
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree_dir)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        fail(f"git worktree add failed: {result.stderr.strip()}", 1)


def remove_worktree(worktree_dir: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_dir)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def build_pipeline(worktree: Path, config_dir: Path, tmpdir: Path) -> Path:
    """Copy the template pipeline into a temp dir with absolute paths.

    agents_dir and additional_files become ABSOLUTE worktree paths (the
    loader resolves pipeline-dir-relative paths, and absolute paths pass
    through; they must resolve inside the workspace and be clean). The
    template carries the full provider config already (no injection).
    """
    template = (config_dir / "pipeline.yaml").read_text(encoding="utf-8")
    text = template
    agents_in_worktree = worktree / config_dir.relative_to(REPO_ROOT) / "agents"
    text = text.replace(
        "agents_dir: agents\n",
        f"agents_dir: {json.dumps(str(agents_in_worktree))}\n",
        1)
    plan_in_worktree = worktree / config_dir.relative_to(REPO_ROOT) / "plan.md"
    if (config_dir / "plan.md").is_file():
        text = text.replace(
            "additional_files: []\n",
            f"additional_files: [{json.dumps(str(plan_in_worktree))}]\n",
            1)
    pipeline = tmpdir / "pipeline.yaml"
    pipeline.write_text(text, encoding="utf-8")
    return pipeline


def probe_viz(port: int) -> bool:
    url = f"http://127.0.0.1:{port}/topology"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test the engine from source against a smoke plan.")
    parser.add_argument(
        "config_dir", type=Path,
        help="smoke config directory containing pipeline.yaml and "
             "agents/dev.agent.md (and optionally plan.md, fed to the run "
             "as an additional file)")
    parser.add_argument(
        "--worktree-dir", type=Path, default=None,
        help="worktree path (default: ~/.agentic-dev-loop/smoke/worktrees/"
             "<config-dir-name>-<timestamp>)")
    parser.add_argument(
        "--keep-worktree", action="store_true",
        help="keep the worktree after the run for inspection")
    parser.add_argument(
        "--viz-port", type=int, default=None,
        help="viz port (default: a free port chosen automatically)")
    parser.add_argument(
        "--timeout", type=int, default=3600,
        help="engine run timeout in seconds (default: 3600)")
    parser.add_argument(
        "--engine-python", type=Path,
        default=REPO_ROOT / ".venv" / "bin" / "python",
        help="python interpreter that can import the engine's dependencies "
             "(default: <repo>/.venv/bin/python)")
    args = parser.parse_args()

    engine_python = args.engine_python
    if not engine_python.is_file():
        fail(f"engine python not found: {engine_python} "
             "(build the repo venv or pass --engine-python)")
    config_dir = args.config_dir.expanduser().resolve()
    if not config_dir.is_dir():
        fail(f"config directory not found: {config_dir}")
    if not (config_dir / "pipeline.yaml").is_file():
        fail(f"pipeline template not found: {config_dir / 'pipeline.yaml'}")
    if not (config_dir / "agents" / "dev.agent.md").is_file():
        fail(f"dev agent not found: {config_dir / 'agents' / 'dev.agent.md'}")

    check_assets_clean(config_dir)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    worktree = args.worktree_dir or (
        Path("~/.agentic-dev-loop/smoke/worktrees").expanduser()
        / f"{config_dir.name}-{stamp}")
    if worktree.exists():
        fail(f"worktree already exists: {worktree}")

    port = args.viz_port or free_port()

    print(f"[smoke] config dir: {config_dir}")
    print(f"[smoke] worktree: {worktree}")
    print(f"[smoke] viz port: {port}")
    print("[smoke] creating worktree from HEAD...")
    create_worktree(worktree)
    try:
        tmpdir = Path(tempfile.mkdtemp(prefix="run-smoke-"))
        pipeline = build_pipeline(worktree, config_dir, tmpdir)
        print(f"[smoke] pipeline: {pipeline}")

        bootstrap = (
            "import sys\n"
            "from agentic_dev_loop.engine.cli import main as cli_main\n"
            "sys.exit(cli_main(sys.argv[1:]))\n")
        cmd = [
            str(engine_python), "-c", bootstrap,
            str(pipeline), "--viz-port", str(port),
        ]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC_DIR)
        print(f"[smoke] running engine from source (PYTHONPATH={SRC_DIR}) "
              f"with cwd={worktree}")

        proc = subprocess.Popen(
            cmd, cwd=str(worktree), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
        lines: "queue.Queue[str]" = queue.Queue()

        def pump() -> None:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, ""):
                print(line, end="", flush=True)
                lines.put(line)

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()

        deadline = time.monotonic() + args.timeout
        marker_seen = False
        engine_rc = None
        while time.monotonic() < deadline:
            if marker_seen:
                break
            try:
                line = lines.get(timeout=0.25)
            except queue.Empty:
                if proc.poll() is not None:
                    engine_rc = proc.wait()
                    break
                continue
            if KEEPALIVE_MARKER in line:
                marker_seen = True

        if marker_seen:
            print("[smoke] keep-alive line observed")
            viz_ok = probe_viz(port)
            print(f"[smoke] viz /topology reachable after completion: "
                  f"{'yes' if viz_ok else 'NO'}")
            proc.send_signal(signal.SIGINT)
            try:
                engine_rc = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                engine_rc = proc.wait()
                print("[smoke] engine did not exit after ctrl+c; killed")
        else:
            if engine_rc is None:
                proc.kill()
                engine_rc = proc.wait()
                print(f"[smoke] engine did not finish within "
                      f"{args.timeout}s; killed")
            print(f"[smoke] keep-alive line NOT observed (change not "
                  f"present, or the run failed first); engine exit code "
                  f"{engine_rc}")
            viz_ok = probe_viz(port)
            print(f"[smoke] viz /topology reachable: {'yes' if viz_ok else 'no'}")

        changed = subprocess.run(
            ["git", "status", "--short"], cwd=worktree,
            capture_output=True, text=True, check=False).stdout.strip()
        if changed:
            print("[smoke] files changed by the run in the worktree:")
            print(changed)
        else:
            print("[smoke] no files changed in the worktree")

        passed = bool(marker_seen and viz_ok and engine_rc == 0)
        print(f"[smoke] engine exit code: {engine_rc}")
        print(f"[smoke] RESULT: {'PASS' if passed else 'FAIL'}")
        return 0 if passed else 1
    finally:
        if args.keep_worktree:
            print(f"[smoke] keeping worktree: {worktree}")
        else:
            remove_worktree(worktree)
            print("[smoke] worktree removed")


if __name__ == "__main__":
    sys.exit(main())
