"""Generate a self-contained index.html with 5Y valuation rank as the headline."""

from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

from fetch import SERIES

ROOT = Path(__file__).parent
DATA = ROOT / "data"

SECTOR_COLORS = {
    20052: "#1b1813",  # S&P 500 - ink
    20517: "#2d6a8a",  # Information Technology - slate blue
    20518: "#7a5ba8",  # Communication Services - violet
    20519: "#a8406a",  # Consumer Discretionary - berry
    20520: "#2f7d5e",  # Financials - pine
    20521: "#c2711d",  # Industrials - amber
    20522: "#3e8a95",  # Utilities - teal
    20523: "#8c4418",  # Energy - umber
    20524: "#4a4a94",  # Real Estate - indigo
    20525: "#718a2c",  # Materials - olive
    20526: "#b8421c",  # Consumer Staples - vermillion
    20527: "#2d7066",  # Health Care - spruce
}

SECTOR_TICKERS = {
    20052: "SPX",
    20517: "IT",
    20518: "COMM",
    20519: "DISC",
    20520: "FIN",
    20521: "IND",
    20522: "UTIL",
    20523: "EGY",
    20524: "RE",
    20525: "MAT",
    20526: "STPL",
    20527: "HLTH",
}


def compute_5y(points):
    latest_date_str, current = points[-1]
    latest = date.fromisoformat(latest_date_str)
    cutoff = latest - timedelta(days=365 * 5)
    window_vals = [
        v for d, v in points
        if v is not None and date.fromisoformat(d) >= cutoff
    ]
    if not window_vals:
        return None
    lower = sum(1 for v in window_vals if v <= current)
    return {
        "rank": lower / len(window_vals) * 100,
        "current": current,
        "min": min(window_vals),
        "median": statistics.median(window_vals),
        "max": max(window_vals),
        "n": len(window_vals),
    }


def assign_rows(rows_asc, row_count=3, min_gap=7.0):
    """Assign each item a row index so labels don't overlap. rows_asc is sorted by rank asc."""
    last = [-999.0] * row_count
    out = []
    for r in rows_asc:
        placed = None
        for ri in range(row_count):
            if r["rank_5y"] - last[ri] >= min_gap:
                placed = ri
                break
        if placed is None:
            placed = min(range(row_count), key=lambda i: last[i])
        last[placed] = r["rank_5y"]
        out.append({**r, "_row": placed})
    return out


def render_strip(rows_with_row):
    parts = []
    for r in rows_with_row:
        pct = r["rank_5y"]
        parts.append(
            f'<div class="pin pin-row-{r["_row"]}" style="left:{pct:.2f}%" '
            f'data-id="{r["id"]}" data-rank="{pct:.0f}">'
            f'<span class="pin-stem" style="background:{r["color"]}"></span>'
            f'<span class="pin-dot" style="background:{r["color"]};color:{r["color"]}"></span>'
            f'<span class="pin-label">{r["ticker"]}'
            f'<span class="pin-pct">{pct:.0f}</span></span>'
            f'</div>'
        )
    return "\n".join(parts)


def render_table(rows):
    parts = []
    for i, r in enumerate(rows, 1):
        pct = r["rank_5y"]
        heat = "hot" if pct >= 75 else "cold" if pct <= 25 else "mid"
        index_cls = " is-index" if r["isIndex"] else ""
        parts.append(f'''
<li class="row heat-{heat}{index_cls}" data-id="{r["id"]}">
  <span class="rank-num">{i:02d}</span>
  <span class="name-col">
    <span class="swatch" style="background:{r["color"]}"></span>
    <span class="name">{r["name"]}</span>
    <span class="ticker">{r["ticker"]}</span>
  </span>
  <span class="val mono">{r["latest"]:.2f}</span>
  <span class="bar-col">
    <span class="bar">
      <span class="bar-fill" style="width:{pct:.2f}%"></span>
      <span class="bar-marker" style="left:{pct:.2f}%"></span>
    </span>
  </span>
  <span class="pct mono">{pct:.0f}</span>
  <span class="range-col mono">
    <span>{r["min_5y"]:.1f}</span><span class="sep">→</span><span>{r["max_5y"]:.1f}</span>
  </span>
</li>'''.strip())
    return "\n".join(parts)


def build() -> Path:
    raw = json.loads((DATA / "raw.json").read_text())
    series_payload = []
    summary_rows = []
    for sid, name in SERIES.items():
        entry = raw.get(f"s:{sid}")
        if not entry:
            continue
        points = entry["series"][0]
        latest_date_str, latest_val = points[-1]
        five = compute_5y(points)
        if not five:
            continue
        series_payload.append({
            "id": sid,
            "name": name,
            "ticker": SECTOR_TICKERS[sid],
            "color": SECTOR_COLORS[sid],
            "points": points,
            "isIndex": sid == 20052,
        })
        summary_rows.append({
            "id": sid,
            "name": name,
            "ticker": SECTOR_TICKERS[sid],
            "color": SECTOR_COLORS[sid],
            "isIndex": sid == 20052,
            "latest_date": latest_date_str,
            "latest": latest_val,
            "rank_5y": five["rank"],
            "min_5y": five["min"],
            "median_5y": five["median"],
            "max_5y": five["max"],
            "n_5y": five["n"],
        })

    summary_rows.sort(key=lambda r: -r["rank_5y"])
    strip_rows = assign_rows(sorted(summary_rows, key=lambda r: r["rank_5y"]))

    latest_date_str = max(r["latest_date"] for r in summary_rows)
    dt = datetime.fromisoformat(latest_date_str)
    latest_label = dt.strftime("%B ") + str(dt.day) + dt.strftime(", %Y")

    payload = json.dumps({"series": series_payload, "summary": summary_rows})

    html = (TEMPLATE
        .replace("__DATA__", payload)
        .replace("__LATEST_ISO__", latest_date_str)
        .replace("__LATEST_LABEL__", latest_label)
        .replace("__STRIP__", render_strip(strip_rows))
        .replace("__TABLE__", render_table(summary_rows)))

    out = ROOT / "index.html"
    out.write_text(html)
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Forward P/E · AlphaLabX1</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght,SOFT,WONK@0,9..144,400..800,0..100,0..1;1,9..144,400..800,0..100,0..1&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  :root {
    --paper:       #f2ecdf;
    --paper-sub:   #ebe3d0;
    --paper-deep:  #e3d9c0;
    --ink:         #1b1813;
    --ink-soft:    #55493b;
    --mute:        #8a7e6d;
    --rule:        #d5c8b1;
    --rule-soft:   #e6dcc6;
    --accent:      #b8421c;
    --cheap:       #2e5d56;
    --font-display: "Fraunces", "Times New Roman", serif;
    --font-body:    "IBM Plex Sans", -apple-system, system-ui, sans-serif;
    --font-mono:    "IBM Plex Mono", ui-monospace, monospace;
  }
  *, *::before, *::after { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: var(--paper);
    color: var(--ink);
    font-family: var(--font-body);
    font-size: 14px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }
  body {
    background-image:
      radial-gradient(circle at 10% 0%, rgba(184,66,28,0.04) 0%, transparent 45%),
      radial-gradient(circle at 90% 100%, rgba(46,93,86,0.04) 0%, transparent 45%);
  }
  .container { max-width: 1240px; margin: 0 auto; padding: 0 40px; }
  .mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }

  /* ─────────────────── Masthead ─────────────────── */
  .masthead { padding: 56px 0 36px; border-bottom: 2px solid var(--ink); position: relative; }
  .masthead::after {
    content: ""; display: block; position: absolute; left: 0; right: 0; bottom: -6px;
    height: 1px; background: var(--ink);
  }
  .kicker {
    font-family: var(--font-body);
    font-size: 10.5px; font-weight: 500;
    letter-spacing: 0.22em; text-transform: uppercase;
    color: var(--accent);
    margin: 0 0 18px;
    display: flex; align-items: center; gap: 12px;
  }
  .kicker::before {
    content: ""; width: 28px; height: 1px; background: currentColor;
    flex: 0 0 auto;
  }
  .kicker::after {
    content: ""; height: 1px; background: var(--rule);
    flex: 1;
  }
  .kicker .edition { color: var(--mute); letter-spacing: 0.2em; }
  .wordmark {
    font-family: var(--font-display);
    font-variation-settings: "opsz" 144, "SOFT" 30, "WONK" 0;
    font-weight: 700;
    font-size: clamp(64px, 11vw, 148px);
    line-height: 0.86;
    letter-spacing: -0.035em;
    color: var(--ink);
    margin: 0;
  }
  .wordmark em {
    font-style: italic;
    font-variation-settings: "opsz" 144, "SOFT" 100, "WONK" 1;
    font-weight: 400;
    color: var(--accent);
    margin-left: 0.08em;
  }
  .standfirst {
    font-family: var(--font-display);
    font-variation-settings: "opsz" 24;
    font-style: italic;
    font-weight: 400;
    font-size: 19px;
    color: var(--ink-soft);
    margin: 24px 0 0;
    max-width: 760px;
    line-height: 1.45;
  }
  .standfirst time {
    font-style: normal;
    font-variation-settings: "opsz" 16;
    font-weight: 500;
    color: var(--ink);
    white-space: nowrap;
  }

  /* ─────────────────── Section framing ─────────────────── */
  .section { padding: 64px 0; border-top: 1px solid var(--rule); }
  .section:first-of-type { border-top: 0; padding-top: 56px; }
  .section-head {
    display: grid;
    grid-template-columns: 64px 1fr auto;
    gap: 28px;
    align-items: baseline;
    margin-bottom: 36px;
  }
  .section-num {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--accent);
    letter-spacing: 0.14em;
    padding-top: 4px;
  }
  .section-num::before { content: "/ "; color: var(--rule); }
  .section-title {
    font-family: var(--font-display);
    font-variation-settings: "opsz" 48;
    font-weight: 600;
    font-size: clamp(24px, 3vw, 34px);
    line-height: 1.1;
    color: var(--ink);
    margin: 0 0 8px;
    letter-spacing: -0.015em;
  }
  .section-title em {
    font-style: italic;
    font-variation-settings: "opsz" 48, "SOFT" 80, "WONK" 1;
    font-weight: 400;
    color: var(--accent);
  }
  .section-lede {
    font-family: var(--font-body);
    font-size: 13.5px;
    color: var(--ink-soft);
    margin: 0;
    max-width: 640px;
    line-height: 1.55;
  }
  .section-lede strong { color: var(--ink); font-weight: 500; }
  .section-meta {
    font-family: var(--font-mono); font-size: 10.5px;
    color: var(--mute); letter-spacing: 0.06em;
    text-align: right;
  }

  /* ─────────────────── Strip (pin chart) ─────────────────── */
  .strip-frame {
    position: relative;
    padding: 64px 56px 20px;
    height: 280px;
    background: var(--paper-sub);
    background-image:
      linear-gradient(to right, transparent 0, transparent calc(50% - 0.5px),
        rgba(27,24,19,0.04) calc(50% - 0.5px), rgba(27,24,19,0.04) calc(50% + 0.5px),
        transparent calc(50% + 0.5px));
    border: 1px solid var(--rule);
  }
  .strip-labels-top {
    position: absolute; top: 20px; left: 56px; right: 56px;
    display: flex; justify-content: space-between;
    font-family: var(--font-body); font-size: 10px;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--mute); font-weight: 500;
  }
  .strip-labels-top .middle { color: var(--ink); }
  .strip-axis {
    position: absolute; top: 58px; left: 56px; right: 56px;
    height: 2px; background: var(--ink);
  }
  .strip-axis::before, .strip-axis::after {
    content: ""; position: absolute; top: -3px;
    width: 2px; height: 8px; background: var(--ink);
  }
  .strip-axis::before { left: 0; }
  .strip-axis::after { right: 0; }
  .strip-ticks { position: absolute; top: 60px; left: 56px; right: 56px; height: 8px; pointer-events: none; }
  .strip-tick {
    position: absolute; top: 0;
    width: 1px; height: 6px; background: var(--ink-soft);
    transform: translateX(-0.5px);
  }
  .strip-tick-label {
    position: absolute; top: 10px;
    font-family: var(--font-mono); font-size: 10px; color: var(--mute);
    transform: translateX(-50%);
    font-variant-numeric: tabular-nums;
  }
  .strip-pins {
    position: absolute; top: 60px; left: 56px; right: 56px; bottom: 20px;
  }
  .pin {
    position: absolute; top: 0;
    transform: translateX(-50%);
    display: flex; flex-direction: column; align-items: center;
    cursor: pointer;
    animation: pinIn 0.6s cubic-bezier(.2,.7,.2,1) both;
  }
  .pin-stem { width: 1px; }
  .pin-row-0 .pin-stem { height: 12px; }
  .pin-row-1 .pin-stem { height: 60px; }
  .pin-row-2 .pin-stem { height: 108px; }
  .pin-dot {
    width: 10px; height: 10px; border-radius: 50%;
    border: 2px solid var(--paper-sub);
    box-shadow: 0 0 0 1.5px currentColor;
    transition: transform .15s ease-out;
  }
  .pin-label {
    margin-top: 7px;
    font-family: var(--font-mono); font-size: 10px; font-weight: 600;
    color: var(--ink); letter-spacing: 0.04em;
    padding: 3px 6px 2px;
    background: var(--paper-sub);
    border-radius: 2px;
    white-space: nowrap;
    display: flex; gap: 6px; align-items: baseline;
    transition: all .15s ease-out;
  }
  .pin-pct {
    font-size: 9px; font-weight: 400; color: var(--mute);
    font-variant-numeric: tabular-nums;
  }
  .pin:hover .pin-dot { transform: scale(1.5); }
  .pin:hover .pin-label { background: var(--ink); color: var(--paper); }
  .pin:hover .pin-pct { color: var(--paper-sub); }
  .strip-frame.highlighting .pin:not(.highlighted) { opacity: 0.22; }
  .strip-frame.highlighting .pin.highlighted .pin-dot { transform: scale(1.7); }

  /* Center band (median zone) */
  .strip-mid-band {
    position: absolute; top: 58px; bottom: 20px;
    left: calc(56px + 45%); width: calc(10% * (100% - 112px) / 100%);
    pointer-events: none;
  }

  /* ─────────────────── Rank table ─────────────────── */
  .rank-table {
    list-style: none; margin: 0; padding: 0;
    border-top: 1.5px solid var(--ink);
    border-bottom: 1.5px solid var(--ink);
  }
  .rank-head, .row {
    display: grid;
    grid-template-columns: 44px 2.4fr 74px 1.3fr 50px 120px;
    gap: 20px;
    align-items: center;
    padding: 14px 8px;
    border-bottom: 1px solid var(--rule-soft);
  }
  .rank-head {
    font-family: var(--font-body); font-size: 10px;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--mute); font-weight: 500;
    padding: 12px 8px;
    border-bottom: 1px solid var(--rule);
  }
  .rank-head > *:last-child { text-align: right; }
  .row {
    font-size: 14px; transition: background .15s;
    cursor: pointer;
    animation: rowIn 0.45s ease-out both;
    position: relative;
  }
  .row:hover { background: var(--paper-sub); }
  .row:last-child { border-bottom: 0; }
  .row.is-index {
    background: linear-gradient(to right, rgba(27,24,19,0.05), rgba(27,24,19,0.01) 60%);
  }
  .row.is-index::before {
    content: ""; position: absolute; left: 0; top: 0; bottom: 0;
    width: 3px; background: var(--ink);
  }
  .rank-num {
    font-family: var(--font-mono); font-size: 12px;
    color: var(--mute); font-weight: 500;
    font-variant-numeric: tabular-nums;
  }
  .name-col {
    display: flex; align-items: center; gap: 12px; min-width: 0;
  }
  .swatch {
    width: 8px; height: 16px; border-radius: 1px; flex: 0 0 8px;
  }
  .name {
    font-family: var(--font-display);
    font-variation-settings: "opsz" 20;
    font-weight: 500;
    font-size: 16.5px;
    color: var(--ink);
    letter-spacing: -0.005em;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .ticker {
    font-family: var(--font-mono);
    font-size: 9.5px;
    color: var(--mute);
    letter-spacing: 0.1em;
    padding: 2px 5px 1px;
    border: 1px solid var(--rule);
    border-radius: 2px;
  }
  .val {
    font-size: 17px; font-weight: 500;
    color: var(--ink);
  }
  .bar-col { position: relative; padding: 0 4px; display: block; }
  .bar {
    position: relative;
    display: block;
    height: 3px;
    background: var(--rule);
    width: 100%;
  }
  .bar-fill {
    position: absolute; left: 0; top: 0;
    height: 100%;
    background: var(--ink);
  }
  .bar-marker {
    position: absolute; top: 50%;
    width: 11px; height: 11px; border-radius: 50%;
    background: var(--ink);
    transform: translate(-50%, -50%);
    border: 2px solid var(--paper);
    box-shadow: 0 0 0 1.5px var(--ink);
  }
  .row.heat-hot .bar-fill, .row.heat-hot .bar-marker { background: var(--accent); }
  .row.heat-hot .bar-marker { box-shadow: 0 0 0 1.5px var(--accent); }
  .row.heat-cold .bar-fill, .row.heat-cold .bar-marker { background: var(--cheap); }
  .row.heat-cold .bar-marker { box-shadow: 0 0 0 1.5px var(--cheap); }
  .pct {
    font-size: 20px; font-weight: 600;
    color: var(--ink);
    text-align: right;
    letter-spacing: -0.01em;
  }
  .row.heat-hot .pct { color: var(--accent); }
  .row.heat-cold .pct { color: var(--cheap); }
  .range-col {
    font-size: 11.5px; color: var(--mute);
    display: flex; gap: 6px; justify-content: flex-end;
  }
  .range-col .sep { color: var(--rule); }

  /* ─────────────────── Chart ─────────────────── */
  .chart-controls {
    display: flex; gap: 8px; margin-bottom: 18px;
    align-items: baseline; flex-wrap: wrap;
  }
  .chart-controls button {
    border: 1px solid var(--rule);
    background: transparent;
    color: var(--ink-soft);
    padding: 7px 14px 6px;
    font-family: var(--font-mono); font-size: 10.5px; font-weight: 500;
    letter-spacing: 0.14em; text-transform: uppercase;
    cursor: pointer;
    transition: all .12s ease-out;
  }
  .chart-controls button:hover { border-color: var(--ink); color: var(--ink); background: var(--paper-sub); }
  .chart-controls button.active {
    background: var(--ink); color: var(--paper); border-color: var(--ink);
  }
  .chart-controls .spacer { flex: 1; }
  .chart-controls .meta {
    font-family: var(--font-mono); font-size: 10px;
    color: var(--mute); letter-spacing: 0.08em;
  }
  .chart-wrap {
    background: var(--paper);
    border: 1px solid var(--rule);
    padding: 14px 8px 6px;
  }
  #chart { width: 100%; height: 560px; }

  /* ─────────────────── Footer ─────────────────── */
  footer {
    margin-top: 48px;
    padding: 28px 0 60px;
    border-top: 1.5px solid var(--ink);
  }
  footer .inner {
    display: flex; justify-content: space-between; align-items: baseline;
    font-family: var(--font-mono); font-size: 10.5px;
    color: var(--mute); letter-spacing: 0.08em;
    flex-wrap: wrap; gap: 10px;
  }
  footer .inner .left { display: flex; gap: 14px; flex-wrap: wrap; }
  footer .dot { color: var(--rule); }
  footer strong { color: var(--ink); font-weight: 600; letter-spacing: 0.14em; }

  /* ─────────────────── Animations ─────────────────── */
  @keyframes rise {
    from { opacity: 0; transform: translateY(14px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @keyframes pinIn {
    from { opacity: 0; transform: translateX(-50%) translateY(-8px); }
    to   { opacity: 1; transform: translateX(-50%) translateY(0); }
  }
  @keyframes rowIn {
    from { opacity: 0; transform: translateX(-6px); }
    to   { opacity: 1; transform: translateX(0); }
  }
  .kicker       { animation: rise .55s ease-out .00s both; }
  .wordmark     { animation: rise .80s cubic-bezier(.2,.6,.2,1) .10s both; }
  .standfirst   { animation: rise .60s ease-out .35s both; }

  @media (max-width: 860px) {
    .container { padding: 0 20px; }
    .section { padding: 40px 0; }
    .section-head { grid-template-columns: 1fr; gap: 4px; }
    .section-meta { text-align: left; }
    .rank-head, .row { grid-template-columns: 30px 1.5fr 60px 1fr 44px; gap: 12px; padding: 12px 6px; }
    .rank-head > *:nth-child(6), .row > *:nth-child(6) { display: none; }
    .name { font-size: 14px; }
    .val { font-size: 14px; }
    .pct { font-size: 16px; }
    .strip-frame { padding: 56px 30px 16px; height: 240px; }
    .strip-labels-top, .strip-axis, .strip-ticks, .strip-pins { left: 30px; right: 30px; }
  }
</style>
</head>
<body>

<header class="masthead">
  <div class="container">
    <p class="kicker">AlphaLabX1 · internal research <span class="edition">Vol. I</span></p>
    <h1 class="wordmark">Forward <em>P/E</em></h1>
    <p class="standfirst">The S&amp;P 500 and its eleven sectors, ranked by where each sits against its own trailing five years of daily valuation. Updated <time>__LATEST_LABEL__</time>.</p>
  </div>
</header>

<main>
  <section class="section">
    <div class="container">
      <div class="section-head">
        <span class="section-num">01</span>
        <div>
          <h2 class="section-title">Where everyone <em>stands today</em></h2>
          <p class="section-lede">Each marker is a sector's current forward P/E placed as a percentile of its own trailing five years. <strong>Right is expensive.</strong> A reading of 50 means the sector is trading at its own five-year median.</p>
        </div>
        <div class="section-meta">5Y window<br>daily observations</div>
      </div>
      <div class="strip-frame" id="strip">
        <div class="strip-labels-top">
          <span>← cheap vs own 5Y</span>
          <span class="middle">median</span>
          <span>expensive vs own 5Y →</span>
        </div>
        <div class="strip-axis"></div>
        <div class="strip-ticks">
          <span class="strip-tick" style="left:0%"></span><span class="strip-tick-label" style="left:0%">0</span>
          <span class="strip-tick" style="left:25%"></span><span class="strip-tick-label" style="left:25%">25</span>
          <span class="strip-tick" style="left:50%"></span><span class="strip-tick-label" style="left:50%">50</span>
          <span class="strip-tick" style="left:75%"></span><span class="strip-tick-label" style="left:75%">75</span>
          <span class="strip-tick" style="left:100%"></span><span class="strip-tick-label" style="left:100%">100</span>
        </div>
        <div class="strip-pins">__STRIP__</div>
      </div>
    </div>
  </section>

  <section class="section">
    <div class="container">
      <div class="section-head">
        <span class="section-num">02</span>
        <div>
          <h2 class="section-title">Five-year <em>rank</em></h2>
          <p class="section-lede">Sectors ordered from richest to cheapest relative to their own history. <strong>Click any row</strong> to isolate it on the chart below.</p>
        </div>
        <div class="section-meta">sorted by 5Y percentile</div>
      </div>
      <ul class="rank-table">
        <li class="rank-head">
          <span></span>
          <span>Sector</span>
          <span>P/E</span>
          <span>5Y percentile</span>
          <span style="text-align:right">pctl</span>
          <span style="text-align:right">5Y range</span>
        </li>
        __TABLE__
      </ul>
    </div>
  </section>

  <section class="section">
    <div class="container">
      <div class="section-head">
        <span class="section-num">03</span>
        <div>
          <h2 class="section-title">Historical <em>path</em></h2>
          <p class="section-lede">Daily twelve-month forward P/E. The Y axis auto-scales to whichever window and sectors are visible — outlier spikes (forward P/E &gt; 80) are filtered from the scale.</p>
        </div>
        <div class="section-meta">since 1999</div>
      </div>
      <div class="chart-controls">
        <button data-range="all">All</button>
        <button data-range="10y">10Y</button>
        <button data-range="5y" class="active">5Y</button>
        <button data-range="3y">3Y</button>
        <button data-range="1y">1Y</button>
        <button data-range="ytd">YTD</button>
        <span class="spacer"></span>
        <button id="only-index">Index</button>
        <button id="only-sectors">Sectors</button>
        <button id="show-all">Reset</button>
      </div>
      <div class="chart-wrap">
        <div id="chart"></div>
      </div>
    </div>
  </section>
</main>

<footer>
  <div class="container">
    <div class="inner">
      <div class="left">
        <span><strong>AlphaLabX1</strong> internal</span>
        <span class="dot">·</span>
        <span>Data · MacroMicro</span>
        <span class="dot">·</span>
        <span>Chart · Plotly</span>
      </div>
      <span>as of __LATEST_ISO__ · 12 series · 5-year window</span>
    </div>
  </div>
</footer>

<script>
const DATA = __DATA__;
const LATEST = "__LATEST_ISO__";

DATA.series.forEach(s => { s._t = s.points.map(p => Date.parse(p[0])); });

const traces = DATA.series.map(s => ({
  x: s.points.map(p => p[0]),
  y: s.points.map(p => p[1]),
  type: "scattergl",
  mode: "lines",
  name: s.name,
  line: { color: s.color, width: s.isIndex ? 2.6 : 1.4 },
  hovertemplate: "<b>" + s.name + "</b>  %{y:.2f}<extra></extra>",
  visible: true,
  meta: s.id,
}));

const layout = {
  margin: { l: 56, r: 20, t: 10, b: 44 },
  hovermode: "x unified",
  hoverlabel: {
    font: { family: '"IBM Plex Mono", monospace', size: 11, color: "#f2ecdf" },
    bgcolor: "#1b1813",
    bordercolor: "#1b1813",
  },
  xaxis: {
    showgrid: false,
    linecolor: "#d5c8b1",
    tickcolor: "#8a7e6d",
    tickfont: { family: '"IBM Plex Mono", monospace', size: 10, color: "#55493b" },
    type: "date",
  },
  yaxis: {
    gridcolor: "#e6dcc6",
    zeroline: false,
    tickfont: { family: '"IBM Plex Mono", monospace', size: 10, color: "#55493b" },
    tickcolor: "#8a7e6d",
    title: { text: "forward P/E", font: { family: '"IBM Plex Sans", sans-serif', size: 11, color: "#8a7e6d" }, standoff: 14 },
  },
  legend: {
    orientation: "h", y: -0.18,
    font: { family: '"IBM Plex Mono", monospace', size: 10, color: "#1b1813" },
  },
  paper_bgcolor: "#f2ecdf",
  plot_bgcolor: "#f2ecdf",
  font: { family: '"IBM Plex Sans", sans-serif', size: 11, color: "#1b1813" },
};

const config = {
  displaylogo: false, responsive: true,
  modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"],
};

Plotly.newPlot("chart", traces, layout, config).then(() => applyRange("5y"));

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

let _skipRelayout = false;
function applyRange(key) {
  const now = new Date(LATEST);
  let start;
  if (key === "all") start = new Date("1999-01-01");
  else if (key === "ytd") start = new Date(now.getFullYear(), 0, 1);
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

document.getElementById("chart").on("plotly_relayout", () => {
  if (_skipRelayout) return;
  rescaleY();
});

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

function setVisible(fn) {
  const vis = DATA.series.map(s => fn(s) ? true : "legendonly");
  Plotly.restyle("chart", { visible: vis }).then(rescaleY);
}
document.getElementById("only-index").addEventListener("click", () => setVisible(s => s.isIndex));
document.getElementById("only-sectors").addEventListener("click", () => setVisible(s => !s.isIndex));
document.getElementById("show-all").addEventListener("click", () => setVisible(() => true));

// Stagger pin entrance
document.querySelectorAll(".pin").forEach((pin, i) => {
  pin.style.animationDelay = (0.3 + i * 0.04) + "s";
  const id = parseInt(pin.dataset.id, 10);
  pin.addEventListener("mouseenter", () => soloViaStrip(id));
  pin.addEventListener("mouseleave", () => unsoloViaStrip());
  pin.addEventListener("click", () => stickSolo(id));
});

// Stagger row entrance + click
document.querySelectorAll(".row:not(.rank-head)").forEach((row, i) => {
  row.style.animationDelay = (0.1 + i * 0.03) + "s";
  const id = parseInt(row.dataset.id, 10);
  row.addEventListener("click", () => stickSolo(id));
});

function soloViaStrip(id) {
  const stripEl = document.getElementById("strip");
  stripEl.classList.add("highlighting");
  document.querySelectorAll(".pin").forEach(p => {
    p.classList.toggle("highlighted", parseInt(p.dataset.id, 10) === id);
  });
  const op = DATA.series.map(s => s.id === id ? 1 : 0.12);
  Plotly.restyle("chart", { opacity: op });
}
function unsoloViaStrip() {
  document.getElementById("strip").classList.remove("highlighting");
  document.querySelectorAll(".pin").forEach(p => p.classList.remove("highlighted"));
  Plotly.restyle("chart", { opacity: DATA.series.map(() => 1) });
}
function stickSolo(id) {
  const idx = DATA.series.findIndex(s => s.id === id);
  if (idx < 0) return;
  const vis = DATA.series.map((_, i) => i === idx ? true : "legendonly");
  Plotly.restyle("chart", { visible: vis, opacity: DATA.series.map(() => 1) }).then(rescaleY);
  document.getElementById("chart").scrollIntoView({ behavior: "smooth", block: "center" });
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
