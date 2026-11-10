(function () {
  "use strict";

  var charts = [];
  var mode = "list";
  var state = { payload: null, runId: null };

  function el(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function prettyMetricName(value) {
    return String(value || "").split("_").filter(function (word) {
      return word.length > 0;
    }).map(function (word) {
      return word.charAt(0).toUpperCase() + word.slice(1);
    }).join(" ");
  }

  function formatEntryValue(value, depth) {
    if (depth < 1 && value !== null && typeof value === "object"
        && !Array.isArray(value)) {
      return " (" + formatMetricValue(value, depth + 1) + ")";
    }
    return ": " + esc(value !== null && typeof value === "object"
      ? String(value) : value);
  }

  function formatMetricValue(value, depth) {
    depth = depth || 0;
    if (value !== null && typeof value === "object"
        && !Array.isArray(value)) {
      var entries = Object.keys(value).sort().map(function (key) {
        return esc(prettyMetricName(key)) + formatEntryValue(value[key], depth);
      });
      return entries.length ? entries.join(", ") : "--";
    }
    return esc(value);
  }

  function setText(id, value) {
    var node = el(id);
    if (node) { node.textContent = value === null || value === undefined ? "" : value; }
  }

  function show(node, visible) {
    if (node) { node.hidden = !visible; }
  }

  /* Theme ------------------------------------------------------------- */
  function applyTheme(theme) {
    if (document.documentElement.setAttribute) {
      document.documentElement.setAttribute("data-theme", theme);
      document.documentElement.setAttribute("class",
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

  function token(name) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(name).trim();
  }

  function colors() {
    return {
      text: token("--cds-text-primary") || "#EEF1F3",
      muted: token("--cds-text-helper") || "#8A949C",
      line: token("--cds-border-subtle-01") || "#2E353B",
      layer: token("--cds-layer-01") || "#171B1F",
      ok: token("--cds-support-success") || "#42BE65",
      warn: token("--cds-support-warning") || "#F1C21B",
      err: token("--cds-support-error") || "#FF6B72",
      info: token("--cds-support-info") || "#08BDBA",
      unv: token("--adl-support-unverifiable") || "#BE95FF"
    };
  }

  /* Formatting -------------------------------------------------------- */
  function fmtTs(value) {
    if (!value) { return "--"; }
    var date = new Date(value);
    if (isNaN(date.getTime())) { return String(value); }
    return date.toISOString().replace("T", " ").replace(".000Z", "Z");
  }

  function titleCase(value) {
    var text = String(value || "");
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function verdictClass(value) {
    var v = String(value || "").toLowerCase();
    if (v === "pass" || v === "success" || v === "approved") { return "badge-pass"; }
    if (v === "fail" || v === "failed" || v === "needs changes") { return "badge-fail"; }
    if (v === "unverifiable") { return "badge-unverifiable"; }
    return "badge-unclear";
  }

  function badge(value) {
    return '<span class="badge ' + verdictClass(value) + '">'
      + esc(value) + "</span>";
  }

  function provenanceBadge(provenance) {
    return '<span class="badge badge-prov">[' + esc(provenance || "unknown")
      + "]</span>";
  }

  function statusBadge(status) {
    return '<span class="badge status-' + esc(status) + '">'
      + esc(status || "unknown") + "</span>";
  }

  /* Metric cards ------------------------------------------------------ */
  var KEY_STATS = {
    loop_health: {
      verdict_distribution: "verdictMix",
      attempt_count_distribution: "attemptBars",
      retry_target_frequency: "attemptBars",
      audit_step_durations: "durationBars",
      per_step_durations: "durationBars",
      thread_count: "bigNumber"
    },
    tdd: {
      pct_attempts_red_then_green: "gauge",
      pct_threads_red_then_green: "gauge",
      attempts_zero_test_verdicts: "bigNumber"
    },
    review: {
      review_missed_rate: "gauge",
      defect_yield: "bigNumber",
      rubber_stamp: "bigNumber"
    },
    qa: {
      catch_rate: "gauge",
      qa_pass_rate: "gauge",
      unverifiable_rate: "bigNumber"
    }
  };

  var THREAD_KEY_STATS = {
    attempt_count: "bigNumber",
    pct_attempts_red_then_green: "gauge",
    qa_pass_rate: "gauge"
  };

  var VERDICT_ORDER = ["pass", "fail", "unverifiable", "env"];
  var keyStatBodies = {};

  function metricCard(name, metric) {
    var available = metric && metric.availability === "available";
    var value = metric && metric.value !== null && metric.value !== undefined
      ? metric.value : "unavailable";
    var html = '<div class="metric-card' + (available ? "" : " unavailable")
      + '" data-metric="' + esc(name || (metric && metric.name)) + '">';
    html += '<div class="metric-head"><span class="metric-name">'
      + esc(prettyMetricName(name || (metric && metric.name))) + "</span>"
      + provenanceBadge(metric && metric.provenance) + "</div>";
    html += '<div class="metric-value">' + formatMetricValue(value) + "</div>";
    html += '<div class="metric-basis">' + esc(metric && metric.basis) + "</div>";
    if (!available && metric && metric.availability) {
      html += '<div class="metric-reason">' + esc(metric.availability) + "</div>";
    }
    html += "</div>";
    return html;
  }

  function metricGrid(names, metrics) {
    if (!names.length) {
      return '<p class="empty">No metrics.</p>';
    }
    return '<div class="metric-grid">' + names.map(function (name) {
      return metricCard(name, metrics[name]);
    }).join("") + "</div>";
  }

  function metricAvailable(metric) {
    return Boolean(metric) && metric.availability === "available"
      && metric.value !== null && metric.value !== undefined;
  }

  function metricSlug(name) {
    return String(name || "").replace(/[^a-z0-9]+/gi, "-").toLowerCase();
  }

  /* Key-stat renderers ------------------------------------------------ */
  function statBaseOption() {
    var c = colors();
    return {
      backgroundColor: "transparent",
      textStyle: { color: c.text, fontFamily: token("--adl-font-sans") }
    };
  }

  function gaugeOption(metric) {
    if (!metricAvailable(metric)) { return null; }
    var c = colors();
    var value = Number(metric.value);
    if (isNaN(value)) { return null; }
    var option = statBaseOption();
    option.series = [{
      type: "gauge",
      startAngle: 180,
      endAngle: 0,
      min: 0,
      max: 100,
      radius: "100%",
      center: ["50%", "75%"],
      axisLine: { lineStyle: { width: 10, color: [[1, c.line]] } },
      axisTick: { show: false },
      axisLabel: { show: false },
      splitLine: { show: false },
      pointer: { show: true, showAbove: false, itemStyle: { color: c.muted } },
      progress: { show: true, width: 10, itemStyle: { color: c.ok } },
      detail: {
        valueAnimation: false,
        formatter: "{value}%",
        color: c.text,
        fontSize: 18,
        offsetCenter: [0, "-10%"]
      },
      data: [{ value: value, name: "" }]
    }];
    return option;
  }

  function verdictKindOrder(kinds) {
    var ordered = VERDICT_ORDER.filter(function (kind) {
      return kinds.indexOf(kind) !== -1;
    });
    kinds.slice().sort().forEach(function (kind) {
      if (ordered.indexOf(kind) === -1) { ordered.push(kind); }
    });
    return ordered;
  }

  function verdictMixOption(metric) {
    var dist = metric && metric.value;
    if (!dist || typeof dist !== "object" || Array.isArray(dist)) {
      return null;
    }
    var steps = Object.keys(dist);
    if (!steps.length) { return null; }
    var kinds = [];
    steps.forEach(function (step) {
      Object.keys(dist[step] || {}).forEach(function (kind) {
        var key = String(kind).toLowerCase();
        if (kinds.indexOf(key) === -1) { kinds.push(key); }
      });
    });
    kinds = verdictKindOrder(kinds);
    var c = colors();
    var colorsBy = { pass: c.ok, fail: c.warn, unverifiable: c.unv, env: c.err };
    var option = statBaseOption();
    option.tooltip = { trigger: "axis" };
    option.legend = { show: true, top: 0, textStyle: { color: c.muted } };
    option.grid = { left: 64, right: 16, top: 28, bottom: 24 };
    option.xAxis = {
      type: "value", minInterval: 1,
      axisLabel: { color: c.muted },
      splitLine: { lineStyle: { color: c.line } }
    };
    option.yAxis = {
      type: "category", data: steps,
      axisLabel: { color: c.muted },
      axisLine: { lineStyle: { color: c.line } }
    };
    option.series = kinds.map(function (kind) {
      return {
        name: titleCase(kind),
        type: "bar",
        stack: "verdicts",
        itemStyle: { color: colorsBy[kind] || c.info },
        data: steps.map(function (step) {
          var bucket = dist[step] || {};
          var found = Object.keys(bucket).filter(function (key) {
            return String(key).toLowerCase() === kind;
          })[0];
          return found === undefined ? 0 : Number(bucket[found]) || 0;
        })
      };
    });
    return option;
  }

  function attemptBarsOption(metric) {
    var dist = metric && metric.value;
    if (!dist || typeof dist !== "object" || Array.isArray(dist)) {
      return null;
    }
    var keys = Object.keys(dist);
    if (!keys.length) { return null; }
    keys.sort(function (a, b) {
      var left = Number(a);
      var right = Number(b);
      if (!isNaN(left) && !isNaN(right)) { return left - right; }
      return a < b ? -1 : a > b ? 1 : 0;
    });
    var c = colors();
    var option = statBaseOption();
    option.tooltip = { trigger: "axis" };
    option.grid = { left: 40, right: 16, top: 16, bottom: 28 };
    option.xAxis = {
      type: "category", data: keys,
      axisLabel: { color: c.muted },
      axisLine: { lineStyle: { color: c.line } }
    };
    option.yAxis = {
      type: "value", minInterval: 1,
      axisLabel: { color: c.muted },
      splitLine: { lineStyle: { color: c.line } }
    };
    option.series = [{
      type: "bar",
      itemStyle: { color: c.info },
      data: keys.map(function (key) { return Number(dist[key]) || 0; })
    }];
    return option;
  }

  function durationBarsOption(metric) {
    var value = metric && metric.value;
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return null;
    }
    var steps = Object.keys(value);
    if (!steps.length) { return null; }
    var counts = (metric && metric.detail && metric.detail.counts) || {};
    var c = colors();
    var option = statBaseOption();
    option.tooltip = {
      trigger: "item",
      formatter: function (params) {
        var n = counts[params.name];
        return esc(params.name) + ": " + params.value + "s"
          + (n !== undefined && n !== null ? " (n=" + n + ")" : "");
      }
    };
    option.grid = { left: 72, right: 24, top: 8, bottom: 24 };
    option.xAxis = {
      type: "value",
      axisLabel: { color: c.muted },
      splitLine: { lineStyle: { color: c.line } }
    };
    option.yAxis = {
      type: "category", data: steps,
      axisLabel: { color: c.muted },
      axisLine: { lineStyle: { color: c.line } }
    };
    option.series = [{
      type: "bar",
      itemStyle: { color: c.info },
      data: steps.map(function (step) { return Number(value[step]) || 0; })
    }];
    return option;
  }

  function bigNumberStat(name, metric) {
    var available = metricAvailable(metric);
    var html = '<div class="stat stat-big' + (available ? "" : " stat-empty")
      + '" data-metric="' + esc(name) + '">';
    html += '<div class="stat-label">' + esc(prettyMetricName(name)) + "</div>";
    html += '<div class="stat-value">'
      + (available ? formatMetricValue(metric.value) : "--") + "</div>";
    html += '<div class="stat-basis">' + esc(metric && metric.basis) + "</div>";
    if (metric && metric.provenance) {
      html += provenanceBadge(metric.provenance);
    }
    html += "</div>";
    return html;
  }

  function chartStat(scope, name, size, option, metric) {
    if (!option) {
      return '<div class="stat stat-empty" data-metric="' + esc(name) + '">'
        + '<div class="stat-label">' + esc(prettyMetricName(name)) + "</div>"
        + '<div class="stat-value mono">--</div>'
        + '<div class="stat-basis">' + esc(metric && metric.basis) + "</div>"
        + "</div>";
    }
    var id = "stat-" + metricSlug(scope) + "-" + metricSlug(name);
    return { id: id, html: '<div class="stat stat-chart" data-metric="'
      + esc(name) + '">'
      + '<div class="stat-label">' + esc(prettyMetricName(name)) + "</div>"
      + '<div id="' + id + '" class="' + size + '"></div>'
      + '<div class="stat-basis">' + esc(metric && metric.basis) + "</div>"
      + "</div>" };
  }

  function keyStatsBand(scope, metrics, table) {
    var mounts = [];
    var bars = Object.keys(table || {}).map(function (name) {
      if (!metrics || !(name in metrics)) { return ""; }
      var metric = metrics[name];
      var renderer = table[name];
      if (renderer === "bigNumber") {
        return bigNumberStat(name, metric);
      }
      var size = renderer === "gauge" ? "stat-gauge"
        : (renderer === "verdictMix" || renderer === "durationBars")
          ? "stat-mix" : "stat-bars";
      var option = null;
      if (renderer === "gauge") { option = gaugeOption(metric); }
      if (renderer === "verdictMix") { option = verdictMixOption(metric); }
      if (renderer === "attemptBars") { option = attemptBarsOption(metric); }
      if (renderer === "durationBars") { option = durationBarsOption(metric); }
      var stat = chartStat(scope, name, size, option, metric);
      if (stat && stat.html) {
        mounts.push({ id: stat.id, option: option });
        return stat.html;
      }
      return stat;
    }).join("");
    return { html: '<div class="stat-band" data-scope="' + esc(scope) + '">'
      + bars + "</div>", mounts: mounts };
  }

  function expanderHTML(scope, names, detailHtml) {
    var id = "details-" + metricSlug(scope);
    keyStatBodies[id] = detailHtml;
    return '<details class="metric-expander" id="' + esc(id) + '">'
      + '<summary class="metric-summary">'
      + '<span class="metric-summary-label">Show more...</span>'
      + '<span class="metric-summary-label metric-summary-less">Show less</span>'
      + '<span class="metric-summary-count mono">(' + names.length
      + " metrics)</span>"
      + "</summary>"
      + '<div class="metric-detail-body"></div></details>';
  }

  function populateExpander(node) {
    if (!node || !node.open) { return; }
    var body = node.querySelector
      && node.querySelector(".metric-detail-body");
    if (body && !body.innerHTML) {
      body.innerHTML = keyStatBodies[node.id] || "";
    }
  }

  function wireExpanders(root) {
    if (!root || !root.querySelectorAll) { return; }
    var nodes = root.querySelectorAll("details.metric-expander");
    for (var i = 0; i < nodes.length; i++) {
      (function (node) {
        if (node.__adlWired) { return; }
        node.__adlWired = true;
        node.addEventListener("toggle", function () {
          populateExpander(node);
        });
      })(nodes[i]);
    }
  }

  function categoryPanel(label, metrics, table, scope) {
    var available = metrics || {};
    var promoted = Object.keys(table).filter(function (name) {
      return name in available;
    });
    var detail = Object.keys(available).filter(function (name) {
      return promoted.indexOf(name) === -1;
    }).sort();
    var band = keyStatsBand(scope, available, table);
    var html = '<div class="metric-section"><h2>' + esc(label) + "</h2>"
      + band.html;
    if (detail.length) {
      html += expanderHTML(scope, detail, metricGrid(detail, available));
    }
    html += "</div>";
    return { html: html, mounts: band.mounts };
  }

  function renderAggregate(payload) {
    var container = el("aggregate");
    if (!container) { return; }
    disposeCharts();
    var metrics = payload.metrics || {};
    var panels = [
      categoryPanel("Loop health", metrics.loop_health,
        KEY_STATS.loop_health, "loop-health"),
      categoryPanel("TDD", metrics.tdd, KEY_STATS.tdd, "tdd"),
      categoryPanel("Review", metrics.review, KEY_STATS.review, "review"),
      categoryPanel("QA", metrics.qa, KEY_STATS.qa, "qa")
    ];
    container.innerHTML = panels.map(function (panel) {
      return panel.html;
    }).join("");
    wireExpanders(container);
    panels.forEach(function (panel) {
      panel.mounts.forEach(function (mount) {
        mountChart(mount.id, mount.option);
      });
    });
  }

  /* List -------------------------------------------------------------- */
  function filtersFromInputs() {
    var params = new URLSearchParams();
    var since = el("since") && el("since").value;
    var until = el("until") && el("until").value;
    if (since) { params.set("since", since); }
    if (until) { params.set("until", until); }
    var statuses = [];
    var boxes = document.querySelectorAll('input[name="status"]:checked');
    for (var i = 0; i < boxes.length; i++) { statuses.push(boxes[i].value); }
    if (statuses.length) { params.set("status", statuses.join(",")); }
    return params;
  }

  function applyFiltersToInputs(params) {
    var since = params.get("since") || "";
    var until = params.get("until") || "";
    if (el("since")) { el("since").value = since; }
    if (el("until")) { el("until").value = until; }
    var selected = (params.get("status") || "").split(",");
    var boxes = document.querySelectorAll('input[name="status"]');
    for (var i = 0; i < boxes.length; i++) {
      boxes[i].checked = selected.indexOf(boxes[i].value) !== -1;
    }
  }

  function verdictSummary(summary) {
    var keys = Object.keys(summary || {});
    if (!keys.length) { return "--"; }
    return keys.map(function (key) {
      return esc(key) + ":" + esc(summary[key]);
    }).join(" ");
  }

  function renderList(payload) {
    state.payload = payload;
    setText("list-notice", payload.notice || "");
    var body = el("runs-body");
    if (!body) { return; }
    var rows = payload.records || [];
    setText("run-count", rows.length + " record" + (rows.length === 1 ? "" : "s"));
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty">'
        + "No runs match these filters.</td></tr>";
      renderAggregate(payload);
      return;
    }
    body.innerHTML = rows.map(function (record) {
      var href = "/history/" + encodeURIComponent(record.thread_id);
      return "<tr>"
        + "<td>" + statusBadge(record.status) + "</td>"
        + '<td><a href="' + href + '">' + esc(record.subject || "(no subject)")
        + "</a></td>"
        + '<td class="mono">' + esc(record.thread_id) + "</td>"
        + '<td class="mono">' + esc(fmtTs(record.ts_start)) + "</td>"
        + '<td class="mono num">' + esc(record.attempt_count) + "</td>"
        + "<td>" + verdictSummary(record.verdict_summary) + "</td>"
        + '<td class="mono path">' + esc(record.run_dir) + "</td>"
        + "</tr>";
    }).join("");
    renderAggregate(payload);
  }

  function loadList() {
    var params = filtersFromInputs();
    var query = params.toString();
    try {
      history.replaceState(null, "", query ? "?" + query : location.pathname);
    } catch (err) { /* history API optional */ }
    fetch("/history/data" + (query ? "?" + query : ""))
      .then(function (response) {
        return response.json().then(function (payload) {
          return { ok: response.ok, payload: payload };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          setText("list-notice", result.payload.error || "request failed");
          return;
        }
        renderList(result.payload);
      })
      .catch(function () { setText("list-notice", "failed to load runs"); });
  }

  /* Detail ------------------------------------------------------------ */
  function detailId() {
    var match = location.pathname.match(/^\/history\/([^/]+)\/?$/);
    if (!match) { return null; }
    var value = decodeURIComponent(match[1]);
    return value === "data" ? null : value;
  }

  function renderThreads(payload) {
    var container = el("detail-threads");
    if (!container) { return; }
    var threads = payload.threads || [];
    if (!threads.length) {
      container.innerHTML = '<p class="empty">No threads in this run.</p>';
      return;
    }
    var mounts = [];
    container.innerHTML = threads.map(function (thread) {
      var attempts = (thread.attempts || []).map(function (attempt) {
        var verdicts = (attempt.verdicts || []).map(function (verdict) {
          return '<span class="verdict-item">' + badge(verdict.verdict)
            + '<span class="verdict-step">' + esc(verdict.step) + "</span>"
            + provenanceBadge(verdict.provenance) + "</span>";
        }).join("");
        return '<div class="attempt"><div class="attempt-label mono">attempt '
          + esc(attempt.number) + '</div><div class="verdicts">' + verdicts
          + "</div></div>";
      }).join("");
      var threadMetrics = thread.metrics || {};
      var scope = "thread-" + thread.thread_id;
      var band = keyStatsBand(scope, threadMetrics, THREAD_KEY_STATS);
      mounts = mounts.concat(band.mounts);
      var detail = Object.keys(threadMetrics).filter(function (name) {
        return !(name in THREAD_KEY_STATS);
      }).sort();
      var metrics = band.html;
      if (detail.length) {
        metrics += expanderHTML(scope, detail,
          metricGrid(detail, threadMetrics));
      }
      return '<section class="panel thread">'
        + '<div class="panel-h"><span>' + esc(thread.thread_id) + "</span>"
        + statusBadge(thread.status) + "</div>"
        + '<div class="thread-body">'
        + '<div class="thread-sub">' + esc(thread.subject || "(no subject)")
        + ' <span class="mono">' + esc(fmtTs(thread.ts_start)) + " to "
        + esc(fmtTs(thread.ts_end)) + "</span></div>"
        + attempts + metrics + "</div></section>";
    }).join("");
    wireExpanders(container);
    mounts.forEach(function (mount) {
      mountChart(mount.id, mount.option);
    });
  }

  function renderArtifacts(payload) {
    var container = el("detail-artifacts");
    if (!container) { return; }
    var artifacts = payload.artifacts || {};
    var names = ["brief", "summary", "findings", "qa"];
    var cards = names.map(function (name) {
      var artifact = artifacts[name] || {};
      var present = artifact.present === true;
      return '<div class="artifact' + (present ? "" : " missing") + '">'
        + '<div class="artifact-name">' + esc(name) + "</div>"
        + '<div class="artifact-file mono">' + esc(artifact.file || "") + "</div>"
        + '<div class="artifact-state">' + (present ? "present" : "missing")
        + "</div></div>";
    }).join("");
    var facts = artifacts.facts || {};
    var factList = Object.keys(facts).map(function (key) {
      var value = facts[key];
      if (Array.isArray(value)) { value = value.join("; "); }
      return "<li><span>" + esc(key) + "</span><span class=\"mono\">"
        + esc(value === null || value === undefined ? "--" : value)
        + "</span></li>";
    }).join("");
    container.innerHTML = '<div class="artifact-grid">' + cards + "</div>"
      + '<ul class="facts">' + factList + "</ul>";
  }

  function disposeCharts() {
    charts.forEach(function (chart) { chart.dispose(); });
    charts = [];
  }

  function mountChart(id, option) {
    var node = el(id);
    if (!node || typeof echarts === "undefined") { return; }
    var instance = echarts.init(node, null, { renderer: "svg" });
    instance.setOption(option);
    charts.push(instance);
  }

  function baseOption() {
    var c = colors();
    return {
      backgroundColor: "transparent",
      textStyle: { color: c.text, fontFamily: token("--adl-font-sans") },
      tooltip: { trigger: "axis" },
      grid: { left: 48, right: 16, top: 24, bottom: 32 }
    };
  }

  function chartAttempts(payload) {
    var c = colors();
    var threads = payload.threads || [];
    var option = baseOption();
    option.xAxis = {
      type: "category",
      data: threads.map(function (thread) { return thread.thread_id; }),
      axisLabel: { color: c.muted, rotate: 30 },
      axisLine: { lineStyle: { color: c.line } }
    };
    option.yAxis = {
      type: "value", minInterval: 1,
      axisLabel: { color: c.muted },
      splitLine: { lineStyle: { color: c.line } }
    };
    option.series = [{
      type: "bar",
      data: threads.map(function (thread) {
        return (thread.attempts || []).length;
      }),
      itemStyle: { color: c.info }
    }];
    return option;
  }

  function chartVerdicts(payload) {
    var c = colors();
    var counts = { pass: 0, fail: 0, unverifiable: 0, env: 0 };
    (payload.threads || []).forEach(function (thread) {
      (thread.attempts || []).forEach(function (attempt) {
        (attempt.verdicts || []).forEach(function (verdict) {
          var key = String(verdict.verdict || "").toLowerCase();
          if (!(key in counts)) { counts[key] = 0; }
          counts[key] += 1;
        });
      });
    });
    var colorsBy = {
      pass: c.ok, fail: c.warn, unverifiable: c.unv, env: c.err
    };
    var keys = Object.keys(counts);
    var option = baseOption();
    option.xAxis = {
      type: "category", data: keys,
      axisLabel: { color: c.muted },
      axisLine: { lineStyle: { color: c.line } }
    };
    option.yAxis = {
      type: "value", minInterval: 1,
      axisLabel: { color: c.muted },
      splitLine: { lineStyle: { color: c.line } }
    };
    option.series = [{
      type: "bar",
      data: keys.map(function (key) {
        return { value: counts[key], itemStyle: { color: colorsBy[key] || c.info } };
      })
    }];
    return option;
  }

  function chartSequence(payload) {
    var c = colors();
    var steps = [];
    var points = [];
    (payload.threads || []).forEach(function (thread, threadIndex) {
      (thread.attempts || []).forEach(function (attempt) {
        (attempt.verdicts || []).forEach(function (verdict) {
          if (steps.indexOf(verdict.step) === -1) { steps.push(verdict.step); }
          points.push([
            steps.indexOf(verdict.step),
            threadIndex * 10 + attempt.number,
            verdict.verdict
          ]);
        });
      });
    });
    var kinds = ["pass", "fail", "unverifiable", "env"];
    var colorsBy = {
      pass: c.ok, fail: c.warn, unverifiable: c.unv, env: c.err
    };
    var option = baseOption();
    option.xAxis = {
      type: "category", data: steps,
      axisLabel: { color: c.muted },
      axisLine: { lineStyle: { color: c.line } }
    };
    option.yAxis = {
      type: "value",
      axisLabel: { color: c.muted },
      splitLine: { lineStyle: { color: c.line } }
    };
    option.series = kinds.map(function (kind) {
      return {
        name: kind,
        type: "line",
        symbol: "none",
        lineStyle: { width: 14, color: colorsBy[kind] },
        data: points.filter(function (point) { return point[2] === kind; })
          .map(function (point) { return [point[0], point[1]]; })
      };
    });
    return option;
  }

  function renderCharts(payload) {
    disposeCharts();
    mountChart("chart-attempts", chartAttempts(payload));
    mountChart("chart-verdicts", chartVerdicts(payload));
    mountChart("chart-sequence", chartSequence(payload));
  }

  function renderDetail(payload) {
    state.payload = payload;
    state.runId = payload.run_id;
    setText("detail-id", payload.run_id);
    setText("detail-notice", payload.notice || "");
    renderCharts(payload);
    renderThreads(payload);
    renderArtifacts(payload);
  }

  function notFound() {
    show(el("list-view"), false);
    show(el("detail-view"), false);
    show(el("not-found"), true);
  }

  function loadDetail(runId) {
    fetch("/history/" + encodeURIComponent(runId) + "/data")
      .then(function (response) {
        if (response.status === 404) { notFound(); return null; }
        return response.json();
      })
      .then(function (payload) {
        if (payload) { renderDetail(payload); }
      })
      .catch(function () { notFound(); });
  }

  /* Routing and refresh ----------------------------------------------- */
  function renderCurrent() {
    if (mode === "list" && state.payload) {
      renderList(state.payload);
    } else if (mode === "detail" && state.payload) {
      renderDetail(state.payload);
    }
  }

  function load() {
    var runId = detailId();
    if (runId) {
      mode = "detail";
      show(el("list-view"), false);
      show(el("detail-view"), true);
      show(el("not-found"), false);
      loadDetail(runId);
      return;
    }
    mode = "list";
    show(el("list-view"), true);
    show(el("detail-view"), false);
    show(el("not-found"), false);
    applyFiltersToInputs(new URLSearchParams(location.search));
    loadList();
  }

  function resizeCharts() {
    charts.forEach(function (chart) { chart.resize(); });
  }

  function init() {
    var form = el("filters");
    if (form) {
      // Filters auto-apply: Enter in a date field must not reload the page.
      form.addEventListener("submit", function (event) {
        event.preventDefault();
      });
    }
    var autoApply = function () { loadList(); };
    var field = el("since");
    if (field) { field.addEventListener("change", autoApply); }
    field = el("until");
    if (field) { field.addEventListener("change", autoApply); }
    var boxes = document.querySelectorAll('input[name="status"]');
    for (var i = 0; i < boxes.length; i++) {
      boxes[i].addEventListener("change", autoApply);
    }
    var reset = el("reset");
    if (reset) {
      reset.addEventListener("click", function () {
        if (el("since")) { el("since").value = ""; }
        if (el("until")) { el("until").value = ""; }
        var boxes = document.querySelectorAll('input[name="status"]');
        for (var i = 0; i < boxes.length; i++) { boxes[i].checked = false; }
        loadList();
      });
    }
    var themeButton = el("theme-toggle");
    if (themeButton) {
      themeButton.addEventListener("click", function () {
        theme = theme === "dark" ? "light" : "dark";
        applyTheme(theme);
        try { window.localStorage.setItem("adl-theme", theme); }
        catch (err) { /* session-only */ }
        renderCurrent();
        resizeCharts();
      });
    }
    window.addEventListener("resize", resizeCharts);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) { load(); }
    });
    load();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
