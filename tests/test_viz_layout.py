"""Layout algorithm tests: golden parity with the reference ES5 plus the
invariants the unit-9 design pins (FD section 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agentic_dev_loop.viz.layout import (
    CH, GAP, LANE, LANE_PAD, LANE_TOP, NH, NW, STEP, layout_pipeline,
)

GOLDEN = Path(__file__).parent / "viz_layout_golden.json"


def _golden() -> list[dict]:
    return json.loads(GOLDEN.read_text())


def _width_for_rows(opts: dict, rows: int) -> tuple[int, int]:
    """Reference width formula for a forced row count (fewest-rows check)."""
    names = opts["nodes"]
    n = len(names)
    idx = {name: i for i, name in enumerate(names)}
    per = max(1, -(-n // rows))
    actual = max(1, -(-n // per))
    pos = [{"r": i // per, "c": i % per} for i in range(n)]
    classified = []
    for edge in opts.get("edges") or []:
        if edge["src"] not in idx or edge["dst"] not in idx:
            continue
        src, dst = idx[edge["src"]], idx[edge["dst"]]
        classified.append((pos[src]["r"], pos[dst]["r"]))
    for edge in opts.get("flow") or []:
        if edge["src"] in idx and edge["dst"] in idx:
            if idx[edge["dst"]] != idx[edge["src"]] + 1:
                src, dst = idx[edge["src"]], idx[edge["dst"]]
                classified.append((pos[src]["r"], pos[dst]["r"]))
    back = len([1 for rs, rt in classified if rs > rt])
    forward = len([1 for rs, rt in classified if rs < rt])
    multi = actual > 1
    left = CH + CH * back + 8 if multi else 0
    core = per * STEP - GAP
    right = (14 if multi else 0) + (8 + CH * forward if forward else 0)
    return per, left + core + right


def _gutter_gaps(result: dict) -> list[int]:
    ys = sorted({n["y"] for n in result["nodes"]})
    gaps = []
    for upper, lower in zip(ys, ys[1:]):
        gaps.append(lower - upper - NH)
    return gaps


@pytest.mark.parametrize("case", _golden(), ids=lambda c: c["name"])
def test_matches_reference_es5_golden(case):
    assert layout_pipeline(case["opts"]) == case["result"]


@pytest.mark.parametrize(
    "width, rows, per_row",
    [(1336, 1, 10), (1100, 2, 5), (700, 3, 4)],
)
def test_row_counts_at_spec_widths(width, rows, per_row):
    opts = next(c["opts"] for c in _golden() if c["name"] == "full-1336")
    opts = dict(opts, availWidth=width)
    result = layout_pipeline(opts)
    assert (result["rows"], result["perRow"]) == (rows, per_row)


def test_short_topology_single_centred_row():
    opts = next(c["opts"] for c in _golden() if c["name"] == "short-1100")
    result = layout_pipeline(opts)
    assert result["rows"] == 1
    assert result["perRow"] == 6
    assert result["width"] == 6 * STEP - GAP


def test_single_node_natural_size():
    result = layout_pipeline(
        {"nodes": ["only"], "flow": [], "edges": [], "status": {}, "availWidth": 800}
    )
    assert result["nodes"] == [
        {"id": "only", "index": 0, "x": 0, "y": 0, "w": NW, "h": NH}
    ]
    assert result["width"] == NW
    assert result["rows"] == 1


def test_stations_never_scaled_and_within_bounds():
    for case in _golden():
        result = layout_pipeline(case["opts"])
        for node in result["nodes"]:
            assert (node["w"], node["h"]) == (NW, NH)
            assert 0 <= node["x"] <= result["width"] - NW
            assert 0 <= node["y"] <= result["height"] - NH


def test_gutter_heights_follow_lane_formula():
    for case in _golden():
        result = layout_pipeline(case["opts"])
        for gap in _gutter_gaps(result):
            assert gap == 4 or (gap - (LANE_TOP + LANE_PAD)) % LANE == 0
        if result["rows"] == 1:
            assert result["height"] >= NH


def test_per_row_never_below_two_when_multi_row():
    opts = next(c["opts"] for c in _golden() if c["name"] == "full-1336")
    for width in range(40, 1400, 40):
        result = layout_pipeline(dict(opts, availWidth=width))
        if result["rows"] > 1:
            assert result["perRow"] >= 2


def test_fewest_rows_that_fit():
    opts = next(c["opts"] for c in _golden() if c["name"] == "full-1336")
    n = len(opts["nodes"])
    for width in (300, 500, 700, 900, 1100, 1336, 1600):
        result = layout_pipeline(dict(opts, availWidth=width))
        rows = result["rows"]
        per = result["perRow"]
        assert per == max(1, -(-n // rows))
        chosen_width = _width_for_rows(dict(opts, availWidth=width), rows)[1]
        assert chosen_width <= width or per <= 2
        if rows > 1:
            smaller_per, smaller_width = _width_for_rows(
                dict(opts, availWidth=width), rows - 1)
            assert smaller_width > width or smaller_per <= 2


def test_determinism_golden():
    for case in _golden():
        assert layout_pipeline(case["opts"]) == layout_pipeline(case["opts"])


def _proper_crossing(a: dict, b: dict) -> bool:
    horizontal = a if a["dir"] == "h" else b
    vertical = b if a["dir"] == "h" else a
    if horizontal["dir"] != "h" or vertical["dir"] != "v":
        return False
    if horizontal["w"] == 0 or vertical["h"] == 0:
        return False
    x1, x2 = horizontal["x"], horizontal["x"] + horizontal["w"]
    y = horizontal["y"]
    x = vertical["x"]
    y1, y2 = vertical["y"], vertical["y"] + vertical["h"]
    return x1 < x < x2 and y1 < y < y2


def _assert_no_back_crossings(result: dict) -> None:
    back = [line for line in result["lines"] if line["kind"] == "back"]
    for i, left in enumerate(back):
        for right in back[i + 1:]:
            assert not _proper_crossing(left, right), (left, right)


@settings(max_examples=120, deadline=None)
@given(
    count=st.integers(min_value=1, max_value=12),
    seed=st.integers(min_value=0, max_value=10_000),
)
def test_property_no_u_c_crossings(count, seed):
    import random

    rng = random.Random(seed)
    names = [f"s{i}" for i in range(count)]
    target = rng.randrange(0, max(1, count - 1))
    edges = []
    if count > 1:
        for _ in range(rng.randint(0, 6)):
            src = rng.randrange(target + 1, count)
            edges.append({"src": names[src], "dst": names[target],
                          "label": rng.choice([None, "fail", "FAIL"])})
    opts = {"nodes": names, "flow": [], "edges": edges, "status": {},
            "availWidth": rng.choice([400, 700, 1100, 1600])}
    _assert_no_back_crossings(layout_pipeline(opts))


@settings(max_examples=100, deadline=None)
@given(
    count=st.integers(min_value=1, max_value=12),
    flow_ratio=st.floats(min_value=0.0, max_value=1.0),
    edge_count=st.integers(min_value=0, max_value=5),
    width=st.sampled_from([320, 480, 700, 900, 1100, 1336, 1600, 2400]),
    seed=st.integers(min_value=0, max_value=10_000),
)
def test_property_layout_invariants(count, flow_ratio, edge_count, width, seed):
    import random

    rng = random.Random(seed)
    names = [f"s{i}" for i in range(count)]
    flow = [{"src": names[i], "dst": names[i + 1]}
            for i in range(count - 1) if rng.random() < flow_ratio]
    edges = []
    for _ in range(edge_count):
        src, dst = rng.randrange(count), rng.randrange(count)
        if src != dst:
            edges.append({"src": names[src], "dst": names[dst],
                          "label": rng.choice([None, "fail", "retry"])})
    status = {name: rng.choice(["pass", "running", "fail", "fatal",
                                "unverifiable", "pending"])
              for name in names if rng.random() < 0.5}
    opts = {"nodes": names, "flow": flow, "edges": edges, "status": status,
            "availWidth": width}
    result = layout_pipeline(opts)
    assert result == layout_pipeline(opts)
    assert result["rows"] >= 1 and result["perRow"] >= 1
    if result["rows"] > 1:
        assert result["perRow"] >= 2
    for node in result["nodes"]:
        assert (node["w"], node["h"]) == (NW, NH)
        assert 0 <= node["x"] <= result["width"] - NW
        assert 0 <= node["y"] <= result["height"] - NH
    for line in result["lines"]:
        assert 0 <= line["x"] <= result["width"]
        assert 0 <= line["y"] <= result["height"]
        assert line["dir"] in ("h", "v")
        assert line["kind"] in ("flow", "back")
        assert line["w"] == 0 or line["h"] == 0
    for arrow in result["arrows"]:
        assert arrow["dir"] in ("right", "up")
        assert 0 <= arrow["x"] <= result["width"]
        assert 0 <= arrow["y"] <= result["height"]
    for label in result["labels"]:
        assert label["text"]
        assert 0 <= label["x"] <= result["width"]
        assert 0 <= label["y"] <= result["height"]
