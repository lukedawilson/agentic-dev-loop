"""Module-split dependency-contract guard tests.

The core/engine/viz split is load-bearing (kept when the unit-4 Claude
adapter was ripped out): ``core`` must import neither ``engine`` nor ``viz``
nor any heavy SDK dep, and ``viz`` must stay stdlib-only plus dependency-light
``core``. These guards pin that contract at import time and from a fresh
interpreter.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import agentic_dev_loop

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

FORBIDDEN = ("agentic_dev_loop.engine", "agentic_dev_loop.viz",
             "openhands", "langgraph", "langchain", "docker")

# `viz` may import `core` (dependency-light pydantic/PyYAML) but never the
# heavy engine/SDK deps.
VIZ_FORBIDDEN = ("agentic_dev_loop.engine", "openhands", "langgraph",
                 "langchain", "docker")


def _forbidden_imports(py_dir: Path, forbidden: tuple[str, ...]) -> list[str]:
    offenders: list[str] = []
    for py in py_dir.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                if name.startswith(forbidden):
                    offenders.append(f"{py.name}: forbidden import {name!r}")
    return offenders


def test_core_import_graph_guard():
    core_dir = Path(agentic_dev_loop.core.__file__).parent
    assert _forbidden_imports(core_dir, FORBIDDEN) == []


def test_viz_import_graph_guard():
    import agentic_dev_loop.viz
    viz_dir = Path(agentic_dev_loop.viz.__file__).parent
    assert _forbidden_imports(viz_dir, VIZ_FORBIDDEN) == []


def test_core_fresh_import_loads_no_heavy_deps():
    code = (
        "import sys, json\n"
        "import agentic_dev_loop.core\n"
        "heavy = sorted(m for m in sys.modules if any(\n"
        "    m == p or m.startswith(p + '.') for p in "
        "['openhands', 'langgraph', 'langchain', 'docker', "
        "'agentic_dev_loop.engine', 'agentic_dev_loop.viz']))\n"
        "print(json.dumps(heavy))\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, env=env,
                         cwd=REPO_ROOT)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", out.stdout


def test_core_profiles_fresh_import_loads_no_heavy_deps():
    code = (
        "import sys, json\n"
        "import agentic_dev_loop.core.profiles\n"
        "heavy = sorted(m for m in sys.modules if any(\n"
        "    m == p or m.startswith(p + '.') for p in "
        "['openhands', 'langgraph', 'langchain', 'docker', "
        "'agentic_dev_loop.engine', 'agentic_dev_loop.viz']))\n"
        "print(json.dumps(heavy))\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, env=env,
                         cwd=REPO_ROOT)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", out.stdout


def test_viz_fresh_import_loads_no_heavy_deps():
    code = (
        "import sys, json\n"
        "import agentic_dev_loop.viz\n"
        "heavy = sorted(m for m in sys.modules if any(\n"
        "    m == p or m.startswith(p + '.') for p in "
        "['openhands', 'langgraph', 'langchain', 'docker', "
        "'agentic_dev_loop.engine']))\n"
        "print(json.dumps(heavy))\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, env=env,
                         cwd=REPO_ROOT)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", out.stdout


def test_runtimes_package_layout_and_light_imports():
    """Unit 10 (BR-10.10): the runtimes package is present with one module per
    concern and imports without either optional harness SDK."""
    import importlib

    modules = [
        "protocol", "catalog", "bridge", "openhands_runtime", "factory",
        "mcp_server", "async_bridge", "claude_runtime", "copilot_auth",
        "copilot_runtime",
    ]
    for name in modules:
        importlib.import_module(f"agentic_dev_loop.engine.runtimes.{name}")
    assert "claude_agent_sdk" not in sys.modules
    assert "openai_codex" not in sys.modules
