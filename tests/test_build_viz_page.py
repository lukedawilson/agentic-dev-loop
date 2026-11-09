"""The build tool must leave page.py and the emitted layout asset consistent
after a SINGLE run.

Regression: main() wrote page.py (embedding the stale emitted copy of
pipeline-layout.js) before overwriting that copy with the updated reference.
One build run therefore left page.py stale whenever the reference had
changed, and the drift check failed until a second run healed it.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "build_viz_page.py"


@pytest.fixture
def build_tool(tmp_path):
    spec = importlib.util.spec_from_file_location("build_viz_page", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _scaffold(build_tool, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "page.html").write_text(
        '<link rel="stylesheet" href="styles.css">'
        '<script src="pipeline-layout.js"></script>'
        '<script src="page.js"></script>')
    (assets / "styles.css").write_text("body { color: red; }")
    (assets / "page.js").write_text("// page")
    # Stale emitted copy: the reference has moved on.
    (assets / "pipeline-layout.js").write_text("var OLD = 1;\n")
    reference = tmp_path / "reference-layout.js"
    reference.write_text("var NEW = 2;\n")
    build_tool.ROOT = tmp_path  # main() prints paths relative to ROOT
    build_tool.ASSETS = assets
    build_tool.PAGE_HTML = assets / "page.html"
    build_tool.STYLES_CSS = assets / "styles.css"
    build_tool.PAGE_JS = assets / "page.js"
    build_tool.LAYOUT_JS = assets / "pipeline-layout.js"
    build_tool.RUNS_HTML = assets / "runs.html"
    build_tool.RUNS_JS = assets / "runs.js"
    build_tool.ECHARTS_JS = assets / "echarts.common.min.js"
    build_tool.PAGE_PY = tmp_path / "page.py"
    build_tool.REFERENCE_LAYOUT = reference


def test_single_write_run_leaves_page_consistent(build_tool, tmp_path,
                                                 capsys):
    _scaffold(build_tool, tmp_path)
    # One write-mode run must both emit the updated layout asset and embed
    # the SAME content in page.py.
    assert build_tool.main([]) == 0
    # The drift check must then pass without a second build run.
    assert build_tool.main(["--check"]) == 0


def test_reference_is_what_gets_embedded(build_tool, tmp_path):
    _scaffold(build_tool, tmp_path)
    assert build_tool.main([]) == 0
    page = (tmp_path / "page.py").read_text()
    assert "var NEW = 2;" in page, "page.py embedded the stale layout copy"
    assert (tmp_path / "assets" / "pipeline-layout.js").read_text() == (
        "var NEW = 2;\n")
