"""The Jev stats page: one self-contained HTML file, no libraries.

``render_page(stats)`` takes the dict from ``stats.build_stats`` and returns
HTML with the data embedded and every chart drawn in the page's own script as
SVG.  ``standalone=True`` wraps it in a full document for opening from disk;
``False`` gives the fragment the artifact publisher expects (it supplies the
document around it).

Colours follow the dataviz reference palette: one blue for magnitude, status
colours only for the creature verdicts (always with an icon and a word), and
separate light and dark steps selected rather than flipped.
"""
from __future__ import annotations

import json

TITLE = "Jev Stats"

_PAGE = r"""<title>Jev Stats</title>
<style>
  /* One screen of numbers first, then charts, then the two tables.
     Tokens follow the dataviz reference palette; text never wears the series colour. */
  :root {
    --plane: #f9f9f7;  --surface: #fcfcfb;
    --ink: #0b0b0b;    --ink-2: #52514e;   --muted: #898781;
    --grid: #e1e0d9;   --axis: #c3c2b7;    --ring: rgba(11,11,11,.10);
    --series: #2a78d6; --series-wash: rgba(42,120,214,.10);
    --good: #0ca30c;   --good-text: #006300;
    --serious: #ec835a; --critical: #d03b3b;
    --font: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    --gutter: 16px;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --plane: #0d0d0d;  --surface: #1a1a19;
      --ink: #ffffff;    --ink-2: #c3c2b7;
      --grid: #2c2c2a;   --axis: #383835;    --ring: rgba(255,255,255,.10);
      --series: #3987e5; --series-wash: rgba(57,135,229,.12);
      --good-text: #0ca30c;
      color-scheme: dark;
    }
  }
  :root[data-theme="dark"] {
    --plane: #0d0d0d;  --surface: #1a1a19;
    --ink: #ffffff;    --ink-2: #c3c2b7;
    --grid: #2c2c2a;   --axis: #383835;    --ring: rgba(255,255,255,.10);
    --series: #3987e5; --series-wash: rgba(57,135,229,.12);
    --good-text: #0ca30c;
    color-scheme: dark;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--plane); color: var(--ink);
    font: 14px/1.45 var(--font);
    padding-inline: var(--gutter); padding-block: 24px 48px;
  }
  main { max-width: 1040px; margin: 0 auto; display: grid; gap: 20px; }
  h1 { font-size: 22px; line-height: 1.2; margin: 0; font-weight: 650; }
  h2 { font-size: 15px; margin: 0; font-weight: 650; text-wrap: balance; }
  .sub { color: var(--ink-2); margin: 2px 0 0; }
  header { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 16px; align-items: baseline; }
  .stamp { color: var(--muted); font-size: 12px; }

  .panel { background: var(--surface); border-radius: 12px; box-shadow: 0 0 0 1px var(--ring); padding: 16px; min-width: 0; }
  .hero { display: grid; gap: 4px; }
  .hero .value { font-size: 56px; line-height: 1; font-weight: 650; letter-spacing: -.02em; }
  .hero .label { color: var(--ink-2); }
  .tiles { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
  .tile { background: var(--surface); border-radius: 12px; box-shadow: 0 0 0 1px var(--ring); padding: 12px 14px; min-width: 0; }
  .tile .label { color: var(--ink-2); font-size: 12px; }
  .tile .value { font-size: 26px; line-height: 1.15; font-weight: 650; margin-top: 2px; }
  .tile .note { color: var(--muted); font-size: 12px; margin-top: 2px; }

  .grid { display: grid; gap: 20px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 440px), 1fr)); }
  .chart { margin-top: 10px; position: relative; }
  .chart svg { display: block; width: 100%; height: auto; overflow: visible; }
  .chart text { font-family: var(--font); fill: var(--muted); font-size: 11px; }
  .chart text.strong { fill: var(--ink); font-size: 12px; font-weight: 600; }
  .chart text.row { fill: var(--ink-2); font-size: 12px; }
  .chart .grid-line { stroke: var(--grid); stroke-width: 1; }
  .chart .axis-line { stroke: var(--axis); stroke-width: 1; }
  .chart .mark { fill: var(--series); }
  .chart .band:hover + .mark, .chart .band:focus + .mark { filter: brightness(1.12); }
  .chart .band { fill: transparent; outline: none; }
  .chart .band:focus-visible { stroke: var(--ink); stroke-width: 1.5; }
  .empty { color: var(--muted); padding: 28px 0; text-align: center; }

  .tip { position: fixed; z-index: 10; pointer-events: none; background: var(--ink); color: var(--surface);
         font-size: 12px; line-height: 1.4; padding: 6px 9px; border-radius: 8px; max-width: 240px; opacity: 0; }
  .tip.on { opacity: 1; }
  .tip b { font-weight: 650; }

  details { margin-top: 8px; }
  summary { cursor: pointer; color: var(--ink-2); font-size: 12px; width: fit-content; }
  summary:focus-visible { outline: 2px solid var(--series); outline-offset: 2px; border-radius: 4px; }
  .scroll { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--grid); white-space: nowrap; }
  th { color: var(--ink-2); font-weight: 600; font-size: 12px; }
  td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
  tr:last-child td { border-bottom: 0; }

  .pill { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; padding: 2px 9px 2px 7px;
          border-radius: 999px; box-shadow: 0 0 0 1px var(--ring); color: var(--ink); }
  .pill i { font-style: normal; font-weight: 700; }
  .pill.hunt i { color: var(--good); } .pill.avoid i { color: var(--serious); } .pill.ignore i { color: var(--muted); }
  .meter { display: inline-block; width: 64px; height: 6px; border-radius: 3px; background: var(--series-wash); vertical-align: middle; overflow: hidden; }
  .meter span { display: block; height: 100%; background: var(--series); }
  .learned { color: var(--muted); font-size: 12px; margin-left: 6px; }
  @media (max-width: 480px) { .hero .value { font-size: 44px; } }
</style>

<main>
  <header>
    <div>
      <h1>Jev Stats</h1>
      <p class="sub" id="lede"></p>
    </div>
    <div class="stamp" id="stamp"></div>
  </header>

  <section class="panel hero" aria-label="Gold earned">
    <div class="label">Gold earned with Jev in control</div>
    <div class="value" id="hero-value"></div>
    <div class="label" id="hero-note"></div>
  </section>

  <section class="tiles" id="tiles" aria-label="Totals"></section>

  <section class="panel">
    <h2>Gold per run</h2>
    <p class="sub">What the character's gold changed by between the start and end of each run.</p>
    <div class="chart" id="c-gold"></div>
  </section>

  <div class="grid">
    <section class="panel">
      <h2>What Jev chose</h2>
      <p class="sub" id="choice-sub"></p>
      <div class="chart" id="c-actions"></div>
    </section>
    <section class="panel">
      <h2>How sure it was</h2>
      <p class="sub">Confidence on the decisions Jev made itself, in tenths.</p>
      <div class="chart" id="c-confidence"></div>
    </section>
    <section class="panel">
      <h2>Tokens per run</h2>
      <p class="sub">Input tokens sent to Jev. Output tokens are free.</p>
      <div class="chart" id="c-tokens"></div>
    </section>
    <section class="panel">
      <h2>Held back for low confidence</h2>
      <p class="sub">Times Jev's pick fell under the bar for that action, so the cautious move ran instead.</p>
      <div class="chart" id="c-held"></div>
    </section>
  </div>

  <section class="panel">
    <h2>Runs</h2>
    <div class="scroll" id="runs"></div>
  </section>

  <section class="panel">
    <h2>Creatures Jev has judged</h2>
    <p class="sub">Judged once per name. "Learned" means it beat us, whatever Jev thought.</p>
    <div class="scroll" id="creatures"></div>
  </section>
</main>
<div class="tip" id="tip" role="status"></div>

<script id="stats-data" type="application/json">__DATA__</script>
<script>
(function () {
  var S = JSON.parse(document.getElementById("stats-data").textContent);
  var T = S.totals, D = S.decisions;
  var SVGNS = "http://www.w3.org/2000/svg";

  // ---- small helpers -------------------------------------------------------
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    for (var k in (attrs || {})) n.setAttribute(k, attrs[k]);
    (kids || []).forEach(function (c) { n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function sv(tag, attrs, text) {
    var n = document.createElementNS(SVGNS, tag);
    for (var k in (attrs || {})) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }
  function num(n) { return n == null ? "–" : Number(n).toLocaleString(); }
  function compact(n) {
    if (n == null) return "–";
    var a = Math.abs(n);
    if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "M";
    if (a >= 1e3) return (n / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "K";
    return String(n);
  }
  function money(n, digits) { return n == null ? "–" : "$" + Number(n).toFixed(digits == null ? 2 : digits); }
  function signed(n) { return (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n).toLocaleString(); }
  function dur(seconds) {
    var m = Math.round(seconds / 60);
    return m >= 60 ? Math.floor(m / 60) + " h " + (m % 60) + " min" : m + " min";
  }
  function when(ts) {
    if (!ts) return "";
    return new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  function niceMax(v) {
    if (v <= 0) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(v))), f = v / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }

  // ---- tooltip -------------------------------------------------------------
  var tip = document.getElementById("tip");
  function showTip(html, x, y) {
    tip.innerHTML = html;                       // built only from numbers and escaped text
    var w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, x - w / 2)) + "px";
    tip.style.top = Math.max(8, y - h - 12) + "px";
    tip.classList.add("on");
  }
  function hideTip() { tip.classList.remove("on"); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function bindTip(node, html) {
    function at() { var r = node.getBoundingClientRect(); showTip(html, r.left + r.width / 2, r.top); }
    node.addEventListener("pointerenter", at);
    node.addEventListener("pointermove", function (e) { showTip(html, e.clientX, e.clientY - 4); });
    node.addEventListener("pointerleave", hideTip);
    node.addEventListener("focus", at);
    node.addEventListener("blur", hideTip);
  }

  // ---- charts --------------------------------------------------------------
  // A column chart: bars <= 24px, 4px rounded tops, square at the baseline,
  // one baseline, hairline grid, the value labelled only on the tallest and the last.
  function columns(host, items, opt) {
    host.textContent = "";
    if (!items.length) { host.appendChild(el("div", { "class": "empty" }, [opt.empty || "Nothing recorded yet."])); return; }
    var W = Math.max(260, host.clientWidth), H = opt.height || 190;
    var m = { l: 44, r: 8, t: 14, b: 26 };
    var top = niceMax(Math.max.apply(null, items.map(function (i) { return i.value; })));
    var innerW = W - m.l - m.r, innerH = H - m.t - m.b;
    var y = function (v) { return m.t + innerH - (v / top) * innerH; };
    var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": opt.label });
    for (var t = 0; t <= 4; t++) {
      var tv = top * t / 4;
      svg.appendChild(sv("line", { "class": t ? "grid-line" : "axis-line", x1: m.l, x2: W - m.r, y1: y(tv), y2: y(tv) }));
      svg.appendChild(sv("text", { x: m.l - 6, y: y(tv) + 4, "text-anchor": "end" }, (opt.fmt || compact)(tv)));
    }
    var band = innerW / items.length, bw = Math.min(24, band * 0.6);
    var maxAt = 0; items.forEach(function (it, i) { if (it.value > items[maxAt].value) maxAt = i; });
    items.forEach(function (it, i) {
      var cx = m.l + band * i + band / 2, x0 = cx - bw / 2;
      var h = Math.max(0, y(0) - y(it.value));
      var hit = sv("rect", { "class": "band", x: m.l + band * i, y: m.t, width: band, height: innerH, tabindex: 0, "aria-label": it.tip.replace(/<[^>]+>/g, " ") });
      svg.appendChild(hit);
      if (h > 0) {
        var r = Math.min(4, h, bw / 2);
        svg.appendChild(sv("path", { "class": "mark", d:
          "M" + x0 + "," + y(0) + "V" + (y(0) - h + r) + "Q" + x0 + "," + (y(0) - h) + " " + (x0 + r) + "," + (y(0) - h) +
          "H" + (x0 + bw - r) + "Q" + (x0 + bw) + "," + (y(0) - h) + " " + (x0 + bw) + "," + (y(0) - h + r) + "V" + y(0) + "Z" }));
      } else {
        svg.appendChild(sv("rect", { "class": "mark", x: x0, y: y(0) - 2, width: bw, height: 2 }));
      }
      if (i === maxAt || i === items.length - 1) {
        svg.appendChild(sv("text", { "class": "strong", x: cx, y: y(it.value) - 6, "text-anchor": "middle" }, (opt.fmt || compact)(it.value)));
      }
      var every = Math.ceil(items.length / Math.max(1, Math.floor(innerW / 34)));
      if (i % every === 0 || i === items.length - 1) {
        svg.appendChild(sv("text", { x: cx, y: H - 8, "text-anchor": "middle" }, it.label));
      }
      bindTip(hit, it.tip);
    });
    host.appendChild(svg);
  }

  // Horizontal bars: label on the left, value at the tip.
  function rows(host, items, opt) {
    host.textContent = "";
    if (!items.length) { host.appendChild(el("div", { "class": "empty" }, [opt.empty || "Nothing recorded yet."])); return; }
    var W = Math.max(260, host.clientWidth), rowH = 30, m = { l: 84, r: 52, t: 4, b: 4 };
    var H = m.t + m.b + rowH * items.length;
    var top = Math.max.apply(null, items.map(function (i) { return i.value; })) || 1;
    var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": opt.label });
    svg.appendChild(sv("line", { "class": "axis-line", x1: m.l, x2: m.l, y1: m.t, y2: H - m.b }));
    items.forEach(function (it, i) {
      var cy = m.t + rowH * i + rowH / 2, len = Math.max(2, (W - m.l - m.r) * it.value / top), bh = 18;
      var hit = sv("rect", { "class": "band", x: 0, y: cy - rowH / 2, width: W, height: rowH, tabindex: 0, "aria-label": it.tip.replace(/<[^>]+>/g, " ") });
      svg.appendChild(hit);
      var r = Math.min(4, len, bh / 2);
      svg.appendChild(sv("path", { "class": "mark", d:
        "M" + m.l + "," + (cy - bh / 2) + "H" + (m.l + len - r) + "Q" + (m.l + len) + "," + (cy - bh / 2) + " " + (m.l + len) + "," + (cy - bh / 2 + r) +
        "V" + (cy + bh / 2 - r) + "Q" + (m.l + len) + "," + (cy + bh / 2) + " " + (m.l + len - r) + "," + (cy + bh / 2) + "H" + m.l + "Z" }));
      svg.appendChild(sv("text", { "class": "row", x: m.l - 8, y: cy + 4, "text-anchor": "end" }, it.label));
      svg.appendChild(sv("text", { "class": "strong", x: m.l + len + 6, y: cy + 4 }, num(it.value)));
      bindTip(hit, it.tip);
    });
    host.appendChild(svg);
  }

  // The table behind a chart, for anyone who would rather read the numbers.
  function tableUnder(host, head, body) {
    var old = host.parentNode.querySelector("details"); if (old) old.remove();
    var t = el("table", {}, [el("thead", {}, [el("tr", {}, head.map(function (h, i) { return el("th", i ? { "class": "n" } : {}, [h]); }))]),
      el("tbody", {}, body.map(function (r) { return el("tr", {}, r.map(function (c, i) { return el("td", i ? { "class": "n" } : {}, [String(c)]); })); }))]);
    host.parentNode.appendChild(el("details", {}, [el("summary", {}, ["Show as table"]), el("div", { "class": "scroll" }, [t])]));
  }

  // ---- fill the page ---------------------------------------------------------
  var C = S.creatures;
  document.getElementById("lede").textContent =
    T.runs ? T.runs + " runs, " + T.hours + " hours. Jev makes the calls; the client does the walking, casting and looting."
           : "No runs recorded yet. Start one with: uo jev";
  document.getElementById("stamp").textContent = "Updated " + new Date(S.generated_at * 1000).toLocaleString();
  document.getElementById("hero-value").textContent = signed(T.gold);
  document.getElementById("hero-note").textContent =
    T.runs ? num(T.gold_per_hour) + " gold an hour" + (T.cost_per_1k_gold != null ? ", at " + money(T.cost_per_1k_gold, 3) + " of Jev per 1,000 gold" : "") : "";

  var known = S.runs.filter(function (r) { return r.kills != null; }).length;
  var tiles = [
    ["Jev calls", num(T.calls), num(T.tokens_per_call) + " tokens each"],
    ["Input tokens", compact(T.tokens), money(T.cost_usd, 4) + " spent"],
    ["Decisions logged", num(D.count), D.latency_ms.median != null ? D.latency_ms.median + " ms median, " + D.latency_ms.p95 + " ms p95" : ""],
    ["Kills", known ? num(T.kills) : "–", known ? known + " of " + T.runs + " runs counted" : "counted from the next run"],
    ["Items taken", known ? num(T.items) : "–", "from corpses, Jev's pick"],
    ["Deaths", num(T.deaths), T.deaths ? "each one teaches it a creature" : "none so far"]
  ];
  var tileHost = document.getElementById("tiles");
  tiles.forEach(function (t) {
    tileHost.appendChild(el("div", { "class": "tile" }, [el("div", { "class": "label" }, [t[0]]), el("div", { "class": "value" }, [t[1]]), el("div", { "class": "note" }, [t[2]])]));
  });
  document.getElementById("choice-sub").textContent =
    D.count ? num(D.count) + " decisions. " + (D.sources.jev || 0) + " by Jev, " + (D.sources.fallback || 0) + " by fallback, " + (D.sources.rule || 0) + " by the health rule." : "";

  var ACTION_NOTE = { roam: "walked on to find prey", fight: "attacked or carried on", wait: "stood still", flee: "ran from danger",
                      back_off: "kept clear of something strong", heal: "healed itself", loot: "took gold and items", rest: "meditated for mana" };

  function drawAll() {
    columns(document.getElementById("c-gold"), S.runs.map(function (r, i) {
      return { label: String(i + 1), value: Math.max(0, r.gold),
               tip: "<b>Run " + (i + 1) + "</b> " + esc(when(r.started_at)) + "<br>" + signed(r.gold) + " gold in " + dur(r.seconds) +
                    (r.kills != null ? "<br>" + r.kills + " kills" : "") + "<br>" + esc(r.stopped) };
    }), { label: "Gold per run", empty: "No runs yet.", fmt: compact });
    tableUnder(document.getElementById("c-gold"), ["Run", "Started", "Length", "Gold"],
      S.runs.map(function (r, i) { return [i + 1, when(r.started_at), dur(r.seconds), signed(r.gold)]; }));

    var acts = Object.keys(D.actions).map(function (k) { return { label: k.replace("_", " "), value: D.actions[k],
      tip: "<b>" + esc(k.replace("_", " ")) + "</b> " + num(D.actions[k]) + " times<br>" + esc(ACTION_NOTE[k] || "") +
           (D.confidence_by_action[k] != null ? "<br>average confidence " + D.confidence_by_action[k].toFixed(2) : "") }; });
    rows(document.getElementById("c-actions"), acts, { label: "Decisions by action", empty: "No decisions yet." });
    tableUnder(document.getElementById("c-actions"), ["Action", "Decisions", "Avg confidence"],
      Object.keys(D.actions).map(function (k) { return [k.replace("_", " "), num(D.actions[k]), D.confidence_by_action[k] != null ? D.confidence_by_action[k].toFixed(2) : "–"]; }));

    var n = D.confidence_histogram.length, total = D.confidence_histogram.reduce(function (a, b) { return a + b; }, 0);
    columns(document.getElementById("c-confidence"), D.confidence_histogram.map(function (v, i) {
      var lo = i / n, hi = (i + 1) / n;
      return { label: lo.toFixed(1), value: v, tip: "<b>" + lo.toFixed(1) + " to " + hi.toFixed(1) + "</b><br>" + num(v) + " decisions" + (total ? " (" + Math.round(100 * v / total) + "%)" : "") };
    }), { label: "Decisions by confidence", empty: "No decisions yet." });
    tableUnder(document.getElementById("c-confidence"), ["Confidence", "Decisions"],
      D.confidence_histogram.map(function (v, i) { return [(i / n).toFixed(1) + " to " + ((i + 1) / n).toFixed(1), num(v)]; }));

    columns(document.getElementById("c-tokens"), S.runs.map(function (r, i) {
      return { label: String(i + 1), value: r.tokens, tip: "<b>Run " + (i + 1) + "</b><br>" + num(r.tokens) + " tokens, " + num(r.calls) + " calls<br>" + money(r.cost_usd, 4) };
    }), { label: "Input tokens per run", empty: "No runs yet." });
    tableUnder(document.getElementById("c-tokens"), ["Run", "Calls", "Tokens", "Cost"],
      S.runs.map(function (r, i) { return [i + 1, num(r.calls), num(r.tokens), money(r.cost_usd, 4)]; }));

    var held = Object.keys(D.fallbacks).map(function (k) { return { label: k.replace("_", " "), value: D.fallbacks[k],
      tip: "<b>" + esc(k.replace("_", " ")) + "</b><br>" + num(D.fallbacks[k]) + " picks under the confidence bar" }; });
    rows(document.getElementById("c-held"), held, { label: "Picks held back", empty: "Nothing held back." });
    tableUnder(document.getElementById("c-held"), ["Action", "Held back"],
      Object.keys(D.fallbacks).map(function (k) { return [k.replace("_", " "), num(D.fallbacks[k])]; }));
  }

  // The two plain tables.
  var runHost = document.getElementById("runs");
  if (!S.runs.length) runHost.appendChild(el("div", { "class": "empty" }, ["No runs yet."]));
  else {
    var head = ["When", "Length", "Calls", "Tokens", "Cost", "Gold", "Kills", "Ended"];
    var body = S.runs.slice().reverse().map(function (r) {
      return [when(r.started_at), dur(r.seconds), num(r.calls), num(r.tokens), money(r.cost_usd, 4), signed(r.gold), r.kills == null ? "–" : num(r.kills), r.stopped];
    });
    runHost.appendChild(el("table", {}, [el("thead", {}, [el("tr", {}, head.map(function (h, i) { return el("th", i && i < 7 ? { "class": "n" } : {}, [h]); }))]),
      el("tbody", {}, body.map(function (row) { return el("tr", {}, row.map(function (c, i) { return el("td", i && i < 7 ? { "class": "n" } : {}, [c]); })); }))]));
  }

  var creatureHost = document.getElementById("creatures");
  if (!C.length) creatureHost.appendChild(el("div", { "class": "empty" }, ["No creatures judged yet."]));
  else {
    var ICON = { hunt: "✓", avoid: "▲", ignore: "–" }, WORD = { hunt: "Hunt", avoid: "Avoid", ignore: "Ignore" };
    creatureHost.appendChild(el("table", {}, [
      el("thead", {}, [el("tr", {}, [el("th", {}, ["Creature"]), el("th", {}, ["Verdict"]), el("th", { "class": "n" }, ["Monster odds"]), el("th", {}, ["Danger to us"]), el("th", { "class": "n" }, ["Kills"])])]),
      el("tbody", {}, C.map(function (c) {
        var name = el("td", {}, [c.name]); if (c.learned) name.appendChild(el("span", { "class": "learned" }, ["learned"]));
        var meter = el("span", { "class": "meter", title: "threat " + c.threat.toFixed(1) + " of 3" }, [el("span", { style: "width:" + Math.round(100 * Math.min(3, c.threat) / 3) + "%" })]);
        return el("tr", {}, [name,
          el("td", {}, [el("span", { "class": "pill " + c.verdict }, [el("i", { "aria-hidden": "true" }, [ICON[c.verdict]]), WORD[c.verdict]])]),
          el("td", { "class": "n" }, [c.prey.toFixed(2)]),
          el("td", {}, [meter, " " + c.threat.toFixed(1)]),
          el("td", { "class": "n" }, [known ? num(c.kills) : "–"])]);
      }))]));
  }

  drawAll();
  var timer; window.addEventListener("resize", function () { clearTimeout(timer); timer = setTimeout(drawAll, 120); });
})();
</script>
"""


def _embed(stats: dict) -> str:
    """JSON safe inside a <script> block: a creature name cannot close it."""
    text = json.dumps(stats, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_page(stats: dict, standalone: bool = True) -> str:
    body = _PAGE.replace("__DATA__", _embed(stats))
    if not standalone:
        return body
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "</head>\n<body>\n" + body + "\n</body>\n</html>\n")
