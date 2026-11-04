/*
 * pipeline-layout.js - reference implementation of the viz pipeline layout.
 *
 * Pure function, no DOM access, ES5 (matches the inline-script style of
 * src/agentic_dev_loop/viz/server.py PAGE). Usable as a <script> (exposes
 * window.layoutPipeline) or via require()/import in tests.
 *
 * Contract
 *   layoutPipeline({
 *     nodes:      ["brief", "deps", "dev", ...],          // display order (= /topology nodes)
 *     flow:       [{src, dst}],                           // on_pass chain (solid)
 *     edges:      [{src, dst, label|null}],               // on_fail routes (dashed)
 *     status:     {nodeId: "pass"|"running"|"fail"|"fatal"|"unverifiable"|"pending"},
 *     availWidth: 1200                                     // px available to the graph
 *   }) -> {
 *     width, height, rows, perRow,
 *     nodes:  [{id, index, x, y, w, h}],
 *     lines:  [{x, y, w, h, kind: "flow"|"back", on: bool, dir: "h"|"v"}],
 *     arrows: [{x, y, dir: "right"|"up", kind, on}],     // x,y = top-left of a CSS-border triangle box
 *     labels: [{x, y, text}]                             // x = right edge (translateX(-100%)), y = top
 *   }
 *
 * Algorithm
 *   1. Stations keep natural size (NW x NH). Choose the FEWEST rows R such that
 *      the row (perRow = ceil(N/R)) plus routing margins fits availWidth.
 *      perRow never drops below 2; if 2 still doesn't fit, the caller scrolls.
 *   2. Consecutive flow edges in a row = rail with arrow. Row change = wrap
 *      connector: right stub -> down to the row's deepest gutter lane -> left
 *      to the left channel -> down -> right into the next row's first station.
 *   3. Conditional edges route orthogonally:
 *        U  same row (back or forward): drop into gutter, run, rise into target.
 *        C  backward across rows: drop into source gutter, run left to a
 *           left-hand channel, rise to the target row's gutter, run right,
 *           rise into the target.
 *        F  forward across rows (e.g. -> failure terminal): mirrored through
 *           a right-hand channel, descending.
 *   4. Crossing-free ordering (proved for U and C; F is best-effort):
 *        gutter lanes, top -> bottom: U (shortest span first), C-into-row
 *        (outer channel first), F-into-row, C-leaving-row (leftmost source
 *        first), F-leaving-row, wrap connector last;
 *        C channels: inner -> outer by (source row asc, source index asc);
 *        target entry points: U rightmost, then C inner -> outer, then F,
 *        stepping 14px leftwards across the target's bottom edge.
 */
(function (root) {
  "use strict";

  var NW = 112, NH = 76, GAP = 24, STEP = NW + GAP;
  var LANE = 18, LANE_TOP = 14, LANE_PAD = 16, CH = 12;

  function layoutPipeline(opts) {
    var names = opts.nodes || [];
    var N = names.length;
    var status = opts.status || {};
    var avail = Math.max(0, opts.availWidth || 0);
    var flow = opts.flow || [];
    var condEdges = opts.edges || [];
    var idx = {};
    names.forEach(function (n, i) { idx[n] = i; });
    var reached = function (n) { return (status[n] || "pending") !== "pending"; };

    // flow edges that are NOT to the next station are routed like conditional edges (solid)
    var rails = [], extraFlow = [];
    flow.forEach(function (f) {
      if (!(f.src in idx) || !(f.dst in idx)) { return; }
      if (idx[f.dst] === idx[f.src] + 1) { rails.push(f); }
      else { extraFlow.push({ src: f.src, dst: f.dst, label: null, kind: "flow" }); }
    });
    var routed = condEdges.filter(function (e) { return (e.src in idx) && (e.dst in idx); })
      .map(function (e) { return { src: e.src, dst: e.dst, label: e.label || null, kind: "back" }; })
      .concat(extraFlow);

    function plan(R) {
      var per = Math.max(1, Math.ceil(N / R));
      var rows = Math.max(1, Math.ceil(N / per));
      var pos = names.map(function (n, i) { return { r: Math.floor(i / per), c: i % per }; });
      var es = routed.map(function (e) {
        var si = idx[e.src], ti = idx[e.dst];
        return { label: e.label, kind: e.kind, dst: e.dst, si: si, ti: ti, rs: pos[si].r, rt: pos[ti].r };
      });
      var U = es.filter(function (e) { return e.rs === e.rt; })
        .sort(function (a, b) { return Math.abs(a.si - a.ti) - Math.abs(b.si - b.ti); });
      var C = es.filter(function (e) { return e.rs > e.rt; })
        .sort(function (a, b) { return a.rs - b.rs || a.si - b.si; });
      var F = es.filter(function (e) { return e.rs < e.rt; })
        .sort(function (a, b) { return a.rt - b.rt || b.si - a.si; });
      C.forEach(function (e, j) { e.j = j; });
      F.forEach(function (e, k) { e.k = k; });
      var multi = rows > 1;
      var O = multi ? CH + CH * C.length + 8 : 0;
      var core = per * STEP - GAP;
      var right = (multi ? 14 : 0) + (F.length ? 8 + CH * F.length : 0);
      return { per: per, rows: rows, pos: pos, U: U, C: C, F: F, O: O, core: core, width: O + core + right };
    }

    var L = plan(1);
    for (var R = 2; L.width > avail && L.per > 2; R++) { L = plan(R); }
    var O = L.O;

    // gutter lane assignment
    var gut = [];
    for (var g = 0; g < L.rows; g++) {
      var items = 0;
      var take = function () { return items++; };
      L.U.filter(function (e) { return e.rt === g; }).forEach(function (e) { e.laneS = take(); });
      L.C.filter(function (e) { return e.rt === g; }).sort(function (a, b) { return b.j - a.j; })
        .forEach(function (e) { e.laneT = take(); });
      L.F.filter(function (e) { return e.rt === g; }).forEach(function (e) { e.laneT = take(); });
      L.C.filter(function (e) { return e.rs === g; }).sort(function (a, b) { return a.si - b.si; })
        .forEach(function (e) { e.laneS = take(); });
      L.F.filter(function (e) { return e.rs === g; }).forEach(function (e) { e.laneS = take(); });
      var wrap = g < L.rows - 1 ? take() : -1;
      gut.push({ n: items, wrapLane: wrap });
    }
    var rowY = [0];
    gut.forEach(function (gt, g) {
      gt.h = gt.n ? LANE_TOP + (gt.n - 1) * LANE + LANE_PAD : 4;
      rowY.push(rowY[g] + NH + gt.h);
    });
    var laneY = function (g, l) { return rowY[g] + NH + LANE_TOP + l * LANE; };
    var nx = function (i) { return O + L.pos[i].c * STEP; };
    var ny = function (i) { return rowY[L.pos[i].r]; };

    var entryCount = {};
    L.U.concat(L.C, L.F).forEach(function (e) {
      var m = entryCount[e.ti] || 0;
      entryCount[e.ti] = m + 1;
      e.xe = Math.max(nx(e.ti) + 8, nx(e.ti) + NW - 14 - m * 14);
    });

    var lines = [], arrows = [], labels = [];
    function H(x1, x2, y, kind, on) { lines.push({ x: Math.min(x1, x2), y: y, w: Math.abs(x2 - x1), h: 0, dir: "h", kind: kind, on: !!on }); }
    function V(x, y1, y2, kind, on) { lines.push({ x: x, y: Math.min(y1, y2), w: 0, h: Math.abs(y2 - y1), dir: "v", kind: kind, on: !!on }); }

    rails.forEach(function (f) {
      var i = idx[f.src], on = reached(f.dst);
      if (L.pos[i + 1].r === L.pos[i].r) {
        H(nx(i) + NW, nx(i + 1), ny(i) + 37, "flow", on);
        arrows.push({ x: nx(i + 1) - 6, y: ny(i) + 33, dir: "right", kind: "flow", on: on });
      } else {
        var r = L.pos[i].r, xr = nx(i) + NW + 12, yw = laneY(r, gut[r].wrapLane), yn = ny(i + 1) + 37;
        H(nx(i) + NW, xr + 2, ny(i) + 37, "flow", on);
        V(xr, ny(i) + 37, yw, "flow", on);
        H(O - 12, xr + 2, yw - 1, "flow", on);
        V(O - 12, yw - 1, yn + 2, "flow", on);
        H(O - 12, O, yn, "flow", on);
        arrows.push({ x: O - 6, y: yn - 4, dir: "right", kind: "flow", on: on });
      }
    });

    L.U.concat(L.C, L.F).forEach(function (e) {
      var xs = nx(e.si) + NW / 2, top = ny(e.ti) + NH, y1 = laneY(e.rs, e.laneS);
      V(xs, ny(e.si) + NH, y1, e.kind);
      if (e.rs === e.rt) {
        H(e.xe, xs, y1, e.kind);
        V(e.xe, top + 6, y1, e.kind);
      } else {
        var xc = e.rs > e.rt
          ? O - CH - CH * (e.j + 1)
          : O + L.core + 14 + 8 + CH * e.k;
        var y2 = laneY(e.rt, e.laneT);
        H(xc, xs, y1, e.kind);
        V(xc, y2, y1, e.kind);
        H(xc, e.xe, y2, e.kind);
        V(e.xe, top + 6, y2, e.kind);
      }
      arrows.push({ x: e.xe - 5, y: top, dir: "up", kind: e.kind, on: false });
      if (e.label) { labels.push({ x: xs - 6, y: y1 - 9, text: e.label }); }
    });

    return {
      width: L.width,
      height: rowY[L.rows],
      rows: L.rows,
      perRow: L.per,
      nodes: names.map(function (n, i) { return { id: n, index: i, x: nx(i), y: ny(i), w: NW, h: NH }; }),
      lines: lines, arrows: arrows, labels: labels
    };
  }

  layoutPipeline.metrics = { NW: NW, NH: NH, GAP: GAP, STEP: STEP, LANE: LANE };
  root.layoutPipeline = layoutPipeline;
  if (typeof module !== "undefined" && module.exports) { module.exports = layoutPipeline; }
})(typeof window !== "undefined" ? window : this);
