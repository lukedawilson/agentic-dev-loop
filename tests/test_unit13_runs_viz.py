"""Unit 13 - the read-only historical runs view.

Covers the view-model serialization, the /runs HTTP routes, the history
launcher mode, and the generated-page drift guard. Data comes from the same
real unit-12 fixture databases the reporting suite uses; the server is
exercised over a real socket on port 0.
"""

from __future__ import annotations

import hashlib
import http.client
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentic_dev_loop.viz import VizBus, serve_history, serve_viz
from agentic_dev_loop.viz import runs_view
from agentic_dev_loop.viz.page import PAGE, RUNS_PAGE

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "unit12"


@pytest.fixture
def runs_env(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    state = tmp_path / "state"
    shutil.copytree(FIXTURES / "runs", runs)
    shutil.copytree(FIXTURES / "state", state)
    monkeypatch.setenv("AGENTIC_DEV_LOOP_RUNS_ROOT", str(runs))
    monkeypatch.setenv("AGENTIC_DEV_LOOP_STATE_DIR", str(state))
    return runs, state


def _request(port, path, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path)
    response = conn.getresponse()
    body = response.read()
    headers = dict(response.getheaders())
    conn.close()
    return response.status, headers, body


def _json(port, path):
    status, _headers, body = _request(port, path)
    return status, json.loads(body.decode("utf-8"))


@pytest.fixture
def pipeline_server():
    httpd, thread = serve_viz(VizBus(), "graph TD;\n", "subject", 0)
    yield httpd.server_address[1]
    httpd.shutdown()
    thread.join(timeout=5)


@pytest.fixture
def history_server():
    httpd, thread = serve_history(0)
    yield httpd.server_address[1]
    httpd.shutdown()
    thread.join(timeout=5)


class TestViewPayloads:
    def test_list_payload_has_records_and_serialized_metrics(self, runs_env):
        payload = runs_view.list_payload(None, None, [])
        assert set(payload) == {"filters", "records", "metrics", "notice"}
        assert payload["filters"] == {
            "since": None, "until": None, "statuses": []}
        assert len(payload["records"]) == 6
        record = next(r for r in payload["records"]
                      if r["thread_id"] == "abcdef123456" or
                      r["run_dir"].endswith("abcdef123456"))
        assert set(record) >= {
            "thread_id", "run_dir", "subject", "ts_start", "ts_end",
            "status", "attempt_count", "verdict_summary"}
        assert record["attempt_count"] == 2
        assert isinstance(record["verdict_summary"], dict)
        loop = payload["metrics"]["loop_health"]
        first = next(iter(loop.values()))
        assert set(first) == {
            "name", "value", "provenance", "basis", "availability",
            "numerator", "denominator", "detail"}
        assert "threads" in payload["metrics"]
        # The payload must be pure JSON (ASCII, no dataclasses).
        encoded = json.dumps(payload, ensure_ascii=True)
        assert encoded.isascii()

    def test_list_payload_status_filter(self, runs_env):
        payload = runs_view.list_payload(None, None, ["success"])
        assert payload["records"]
        assert {r["status"] for r in payload["records"]} == {"success"}
        failed = runs_view.list_payload(None, None, ["failed"])
        assert failed["records"]
        assert {r["status"] for r in failed["records"]} == {"failed"}
        empty = runs_view.list_payload("2099-01-01", None, [])
        assert empty["records"] == []

    def test_parse_filters_rejects_bad_values(self):
        assert runs_view.parse_filters("not-a-date", None, None)[0] is None
        assert runs_view.parse_filters(None, "also-bad", None)[0] is None
        assert runs_view.parse_filters(None, None, ["nope"])[0] is None
        filters, error = runs_view.parse_filters(
            "2026-01-01", "2026-02-01", ["success", "failed"])
        assert error is None
        assert filters["statuses"] == ["success", "failed"]

    def test_detail_payload_shape_and_degradation(self, runs_env):
        payload = runs_view.detail_payload("abcdef123456")
        assert payload["run_id"] == "abcdef123456"
        assert len(payload["threads"]) == 2
        readable = next(t for t in payload["threads"]
                        if t["status"] == "success")
        assert [a["number"] for a in readable["attempts"]] == [1, 2]
        assert readable["attempts"][0]["verdicts"][0]["step"] == "brief"
        assert set(readable["metrics"]) >= {"status", "attempt_count"}
        artifacts = payload["artifacts"]
        assert set(artifacts) >= {"brief", "summary", "findings", "qa"}
        assert artifacts["summary"]["present"] is True
        assert artifacts["brief"]["present"] is False
        assert "facts" in artifacts
        assert payload["notice"] is None
        json.dumps(payload, ensure_ascii=True)

    def test_detail_payload_unknown_run_is_none(self, runs_env):
        assert runs_view.detail_payload("does-not-exist") is None

    def test_detail_payload_carries_unreadable_thread(self, runs_env):
        payload = runs_view.detail_payload("222222222222")
        unreadable = [t for t in payload["threads"]
                      if t["status"] == "unreadable"]
        assert unreadable and unreadable[0]["thread_id"] == "bad"
        assert unreadable[0]["metrics"]["status"]["availability"] == "available"


class TestRoutes:
    def test_runs_pages_and_data(self, runs_env, pipeline_server):
        status, headers, body = _request(pipeline_server, "/runs")
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        assert body.decode("utf-8") == RUNS_PAGE

        status, payload = _json(pipeline_server, "/runs/data")
        assert status == 200
        assert payload["records"]

        status, _headers, body = _request(
            pipeline_server, "/runs/abcdef123456")
        assert status == 200
        assert body.decode("utf-8") == RUNS_PAGE

        status, detail = _json(
            pipeline_server, "/runs/abcdef123456/data")
        assert status == 200
        assert detail["run_id"] == "abcdef123456"

    def test_unknown_run_is_404_json(self, runs_env, pipeline_server):
        status, payload = _json(pipeline_server, "/runs/nope/data")
        assert status == 404
        assert "error" in payload

    def test_invalid_filters_are_400_json(self, runs_env, pipeline_server):
        status, payload = _json(
            pipeline_server, "/runs/data?since=not-a-date")
        assert status == 400
        assert "error" in payload

    def test_non_get_is_405_with_allow(self, runs_env, pipeline_server):
        status, headers, body = _request(
            pipeline_server, "/runs/data", method="POST")
        assert status == 405
        assert headers["Allow"] == "GET"
        assert json.loads(body.decode("utf-8"))["error"]

    def test_pipeline_mode_keeps_topology_and_live_page(
            self, runs_env, pipeline_server):
        status, payload = _json(pipeline_server, "/topology")
        assert status == 200
        assert payload["subject"] == "subject"
        status, _headers, body = _request(pipeline_server, "/")
        assert status == 200
        assert body.decode("utf-8") == PAGE
        assert 'href="/runs"' in PAGE

    def test_history_mode_serves_runs_without_topology(
            self, runs_env, history_server):
        for path in ("/", "/runs"):
            status, headers, body = _request(history_server, path)
            assert status == 200
            assert body.decode("utf-8") == RUNS_PAGE
        assert _request(history_server, "/topology")[0] == 404
        assert _request(history_server, "/events")[0] == 404
        status, payload = _json(history_server, "/runs/data")
        assert status == 200
        assert payload["records"]


class TestBuildPipeline:
    def test_generated_page_matches_rebuild(self):
        tool = _load_build_tool()
        assert tool.main(["--check"]) == 0

    def test_generated_page_carries_both_pages(self):
        assert PAGE
        assert RUNS_PAGE
        assert "id=\"runs-root\"" in RUNS_PAGE
        assert "echarts" in RUNS_PAGE.lower()
        assert RUNS_PAGE.isascii()

    def test_vendored_echarts_is_pinned_and_ascii(self):
        vendor = (ROOT / "src" / "agentic_dev_loop" / "viz" / "assets"
                  / "echarts.min.js")
        data = vendor.read_bytes()
        assert len(data) == 1122885
        assert hashlib.sha256(data).hexdigest() == (
            "182157aedac7b25b5ea8f5d2ddb1a3a3eb6c3116492c157156ef6dda86270757")
        data.decode("ascii")

    @pytest.mark.skipif(shutil.which("node") is None,
                        reason="node required to exercise echarts")
    def test_vendored_echarts_renders_gauge_series(self):
        # Regression: the common bundle registered a gauge series that
        # rendered nothing (empty SVG shell), leaving the key-stat gauges
        # blank on the runs page. The vendored bundle must render real
        # gauge output.
        vendor = (ROOT / "src" / "agentic_dev_loop" / "viz" / "assets"
                  / "echarts.min.js")
        driver = r"""
const fs = require("fs");
const vm = require("vm");
const code = fs.readFileSync(process.argv[1], "utf8");
const ctx = {
  console, setTimeout: () => 0, clearTimeout: () => 0,
  setInterval: () => 0, clearInterval: () => 0,
  requestAnimationFrame: () => 0, cancelAnimationFrame: () => 0
};
vm.createContext(ctx);
vm.runInContext(code, ctx);
const chart = ctx.echarts.init(null, null, {
  renderer: "svg", ssr: true, width: 400, height: 300
});
chart.setOption({ series: [{ type: "gauge", data: [{ value: 50 }] }] });
console.log(chart.renderToSVGString().length);
process.exit(0);
"""
        result = subprocess.run(
            ["node", "-e", driver, str(vendor)],
            capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        svg_len = int(result.stdout.strip())
        assert svg_len > 1000


def _load_build_tool():
    spec = importlib.util.spec_from_file_location(
        "build_viz_page_unit13", ROOT / "tools" / "build_viz_page.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
