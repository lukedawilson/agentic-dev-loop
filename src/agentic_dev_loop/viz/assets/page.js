(function () {
  "use strict";

  var nodeStatus = {};
  var stageWord = {};
  var verdictSteps = new Set();
  var nodeIds = new Set();
  var currentVerdicts = {};
  var verdictFail = new Set();
  var verdictPass = new Set();
  var verdictUnverifiable = new Set();
  var verdictEnv = new Set();
  var fatalNodes = new Set();
  var maxAttempts = null;
  var attempt = null;
  var topology = null;
  var stations = {};
  var pendingCompletion = null;
  var stepTimes = {};
  var liveElapsed = {};
  var runStartWall = null;
  var totalTimer = null;
  var lastEventAt = 0;
  var reconnectAttempts = 0;
  var runActive = false;
  var runFailed = false;
  var topologyReady = false;
  var topologyFailed = false;
  var pendingEvents = [];
  var MAX_CONSOLE = 500;
  var PALETTE = ["#0c0c0c", "#c50f1f", "#13a10e", "#c19c00", "#0037da",
                 "#881798", "#3a96dd", "#cccccc"];
  var BRIGHT = ["#767676", "#e74856", "#16c60c", "#f9f1a5", "#3b78ff",
                "#b4009e", "#61d6d6", "#f2f2f2"];
  var COPY_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="14"'
    + ' height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
    + ' aria-hidden="true"><rect x="9" y="9" width="13" height="13"'
    + ' rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1'
    + ' 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
  var CHECK_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="14"'
    + ' height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
    + ' aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg>';

  function el(id) { return document.getElementById(id); }

  function layoutOf(opts) {
    if (typeof layoutPipeline === "function") { return layoutPipeline(opts); }
    if (typeof window !== "undefined" && typeof window.layoutPipeline === "function") {
      return window.layoutPipeline(opts);
    }
    if (typeof globalThis !== "undefined"
        && typeof globalThis.layoutPipeline === "function") {
      return globalThis.layoutPipeline(opts);
    }
    return null;
  }

  function zeroPad(n) { return n < 10 ? "0" + n : String(n); }
  function mmss(totalSeconds) {
    var s = totalSeconds % 60;
    var m = Math.floor(totalSeconds / 60);
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  /* Theme ------------------------------------------------------------- */
  function applyTheme(theme) {
    var root = document.documentElement;
    if (root && root.setAttribute) {
      root.setAttribute("data-theme", theme);
      root.setAttribute("class",
        theme === "dark" ? "cds-theme-zone-g100" : "cds-theme-zone-white");
    }
    var btn = el("theme-toggle");
    if (btn) {
      btn.setAttribute("aria-label", theme === "dark"
        ? "Switch to light theme" : "Switch to dark theme");
    }
  }

  function initialTheme() {
    try {
      var stored = window.localStorage && window.localStorage.getItem("adl-theme");
      if (stored === "dark" || stored === "light") { return stored; }
    } catch (err) { /* session-only */ }
    try {
      if (window.matchMedia
          && window.matchMedia("(prefers-color-scheme: light)").matches) {
        return "light";
      }
    } catch (err) { /* default dark */ }
    return "dark";
  }

  var theme = initialTheme();
  applyTheme(theme);
  (function initThemeToggle() {
    var btn = el("theme-toggle");
    if (!btn) { return; }
    btn.addEventListener("click", function () {
      theme = theme === "dark" ? "light" : "dark";
      applyTheme(theme);
      try { window.localStorage.setItem("adl-theme", theme); }
      catch (err) { /* session-only */ }
    });
  })();

  /* Run log ----------------------------------------------------------- */
  function logLine(text, cls) {
    var li = document.createElement("li");
    if (cls) { li.className = cls; }
    li.textContent = text;
    var log = el("run-log");
    if (!log) { return; }
    log.appendChild(li);
    log.scrollTop = log.scrollHeight;
  }

  function logConsole(html) {
    var li = document.createElement("li");
    li.innerHTML = html;
    var log = el("run-log");
    if (!log) { return; }
    log.appendChild(li);
    log.scrollTop = log.scrollHeight;
  }

  /* Summary tiles ----------------------------------------------------- */
  function renderMeter() {
    var box = el("meter");
    if (!box) { return; }
    box.innerHTML = "";
    box.className = "meter";
    if (maxAttempts === null) { return; }
    var used = attempt || 0;
    for (var i = 0; i < maxAttempts; i++) {
      var seg = document.createElement("span");
      seg.className = i < used ? "on" : "";
      box.appendChild(seg);
    }
    if (runFailed && used >= maxAttempts) { box.className = "meter exhausted"; }
  }

  function updateAttempt() {
    var line = el("attempt");
    if (line) {
      line.textContent = attempt === null
        ? "?/" + (maxAttempts === null ? "?" : maxAttempts)
        : attempt + "/" + (maxAttempts === null ? "?" : maxAttempts);
    }
    renderMeter();
  }

  function renderTotal() {
    var line = el("total");
    if (!line) { return; }
    line.innerHTML = "";
    if (runStartWall === null) {
      line.textContent = "";
      line.style.display = "none";
    } else {
      line.textContent = "total " + mmss(Math.max(0,
        Math.round(Date.now() / 1000 - runStartWall)));
      line.style.display = "block";
      var note = document.createElement("span");
      note.className = "total-note";
      note.textContent = runActive ? "LIVE" : "FINAL";
      line.appendChild(note);
    }
    if (runActive && Date.now() - lastEventAt > 10000) { recover(); }
  }

  /* Status ------------------------------------------------------------ */
  function statusOf(n) {
    if (nodeStatus[n] === "running") { return "running"; }
    if (fatalNodes.has(n)) { return "fatal"; }
    if (nodeStatus[n] === "fatal") { return "fatal"; }
    if (verdictFail.has(n)) { return "fail"; }
    if (verdictPass.has(n)) { return "pass"; }
    if (verdictUnverifiable.has(n)) { return "unverifiable"; }
    if (verdictEnv.has(n)) { return "env"; }
    return nodeStatus[n] || "pending";
  }

  function renderStatusOf(n) {
    var s = statusOf(n);
    return s === "env" ? "fatal" : s;
  }

  function statusWord(n) {
    var s = renderStatusOf(n);
    if (s === "pass") { return currentVerdicts[n] ? "PASS" : "DONE"; }
    if (s === "running") { return "RUNNING"; }
    if (s === "fail") { return "RETRY"; }
    if (s === "fatal") { return currentVerdicts[n] === "fail" ? "FAIL" : "FATAL"; }
    if (s === "unverifiable") { return "UNVERIF"; }
    return "PENDING";
  }

  function updateBadges() {
    var box = el("verdicts");
    if (!box) { return; }
    box.innerHTML = "";
    Array.from(verdictSteps).forEach(function (step) {
      var b = document.createElement("cds-tag");
      var v = currentVerdicts[step];
      b.setAttribute("size", "sm");
      b.className = "badge";
      if (v === "pass") { b.className += " badge-pass"; }
      else if (v === "fail") { b.className += " badge-fail"; }
      else if (v === "unverifiable") { b.className += " badge-unverifiable"; }
      else if (v === "env") { b.className += " badge-fail"; }
      else { b.className += " badge-unclear"; }
      b.textContent = step + ": "
        + (v === null || v === undefined ? "no verdict" : v);
      box.appendChild(b);
    });
  }

  function resetVerdicts() {
    verdictSteps.clear();
    verdictPass.clear();
    verdictFail.clear();
    verdictUnverifiable.clear();
    verdictEnv.clear();
    fatalNodes.clear();
    currentVerdicts = {};
    nodeStatus = {};
    stageWord = {};
    pendingCompletion = null;
    updateBadges();
    Array.from(nodeIds).forEach(function (n) { renderStageHeader(n); });
    renderGraph();
  }

  /* Pipeline graph ---------------------------------------------------- */
  function ensureStation(id) {
    if (stations[id]) { return stations[id]; }
    var s = document.createElement("div");
    s.className = "node st-pending";
    s.id = "station-" + id;
    s.setAttribute("data-testid", "station-" + id);
    var row = document.createElement("div");
    row.className = "nrow";
    var idx = document.createElement("span");
    idx.className = "nidx";
    var time = document.createElement("span");
    time.className = "ntime";
    row.appendChild(idx);
    row.appendChild(time);
    var name = document.createElement("div");
    name.className = "nname";
    var stat = document.createElement("div");
    stat.className = "nstat";
    var led = document.createElement("span");
    led.className = "led";
    led.setAttribute("aria-hidden", "true");
    var label = document.createElement("span");
    label.className = "nlabel";
    stat.appendChild(led);
    stat.appendChild(label);
    s.appendChild(row);
    s.appendChild(name);
    s.appendChild(stat);
    stations[id] = s;
    return s;
  }

  function nodeIndex(id) {
    if (!topology) { return 0; }
    var i = topology.nodes.indexOf(id);
    return i < 0 ? 0 : i;
  }

  function nodeTime(id) {
    if (nodeStatus[id] === "running" && liveElapsed[id] !== undefined) {
      return mmss(liveElapsed[id]);
    }
    if (stepTimes[id] !== undefined) { return mmss(stepTimes[id]); }
    return "-";
  }

  function updateStation(id) {
    var s = ensureStation(id);
    var status = renderStatusOf(id);
    s.className = "node st-" + status;
    var name = s.querySelector(".nname");
    var idx = s.querySelector(".nidx");
    var time = s.querySelector(".ntime");
    var label = s.querySelector(".nlabel");
    if (name) { name.textContent = id; }
    if (idx) { idx.textContent = zeroPad(nodeIndex(id) + 1); }
    if (time) { time.textContent = nodeTime(id); }
    if (label) { label.textContent = statusWord(id); }
  }

  function graphLabel() {
    if (!topology) { return "Pipeline graph"; }
    var parts = topology.nodes.map(function (n, i) {
      return zeroPad(i + 1) + " " + n + " " + statusWord(n);
    });
    var rows = stations.__rows || 1;
    return "Pipeline graph: " + parts.join(", ") + ". " + rows + " row"
      + (rows === 1 ? "" : "s") + ".";
  }

  function makeLine(l) {
    var d = document.createElement("div");
    d.className = "ln ln-" + l.kind + (l.on ? " on" : "");
    d.setAttribute("data-dir", l.dir);
    d.style.left = l.x + "px";
    d.style.top = l.y + "px";
    d.style.width = (l.dir === "h" ? l.w : 2) + "px";
    d.style.height = (l.dir === "v" ? l.h : 2) + "px";
    return d;
  }

  function makeArrow(a) {
    var d = document.createElement("div");
    d.className = "arr arr-" + a.dir + " arr-" + a.kind + (a.on ? " on" : "");
    d.style.left = a.x + "px";
    d.style.top = a.y + "px";
    return d;
  }

  function makeLabel(l) {
    var d = document.createElement("div");
    d.className = "elabel";
    d.style.left = l.x + "px";
    d.style.top = l.y + "px";
    d.textContent = l.text;
    return d;
  }

  function renderGraph() {
    var box = el("graph");
    if (!box) { return; }
    box.innerHTML = "";
    if (topologyFailed) {
      box.style.width = "";
      box.style.height = "";
      var msg = document.createElement("p");
      msg.className = "graph-msg";
      msg.textContent = "Graph unavailable - the event log below is still live";
      box.appendChild(msg);
      box.setAttribute("aria-label", "Pipeline graph unavailable");
      return;
    }
    if (!topology) {
      var loading = document.createElement("p");
      loading.className = "graph-msg";
      loading.textContent = "Loading graph...";
      box.appendChild(loading);
      box.setAttribute("aria-label", "Loading pipeline graph");
      return;
    }
    var status = {};
    topology.nodes.forEach(function (n) { status[n] = renderStatusOf(n); });
    var avail = (typeof window !== "undefined" && window.innerWidth)
      ? window.innerWidth : 1200;
    var res = layoutOf({
      nodes: topology.nodes,
      flow: topology.flow || [],
      edges: topology.edges || [],
      status: status,
      availWidth: avail
    });
    if (!res) { return; }
    box.style.width = res.width + "px";
    box.style.height = res.height + "px";
    res.lines.forEach(function (l) { box.appendChild(makeLine(l)); });
    res.arrows.forEach(function (a) { box.appendChild(makeArrow(a)); });
    res.labels.forEach(function (l) { box.appendChild(makeLabel(l)); });
    res.nodes.forEach(function (nd) {
      updateStation(nd.id);
      var s = stations[nd.id];
      s.style.left = nd.x + "px";
      s.style.top = nd.y + "px";
      box.appendChild(s);
    });
    stations.__rows = res.rows;
    box.setAttribute("aria-label", graphLabel());
  }

  /* Stages ------------------------------------------------------------ */
  function addPlaceholder(pre) {
    var ph = document.createElement("div");
    ph.className = "stage-placeholder";
    ph.textContent = "No output";
    pre.appendChild(ph);
  }

  function ensureSection(node) {
    var existing = document.getElementById("stage-" + node);
    if (existing) { return existing; }
    var stages = el("stages");
    var section = document.createElement("section");
    section.className = "stage collapsed";
    section.id = "stage-" + node;
    section.setAttribute("data-testid", "stage-" + node);

    var header = document.createElement("button");
    header.className = "stage-header";
    header.type = "button";
    header.setAttribute("aria-expanded", "false");
    header.setAttribute("aria-controls", "console-" + node);
    header.addEventListener("click", function () {
      var willCollapse = !section.classList.contains("collapsed");
      section.classList.toggle("collapsed");
      header.setAttribute("aria-expanded", willCollapse ? "false" : "true");
      renderStageHeader(node);
      if (!willCollapse) {
        requestAnimationFrame(function () { tailSection(section); });
      }
    });

    var glyph = document.createElement("span");
    glyph.className = "stage-glyph";
    glyph.setAttribute("aria-hidden", "true");
    var index = document.createElement("span");
    index.className = "stage-index";
    var led = document.createElement("span");
    led.className = "led";
    led.setAttribute("aria-hidden", "true");
    var name = document.createElement("span");
    name.className = "stage-name";
    var status = document.createElement("span");
    status.className = "stage-status";
    var verdict = document.createElement("span");
    verdict.className = "stage-verdict";
    var time = document.createElement("span");
    time.className = "stage-time";

    header.appendChild(glyph);
    header.appendChild(index);
    header.appendChild(led);
    header.appendChild(name);
    header.appendChild(status);
    header.appendChild(verdict);
    header.appendChild(time);

    var console = document.createElement("pre");
    console.className = "stage-console";
    addPlaceholder(console);

    var wrap = document.createElement("div");
    wrap.className = "console-wrap";
    wrap.id = "console-" + node;
    var copyBtn = document.createElement("button");
    copyBtn.className = "copy-btn";
    copyBtn.type = "button";
    copyBtn.setAttribute("title", "Copy output");
    copyBtn.setAttribute("aria-label", "Copy " + node + " console output");
    copyBtn.setAttribute("data-testid", "copy-console-" + node);
    copyBtn.innerHTML = COPY_ICON;
    copyBtn.addEventListener("click", function () {
      copyConsole(console, copyBtn);
    });
    wrap.appendChild(copyBtn);
    wrap.appendChild(console);

    section.appendChild(header);
    section.appendChild(wrap);
    stages.appendChild(section);
    renderStageHeader(node);
    return section;
  }

  function renderStageHeader(node) {
    var section = ensureSection(node);
    var header = section.querySelector(".stage-header");
    if (!header) { return; }
    var glyph = header.querySelector(".stage-glyph");
    var index = header.querySelector(".stage-index");
    var name = header.querySelector(".stage-name");
    var status = header.querySelector(".stage-status");
    var verdict = header.querySelector(".stage-verdict");
    var time = header.querySelector(".stage-time");
    var collapsed = section.classList.contains("collapsed");
    if (glyph) { glyph.textContent = collapsed ? ">" : "v"; }
    if (index) { index.textContent = zeroPad(nodeIndex(node) + 1); }
    if (name) { name.textContent = node; }
    var word = stageWord[node] || "";
    if (status) { status.textContent = word; }
    var v = currentVerdicts[node];
    if (verdict) {
      verdict.textContent = (word === "completed" && v)
        ? "verdict: " + String(v).toUpperCase() : "";
    }
    if (time) {
      time.textContent = (word === "completed" && stepTimes[node])
        ? mmss(stepTimes[node])
        : (word === "running" && liveElapsed[node] !== undefined)
          ? mmss(liveElapsed[node]) : "";
    }
    header.className = "stage-header status-" + renderStatusOf(node);
    header.setAttribute("aria-expanded", collapsed ? "false" : "true");
  }

  function setStageStatus(node, word) {
    stageWord[node] = word;
    renderStageHeader(node);
  }

  function flushPending() {
    if (pendingCompletion === null) { return; }
    var p = pendingCompletion;
    pendingCompletion = null;
    setStageStatus(p.node, p.word);
  }

  function clearConsoles() {
    stageWord = {};
    Array.from(nodeIds).forEach(function (n) {
      var section = ensureSection(n);
      var pre = consoleOf(section);
      if (pre) {
        pre.innerHTML = "";
        addPlaceholder(pre);
      }
      section.classList.add("collapsed");
      renderStageHeader(n);
    });
    var log = el("run-log");
    if (log) { log.innerHTML = ""; }
  }

  function consoleOf(section) {
    var wrap = section.querySelector(".console-wrap");
    return wrap ? wrap.querySelector(".stage-console") : null;
  }

  function scrollPortOf(section) { return section.querySelector(".console-wrap"); }

  function copyConsole(pre, btn) {
    var lines = Array.prototype.map.call(pre.querySelectorAll(".cline"),
      function (c) { return c.textContent; });
    if (lines.length === 0) {
      btn.setAttribute("title", "Nothing to copy");
      return;
    }
    var text = lines.join("\n");
    function done(ok) {
      btn.innerHTML = ok ? CHECK_ICON : COPY_ICON;
      btn.setAttribute("title", ok ? "Copied!" : "Copy failed");
      if (ok) {
        setTimeout(function () {
          btn.innerHTML = COPY_ICON;
          btn.setAttribute("title", "Copy output");
        }, 1500);
      }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); },
        function () { done(false); });
    } else {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.top = "-1000px";
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
      document.body.removeChild(ta);
      done(ok);
    }
  }

  function nearBottom(pre) {
    return pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
  }

  function tailSection(section) {
    if (section.classList.contains("collapsed")) { return; }
    var sp = scrollPortOf(section);
    if (sp) { sp.scrollTop = sp.scrollHeight; }
  }

  function appendConsole(section, html) {
    var pre = consoleOf(section);
    var sp = scrollPortOf(section);
    if (!pre) { return; }
    var stick = !section.classList.contains("collapsed") && nearBottom(sp);
    var ph = pre.querySelector(".stage-placeholder");
    if (ph) { pre.removeChild(ph); }
    if (pre.querySelectorAll(".cline").length >= MAX_CONSOLE) {
      var first = pre.querySelector(".cline");
      if (first) { pre.removeChild(first); }
      var omitted = pre.querySelector(".omitted");
      if (!omitted) {
        omitted = document.createElement("div");
        omitted.className = "omitted";
        pre.insertBefore(omitted, pre.firstChild);
      }
      var count = parseInt(omitted.getAttribute("data-n") || "0", 10) + 1;
      omitted.setAttribute("data-n", String(count));
      omitted.textContent = "..." + count + " line"
        + (count === 1 ? "" : "s") + " omitted";
    }
    var line = document.createElement("span");
    line.className = "cline";
    line.innerHTML = html;
    pre.appendChild(line);
    if (stick && sp) { sp.scrollTop = sp.scrollHeight; }
  }

  function handleConsole(ev) {
    var html = ansiToHtml(ev.text);
    if (ev.node && nodeIds.has(ev.node)) {
      appendConsole(ensureSection(ev.node), html);
    } else {
      logConsole(html);
    }
  }

  function ansiToHtml(text) {
    var out = "";
    var i = 0;
    var ESC = "\x1b";
    var cur = { fg: null, bg: null, bold: false, italic: false, underline: false };
    var open = false;

    function currentStyle() {
      var parts = [];
      if (cur.bold) { parts.push("font-weight:bold"); }
      if (cur.italic) { parts.push("font-style:italic"); }
      if (cur.underline) { parts.push("text-decoration:underline"); }
      if (cur.fg !== null) { parts.push("color:" + cur.fg); }
      if (cur.bg !== null) { parts.push("background-color:" + cur.bg); }
      return parts.join(";");
    }
    function closeSpan() {
      if (open) { out += "</span>"; open = false; }
    }
    function pushSpan() {
      closeSpan();
      var s = currentStyle();
      if (s) { out += '<span style="' + s + '">'; open = true; }
    }

    var html = text.replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    var n = html.length;
    while (i < n) {
      var idx = html.indexOf(ESC, i);
      if (idx === -1) { out += html.slice(i); break; }
      out += html.slice(i, idx);
      var j = idx + 1;
      if (j >= n) { i = idx + 1; continue; }
      var ch = html.charAt(j);
      if (ch === "[") {
        var k = j + 1;
        while (k < n) {
          var cc = html.charCodeAt(k);
          if (cc >= 0x40 && cc <= 0x7e) { break; }
          k++;
        }
        if (k >= n) { i = n; break; }
        var final = html.charAt(k);
        var params = html.slice(j + 1, k);
        if (final === "m") {
          var parts = params.split(";");
          var codes = [];
          for (var p = 0; p < parts.length; p++) {
            codes.push(parts[p] === "" ? 0 : parseInt(parts[p], 10));
          }
          var changed = false;
          for (var c = 0; c < codes.length; c++) {
            var code = codes[c];
            if (code === 0) {
              cur = { fg: null, bg: null, bold: false, italic: false, underline: false };
              changed = true;
            } else if (code === 1) { cur.bold = true; changed = true; }
            else if (code === 22) { cur.bold = false; changed = true; }
            else if (code === 3) { cur.italic = true; changed = true; }
            else if (code === 23) { cur.italic = false; changed = true; }
            else if (code === 4) { cur.underline = true; changed = true; }
            else if (code === 24) { cur.underline = false; changed = true; }
            else if (code >= 30 && code <= 37) { cur.fg = PALETTE[code - 30]; changed = true; }
            else if (code >= 90 && code <= 97) { cur.fg = BRIGHT[code - 90]; changed = true; }
            else if (code >= 40 && code <= 47) { cur.bg = PALETTE[code - 40]; changed = true; }
            else if (code >= 100 && code <= 107) { cur.bg = BRIGHT[code - 100]; changed = true; }
            else if (code === 39) { cur.fg = null; changed = true; }
            else if (code === 49) { cur.bg = null; changed = true; }
            else if (code === 38 || code === 48) {
              if (c + 2 < codes.length && codes[c + 1] === 5) {
                var val = codes[c + 2];
                var col;
                if (val < 8) { col = PALETTE[val]; }
                else if (val < 16) { col = BRIGHT[val - 8]; }
                else if (val < 232) {
                  var cube = val - 16;
                  var levels = [0, 95, 135, 175, 215, 255];
                  col = "rgb(" + levels[Math.floor(cube / 36)] + ","
                      + levels[Math.floor((cube % 36) / 6)] + ","
                      + levels[cube % 6] + ")";
                } else {
                  var g = 8 + 10 * (val - 232);
                  col = "rgb(" + g + "," + g + "," + g + ")";
                }
                if (code === 38) { cur.fg = col; } else { cur.bg = col; }
                c += 2;
                changed = true;
              } else if (c + 4 < codes.length && codes[c + 1] === 2) {
                var rgb = "rgb(" + codes[c + 2] + "," + codes[c + 3] + ","
                  + codes[c + 4] + ")";
                if (code === 38) { cur.fg = rgb; } else { cur.bg = rgb; }
                c += 4;
                changed = true;
              }
            }
          }
          if (changed) { pushSpan(); }
        }
        i = k + 1;
        continue;
      }
      if (ch === "]") {
        var bel = html.indexOf(String.fromCharCode(7), j);
        var st = html.indexOf(ESC + String.fromCharCode(92), j);
        var end = -1;
        if (bel !== -1) { end = bel + 1; }
        if (st !== -1 && (end === -1 || st + 2 < end)) { end = st + 2; }
        if (end === -1) { i = n; break; }
        i = end;
        continue;
      }
      i = idx + 1;
    }
    closeSpan();
    return out;
  }

  /* Events ------------------------------------------------------------ */
  function handle(ev) {
    switch (ev.type) {
      case "run_started":
        flushPending();
        stepTimes = {};
        liveElapsed = {};
        runActive = true;
        runFailed = false;
        fatalNodes.clear();
        if (totalTimer !== null) { clearInterval(totalTimer); totalTimer = null; }
        runStartWall = (typeof ev.started_at === "number") ? ev.started_at : null;
        totalTimer = setInterval(renderTotal, 1000);
        renderTotal();
        clearConsoles();
        resetVerdicts();
        if (ev.attempt !== undefined) { attempt = ev.attempt; }
        maxAttempts = ev.max_attempts;
        var subject = el("subject");
        if (subject) { subject.textContent = ev.subject; }
        if (ev.thread_id !== undefined) {
          var tid = el("thread-id");
          if (tid) { tid.textContent = ev.thread_id; }
        }
        logLine("run started: " + ev.subject + " (thread " + ev.thread_id + ")",
                "started");
        updateAttempt();
        break;
      case "node_started":
        flushPending();
        if (!nodeIds.has(ev.node)) { break; }
        nodeStatus[ev.node] = "running";
        stageWord[ev.node] = "running";
        liveElapsed = {};
        liveElapsed[ev.node] = 0;
        renderGraph();
        break;
      case "node_finished":
        if (!nodeIds.has(ev.node)) { break; }
        if (ev.elapsed_seconds !== undefined) {
          stepTimes[ev.node] = (stepTimes[ev.node] || 0) + ev.elapsed_seconds;
        }
        if (!fatalNodes.has(ev.node)) {
          nodeStatus[ev.node] = ev.ok ? "pass" : "fail";
        }
        pendingCompletion = { node: ev.node, word: ev.ok ? "completed" : "errored" };
        setStageStatus(ev.node, ev.ok ? "completed" : "errored");
        renderGraph();
        break;
      case "heartbeat":
        if (nodeIds.has(ev.node) && nodeStatus[ev.node] === "running") {
          liveElapsed[ev.node] = ev.elapsed_seconds;
          renderStageHeader(ev.node);
          renderGraph();
        }
        break;
      case "state":
        if (ev.attempt !== undefined) { attempt = ev.attempt; }
        if (ev.step_verdicts !== undefined) {
          Object.keys(ev.step_verdicts).forEach(function (k) {
            verdictSteps.add(k);
            var v = ev.step_verdicts[k];
            if (v === "pass") { verdictPass.add(k); verdictFail.delete(k); verdictUnverifiable.delete(k); verdictEnv.delete(k); }
            else if (v === "fail") { verdictFail.add(k); verdictPass.delete(k); verdictUnverifiable.delete(k); verdictEnv.delete(k); }
            else if (v === "unverifiable") { verdictUnverifiable.add(k); verdictPass.delete(k); verdictFail.delete(k); verdictEnv.delete(k); }
            else if (v === "env") { verdictEnv.add(k); verdictPass.delete(k); verdictFail.delete(k); verdictUnverifiable.delete(k); }
            else { verdictPass.delete(k); verdictFail.delete(k); verdictUnverifiable.delete(k); verdictEnv.delete(k); }
            currentVerdicts[k] = v;
          });
          updateBadges();
          if (pendingCompletion !== null
              && (pendingCompletion.node in ev.step_verdicts)) {
            flushPending();
          }
          renderGraph();
        }
        if (ev.failed) {
          Object.keys(nodeStatus).forEach(function (n) {
            if (nodeStatus[n] === "running") { fatalNodes.add(n); }
          });
          logLine("state: failed", "failed");
          renderGraph();
        }
        updateAttempt();
        break;
      case "run_finished":
        runActive = false;
        if (totalTimer !== null) { clearInterval(totalTimer); totalTimer = null; }
        var total = el("total");
        if (ev.elapsed_seconds !== undefined) {
          runStartWall = null;
          if (total) {
            total.innerHTML = "";
            total.textContent = "total " + mmss(ev.elapsed_seconds);
            total.style.display = "block";
            var finalNote = document.createElement("span");
            finalNote.className = "total-note";
            finalNote.textContent = "FINAL";
            total.appendChild(finalNote);
          }
        }
        flushPending();
        if (!ev.success) {
          runFailed = true;
          Object.keys(nodeStatus).forEach(function (n) {
            var s = statusOf(n);
            if (s === "fail" || s === "running") { nodeStatus[n] = "fatal"; }
          });
          renderGraph();
        }
        renderMeter();
        logLine("run finished: " + (ev.success ? "success" : "failure"),
                ev.success ? "success" : "failed");
        var banner = el("run-banner");
        if (banner) {
          banner.innerHTML = "";
          var title = document.createElement("span");
          title.setAttribute("slot", "title");
          title.textContent = ev.success ? "SUCCESS" : "FAILURE";
          var sub = document.createElement("span");
          sub.textContent = "Run finished - " + (el("subject")
            ? el("subject").textContent : "") + " - attempt "
            + (attempt === null ? "?" : attempt) + "/"
            + (maxAttempts === null ? "?" : maxAttempts)
            + " - total " + (ev.elapsed_seconds !== undefined
              ? mmss(ev.elapsed_seconds) : "-");
          banner.appendChild(title);
          banner.appendChild(sub);
          banner.setAttribute("kind", ev.success ? "success" : "error");
          banner.style.display = "block";
        }
        break;
      case "console":
        handleConsole(ev);
        break;
    }
  }

  function loadTopology() {
    fetch("/topology").then(function (r) { return r.json(); }).then(function (d) {
      var subject = el("subject");
      if (subject) { subject.textContent = d.subject; }
      var provider = el("provider");
      if (provider && d.provider) { provider.textContent = d.provider; }
      topology = {
        nodes: d.nodes || [],
        flow: d.flow || [],
        edges: d.edges || []
      };
      topology.nodes.forEach(function (n) {
        nodeIds.add(n);
        ensureSection(n);
        ensureStation(n);
      });
      topologyReady = true;
      renderGraph();
      pendingEvents.forEach(handle);
      pendingEvents = [];
    }).catch(function () {
      topologyFailed = true;
      topologyReady = true;
      logLine("failed to load topology");
      renderGraph();
      pendingEvents.forEach(handle);
      pendingEvents = [];
    });
  }

  /* Stream ------------------------------------------------------------ */
  var es = null;

  function setStreamLive() {
    var stream = el("stream");
    if (stream) { stream.className = "stream"; }
    var label = el("stream-label");
    if (label) { label.textContent = "STREAM LIVE"; }
  }

  function setStreamReconnecting() {
    var stream = el("stream");
    if (stream) { stream.className = "stream reconnecting"; }
    var label = el("stream-label");
    if (label) { label.textContent = "RECONNECTING"; }
  }

  function connectStream() {
    var stream = new EventSource("/events");
    stream.onmessage = function (e) {
      lastEventAt = Date.now();
      reconnectAttempts = 0;
      var banner = el("stream-banner");
      if (banner) { banner.style.display = "none"; }
      setStreamLive();
      var ev;
      try { ev = JSON.parse(e.data); }
      catch (err) { logLine("bad event: " + e.data); return; }
      if (!topologyReady) { pendingEvents.push(ev); return; }
      try { handle(ev); }
      catch (err) { logLine("bad event: " + e.data); }
    };
    stream.onerror = function () { logLine("event stream interrupted", "interrupted"); };
    return stream;
  }

  function recover() {
    var banner = el("stream-banner");
    reconnectAttempts++;
    setStreamReconnecting();
    if (banner) {
      banner.textContent = "Connection lost, reconnecting... attempt "
        + reconnectAttempts + "/3 (no event for 10 s)";
      banner.style.display = "block";
    }
    if (reconnectAttempts > 3) {
      location.reload();
      return;
    }
    if (es) { es.close(); }
    es = connectStream();
    lastEventAt = Date.now();
  }

  loadTopology();

  setTimeout(function () {
    if (!topologyReady) {
      topologyReady = true;
      logLine("topology load timed out - event log only");
      pendingEvents.forEach(handle);
      pendingEvents = [];
    }
  }, 5000);

  es = connectStream();

  document.addEventListener("visibilitychange", function () {
    if (runActive && Date.now() - lastEventAt > 10000) { recover(); }
  });

  (function initThreadCopy() {
    var tid = el("thread-id");
    if (!tid) { return; }

    function selectFallback() {
      // Explicit degradation: if the clipboard API is unavailable, select
      // the text so the user can copy it manually.
      try {
        var range = document.createRange();
        range.selectNodeContents(tid);
        var sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      } catch (err) { /* selection is best-effort */ }
    }

    function showFeedback(value) {
      tid.textContent = "Copied";
      tid.classList.add("copied");
      setTimeout(function () {
        tid.classList.remove("copied");
        // Only restore when the feedback is still showing; a newer thread
        // id from run_started must not be clobbered.
        if (tid.textContent === "Copied") { tid.textContent = value; }
      }, 1200);
    }

    function copyThreadId() {
      var value = tid.textContent;
      if (!value || value === "--" || value === "Copied") { return; }
      if (!navigator.clipboard || !navigator.clipboard.writeText) {
        selectFallback();
        return;
      }
      navigator.clipboard.writeText(value).then(
        function () { showFeedback(value); }, selectFallback);
    }

    tid.addEventListener("click", copyThreadId);
    tid.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        copyThreadId();
      }
    });
  })();

  (function initRelayout() {
    if (typeof ResizeObserver !== "undefined") {
      var target = el("pipeline-panel") || document.body;
      if (target) {
        new ResizeObserver(function () { renderGraph(); }).observe(target);
      }
    }
    window.addEventListener("resize", function () { renderGraph(); });
  })();
})();
