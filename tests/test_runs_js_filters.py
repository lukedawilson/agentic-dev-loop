"""Regression: runs page filters auto-apply; Refresh/Apply are gone.

The list page used to wait for a form submit (Apply) to reload and
offered a Refresh button. Both were removed: date and status inputs
reload the list as soon as they change, Reset clears and reloads, and
the form submit (Enter in a date field) only prevents a page reload.

The prod asset is an IIFE, so the test boots it in a node vm with a
recording DOM stub: fetch URLs and per-element listener registrations
are captured, then the submit/change/click paths are fired to prove
which inputs trigger a reload.
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

function makeEl(id) {
  return {
    id: id, value: "", checked: false, hidden: false, listeners: {},
    setAttribute: function () {}, removeAttribute: function () {},
    addEventListener: function (type, fn) {
      (this.listeners[type] || (this.listeners[type] = [])).push(fn);
    },
    fire: function (type, event) {
      (this.listeners[type] || []).forEach(function (fn) {
        fn(event || { preventDefault: function () {} });
      });
    }
  };
}

const els = {};
const statusBoxes = [];
for (let i = 0; i < 4; i++) { statusBoxes.push(makeEl("status-" + i)); }

const fetchCalls = [];
const replaceCalls = [];
const sandbox = {
  window: {
    matchMedia: () => undefined,
    localStorage: { getItem: () => null, setItem: () => {} },
    addEventListener: () => {}
  },
  document: {
    readyState: "complete", // boot init() immediately
    hidden: false,
    addEventListener: () => {},
    getElementById: (id) => {
      if (!(id in els)) { els[id] = makeEl(id); }
      return els[id];
    },
    querySelectorAll: (sel) => {
      if (sel === 'input[name="status"]') { return statusBoxes; }
      if (sel === 'input[name="status"]:checked') {
        return statusBoxes.filter((b) => b.checked);
      }
      return [];
    },
    documentElement: { setAttribute: () => {} }
  },
  location: { pathname: "/history", search: "" },
  history: { replaceState: (state, title, url) => { replaceCalls.push(url); } },
  fetch: (url) => { fetchCalls.push(url); return new Promise(() => {}); },
  URLSearchParams
};
vm.runInNewContext(source, sandbox);

const base = fetchCalls.length;

let prevented = false;
els.filters.fire("submit", { preventDefault: () => { prevented = true; } });
const afterSubmit = fetchCalls.length;

els.since.value = "2026-11-01";
els.since.fire("change");
const afterSinceChange = fetchCalls.length;
const urlAfterSinceChange = fetchCalls[fetchCalls.length - 1];

els.reset.fire("click");
const afterReset = fetchCalls.length;
const urlAfterReset = fetchCalls[fetchCalls.length - 1];

process.stdout.write(JSON.stringify({
  bootFetches: base,
  submitPrevented: prevented,
  fetchesAfterSubmit: afterSubmit,
  fetchesAfterSinceChange: afterSinceChange,
  urlAfterSinceChange: urlAfterSinceChange,
  fetchesAfterReset: afterReset,
  urlAfterReset: urlAfterReset,
  sinceValueAfterReset: els.since.value,
  filtersEvents: Object.keys(els.filters.listeners),
  sinceEvents: Object.keys(els.since.listeners),
  untilEvents: Object.keys(els.until.listeners),
  resetEvents: Object.keys(els.reset.listeners),
  statusEvents: statusBoxes.map((b) => Object.keys(b.listeners)),
  queriedApply: "apply" in els,
  queriedRefresh: "refresh" in els
}));
"""


@pytest.fixture
def boot_runs_js(tmp_path):
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


def test_boot_loads_once(boot_runs_js):
    assert boot_runs_js["bootFetches"] == 1


def test_submit_does_not_reload(boot_runs_js):
    assert boot_runs_js["submitPrevented"]
    assert boot_runs_js["fetchesAfterSubmit"] == boot_runs_js["bootFetches"]


def test_date_change_reloads(boot_runs_js):
    assert boot_runs_js["fetchesAfterSinceChange"] == 2
    assert "since=2026-11-01" in boot_runs_js["urlAfterSinceChange"]


def test_reset_clears_and_reloads(boot_runs_js):
    assert boot_runs_js["fetchesAfterReset"] == 3
    assert boot_runs_js["sinceValueAfterReset"] == ""
    assert boot_runs_js["urlAfterReset"] == "/history/data"


def test_auto_apply_bindings(boot_runs_js):
    assert boot_runs_js["filtersEvents"] == ["submit"]
    assert boot_runs_js["sinceEvents"] == ["change"]
    assert boot_runs_js["untilEvents"] == ["change"]
    assert boot_runs_js["resetEvents"] == ["click"]
    assert all(events == ["change"]
               for events in boot_runs_js["statusEvents"])


def test_apply_and_refresh_are_gone(boot_runs_js):
    assert not boot_runs_js["queriedApply"], (
        "runs.js must not reference the removed Apply button")
    assert not boot_runs_js["queriedRefresh"], (
        "runs.js must not reference the removed Refresh button")
