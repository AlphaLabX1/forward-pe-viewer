"""Generate the self-contained dashboard: forward + trailing P/E (with lens
toggle), plus a Fear & Greed gauge and an F&G / SPX dual-axis chart."""

from __future__ import annotations

import csv
import json
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

from fetch import SERIES

ROOT = Path(__file__).parent
DATA = ROOT / "data"
TRAILING_DIR = DATA / "trailing"

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

SECTOR_INFO = {
    20052: {
        "def": "Market-cap weighted index of 500 leading U.S. large-caps across all sectors.",
        "holdings": [("NVDA", "NVIDIA"), ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("GOOGL", "Alphabet"), ("AMZN", "Amazon")],
    },
    20517: {
        "def": "Software, hardware, semiconductors, and IT services.",
        "holdings": [("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "NVIDIA"), ("AVGO", "Broadcom"), ("ORCL", "Oracle")],
    },
    20518: {
        "def": "Interactive media, entertainment, and telecom services.",
        "holdings": [("GOOGL", "Alphabet"), ("META", "Meta"), ("NFLX", "Netflix"), ("VZ", "Verizon"), ("DIS", "Disney")],
    },
    20519: {
        "def": "Autos, retail, apparel, hotels & leisure — cyclically sensitive.",
        "holdings": [("AMZN", "Amazon"), ("TSLA", "Tesla"), ("HD", "Home Depot"), ("MCD", "McDonald's"), ("BKNG", "Booking")],
    },
    20520: {
        "def": "Banks, insurance, capital markets, diversified financials.",
        "holdings": [("BRK.B", "Berkshire Hathaway"), ("JPM", "JPMorgan Chase"), ("V", "Visa"), ("MA", "Mastercard"), ("BAC", "Bank of America")],
    },
    20521: {
        "def": "Aerospace & defense, machinery, transports, professional services.",
        "holdings": [("GE", "GE Aerospace"), ("RTX", "RTX"), ("CAT", "Caterpillar"), ("HON", "Honeywell"), ("UBER", "Uber")],
    },
    20522: {
        "def": "Electric, gas, water, and multi-utilities. Rate-sensitive defensives.",
        "holdings": [("NEE", "NextEra Energy"), ("SO", "Southern Co."), ("DUK", "Duke Energy"), ("CEG", "Constellation"), ("AEP", "American Electric")],
    },
    20523: {
        "def": "Oil & gas exploration, production, refining, and equipment.",
        "holdings": [("XOM", "ExxonMobil"), ("CVX", "Chevron"), ("COP", "ConocoPhillips"), ("EOG", "EOG Resources"), ("SLB", "Schlumberger")],
    },
    20524: {
        "def": "Equity REITs and real-estate management & development.",
        "holdings": [("PLD", "Prologis"), ("AMT", "American Tower"), ("WELL", "Welltower"), ("EQIX", "Equinix"), ("SPG", "Simon Property")],
    },
    20525: {
        "def": "Chemicals, metals & mining, construction materials, paper & forest.",
        "holdings": [("LIN", "Linde"), ("SHW", "Sherwin-Williams"), ("ECL", "Ecolab"), ("APD", "Air Products"), ("NEM", "Newmont")],
    },
    20526: {
        "def": "Food, beverage, household & personal-care — defensive staples.",
        "holdings": [("WMT", "Walmart"), ("COST", "Costco"), ("PG", "Procter & Gamble"), ("KO", "Coca-Cola"), ("PEP", "PepsiCo")],
    },
    20527: {
        "def": "Pharmaceuticals, biotech, medical devices, and health services.",
        "holdings": [("LLY", "Eli Lilly"), ("UNH", "UnitedHealth"), ("JNJ", "Johnson & Johnson"), ("MRK", "Merck"), ("ABBV", "AbbVie")],
    },
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


def _holdings_html(sid):
    info = SECTOR_INFO.get(sid, {})
    items = "".join(
        f'<li><span class="tip-tk">{tk}</span><span class="tip-nm">{nm}</span></li>'
        for tk, nm in info.get("holdings", [])
    )
    return info.get("def", ""), items


def render_strip(rows_with_row):
    parts = []
    for r in rows_with_row:
        pct = r["rank_5y"]
        definition, holdings_html = _holdings_html(r["id"])
        parts.append(
            f'<div class="pin pin-row-{r["_row"]}" style="left:{pct:.2f}%" '
            f'data-id="{r["id"]}" data-rank="{pct:.0f}">'
            f'<span class="pin-stem" style="background:{r["color"]}"></span>'
            f'<span class="pin-dot" style="background:{r["color"]};color:{r["color"]}"></span>'
            f'<span class="pin-label">{r["ticker"]}'
            f'<span class="pin-pct">{pct:.0f}</span></span>'
            f'<div class="pin-tip">'
            f'<div class="pin-tip-name">{r["name"]}</div>'
            f'<div class="pin-tip-def">{definition}</div>'
            f'<div class="pin-tip-label">Largest constituents</div>'
            f'<ul class="tip-holdings">{holdings_html}</ul>'
            f'</div>'
            f'</div>'
        )
    return "\n".join(parts)


def render_table(rows):
    parts = []
    for i, r in enumerate(rows, 1):
        pct = r["rank_5y"]
        heat = "hot" if pct >= 75 else "cold" if pct <= 25 else "mid"
        index_cls = " is-index" if r["isIndex"] else ""
        definition, holdings_html = _holdings_html(r["id"])
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
  <div class="tip">
    <div class="tip-def">{definition}</div>
    <div class="tip-label">Largest constituents</div>
    <ul class="tip-holdings">{holdings_html}</ul>
  </div>
</li>'''.strip())
    return "\n".join(parts)


def _load_csv_points(path: Path) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    if not path.exists():
        return out
    with path.open() as f:
        r = csv.reader(f)
        next(r, None)  # header
        for row in r:
            if len(row) < 2 or not row[1]:
                continue
            try:
                out.append((row[0], float(row[1])))
            except ValueError:
                continue
    return out


def build_family_payload(points_by_sid: dict[int, list[tuple[str, float]]]):
    """Given raw points per series ID, produce the series / summary / strip
    HTML fragments for one P/E family (forward or trailing)."""
    series_payload: list[dict] = []
    summary_rows: list[dict] = []
    for sid, name in SERIES.items():
        points = points_by_sid.get(sid) or []
        if not points:
            continue
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
    latest_date_str = max((r["latest_date"] for r in summary_rows), default="")
    return {
        "series": series_payload,
        "summary": summary_rows,
        "strip_html": render_strip(strip_rows),
        "table_html": render_table(summary_rows),
        "latest_date": latest_date_str,
    }


def _nearest_on_or_before(points: list[tuple[str, float]], target: date):
    target_str = target.isoformat()
    # Points are chronological; find last date <= target.
    for d_str, v in reversed(points):
        if d_str <= target_str:
            return d_str, v
    return None


def gauge_payload(fg_points: list[tuple[str, float]]):
    if not fg_points:
        return None
    latest_d, latest_v = fg_points[-1]
    latest_date = date.fromisoformat(latest_d)
    markers = []
    for label, delta_days in [("1W", 7), ("1M", 30), ("3M", 91), ("1Y", 365)]:
        hit = _nearest_on_or_before(fg_points, latest_date - timedelta(days=delta_days))
        if hit:
            markers.append({"label": label, "date": hit[0], "value": round(hit[1], 1)})
    return {
        "current": {"date": latest_d, "value": round(latest_v, 1)},
        "markers": markers,
    }


def build() -> Path:
    # Forward P/E: already in data/raw.json (MacroMicro batch).
    raw = json.loads((DATA / "raw.json").read_text())
    forward_points: dict[int, list] = {}
    for sid in SERIES:
        entry = raw.get(f"s:{sid}")
        if entry:
            forward_points[sid] = entry["series"][0]
    forward = build_family_payload(forward_points)

    # Trailing P/E: from data/trailing/*.csv written by fetch_trailing.py.
    trailing_points: dict[int, list] = {}
    if TRAILING_DIR.exists():
        for sid, name in SERIES.items():
            slug = name.lower().replace("&", "and").replace(" ", "_")
            p = TRAILING_DIR / f"{sid}_{slug}.csv"
            pts = _load_csv_points(p)
            if pts:
                trailing_points[sid] = pts
    trailing = build_family_payload(trailing_points) if trailing_points else None

    # Sentiment: Fear & Greed + SPX price.
    fg_points = _load_csv_points(DATA / "fear_greed.csv")
    spx_points = _load_csv_points(DATA / "spx_price.csv")
    gauge = gauge_payload(fg_points)

    # Overall page date = max across families.
    latest_candidates = [forward["latest_date"]]
    if trailing:
        latest_candidates.append(trailing["latest_date"])
    if fg_points:
        latest_candidates.append(fg_points[-1][0])
    latest_date_str = max(latest_candidates)
    dt = datetime.fromisoformat(latest_date_str)
    latest_label = dt.strftime("%B ") + str(dt.day) + dt.strftime(", %Y")

    payload = json.dumps({
        "forward": {"series": forward["series"], "summary": forward["summary"]},
        "trailing": (
            {"series": trailing["series"], "summary": trailing["summary"]}
            if trailing else None
        ),
        "fg": {
            "points": fg_points,
            "gauge": gauge,
        },
        "spx": {"points": spx_points},
    })

    html = (TEMPLATE
        .replace("__DATA__", payload)
        .replace("__LATEST_ISO__", latest_date_str)
        .replace("__LATEST_LABEL__", latest_label)
        .replace("__STRIP_FORWARD__", forward["strip_html"])
        .replace("__STRIP_TRAILING__", (trailing or {}).get("strip_html", "") if trailing else "")
        .replace("__TABLE_FORWARD__", forward["table_html"])
        .replace("__TABLE_TRAILING__", (trailing or {}).get("table_html", "") if trailing else "")
        .replace("__GAUGE__", render_gauge(gauge)))

    out = ROOT / "index.html"
    out.write_text(html)
    return out


def render_gauge(g):
    if not g:
        return "<p style='color:var(--mute)'>Fear &amp; Greed data unavailable.</p>"
    cur = g["current"]["value"]
    # Main pointer position (0-100 → percentage along bar)
    pointer_pos = max(0, min(100, cur))
    marker_html = "".join(
        f'<div class="gauge-marker" style="left:{max(0,min(100,m["value"])):.2f}%" '
        f'data-label="{m["label"]}" data-value="{m["value"]}" data-date="{m["date"]}">'
        f'<span class="gm-arrow">▽</span>'
        f'<span class="gm-label">{m["label"]}<span class="gm-val">{m["value"]:.0f}</span></span>'
        f'</div>'
        for m in g["markers"]
    )
    return f'''
<div class="gauge-frame">
  <div class="gauge-track">
    <div class="gauge-zone gz-xfear"  style="left:0%; width:25%"></div>
    <div class="gauge-zone gz-fear"   style="left:25%; width:20%"></div>
    <div class="gauge-zone gz-neut"   style="left:45%; width:11%"></div>
    <div class="gauge-zone gz-greed"  style="left:56%; width:20%"></div>
    <div class="gauge-zone gz-xgreed" style="left:76%; width:24%"></div>
  </div>
  <div class="gauge-axis">
    <span class="ga-tick" style="left:0%"></span>
    <span class="ga-tick" style="left:25%"></span>
    <span class="ga-tick" style="left:45%"></span>
    <span class="ga-tick" style="left:56%"></span>
    <span class="ga-tick" style="left:76%"></span>
    <span class="ga-tick" style="left:100%"></span>
  </div>
  <div class="gauge-labels">
    <span style="left:12.5%">extreme fear</span>
    <span style="left:35%">fear</span>
    <span style="left:50.5%">neutral</span>
    <span style="left:66%">greed</span>
    <span style="left:88%">extreme greed</span>
  </div>
  <div class="gauge-markers">{marker_html}</div>
  <div class="gauge-pointer" style="left:{pointer_pos:.2f}%">
    <span class="gp-arrow">▼</span>
    <span class="gp-value">{cur:.0f}</span>
    <span class="gp-caption">today · {g["current"]["date"]}</span>
  </div>
</div>
'''.strip()


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Valuation &amp; Mood · AlphaLabX1</title>
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
  .kicker::before { content: ""; width: 28px; height: 1px; background: currentColor; flex: 0 0 auto; }
  .kicker::after { content: ""; height: 1px; background: var(--rule); flex: 1; }
  .kicker .edition { color: var(--mute); letter-spacing: 0.2em; }
  .wordmark {
    font-family: var(--font-display);
    font-variation-settings: "opsz" 144, "SOFT" 30, "WONK" 0;
    font-weight: 700;
    font-size: clamp(60px, 10vw, 136px);
    line-height: 0.88;
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

  /* ─────────────────── Lens toggle ─────────────────── */
  .lens-row { margin: 28px 0 0; display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
  .lens-label {
    font-family: var(--font-mono); font-size: 10px;
    color: var(--mute); letter-spacing: 0.18em; text-transform: uppercase;
  }
  .lens {
    display: inline-flex;
    border: 1px solid var(--ink);
    background: var(--paper);
  }
  .lens button {
    border: 0; background: transparent; cursor: pointer;
    padding: 9px 18px 8px;
    font-family: var(--font-mono); font-size: 10.5px; font-weight: 500;
    letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--ink-soft);
    transition: color .12s ease-out, background .12s ease-out;
    position: relative;
  }
  .lens button + button { border-left: 1px solid var(--ink); }
  .lens button:hover { background: var(--paper-sub); color: var(--ink); }
  .lens button.active { background: var(--ink); color: var(--paper); }
  .lens-note {
    font-family: var(--font-display); font-style: italic;
    font-variation-settings: "opsz" 14;
    font-size: 13px; color: var(--ink-soft);
  }

  /* Show/hide views based on body data-lens */
  .view-trailing { display: none; }
  body[data-lens="trailing"] .view-forward { display: none; }
  body[data-lens="trailing"] .view-trailing { display: block; }

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
  .section-meta .lens-echo {
    display: inline-block;
    padding: 3px 8px 2px;
    border: 1px solid var(--rule);
    color: var(--ink);
    letter-spacing: 0.14em;
  }
  body[data-lens="forward"]  .section-meta .lens-echo::before { content: "forward · daily"; }
  body[data-lens="trailing"] .section-meta .lens-echo::before { content: "trailing · monthly"; }

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
  .strip-axis { position: absolute; top: 58px; left: 56px; right: 56px; height: 2px; background: var(--ink); }
  .strip-axis::before, .strip-axis::after {
    content: ""; position: absolute; top: -3px;
    width: 2px; height: 8px; background: var(--ink);
  }
  .strip-axis::before { left: 0; }
  .strip-axis::after { right: 0; }
  .strip-ticks { position: absolute; top: 60px; left: 56px; right: 56px; height: 8px; pointer-events: none; }
  .strip-tick { position: absolute; top: 0; width: 1px; height: 6px; background: var(--ink-soft); transform: translateX(-0.5px); }
  .strip-tick-label {
    position: absolute; top: 10px;
    font-family: var(--font-mono); font-size: 10px; color: var(--mute);
    transform: translateX(-50%);
    font-variant-numeric: tabular-nums;
  }
  .strip-pins { position: absolute; top: 60px; left: 56px; right: 56px; bottom: 20px; }
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
  .pin-pct { font-size: 9px; font-weight: 400; color: var(--mute); font-variant-numeric: tabular-nums; }
  .pin:hover .pin-dot { transform: scale(1.5); }
  .pin:hover .pin-label { background: var(--ink); color: var(--paper); }
  .pin:hover .pin-pct { color: var(--paper-sub); }
  .strip-frame.highlighting .pin:not(.highlighted) { opacity: 0.22; }
  .strip-frame.highlighting .pin.highlighted .pin-dot { transform: scale(1.7); }

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
    z-index: 1;
  }
  .row:hover { background: var(--paper-sub); z-index: 200; }
  .row:last-child { border-bottom: 0; }
  .row.is-index { background: linear-gradient(to right, rgba(27,24,19,0.05), rgba(27,24,19,0.01) 60%); }
  .row.is-index::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--ink); }
  .rank-num { font-family: var(--font-mono); font-size: 12px; color: var(--mute); font-weight: 500; font-variant-numeric: tabular-nums; }
  .name-col { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .swatch { width: 8px; height: 16px; border-radius: 1px; flex: 0 0 8px; }
  .name {
    font-family: var(--font-display);
    font-variation-settings: "opsz" 20;
    font-weight: 500; font-size: 16.5px;
    color: var(--ink);
    letter-spacing: -0.005em;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .ticker {
    font-family: var(--font-mono); font-size: 9.5px;
    color: var(--mute); letter-spacing: 0.1em;
    padding: 2px 5px 1px;
    border: 1px solid var(--rule); border-radius: 2px;
  }
  .val { font-size: 17px; font-weight: 500; color: var(--ink); }
  .bar-col { position: relative; padding: 0 4px; display: block; }
  .bar { position: relative; display: block; height: 3px; background: var(--rule); width: 100%; }
  .bar-fill { position: absolute; left: 0; top: 0; height: 100%; background: var(--ink); }
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
  .pct { font-size: 20px; font-weight: 600; color: var(--ink); text-align: right; letter-spacing: -0.01em; }
  .row.heat-hot .pct { color: var(--accent); }
  .row.heat-cold .pct { color: var(--cheap); }
  .range-col { font-size: 11.5px; color: var(--mute); display: flex; gap: 6px; justify-content: flex-end; }
  .range-col .sep { color: var(--rule); }

  /* ─────────────────── Row / pin tooltips ─────────────────── */
  .rank-table { overflow: visible; }
  .row .tip {
    position: absolute;
    top: calc(100% - 2px); right: 0;
    width: 380px; max-width: calc(100vw - 80px);
    z-index: 50;
    background: var(--ink); color: var(--paper);
    padding: 18px 22px 20px;
    opacity: 0; pointer-events: none;
    transform: translateY(-4px);
    transition: opacity .16s ease-out, transform .16s ease-out;
    box-shadow: 0 14px 36px rgba(27,24,19,0.25), 0 0 0 1px var(--ink);
    text-align: left;
  }
  .row:hover .tip { opacity: 1; transform: translateY(0); pointer-events: auto; }
  .row:nth-last-child(-n+4) .tip { top: auto; bottom: calc(100% - 2px); transform: translateY(4px); }
  .row:nth-last-child(-n+4):hover .tip { transform: translateY(0); }
  .tip-def {
    font-family: var(--font-display);
    font-variation-settings: "opsz" 18;
    font-style: italic; font-weight: 400;
    font-size: 14.5px; line-height: 1.45;
    color: var(--paper);
    margin: 0 0 14px; padding-bottom: 14px;
    border-bottom: 1px solid rgba(242,236,223,0.18);
  }
  .tip-label {
    font-family: var(--font-mono); font-size: 9.5px;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: rgba(242,236,223,0.55);
    margin: 0 0 8px;
  }
  .tip-holdings { list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: 1fr; gap: 5px; }
  .tip-holdings li { display: grid; grid-template-columns: 68px 1fr; gap: 14px; align-items: baseline; }
  .tip-tk { font-family: var(--font-mono); font-weight: 500; font-size: 11px; letter-spacing: 0.08em; color: #e8955a; }
  .tip-nm { font-family: var(--font-body); font-size: 13px; font-weight: 400; color: var(--paper); }

  .pin-tip {
    position: absolute; top: auto; bottom: calc(100% + 18px); left: 50%;
    transform: translate(-50%, 6px);
    width: 260px; max-width: calc(100vw - 60px);
    background: var(--ink); color: var(--paper);
    padding: 14px 16px 16px;
    opacity: 0; pointer-events: none;
    transition: opacity .16s ease-out, transform .16s ease-out;
    box-shadow: 0 14px 36px rgba(27,24,19,0.25), 0 0 0 1px var(--ink);
    z-index: 100; text-align: left;
  }
  .pin:hover .pin-tip { opacity: 1; transform: translate(-50%, 0); }
  .pin-row-2 .pin-tip { bottom: auto; top: calc(100% + 10px); transform: translate(-50%, -6px); }
  .pin-row-2:hover .pin-tip { transform: translate(-50%, 0); }
  .pin-tip-name {
    font-family: var(--font-display);
    font-variation-settings: "opsz" 24;
    font-weight: 600; font-size: 15px; letter-spacing: -0.005em;
    margin: 0 0 6px;
  }
  .pin-tip-def {
    font-family: var(--font-display);
    font-variation-settings: "opsz" 14;
    font-style: italic; font-size: 12.5px; line-height: 1.4;
    color: rgba(242,236,223,0.82);
    margin: 0 0 12px; padding-bottom: 10px;
    border-bottom: 1px solid rgba(242,236,223,0.18);
  }
  .pin-tip-label {
    font-family: var(--font-mono); font-size: 9px;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: rgba(242,236,223,0.5);
    margin: 0 0 6px;
  }
  .pin-tip .tip-holdings li { grid-template-columns: 54px 1fr; gap: 10px; }
  .pin-tip .tip-tk { font-size: 10px; }
  .pin-tip .tip-nm { font-size: 12px; }

  /* ─────────────────── Chart ─────────────────── */
  .chart-controls { display: flex; gap: 8px; margin-bottom: 18px; align-items: baseline; flex-wrap: wrap; }
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
  .chart-controls button.active { background: var(--ink); color: var(--paper); border-color: var(--ink); }
  .chart-controls .spacer { flex: 1; }
  .chart-controls .meta { font-family: var(--font-mono); font-size: 10px; color: var(--mute); letter-spacing: 0.08em; }
  .chart-wrap { background: var(--paper); border: 1px solid var(--rule); padding: 14px 8px 6px; }
  #chart, #mood-chart { width: 100%; height: 560px; }

  /* ─────────────────── Gauge (Fear & Greed) ─────────────────── */
  .gauge-frame {
    position: relative;
    padding: 96px 56px 96px;
    background: var(--paper-sub);
    border: 1px solid var(--rule);
    height: 280px;
  }
  .gauge-track {
    position: absolute;
    top: 96px; left: 56px; right: 56px;
    height: 16px;
    display: block;
    border: 1px solid var(--ink);
    overflow: hidden;
  }
  .gauge-zone {
    position: absolute; top: 0; bottom: 0;
  }
  .gz-xfear  { background: var(--cheap); }
  .gz-fear   { background: var(--cheap); opacity: 0.45; }
  .gz-neut   { background: var(--paper-deep); }
  .gz-greed  { background: var(--accent); opacity: 0.45; }
  .gz-xgreed { background: var(--accent); }

  .gauge-axis {
    position: absolute;
    top: 113px; left: 56px; right: 56px; height: 8px;
    pointer-events: none;
  }
  .ga-tick {
    position: absolute; top: 0;
    width: 1px; height: 6px; background: var(--ink-soft);
    transform: translateX(-0.5px);
  }
  .gauge-labels {
    position: absolute;
    top: 130px; left: 56px; right: 56px;
    font-family: var(--font-mono); font-size: 9.5px;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--mute);
    pointer-events: none;
  }
  .gauge-labels span {
    position: absolute;
    transform: translateX(-50%);
    white-space: nowrap;
  }
  .gauge-pointer {
    position: absolute;
    top: 40px;
    transform: translateX(-50%);
    display: flex; flex-direction: column; align-items: center;
    animation: gaugeDrop 0.8s cubic-bezier(.2,.6,.3,1.2) both .2s;
  }
  .gp-arrow {
    font-size: 18px;
    color: var(--ink);
    line-height: 1;
  }
  .gp-value {
    font-family: var(--font-display);
    font-variation-settings: "opsz" 64, "SOFT" 40, "WONK" 0;
    font-weight: 700;
    font-size: 48px; line-height: 0.9;
    color: var(--ink);
    letter-spacing: -0.02em;
    margin-top: -42px;
  }
  .gp-caption {
    margin-top: 6px;
    font-family: var(--font-mono); font-size: 9.5px;
    letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--mute);
    white-space: nowrap;
  }
  .gauge-markers {
    position: absolute; bottom: 30px; left: 56px; right: 56px; height: 40px;
    pointer-events: none;
  }
  .gauge-marker {
    position: absolute; bottom: 0;
    transform: translateX(-50%);
    display: flex; flex-direction: column; align-items: center;
    animation: pinIn 0.6s ease-out both;
  }
  .gm-arrow { font-size: 11px; color: var(--ink-soft); line-height: 1; }
  .gm-label {
    margin-top: 3px;
    font-family: var(--font-mono); font-size: 9.5px; font-weight: 500;
    color: var(--ink-soft);
    letter-spacing: 0.08em;
    white-space: nowrap;
    display: flex; gap: 4px; align-items: baseline;
  }
  .gm-val { font-size: 8.5px; color: var(--mute); font-weight: 400; }

  @keyframes gaugeDrop {
    from { opacity: 0; transform: translateX(-50%) translateY(-14px); }
    to   { opacity: 1; transform: translateX(-50%) translateY(0); }
  }

  /* ─────────────────── Footer ─────────────────── */
  footer { margin-top: 48px; padding: 28px 0 60px; border-top: 1.5px solid var(--ink); }
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
  @keyframes rise { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes pinIn { from { opacity: 0; transform: translateX(-50%) translateY(-8px); } to { opacity: 1; transform: translateX(-50%) translateY(0); } }
  @keyframes rowIn { from { opacity: 0; } to { opacity: 1; } }
  .kicker       { animation: rise .55s ease-out .00s both; }
  .wordmark     { animation: rise .80s cubic-bezier(.2,.6,.2,1) .10s both; }
  .standfirst   { animation: rise .60s ease-out .35s both; }
  .lens-row     { animation: rise .50s ease-out .55s both; }

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
    .gauge-frame { padding: 96px 30px 96px; }
    .gauge-track, .gauge-axis, .gauge-labels, .gauge-markers { left: 30px; right: 30px; }
  }
</style>
</head>
<body data-lens="forward">

<header class="masthead">
  <div class="container">
    <p class="kicker">AlphaLabX1 · internal research <span class="edition">Vol. II</span></p>
    <h1 class="wordmark">Valuation <em>&amp; Mood</em></h1>
    <p class="standfirst">S&amp;P 500 and its eleven sectors, seen through two P/E lenses — and the market's mood, plotted against the price beneath it. Updated <time>__LATEST_LABEL__</time>.</p>
    <div class="lens-row">
      <span class="lens-label">valuation lens</span>
      <div class="lens" id="lens">
        <button data-lens="forward" class="active">Forward</button>
        <button data-lens="trailing">Trailing</button>
      </div>
      <span class="lens-note">Forward uses 12-month analyst estimates; trailing uses reported TTM earnings.</span>
    </div>
  </div>
</header>

<main>
  <!-- ═══ 01. Pin strip ═══ -->
  <section class="section">
    <div class="container">
      <div class="section-head">
        <span class="section-num">01</span>
        <div>
          <h2 class="section-title">Where everyone <em>stands today</em></h2>
          <p class="section-lede">Each marker is a sector's current P/E placed as a percentile of its own trailing five years. <strong>Right is expensive.</strong> A reading of 50 means the sector is trading at its own five-year median.</p>
        </div>
        <div class="section-meta"><span class="lens-echo"></span></div>
      </div>

      <div class="view-forward">
        <div class="strip-frame" id="strip-forward" data-family="forward">
          <div class="strip-labels-top"><span>← cheap vs own 5Y</span><span class="middle">median</span><span>expensive vs own 5Y →</span></div>
          <div class="strip-axis"></div>
          <div class="strip-ticks">
            <span class="strip-tick" style="left:0%"></span><span class="strip-tick-label" style="left:0%">0</span>
            <span class="strip-tick" style="left:25%"></span><span class="strip-tick-label" style="left:25%">25</span>
            <span class="strip-tick" style="left:50%"></span><span class="strip-tick-label" style="left:50%">50</span>
            <span class="strip-tick" style="left:75%"></span><span class="strip-tick-label" style="left:75%">75</span>
            <span class="strip-tick" style="left:100%"></span><span class="strip-tick-label" style="left:100%">100</span>
          </div>
          <div class="strip-pins">__STRIP_FORWARD__</div>
        </div>
      </div>

      <div class="view-trailing">
        <div class="strip-frame" id="strip-trailing" data-family="trailing">
          <div class="strip-labels-top"><span>← cheap vs own 5Y</span><span class="middle">median</span><span>expensive vs own 5Y →</span></div>
          <div class="strip-axis"></div>
          <div class="strip-ticks">
            <span class="strip-tick" style="left:0%"></span><span class="strip-tick-label" style="left:0%">0</span>
            <span class="strip-tick" style="left:25%"></span><span class="strip-tick-label" style="left:25%">25</span>
            <span class="strip-tick" style="left:50%"></span><span class="strip-tick-label" style="left:50%">50</span>
            <span class="strip-tick" style="left:75%"></span><span class="strip-tick-label" style="left:75%">75</span>
            <span class="strip-tick" style="left:100%"></span><span class="strip-tick-label" style="left:100%">100</span>
          </div>
          <div class="strip-pins">__STRIP_TRAILING__</div>
        </div>
      </div>
    </div>
  </section>

  <!-- ═══ 02. Rank table ═══ -->
  <section class="section">
    <div class="container">
      <div class="section-head">
        <span class="section-num">02</span>
        <div>
          <h2 class="section-title">Five-year <em>rank</em></h2>
          <p class="section-lede">Sectors ordered from richest to cheapest relative to their own history. <strong>Click any row</strong> to isolate it on the chart below.</p>
        </div>
        <div class="section-meta"><span class="lens-echo"></span></div>
      </div>

      <div class="view-forward">
        <ul class="rank-table" data-family="forward">
          <li class="rank-head">
            <span></span><span>Sector</span><span>P/E</span><span>5Y percentile</span>
            <span style="text-align:right">pctl</span><span style="text-align:right">5Y range</span>
          </li>
          __TABLE_FORWARD__
        </ul>
      </div>

      <div class="view-trailing">
        <ul class="rank-table" data-family="trailing">
          <li class="rank-head">
            <span></span><span>Sector</span><span>P/E</span><span>5Y percentile</span>
            <span style="text-align:right">pctl</span><span style="text-align:right">5Y range</span>
          </li>
          __TABLE_TRAILING__
        </ul>
      </div>
    </div>
  </section>

  <!-- ═══ 03. Historical chart ═══ -->
  <section class="section">
    <div class="container">
      <div class="section-head">
        <span class="section-num">03</span>
        <div>
          <h2 class="section-title">Historical <em>path</em></h2>
          <p class="section-lede">Forward view shows 12-month analyst estimates (daily, since 2008). Trailing view uses reported TTM earnings (monthly, since 1995 for sectors — since 1871 for the index). The Y axis auto-scales to whichever window and series are visible.</p>
        </div>
        <div class="section-meta"><span class="lens-echo"></span></div>
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
      <div class="chart-wrap"><div id="chart"></div></div>
    </div>
  </section>

  <!-- ═══ 04. Fear & Greed gauge ═══ -->
  <section class="section">
    <div class="container">
      <div class="section-head">
        <span class="section-num">04</span>
        <div>
          <h2 class="section-title">Sentiment, <em>at a glance</em></h2>
          <p class="section-lede">MacroMicro's Fear &amp; Greed composite reduces the market's mood to a single 0–100 reading. Under 25 is panicked fear; over 75 is euphoric greed. The open triangles show how mood has drifted over the past week, month, quarter, and year.</p>
        </div>
        <div class="section-meta">composite · 0–100</div>
      </div>
      __GAUGE__
    </div>
  </section>

  <!-- ═══ 05. F&G vs SPX chart ═══ -->
  <section class="section">
    <div class="container">
      <div class="section-head">
        <span class="section-num">05</span>
        <div>
          <h2 class="section-title">Mood <em>against price</em></h2>
          <p class="section-lede">Sentiment on the left axis, S&amp;P 500 on the right. Bear phases bottom with fear readings below 25; tops tend to coincide with extreme-greed plateaus — not coincidence, but also not a tradable signal on its own.</p>
        </div>
        <div class="section-meta">dual axis · shared X</div>
      </div>
      <div class="chart-controls">
        <button data-mood-range="all">All</button>
        <button data-mood-range="10y">10Y</button>
        <button data-mood-range="5y" class="active">5Y</button>
        <button data-mood-range="3y">3Y</button>
        <button data-mood-range="1y">1Y</button>
        <button data-mood-range="ytd">YTD</button>
      </div>
      <div class="chart-wrap"><div id="mood-chart"></div></div>
    </div>
  </section>
</main>

<footer>
  <div class="container">
    <div class="inner">
      <div class="left">
        <span><strong>AlphaLabX1</strong> internal</span>
        <span class="dot">·</span>
        <span>Forward P/E &amp; Sentiment · MacroMicro</span>
        <span class="dot">·</span>
        <span>Trailing P/E · worldperatio.com</span>
        <span class="dot">·</span>
        <span>Chart · Plotly</span>
      </div>
      <span>as of __LATEST_ISO__ · 5-year window</span>
    </div>
  </div>
</footer>

<script>
const DATA = __DATA__;
const LATEST = "__LATEST_ISO__";

// ═══ Shared utilities ═══
function prepareFamily(f) {
  if (!f || !f.series) return null;
  f.series.forEach(s => { s._t = s.points.map(p => Date.parse(p[0])); });
  return f;
}
prepareFamily(DATA.forward);
prepareFamily(DATA.trailing);

function makeTraces(family, outlierMax) {
  return family.series.map(s => ({
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
}

const baseLayout = {
  margin: { l: 56, r: 20, t: 10, b: 44 },
  hovermode: "x unified",
  hoverlabel: {
    font: { family: '"IBM Plex Mono", monospace', size: 11, color: "#f2ecdf" },
    bgcolor: "#1b1813", bordercolor: "#1b1813",
  },
  xaxis: {
    showgrid: false, linecolor: "#d5c8b1", tickcolor: "#8a7e6d",
    tickfont: { family: '"IBM Plex Mono", monospace', size: 10, color: "#55493b" },
    type: "date",
  },
  yaxis: {
    gridcolor: "#e6dcc6", zeroline: false,
    tickfont: { family: '"IBM Plex Mono", monospace', size: 10, color: "#55493b" },
    tickcolor: "#8a7e6d",
    title: { text: "P/E", font: { family: '"IBM Plex Sans", sans-serif', size: 11, color: "#8a7e6d" }, standoff: 14 },
  },
  legend: { orientation: "h", y: -0.18, font: { family: '"IBM Plex Mono", monospace', size: 10, color: "#1b1813" } },
  paper_bgcolor: "#f2ecdf", plot_bgcolor: "#f2ecdf",
  font: { family: '"IBM Plex Sans", sans-serif', size: 11, color: "#1b1813" },
};

const chartConfig = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"] };

// ═══ Section 03 chart with lens switching ═══
let currentLens = "forward";
let currentRange = "5y";

function yRangeForWindow(startMs, endMs) {
  const family = DATA[currentLens];
  if (!family) return null;
  const gd = document.getElementById("chart");
  const visMap = {};
  (gd.data || []).forEach((t, i) => { visMap[i] = t.visible !== "legendonly" && t.visible !== false; });
  let lo = Infinity, hi = -Infinity;
  family.series.forEach((s, i) => {
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
  currentRange = key;
  const now = new Date(LATEST);
  let start;
  if (key === "all") start = new Date("1995-01-01");
  else if (key === "ytd") start = new Date(now.getFullYear(), 0, 1);
  else { const years = parseInt(key, 10); start = new Date(now); start.setFullYear(start.getFullYear() - years); }
  const yr = yRangeForWindow(start.getTime(), now.getTime());
  const upd = {
    "xaxis.range": [start.toISOString().slice(0,10), now.toISOString().slice(0,10)],
    "xaxis.autorange": false,
  };
  if (yr) { upd["yaxis.range"] = yr; upd["yaxis.autorange"] = false; }
  _skipRelayout = true;
  Plotly.relayout("chart", upd).then(() => { _skipRelayout = false; });
}

function renderChart() {
  const family = DATA[currentLens];
  if (!family) return;
  const traces = makeTraces(family);
  return Plotly.react("chart", traces, baseLayout, chartConfig).then(() => applyRange(currentRange));
}

renderChart();

function rescaleY() {
  const gd = document.getElementById("chart");
  const xr = gd.layout.xaxis.range;
  if (!xr || xr.length !== 2) return;
  const startMs = typeof xr[0] === "string" ? Date.parse(xr[0]) : +xr[0];
  const endMs = typeof xr[1] === "string" ? Date.parse(xr[1]) : +xr[1];
  const yr = yRangeForWindow(startMs, endMs);
  if (!yr) return;
  _skipRelayout = true;
  Plotly.relayout("chart", { "yaxis.range": yr, "yaxis.autorange": false }).then(() => { _skipRelayout = false; });
}

document.getElementById("chart").on("plotly_relayout", () => { if (_skipRelayout) return; rescaleY(); });

document.querySelectorAll("[data-range]").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("[data-range]").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    applyRange(btn.dataset.range);
  });
});

function setVisible(fn) {
  const family = DATA[currentLens];
  if (!family) return;
  const vis = family.series.map(s => fn(s) ? true : "legendonly");
  Plotly.restyle("chart", { visible: vis }).then(rescaleY);
}
document.getElementById("only-index").addEventListener("click", () => setVisible(s => s.isIndex));
document.getElementById("only-sectors").addEventListener("click", () => setVisible(s => !s.isIndex));
document.getElementById("show-all").addEventListener("click", () => setVisible(() => true));

// ═══ Lens toggle ═══
document.querySelectorAll(".lens button").forEach(btn => {
  btn.addEventListener("click", () => {
    const newLens = btn.dataset.lens;
    if (newLens === currentLens) return;
    if (newLens === "trailing" && !DATA.trailing) return;
    currentLens = newLens;
    document.body.setAttribute("data-lens", newLens);
    document.querySelectorAll(".lens button").forEach(b => b.classList.toggle("active", b === btn));
    renderChart();
    wireSectorInteractions();
  });
});

// ═══ Strip / row interactivity (re-wire after lens switch for visible family only) ═══
function wireSectorInteractions() {
  // Pin entrance + hover
  document.querySelectorAll(".pin").forEach((pin, i) => {
    pin.style.animationDelay = (0.3 + (i % 12) * 0.04) + "s";
    const id = parseInt(pin.dataset.id, 10);
    pin.onmouseenter = () => soloViaStrip(id);
    pin.onmouseleave = () => unsoloViaStrip();
    pin.onclick = () => stickSolo(id);
  });
  // Row click
  document.querySelectorAll(".row:not(.rank-head)").forEach((row, i) => {
    row.style.animationDelay = (0.1 + (i % 12) * 0.03) + "s";
    const id = parseInt(row.dataset.id, 10);
    row.onclick = () => stickSolo(id);
  });
}
wireSectorInteractions();

function soloViaStrip(id) {
  document.querySelectorAll(".strip-frame").forEach(el => el.classList.add("highlighting"));
  document.querySelectorAll(".pin").forEach(p => {
    p.classList.toggle("highlighted", parseInt(p.dataset.id, 10) === id);
  });
  const family = DATA[currentLens];
  if (family) {
    const op = family.series.map(s => s.id === id ? 1 : 0.12);
    Plotly.restyle("chart", { opacity: op });
  }
}
function unsoloViaStrip() {
  document.querySelectorAll(".strip-frame").forEach(el => el.classList.remove("highlighting"));
  document.querySelectorAll(".pin").forEach(p => p.classList.remove("highlighted"));
  const family = DATA[currentLens];
  if (family) Plotly.restyle("chart", { opacity: family.series.map(() => 1) });
}
function stickSolo(id) {
  const family = DATA[currentLens];
  if (!family) return;
  const idx = family.series.findIndex(s => s.id === id);
  if (idx < 0) return;
  const vis = family.series.map((_, i) => i === idx ? true : "legendonly");
  Plotly.restyle("chart", { visible: vis, opacity: family.series.map(() => 1) }).then(rescaleY);
  document.getElementById("chart").scrollIntoView({ behavior: "smooth", block: "center" });
}

// ═══ Section 05: F&G vs SPX dual-axis chart ═══
(function renderMoodChart() {
  const fg = DATA.fg.points;
  const spx = DATA.spx.points;
  if (!fg.length || !spx.length) return;

  const moodTraces = [
    {
      x: spx.map(p => p[0]),
      y: spx.map(p => p[1]),
      type: "scattergl", mode: "lines",
      name: "S&P 500",
      line: { color: "#1b1813", width: 2.2 },
      yaxis: "y2",
      hovertemplate: "<b>S&P 500</b>  %{y:.2f}<extra></extra>",
    },
    {
      x: fg.map(p => p[0]),
      y: fg.map(p => p[1]),
      type: "scattergl", mode: "lines",
      name: "Fear & Greed",
      line: { color: "#b8421c", width: 1.2 },
      yaxis: "y",
      hovertemplate: "<b>F&G</b>  %{y:.1f}<extra></extra>",
    },
  ];
  const moodLayout = Object.assign({}, baseLayout, {
    yaxis: {
      gridcolor: "#e6dcc6", zeroline: false,
      tickfont: { family: '"IBM Plex Mono", monospace', size: 10, color: "#55493b" },
      tickcolor: "#8a7e6d",
      title: { text: "fear & greed", font: { family: '"IBM Plex Sans", sans-serif', size: 11, color: "#b8421c" }, standoff: 14 },
      range: [0, 100],
      tickvals: [0, 25, 50, 75, 100],
    },
    yaxis2: {
      overlaying: "y", side: "right",
      gridcolor: "rgba(0,0,0,0)", zeroline: false,
      tickfont: { family: '"IBM Plex Mono", monospace', size: 10, color: "#55493b" },
      tickcolor: "#8a7e6d",
      title: { text: "S&P 500", font: { family: '"IBM Plex Sans", sans-serif', size: 11, color: "#1b1813" }, standoff: 14 },
    },
    shapes: [
      { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: 25, y1: 25, line: { color: "#2e5d56", width: 1, dash: "dot" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: 75, y1: 75, line: { color: "#b8421c", width: 1, dash: "dot" } },
    ],
  });
  Plotly.newPlot("mood-chart", moodTraces, moodLayout, chartConfig).then(() => applyMoodRange("5y"));

  function applyMoodRange(key) {
    const now = new Date(LATEST);
    let start;
    if (key === "all") start = new Date(fg[0][0]);
    else if (key === "ytd") start = new Date(now.getFullYear(), 0, 1);
    else { const y = parseInt(key, 10); start = new Date(now); start.setFullYear(start.getFullYear() - y); }
    const startStr = start.toISOString().slice(0,10);
    const endStr = now.toISOString().slice(0,10);
    // Compute SPX Y-axis range for window
    let lo = Infinity, hi = -Infinity;
    const startMs = start.getTime(), endMs = now.getTime();
    for (let j = 0; j < spx.length; j++) {
      const t = Date.parse(spx[j][0]);
      if (t < startMs || t > endMs) continue;
      const v = spx[j][1];
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    const upd = {
      "xaxis.range": [startStr, endStr],
      "xaxis.autorange": false,
    };
    if (isFinite(lo)) {
      const pad = (hi - lo) * 0.05;
      upd["yaxis2.range"] = [lo - pad, hi + pad];
      upd["yaxis2.autorange"] = false;
    }
    Plotly.relayout("mood-chart", upd);
  }

  document.querySelectorAll("[data-mood-range]").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-mood-range]").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      applyMoodRange(btn.dataset.moodRange);
    });
  });
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
