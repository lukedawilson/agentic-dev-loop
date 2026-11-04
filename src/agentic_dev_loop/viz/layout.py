"""Pure-Python port of the viz pipeline layout algorithm.

Mirrors ``planning-docs/design/pipeline-layout.js`` one-to-one (contract in
its header and in the unit-9 business-logic model): no DOM, no I/O, same
input/output shapes and the same crossing-free ordering rules. The reference
ES5 implementation remains the browser-side copy; this port is the tested
source of truth (decision Q3=A).
"""

from __future__ import annotations

from typing import Any

NW = 112
NH = 76
GAP = 24
STEP = NW + GAP
LANE = 18
LANE_TOP = 14
LANE_PAD = 16
CH = 12

METRICS = {"NW": NW, "NH": NH, "GAP": GAP, "STEP": STEP, "LANE": LANE}


class _Edge:
    """Mutable routing record, mirroring the JS object the reference mutates."""

    __slots__ = ("src", "dst", "label", "kind", "si", "ti", "rs", "rt",
                 "lane_s", "lane_t", "j", "k", "xe")

    def __init__(self, src: str, dst: str, label: str | None, kind: str):
        self.src = src
        self.dst = dst
        self.label = label
        self.kind = kind
        self.si = 0
        self.ti = 0
        self.rs = 0
        self.rt = 0
        self.lane_s = 0
        self.lane_t = 0
        self.j = 0
        self.k = 0
        self.xe = 0


def _plan(names: list[str], routed: list[_Edge], idx: dict[str, int], r: int) -> dict[str, Any]:
    n = len(names)
    per = max(1, (n + r - 1) // r)
    rows = max(1, (n + per - 1) // per)
    pos = [{"r": i // per, "c": i % per} for i in range(n)]
    edges = []
    for e in routed:
        rec = _Edge(e.src, e.dst, e.label, e.kind)
        rec.si = idx[e.src]
        rec.ti = idx[e.dst]
        rec.rs = pos[rec.si]["r"]
        rec.rt = pos[rec.ti]["r"]
        edges.append(rec)
    same = sorted((e for e in edges if e.rs == e.rt),
                  key=lambda e: abs(e.si - e.ti))
    back = sorted((e for e in edges if e.rs > e.rt),
                  key=lambda e: (e.rs, e.si))
    forward = sorted((e for e in edges if e.rs < e.rt),
                     key=lambda e: (e.rt, -e.si))
    for j, e in enumerate(back):
        e.j = j
    for k, e in enumerate(forward):
        e.k = k
    multi = rows > 1
    left = CH + CH * len(back) + 8 if multi else 0
    core = per * STEP - GAP
    right = (14 if multi else 0) + (8 + CH * len(forward) if forward else 0)
    return {"per": per, "rows": rows, "pos": pos, "U": same, "C": back,
            "F": forward, "O": left, "core": core, "width": left + core + right}


def layout_pipeline(opts: dict[str, Any]) -> dict[str, Any]:
    """Lay out stations and routes for ``opts`` (contract: FD section 1.1)."""
    names = list(opts.get("nodes") or [])
    n = len(names)
    status = opts.get("status") or {}
    avail = max(0, opts.get("availWidth") or 0)
    flow = opts.get("flow") or []
    cond_edges = opts.get("edges") or []
    idx = {name: i for i, name in enumerate(names)}

    def reached(name: str) -> bool:
        return (status.get(name) or "pending") != "pending"

    rails: list[dict[str, Any]] = []
    extra_flow: list[_Edge] = []
    for f in flow:
        if f["src"] not in idx or f["dst"] not in idx:
            continue
        if idx[f["dst"]] == idx[f["src"]] + 1:
            rails.append(f)
        else:
            extra_flow.append(_Edge(f["src"], f["dst"], None, "flow"))
    routed = [
        _Edge(e["src"], e["dst"], e.get("label") or None, "back")
        for e in cond_edges
        if e["src"] in idx and e["dst"] in idx
    ] + extra_flow

    layout = _plan(names, routed, idx, 1)
    r = 2
    while layout["width"] > avail and layout["per"] > 2:
        layout = _plan(names, routed, idx, r)
        r += 1
    offset = layout["O"]

    gutters: list[dict[str, int]] = []
    for g in range(layout["rows"]):
        items = 0

        def take() -> int:
            nonlocal items
            value = items
            items += 1
            return value

        for e in layout["U"]:
            if e.rt == g:
                e.lane_s = take()
        for e in sorted((e for e in layout["C"] if e.rt == g),
                        key=lambda e: -e.j):
            e.lane_t = take()
        for e in layout["F"]:
            if e.rt == g:
                e.lane_t = take()
        for e in sorted((e for e in layout["C"] if e.rs == g),
                        key=lambda e: e.si):
            e.lane_s = take()
        for e in layout["F"]:
            if e.rs == g:
                e.lane_s = take()
        wrap = take() if g < layout["rows"] - 1 else -1
        gutters.append({"n": items, "wrapLane": wrap})

    row_y = [0]
    for g, gutter in enumerate(gutters):
        gutter["h"] = (LANE_TOP + (gutter["n"] - 1) * LANE + LANE_PAD
                       if gutter["n"] else 4)
        row_y.append(row_y[g] + NH + gutter["h"])

    def lane_y(g: int, lane: int) -> int:
        return row_y[g] + NH + LANE_TOP + lane * LANE

    def node_x(i: int) -> int:
        return offset + layout["pos"][i]["c"] * STEP

    def node_y(i: int) -> int:
        return row_y[layout["pos"][i]["r"]]

    entry_count: dict[int, int] = {}
    for e in layout["U"] + layout["C"] + layout["F"]:
        m = entry_count.get(e.ti, 0)
        entry_count[e.ti] = m + 1
        e.xe = max(node_x(e.ti) + 8, node_x(e.ti) + NW - 14 - m * 14)

    lines: list[dict[str, Any]] = []
    arrows: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []

    def horizontal(x1: int, x2: int, y: int, kind: str, on: bool = False) -> None:
        lines.append({"x": min(x1, x2), "y": y, "w": abs(x2 - x1), "h": 0,
                      "dir": "h", "kind": kind, "on": bool(on)})

    def vertical(x: int, y1: int, y2: int, kind: str, on: bool = False) -> None:
        lines.append({"x": x, "y": min(y1, y2), "w": 0, "h": abs(y2 - y1),
                      "dir": "v", "kind": kind, "on": bool(on)})

    for f in rails:
        i = idx[f["src"]]
        on = reached(f["dst"])
        if layout["pos"][i + 1]["r"] == layout["pos"][i]["r"]:
            horizontal(node_x(i) + NW, node_x(i + 1), node_y(i) + 37, "flow", on)
            arrows.append({"x": node_x(i + 1) - 6, "y": node_y(i) + 33,
                           "dir": "right", "kind": "flow", "on": on})
        else:
            r = layout["pos"][i]["r"]
            xr = node_x(i) + NW + 12
            yw = lane_y(r, gutters[r]["wrapLane"])
            yn = node_y(i + 1) + 37
            horizontal(node_x(i) + NW, xr + 2, node_y(i) + 37, "flow", on)
            vertical(xr, node_y(i) + 37, yw, "flow", on)
            horizontal(offset - 12, xr + 2, yw - 1, "flow", on)
            vertical(offset - 12, yw - 1, yn + 2, "flow", on)
            horizontal(offset - 12, offset, yn, "flow", on)
            arrows.append({"x": offset - 6, "y": yn - 4, "dir": "right",
                           "kind": "flow", "on": on})

    for e in layout["U"] + layout["C"] + layout["F"]:
        xs = node_x(e.si) + NW // 2
        top = node_y(e.ti) + NH
        y1 = lane_y(e.rs, e.lane_s)
        vertical(xs, node_y(e.si) + NH, y1, e.kind)
        if e.rs == e.rt:
            horizontal(e.xe, xs, y1, e.kind)
            vertical(e.xe, top + 6, y1, e.kind)
        else:
            if e.rs > e.rt:
                xc = offset - CH - CH * (e.j + 1)
            else:
                xc = offset + layout["core"] + 14 + 8 + CH * e.k
            y2 = lane_y(e.rt, e.lane_t)
            horizontal(xc, xs, y1, e.kind)
            vertical(xc, y2, y1, e.kind)
            horizontal(xc, e.xe, y2, e.kind)
            vertical(e.xe, top + 6, y2, e.kind)
        arrows.append({"x": e.xe - 5, "y": top, "dir": "up", "kind": e.kind,
                       "on": False})
        if e.label:
            labels.append({"x": xs - 6, "y": y1 - 9, "text": e.label})

    return {
        "width": layout["width"],
        "height": row_y[layout["rows"]],
        "rows": layout["rows"],
        "perRow": layout["per"],
        "nodes": [{"id": name, "index": i, "x": node_x(i), "y": node_y(i),
                   "w": NW, "h": NH} for i, name in enumerate(names)],
        "lines": lines,
        "arrows": arrows,
        "labels": labels,
    }
