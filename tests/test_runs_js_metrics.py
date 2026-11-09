"""Regression: dict-valued metrics must not render as [object Object].

The five dict-valued metrics (attempt_count_distribution,
audit_step_durations, per_step_durations, retry_target_frequency,
verdict_distribution) serialize as JSON objects. runs.js metricCard
stringified them with String(value), so each card showed the literal text
"[object Object]". The card must render sorted key: value entries instead.

The prod asset is an IIFE with browser boot at the bottom, so the test
loads it in a node vm with minimal DOM stubs and a hook injected into the
read source (the file itself is untouched).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNS_JS = ROOT / "src" / "agentic_dev_loop" / "viz" / "assets" / "runs.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node required to exercise runs.js"
)

DRIVER = r"""
"use strict";
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[2], "utf8");
if (!/}\)\(\);\s*$/.test(source)) {
  throw new Error("runs.js no longer ends with the expected IIFE close");
}
// Export the renderers out of the IIFE so the test can drive them without
// a browser. The prod file on disk is only read, never modified.
const hooked = source.replace(
  /}\)\(\);\s*$/,
  "window.__hooks = { metricCard: metricCard, esc: esc };\n})();"
);

const sandbox = {
  window: { matchMedia: () => undefined, localStorage: undefined },
  document: {
    readyState: "loading", // keep init() from booting the app
    addEventListener: () => {},
    getElementById: () => null,
    documentElement: { setAttribute: () => {} }
  }
};
vm.runInNewContext(hooked, sandbox);

const metricCard = sandbox.window.__hooks.metricCard;
const results = {
  dict: metricCard("verdict_distribution", {
    value: { pass: 3, fail: 1 },
    availability: "available",
    basis: "2 runs"
  }),
  scalar: metricCard("attempt_count", {
    value: 42,
    availability: "available"
  }),
  emptyDict: metricCard("attempt_count_distribution", {
    value: {},
    availability: "available"
  }),
  missing: metricCard("audit_step_durations", {
    availability: "unavailable: no audit data"
  }),
  escaped: metricCard("retry_target_frequency", {
    value: { "a&b": "<tag>" },
    availability: "available"
  })
};
process.stdout.write(JSON.stringify(results));
"""


@pytest.fixture
def render_cards(tmp_path):
    driver = tmp_path / "driver.js"
    driver.write_text(DRIVER, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(driver), str(RUNS_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"node driver failed: {proc.stderr}"
    return json.loads(proc.stdout)


def test_dict_metric_renders_entries_not_object_tag(render_cards):
    html = render_cards["dict"]
    assert "[object Object]" not in html
    assert "pass: 3" in html
    assert "fail: 1" in html


def test_dict_metric_entries_are_sorted(render_cards):
    html = render_cards["dict"]
    assert html.index("fail: 1") < html.index("pass: 3")


def test_scalar_metric_renders_unchanged(render_cards):
    html = render_cards["scalar"]
    assert "[object Object]" not in html
    assert "42" in html


def test_empty_dict_metric_renders_dash(render_cards):
    html = render_cards["emptyDict"]
    assert "[object Object]" not in html
    assert "--" in html


def test_missing_metric_keeps_unavailable_text(render_cards):
    html = render_cards["missing"]
    assert "[object Object]" not in html
    assert "unavailable" in html


def test_dict_metric_escapes_keys_and_values(render_cards):
    html = render_cards["escaped"]
    assert "[object Object]" not in html
    assert "a&amp;b" in html
    assert "&lt;tag&gt;" in html
