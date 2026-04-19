"""Generate a self-contained index.html from data/raw.json."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from fetch import SERIES

ROOT = Path(__file__).parent
DATA = ROOT / "data"

SECTOR_COLORS = {
    20052: "#0a0a0a",  # S&P 500 - black (index)
    20517: "#2563eb",  # Information Technology - blue
    20518: "#8b5cf6",  # Communication Services - violet
    20519: "#ec4899",  # Consumer Discretionary - pink
    20520: "#10b981",  # Financials - emerald
    20521: "#f59e0b",  # Industrials - amber
    20522: "#06b6d4",  # Utilities - cyan
    20523: "#b45309",  # Energy - brown
    20524: "#6366f1",  # Real Estate - indigo
    20525: "#84cc16",  # Materials - lime
    20526: "#ef4444",  # Consumer Staples - red
    20527: "#14b8a6",  # Health Care - teal
}


def build() -> Path:
    raw = json.loads((DATA / "raw.json").read_text())
    series_payload = []
    summary_rows = []
    for sid, name in SERIES.items():
        entry = raw.get(f"s:{sid}")
        if not entry:
            continue
        points = entry["series"][0]
        values = [v for _, v in points if v is not None]
        latest_date, latest_val = points[-1]
        mean = statistics.fmean(values)
        median = statistics.median(values)
        mn = min(values)
        mx = max(values)
        pct_from_median = (latest_val - median) / median * 100
        series_payload.append({
            "id": sid,
            "name": name,
            "color": SECTOR_COLORS[sid],
            "points": points,
            "isIndex": sid == 20052,
        })
        summary_rows.append({
            "id": sid,
            "name": name,
            "color": SECTOR_COLORS[sid],
            "latest_date": latest_date,
            "latest": latest_val,
            "mean": mean,
            "median": median,
            "min": mn,
            "max": mx,
            "pct_from_median": pct_from_median,
        })

    # sort summary: index first, then by pct_from_median desc (most expensive to cheapest)
    summary_rows.sort(key=lambda r: (0 if r["id"] == 20052 else 1, -r["pct_from_median"]))

    latest_date = max(r["latest_date"] for r in summary_rows)
    embedded = json.dumps({"series": series_payload, "summary": summary_rows})

    html = TEMPLATE.replace("__DATA__", embedded).replace("__LATEST_DATE__", latest_date)
    out = ROOT / "index.html"
    out.write_text(html)
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>S&P 500 Forward P/E — sector viewer</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  :root {
    --bg: #fafaf9;
    --panel: #ffffff;
    --ink: #0a0a0a;
    --muted: #6b7280;
    --line: #e7e5e4;
    --accent: #111111;
    --pos: #059669;
    --neg: #dc2626;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--ink);
    font: 14px/1.5 -apple-system, "SF Pro Text", "Segoe UI", system-ui, sans-serif; }
  header { padding: 28px 32px 16px; border-bottom: 1px solid var(--line); background: var(--panel); }
  h1 { margin: 0 0 4px; font-size: 20px; font-weight: 600; letter-spacing: -0.01em; }
  header .sub { color: var(--muted); font-size: 13px; }
  main { max-width: 1280px; margin: 0 auto; padding: 24px 32px 48px; }
  .controls { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; align-items: center; }
  .controls button {
    border: 1px solid var(--line); background: var(--panel); color: var(--ink);
    padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 12px;
    font-family: inherit;
  }
  .controls button:hover { background: #f5f5f4; }
  .controls button.active { background: var(--ink); color: #fff; border-color: var(--ink); }
  .chart-wrap { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
  #chart { width: 100%; height: 560px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; margin-top: 20px; }
  .card {
    background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
    padding: 12px 14px; cursor: pointer; transition: border-color .1s, transform .1s;
  }
  .card:hover { border-color: #a8a29e; transform: translateY(-1px); }
  .card.muted { opacity: 0.35; }
  .card .row { display: flex; justify-content: space-between; align-items: baseline; }
  .card .name { font-weight: 600; font-size: 13px; display: flex; align-items: center; gap: 6px; }
  .card .swatch { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
  .card .latest { font-variant-numeric: tabular-nums; font-size: 20px; font-weight: 600; letter-spacing: -0.02em; }
  .card .delta { font-size: 12px; font-variant-numeric: tabular-nums; }
  .card .delta.pos { color: var(--neg); } /* higher PE vs median = more expensive = red-ish */
  .card .delta.neg { color: var(--pos); }
  .card .range { color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; margin-top: 4px; }
  table.summary { width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 13px; }
  table.summary th, table.summary td { padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: right; }
  table.summary th:first-child, table.summary td:first-child { text-align: left; }
  table.summary th { color: var(--muted); font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
  table.summary td.num { font-variant-numeric: tabular-nums; }
  table.summary tr.index td { font-weight: 600; background: #f5f5f4; }
  footer { color: var(--muted); font-size: 11px; text-align: center; padding: 24px; }
  footer a { color: var(--muted); }
</style>
</head>
<body>
<header>
  <h1>S&amp;P 500 Forward P/E — index &amp; 11 sectors</h1>
  <div class="sub">Daily 12-month forward price-to-earnings. Data through <span id="asof">__LATEST_DATE__</span>. Source: MacroMicro.</div>
</header>
<main>
  <div class="controls">
    <button data-range="all">All</button>
    <button data-range="10y" class="active">10Y</button>
    <button data-range="5y">5Y</button>
    <button data-range="3y">3Y</button>
    <button data-range="1y">1Y</button>
    <button data-range="ytd">YTD</button>
    <span style="flex:1"></span>
    <button id="only-index">Index only</button>
    <button id="only-sectors">Sectors only</button>
    <button id="show-all">Show all</button>
  </div>
  <div class="chart-wrap"><div id="chart"></div></div>

  <div class="grid" id="cards"></div>

  <table class="summary">
    <thead>
      <tr>
        <th>Series</th>
        <th>Latest</th>
        <th>vs median</th>
        <th>Median</th>
        <th>Mean</th>
        <th>Min</th>
        <th>Max</th>
      </tr>
    </thead>
    <tbody id="summary-body"></tbody>
  </table>
</main>
<footer>Built from MacroMicro series data. Chart powered by Plotly.</footer>

<script>
const DATA = __DATA__;

const fmt = n => n == null ? "—" : n.toFixed(2);
const fmtPct = n => (n >= 0 ? "+" : "") + n.toFixed(1) + "%";

// precompute numeric timestamps for fast window filtering
DATA.series.forEach(s => { s._t = s.points.map(p => Date.parse(p[0])); });

const traces = DATA.series.map(s => ({
  x: s.points.map(p => p[0]),
  y: s.points.map(p => p[1]),
  type: "scattergl",
  mode: "lines",
  name: s.name,
  line: { color: s.color, width: s.isIndex ? 2.8 : 1.8, shape: "linear" },
  hovertemplate: "<b>" + s.name + "</b>  %{y:.2f}<extra></extra>",
  visible: true,
  meta: s.id,
}));

const layout = {
  margin: { l: 52, r: 16, t: 10, b: 36 },
  hovermode: "x unified",
  xaxis: { showgrid: false, linecolor: "#e7e5e4", type: "date" },
  yaxis: { gridcolor: "#f5f5f4", zeroline: false, title: { text: "Forward P/E", font: { size: 12, color: "#6b7280" } } },
  legend: { orientation: "h", y: -0.18, font: { size: 11 } },
  paper_bgcolor: "#ffffff",
  plot_bgcolor: "#ffffff",
  font: { family: "-apple-system, SF Pro Text, Segoe UI, system-ui, sans-serif", size: 12, color: "#0a0a0a" },
};

const config = { displaylogo: false, responsive: true,
  modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"] };

Plotly.newPlot("chart", traces, layout, config).then(() => applyRange("10y"));

// compute a sensible Y range given a time window, across *visible* series,
// ignoring extreme anomalies (forward P/E outside [0, 80] is noise).
function yRangeForWindow(startMs, endMs) {
  const gd = document.getElementById("chart");
  const visMap = {};
  (gd.data || []).forEach((t, i) => { visMap[i] = t.visible !== "legendonly" && t.visible !== false; });
  let lo = Infinity, hi = -Infinity;
  DATA.series.forEach((s, i) => {
    if (!visMap[i]) return;
    const ts = s._t, pts = s.points;
    for (let j = 0; j < pts.length; j++) {
      const t = ts[j];
      if (t < startMs || t > endMs) continue;
      const v = pts[j][1];
      if (v == null || v < 0 || v > 80) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  });
  if (!isFinite(lo) || !isFinite(hi)) return null;
  const pad = Math.max((hi - lo) * 0.06, 0.5);
  return [Math.max(0, lo - pad), hi + pad];
}

function applyRange(key) {
  const nowStr = DATA.summary[0].latest_date;
  const now = new Date(nowStr);
  let start;
  if (key === "all") { start = new Date("1999-01-01"); }
  else if (key === "ytd") { start = new Date(now.getFullYear(), 0, 1); }
  else {
    const years = parseInt(key, 10);
    start = new Date(now); start.setFullYear(start.getFullYear() - years);
  }
  const yr = yRangeForWindow(start.getTime(), now.getTime());
  const upd = {
    "xaxis.range": [start.toISOString().slice(0,10), now.toISOString().slice(0,10)],
    "xaxis.autorange": false,
  };
  if (yr) { upd["yaxis.range"] = yr; upd["yaxis.autorange"] = false; }
  _skipRelayout = true;
  Plotly.relayout("chart", upd).then(() => { _skipRelayout = false; });
}
document.querySelectorAll("[data-range]").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("[data-range]").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    applyRange(btn.dataset.range);
  });
});

// rescale Y automatically when the user pans/zooms the X axis
let _skipRelayout = false;
document.getElementById("chart").on("plotly_relayout", ev => {
  if (_skipRelayout) return;
  const gd = document.getElementById("chart");
  let xr = gd.layout.xaxis.range;
  if (!xr || xr.length !== 2) return;
  const startMs = typeof xr[0] === "string" ? Date.parse(xr[0]) : +xr[0];
  const endMs = typeof xr[1] === "string" ? Date.parse(xr[1]) : +xr[1];
  const yr = yRangeForWindow(startMs, endMs);
  if (!yr) return;
  _skipRelayout = true;
  Plotly.relayout("chart", { "yaxis.range": yr, "yaxis.autorange": false })
    .then(() => { _skipRelayout = false; });
});

// visibility toggles
function setVisible(fn) {
  const vis = DATA.series.map(s => fn(s) ? true : "legendonly");
  Plotly.restyle("chart", { visible: vis }).then(rescaleY);
  document.querySelectorAll(".card").forEach(c => {
    const id = parseInt(c.dataset.id, 10);
    const s = DATA.series.find(x => x.id === id);
    c.classList.toggle("muted", !fn(s));
  });
}
function rescaleY() {
  const gd = document.getElementById("chart");
  const xr = gd.layout.xaxis.range;
  if (!xr || xr.length !== 2) return;
  const startMs = typeof xr[0] === "string" ? Date.parse(xr[0]) : +xr[0];
  const endMs = typeof xr[1] === "string" ? Date.parse(xr[1]) : +xr[1];
  const yr = yRangeForWindow(startMs, endMs);
  if (!yr) return;
  _skipRelayout = true;
  Plotly.relayout("chart", { "yaxis.range": yr, "yaxis.autorange": false })
    .then(() => { _skipRelayout = false; });
}
document.getElementById("only-index").addEventListener("click", () => setVisible(s => s.isIndex));
document.getElementById("only-sectors").addEventListener("click", () => setVisible(s => !s.isIndex));
document.getElementById("show-all").addEventListener("click", () => setVisible(() => true));

// cards
const cardsEl = document.getElementById("cards");
DATA.summary.forEach(row => {
  const card = document.createElement("div");
  card.className = "card";
  card.dataset.id = row.id;
  const deltaCls = row.pct_from_median > 0 ? "pos" : "neg";
  card.innerHTML = `
    <div class="row">
      <div class="name"><span class="swatch" style="background:${row.color}"></span>${row.name}</div>
      <div class="latest">${fmt(row.latest)}</div>
    </div>
    <div class="row">
      <div class="delta ${deltaCls}">${fmtPct(row.pct_from_median)} vs median</div>
      <div class="range">${fmt(row.min)}–${fmt(row.max)}</div>
    </div>
  `;
  card.addEventListener("click", () => {
    // solo this series
    const idx = DATA.series.findIndex(s => s.id === row.id);
    const vis = DATA.series.map((_, i) => i === idx ? true : "legendonly");
    Plotly.restyle("chart", { visible: vis }).then(rescaleY);
    document.querySelectorAll(".card").forEach(c => c.classList.toggle("muted", parseInt(c.dataset.id,10) !== row.id));
  });
  cardsEl.appendChild(card);
});

// summary table
const body = document.getElementById("summary-body");
DATA.summary.forEach(row => {
  const tr = document.createElement("tr");
  if (row.id === 20052) tr.classList.add("index");
  const deltaCls = row.pct_from_median > 0 ? "pos" : "neg";
  tr.innerHTML = `
    <td><span class="swatch" style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${row.color};margin-right:6px"></span>${row.name}</td>
    <td class="num">${fmt(row.latest)}</td>
    <td class="num ${deltaCls}">${fmtPct(row.pct_from_median)}</td>
    <td class="num">${fmt(row.median)}</td>
    <td class="num">${fmt(row.mean)}</td>
    <td class="num">${fmt(row.min)}</td>
    <td class="num">${fmt(row.max)}</td>
  `;
  body.appendChild(tr);
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
