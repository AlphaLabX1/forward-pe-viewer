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
TRAILING_DIR = DATA / "trailing"  # kept for back-compat; trailing now lives in raw.json

SECTOR_COLORS = {
    20052: "#E9E3D4",  # S&P 500 - ivory (benchmark, reads white on dark)
    20517: "#3B6FB0",  # Information Technology - slate blue
    20518: "#5AA5C4",  # Communication Services - sky
    20519: "#4E7C59",  # Consumer Discretionary - moss
    20520: "#6E9A46",  # Financials - lime olive
    20521: "#CBA02B",  # Industrials - gold
    20522: "#3A5878",  # Utilities - steel
    20523: "#A6392B",  # Energy - rust
    20524: "#7A5AA6",  # Real Estate - violet
    20525: "#9A6A38",  # Materials - bronze
    20526: "#C77D3B",  # Consumer Staples - amber
    20527: "#3E8A86",  # Health Care - teal
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


def assign_rows(rows_asc, row_count=4, min_gap=7.0):
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
        heat = "hot" if pct >= 85 else "cold" if pct <= 45 else "mid"
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
    <span>{r["min_5y"]:.1f}</span><span class="sep">–</span><span>{r["max_5y"]:.1f}</span>
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


def valuation_payload(spx_fwd_pe: list, us10y: list):
    """Build payload for the SPX valuation chart (section 06):
      - forward P/E with 5Y rolling 20th/50th/80th percentile bands
      - forward earnings yield (1/PE × 100) and 10Y treasury yield
      - their spread (EY - 10Y), an equity-vs-bonds risk-premium proxy.

    Monthly forward PE drives the cadence: each spread / EY point is dated
    to the forward PE's month; the 10Y yield is the value on (or nearest
    prior to) that month-end date.
    """
    if not spx_fwd_pe or not us10y:
        return None

    # Forward EY = 1 / PE * 100 (in percent)
    pe_pts = [(d, float(v)) for d, v in spx_fwd_pe if v]
    ey_pts = [[d, 100.0 / v] for d, v in pe_pts]

    # 5Y rolling stats — date-based 1825-day trailing window. Works for either
    # monthly or daily input. Emit once at least 1 year of history is available.
    from bisect import insort, bisect_left
    from datetime import date as _date
    band_p20, band_p80 = [], []
    window_vals: list[float] = []
    window_dates: list[str] = []
    sorted_window: list[float] = []
    WINDOW_DAYS = 365 * 5
    MIN_DAYS = 365  # need ≥1Y of points before emitting a band

    for d, v in pe_pts:
        insort(sorted_window, v)
        window_vals.append(v)
        window_dates.append(d)
        # Drop expired points
        cutoff = _date.fromisoformat(d) - timedelta(days=WINDOW_DAYS)
        cutoff_str = cutoff.isoformat()
        while window_dates and window_dates[0] < cutoff_str:
            old_v = window_vals.pop(0)
            window_dates.pop(0)
            idx = bisect_left(sorted_window, old_v)
            sorted_window.pop(idx)
        if not window_dates:
            continue
        span_days = (_date.fromisoformat(d) - _date.fromisoformat(window_dates[0])).days
        if span_days < MIN_DAYS:
            continue
        n = len(sorted_window)
        def pct(p, _w=sorted_window, _n=n):
            idx = (_n - 1) * p
            lo = int(idx); hi = min(lo + 1, _n - 1)
            frac = idx - lo
            return _w[lo] * (1 - frac) + _w[hi] * frac
        band_p20.append([d, pct(0.20)])
        band_p80.append([d, pct(0.80)])

    # 200-point trailing simple moving average. Daily input → ~10-month SMA;
    # monthly input → 200 months (not meaningful, but harmless — gates on len).
    sma200 = []
    running_sum_sma = 0.0
    SMA_WINDOW = 200
    for i, (d, v) in enumerate(pe_pts):
        running_sum_sma += v
        if i >= SMA_WINDOW:
            running_sum_sma -= pe_pts[i - SMA_WINDOW][1]
        if i >= SMA_WINDOW - 1:
            sma200.append([d, running_sum_sma / SMA_WINDOW])

    # Build a date -> 10Y yield lookup, then for each EY point find the nearest
    # prior (or same-day) yield observation. 10Y is daily; PE is monthly.
    yield_by_date = {d: float(v) for d, v in us10y if v}
    yield_dates_sorted = sorted(yield_by_date.keys())

    def yield_on_or_before(target_d: str):
        # Binary search the last date <= target_d.
        from bisect import bisect_right
        idx = bisect_right(yield_dates_sorted, target_d) - 1
        if idx < 0:
            return None
        return yield_by_date[yield_dates_sorted[idx]]

    spread_pts = []
    for d, ey in ey_pts:
        y10 = yield_on_or_before(d)
        if y10 is not None:
            spread_pts.append([d, ey - y10])

    return {
        "pe": [[d, round(v, 4)] for d, v in pe_pts],
        "ey": [[d, round(v, 4)] for d, v in ey_pts],
        "us10y": [[d, round(v, 3)] for d, v in us10y],
        "spread": [[d, round(v, 4)] for d, v in spread_pts],
        "band_p20": [[d, round(v, 4)] for d, v in band_p20],
        "band_p80": [[d, round(v, 4)] for d, v in band_p80],
        "sma200": [[d, round(v, 4)] for d, v in sma200],
    }


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


def _round_series(pts, ndigits=4):
    return [[d, round(float(v), ndigits)] for d, v in pts if v is not None]


def build() -> Path:
    # Forward + trailing P/E come from data/raw.json under nested keys
    # `forward`/`trailing` (each maps str(series_id) -> [[date, value], ...]).
    # Schema set by fetch.py after the Koyfin migration (2026-05).
    raw = json.loads((DATA / "raw.json").read_text())
    fwd_raw = raw.get("forward", {})
    trl_raw = raw.get("trailing", {})

    # Guard: raw.json is gitignored and regenerated by fetch.py, so a stale
    # local copy silently rolls every P/E series back in time while the
    # tracked CSVs (and F&G/price sections) stay current. Compare against the
    # committed S&P 500 forward CSV and refuse to build from older data.
    spx_csv = _load_csv_points(DATA / "20052_sandp_500.csv")
    raw_spx = fwd_raw.get("20052") or []
    if spx_csv and raw_spx and raw_spx[-1][0] < spx_csv[-1][0]:
        raise SystemExit(
            f"data/raw.json is stale: forward S&P 500 ends {raw_spx[-1][0]} "
            f"but data/20052_sandp_500.csv ends {spx_csv[-1][0]}. "
            "Run `python fetch.py` first."
        )

    forward_points: dict[int, list] = {}
    trailing_points: dict[int, list] = {}
    for sid in SERIES:
        if str(sid) in fwd_raw:
            forward_points[sid] = _round_series(fwd_raw[str(sid)], 4)
        if str(sid) in trl_raw:
            trailing_points[sid] = _round_series(trl_raw[str(sid)], 4)
    forward = build_family_payload(forward_points)
    trailing = build_family_payload(trailing_points) if trailing_points else None

    # Sentiment: Fear & Greed + SPX price.
    fg_points = _round_series(_load_csv_points(DATA / "fear_greed.csv"), 2)
    spx_points = _round_series(_load_csv_points(DATA / "spx_price.csv"), 2)
    us10y_points = _round_series(_load_csv_points(DATA / "us10y.csv"), 3)
    gauge = gauge_payload(fg_points)

    # Valuation panel (section 06) — built per index, switchable in the UI.
    qqq_pe = _round_series(raw.get("qqq_pe") or [], 4)
    qqq_price = _round_series(raw.get("qqq_price") or [], 2)
    valuation_spy = valuation_payload(forward_points.get(20052) or [], us10y_points)
    valuation_qqq = valuation_payload(qqq_pe, us10y_points)

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
        "qqq": {"price": qqq_price},
        "valuation": {"spy": valuation_spy, "qqq": valuation_qqq},
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


def _fg_word(v):
    if v < 25:
        return "Extreme fear"
    if v < 45:
        return "Fear"
    if v <= 55:
        return "Neutral"
    if v <= 75:
        return "Greed"
    return "Extreme greed"


def render_gauge(g):
    if not g:
        return "<p style='color:var(--dim)'>Fear &amp; Greed data unavailable.</p>"
    cur = g["current"]["value"]
    pointer_pos = max(0, min(100, cur))
    tile_names = {"1W": "1 week", "1M": "1 month", "3M": "3 months", "1Y": "1 year"}
    tiles = (
        f'<div class="gauge-tile"><div class="gt-label">Now</div>'
        f'<div class="gt-val mono">{cur:.0f}</div></div>'
    )
    tiles += "".join(
        f'<div class="gauge-tile" title="{m["date"]}">'
        f'<div class="gt-label">{tile_names.get(m["label"], m["label"])}</div>'
        f'<div class="gt-val mono">{m["value"]:.0f}</div></div>'
        for m in g["markers"]
    )
    return f'''
<div class="gauge">
  <div class="gauge-big">
    <div class="gauge-num">{cur:.0f}</div>
    <div class="gauge-word mono">Today · {_fg_word(cur)}</div>
  </div>
  <div class="gauge-right">
    <div class="gauge-bar">
      <span class="gauge-pointer" style="left:{pointer_pos:.2f}%" title="today · {g["current"]["date"]}"></span>
    </div>
    <div class="gauge-scale mono">
      <span>Extreme fear</span><span>Fear</span><span>Neutral</span><span>Greed</span><span>Extreme greed</span>
    </div>
    <div class="gauge-tiles">{tiles}</div>
  </div>
</div>
'''.strip()


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Valuation &amp; Mood · AlphaLabX1</title>
<link rel="icon" href="favicon.ico" sizes="48x48">
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400;1,500&family=Spline+Sans+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  :root {
    --bg:           #060608;
    --shell-border: #1E2026;
    --card:         rgba(22,24,31,0.55);
    --card-border:  rgba(255,255,255,0.07);
    --inset:        rgba(10,11,14,0.5);
    --inset-border: rgba(255,255,255,0.04);
    --hair:         rgba(255,255,255,0.08);
    --text:         #EDEEF0;
    --text-2:       #DDE0E5;
    --soft:         #9BA0AB;
    --lede:         #868A93;
    --dim:          #6B7078;
    --dimmer:       #565C64;
    --accent:       #8B7CF6;
    --accent-hi:    #9B8CFA;
    --magenta:      #D66AE0;
    --hot:          #F87171;
    --cold:         #34D399;
    --font-body: "Plus Jakarta Sans", -apple-system, system-ui, sans-serif;
    --font-mono: "Spline Sans Mono", ui-monospace, "SF Mono", monospace;
  }
  *, *::before, *::after { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-body);
    font-size: 14px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }
  body { padding: 48px 24px; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }

  /* ─────────────────── Shell + ambient blobs ─────────────────── */
  .shell {
    position: relative;
    max-width: 1180px;
    margin: 0 auto;
    overflow: hidden;
    border-radius: 28px;
    background: linear-gradient(158deg, #0D0F16 0%, #0B0C0E 48%, #140B18 100%);
    border: 1px solid var(--shell-border);
    box-shadow: 0 40px 80px -40px rgba(0,0,0,0.7);
  }
  .blobs { position: absolute; inset: 0; z-index: 0; overflow: hidden; pointer-events: none; }
  .blobs i { position: absolute; border-radius: 50%; }
  .blob-1 { top: -120px; left: 4%; width: 480px; height: 480px; background: radial-gradient(circle, #7C5CF0, transparent 66%); opacity: 0.42; filter: blur(40px); }
  .blob-2 { top: 12%; right: -110px; width: 540px; height: 540px; background: radial-gradient(circle, #2E8FA0, transparent 66%); opacity: 0.28; filter: blur(48px); }
  .blob-3 { top: 31%; left: 38%; width: 560px; height: 560px; background: radial-gradient(circle, #B04AD6, transparent 66%); opacity: 0.24; filter: blur(52px); }
  .blob-4 { top: 52%; left: -140px; width: 520px; height: 520px; background: radial-gradient(circle, #7C5CF0, transparent 66%); opacity: 0.22; filter: blur(48px); }
  .blob-5 { top: 71%; right: -130px; width: 560px; height: 560px; background: radial-gradient(circle, #2E8FA0, transparent 66%); opacity: 0.2; filter: blur(50px); }
  .blob-6 { bottom: -160px; left: 28%; width: 540px; height: 540px; background: radial-gradient(circle, #B04AD6, transparent 66%); opacity: 0.22; filter: blur(50px); }
  .inner {
    position: relative; z-index: 1;
    padding: 44px 44px 40px;
    display: flex; flex-direction: column; gap: 22px;
  }

  /* ─────────────────── Masthead ─────────────────── */
  .masthead {
    display: flex; justify-content: space-between; align-items: flex-end; gap: 28px;
    border-bottom: 1px solid var(--hair);
    padding-bottom: 24px;
  }
  .kicker {
    font-family: var(--font-mono);
    font-size: 11px; letter-spacing: 0.16em; text-transform: uppercase;
    color: #7A7F8A; margin: 0;
  }
  .wordmark {
    margin: 12px 0 0;
    font-size: clamp(38px, 5.5vw, 58px);
    font-weight: 800;
    letter-spacing: -0.03em;
    line-height: 0.98;
    color: var(--text);
  }
  .wordmark em {
    font-style: normal;
    background: linear-gradient(120deg, #9B8CFA, #D66AE0);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .standfirst {
    margin: 16px 0 0;
    font-size: 17px; line-height: 1.55;
    color: var(--soft);
    max-width: 640px;
  }
  .standfirst time { color: var(--dim); }
  .masthead-side { text-align: right; flex: 0 0 auto; }

  /* ─────────────────── Lens toggle ─────────────────── */
  .lens {
    display: inline-flex;
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 12px;
    padding: 4px; gap: 2px;
    backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
  }
  .lens button {
    border: 0; background: transparent; cursor: pointer;
    padding: 8px 18px;
    border-radius: 8px;
    font-family: var(--font-body); font-size: 13px; font-weight: 700;
    color: var(--dim);
    transition: color .15s ease-out, background .15s ease-out;
  }
  .lens button:hover { color: var(--text); }
  .lens button.active {
    background: linear-gradient(120deg, #8B7CF6, #6C5CE7);
    color: #FFF;
  }
  .lens-note {
    font-family: var(--font-mono);
    font-size: 11px; color: var(--dim);
    margin-top: 10px;
  }
  body[data-lens="forward"]  .lens-note::before { content: "12-month analyst estimates"; }
  body[data-lens="trailing"] .lens-note::before { content: "reported TTM earnings"; }

  /* Show/hide views based on body data-lens */
  .view-trailing { display: none; }
  body[data-lens="trailing"] .view-forward { display: none; }
  body[data-lens="trailing"] .view-trailing { display: block; }

  /* ─────────────────── Cards + section heads ─────────────────── */
  .card {
    position: relative;
    background: var(--card);
    backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
    border: 1px solid var(--card-border);
    border-radius: 22px;
    padding: 26px 30px;
  }
  .card:hover { z-index: 30; } /* tooltips escape sibling stacking contexts */
  .card-head {
    display: flex; justify-content: space-between; align-items: flex-start;
    gap: 24px; margin-bottom: 6px;
  }
  .card-title { display: flex; gap: 14px; align-items: baseline; min-width: 0; }
  .card-num { font-family: var(--font-mono); font-size: 12px; font-weight: 600; color: var(--accent); }
  .card h2 { margin: 0; font-size: 22px; font-weight: 800; letter-spacing: -0.02em; color: var(--text); }
  .lede { margin: 6px 0 0; font-size: 14px; line-height: 1.5; color: var(--lede); max-width: 640px; }
  .lede strong { color: var(--soft); font-weight: 600; }
  .card-aside {
    flex: 0 0 auto; text-align: right;
    font-family: var(--font-mono); font-size: 11px; color: var(--dim);
    white-space: nowrap; padding-top: 4px;
  }
  .lens-echo {
    display: inline-block;
    padding: 4px 10px;
    border: 1px solid var(--hair); border-radius: 8px;
    color: var(--soft); letter-spacing: 0.06em;
  }
  body[data-lens="forward"]  .lens-echo::before { content: "forward · daily"; }
  body[data-lens="trailing"] .lens-echo::before { content: "trailing · monthly"; }

  /* ─────────────────── Controls: seg pills + outline buttons ─────────────────── */
  .chart-controls {
    display: flex; justify-content: space-between; align-items: center;
    gap: 10px; margin: 14px 0; flex-wrap: wrap;
  }
  .ctrl-group { display: flex; gap: 8px; align-items: center; }
  .seg {
    display: inline-flex;
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 3px; gap: 2px;
  }
  .seg button {
    border: 0; background: transparent; cursor: pointer;
    padding: 6px 12px; border-radius: 7px;
    font-family: var(--font-mono); font-size: 11px; font-weight: 600;
    letter-spacing: 0.02em;
    color: var(--dim);
    transition: color .12s ease-out, background .12s ease-out;
  }
  .seg button:hover { color: var(--text); }
  .seg button.active { background: var(--accent); color: #0B0C0E; }
  .obtn {
    border: 1px solid rgba(255,255,255,0.1); background: transparent;
    color: var(--lede);
    padding: 6px 12px; border-radius: 8px;
    font-family: var(--font-mono); font-size: 11px; font-weight: 600;
    cursor: pointer;
    transition: color .12s ease-out, border-color .12s ease-out;
  }
  .obtn:hover { border-color: var(--accent); color: var(--text); }

  .chart-wrap {
    background: var(--inset);
    border: 1px solid var(--inset-border);
    border-radius: 14px;
    padding: 14px 16px 6px;
  }
  #chart, #mood-chart { width: 100%; height: 560px; }
  #val-chart { width: 100%; height: 760px; }

  /* ─────────────────── Strip (dot distribution) ─────────────────── */
  .strip-frame {
    position: relative;
    height: 240px;
    margin-top: 18px;
    background: var(--inset);
    border: 1px solid var(--inset-border);
    border-radius: 14px;
  }
  .strip-frame::before { /* baseline */
    content: ""; position: absolute; left: 24px; right: 24px; bottom: 34px;
    height: 1px; background: rgba(255,255,255,0.1);
  }
  .strip-frame::after { /* dashed median */
    content: ""; position: absolute; left: 50%; top: 16px; bottom: 34px; width: 1px;
    background: repeating-linear-gradient(rgba(255,255,255,0.12), rgba(255,255,255,0.12) 3px, transparent 3px, transparent 7px);
  }
  .strip-labels {
    position: absolute; left: 24px; right: 24px; bottom: 10px;
    display: flex; justify-content: space-between;
    font-family: var(--font-mono);
    font-size: 10px; font-weight: 600;
    letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--dimmer);
  }
  .strip-pins { position: absolute; left: 24px; right: 24px; top: 18px; bottom: 34px; }
  .pin {
    position: absolute;
    transform: translateX(-50%);
    display: flex; flex-direction: column; align-items: center; gap: 5px;
    cursor: pointer;
    animation: pinIn 0.6s cubic-bezier(.2,.7,.2,1) both;
  }
  .pin-row-0 { top: 2px; }
  .pin-row-1 { top: 48px; }
  .pin-row-2 { top: 94px; }
  .pin-row-3 { top: 140px; }
  .pin-dot {
    width: 12px; height: 12px; border-radius: 50%;
    border: 2px solid #141518;
    box-shadow: 0 0 8px -1px rgba(0,0,0,0.6);
    transition: transform .15s ease-out;
  }
  .pin-label {
    font-family: var(--font-mono); font-size: 9.5px; font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--dim);
    background: rgba(10,11,14,0.7);
    padding: 2px 6px; border-radius: 6px;
    white-space: nowrap;
    display: flex; gap: 5px; align-items: baseline;
    transition: color .15s ease-out, background .15s ease-out;
  }
  .pin-pct { font-size: 9px; font-weight: 400; color: var(--dimmer); font-variant-numeric: tabular-nums; }
  .pin:hover .pin-dot { transform: scale(1.45); }
  .pin:hover .pin-label { color: var(--text); background: rgba(139,124,246,0.2); }
  .strip-frame.highlighting .pin:not(.highlighted) { opacity: 0.22; }
  .strip-frame.highlighting .pin.highlighted .pin-dot { transform: scale(1.6); }

  /* ─────────────────── Rank table ─────────────────── */
  .rank-table { list-style: none; margin: 12px 0 0; padding: 0; }
  .rank-head, .row {
    display: grid;
    grid-template-columns: 34px 2.2fr 84px 1.9fr 54px 120px;
    gap: 16px;
    align-items: center;
  }
  .rank-head {
    padding: 0 8px 10px;
    font-family: var(--font-mono);
    font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--dim);
    border-bottom: 1px solid var(--hair);
  }
  .row {
    padding: 11px 8px;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    border-radius: 10px;
    cursor: pointer;
    position: relative;
    animation: rowIn 0.45s ease-out both;
    transition: background .15s;
  }
  .row:last-child { border-bottom: 0; }
  .row:hover { background: rgba(255,255,255,0.04); }
  .row.is-index { background: rgba(139,124,246,0.12); }
  .row.is-index:hover { background: rgba(139,124,246,0.18); }
  .rank-num { font-family: var(--font-mono); font-size: 11px; color: var(--dimmer); font-variant-numeric: tabular-nums; }
  .name-col { display: flex; align-items: center; gap: 10px; min-width: 0; }
  .swatch { width: 9px; height: 9px; border-radius: 3px; flex: 0 0 9px; }
  .name {
    font-size: 15px; font-weight: 600; color: var(--text-2);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .ticker { font-family: var(--font-mono); font-size: 10px; color: var(--dimmer); letter-spacing: 0.06em; }
  .val { font-size: 15px; font-weight: 600; text-align: right; color: var(--text); }
  .bar-col { position: relative; display: block; }
  .bar { position: relative; display: flex; align-items: center; height: 14px; width: 100%; }
  .bar::before {
    content: ""; position: absolute; left: 0; right: 0;
    height: 2px; border-radius: 2px; background: rgba(255,255,255,0.1);
  }
  .bar-fill { position: absolute; left: 0; height: 3px; border-radius: 2px; }
  .bar-marker {
    position: absolute;
    width: 12px; height: 12px; border-radius: 50%;
    border: 2px solid #141518;
    box-shadow: 0 0 6px rgba(0,0,0,0.5);
    transform: translateX(-50%);
  }
  .heat-mid  .bar-fill { background: linear-gradient(90deg, #A78BFA, #7C6CF0); }
  .heat-mid  .bar-marker { background: var(--accent); }
  .heat-hot  .bar-fill { background: linear-gradient(90deg, #FB9B8B, #F87171); }
  .heat-hot  .bar-marker { background: var(--hot); }
  .heat-cold .bar-fill { background: linear-gradient(90deg, #5EEAB4, #34D399); }
  .heat-cold .bar-marker { background: var(--cold); }
  .pct { font-size: 17px; font-weight: 800; text-align: right; letter-spacing: -0.01em; }
  .heat-mid  .pct { color: var(--accent); }
  .heat-hot  .pct { color: var(--hot); }
  .heat-cold .pct { color: var(--cold); }
  .range-col {
    font-family: var(--font-mono);
    font-size: 12px; color: var(--dim);
    display: flex; gap: 6px; justify-content: flex-end;
  }
  .range-col .sep { color: #3A3D45; }

  /* ─────────────────── Row / pin tooltips ─────────────────── */
  .row .tip {
    position: absolute;
    top: calc(100% - 2px); right: 0;
    width: 380px; max-width: calc(100vw - 80px);
    z-index: 50;
    background: linear-gradient(180deg, #1A1C25, #14161D);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 16px;
    color: var(--text);
    padding: 18px 22px 20px;
    opacity: 0; pointer-events: none;
    transform: translateY(-4px);
    transition: opacity .16s ease-out, transform .16s ease-out;
    box-shadow: 0 24px 60px -12px rgba(0,0,0,0.7);
    text-align: left;
    cursor: default;
  }
  .row:hover .tip { opacity: 1; transform: translateY(0); pointer-events: auto; }
  .row:nth-last-child(-n+4) .tip { top: auto; bottom: calc(100% - 2px); transform: translateY(4px); }
  .row:nth-last-child(-n+4):hover .tip { transform: translateY(0); }
  .tip-def {
    font-size: 13.5px; line-height: 1.5;
    color: var(--soft);
    margin: 0 0 14px; padding-bottom: 14px;
    border-bottom: 1px solid var(--hair);
  }
  .tip-label {
    font-family: var(--font-mono); font-size: 9.5px;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--dimmer);
    margin: 0 0 8px;
  }
  .tip-holdings { list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: 1fr; gap: 5px; }
  .tip-holdings li { display: grid; grid-template-columns: 68px 1fr; gap: 14px; align-items: baseline; }
  .tip-tk { font-family: var(--font-mono); font-weight: 500; font-size: 11px; letter-spacing: 0.08em; color: var(--accent-hi); }
  .tip-nm { font-size: 13px; font-weight: 400; color: var(--text-2); }

  .pin-tip {
    position: absolute; top: calc(100% + 14px); left: 50%;
    transform: translate(-50%, -6px);
    width: 260px; max-width: calc(100vw - 60px);
    background: linear-gradient(180deg, #1A1C25, #14161D);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 16px;
    color: var(--text);
    padding: 14px 16px 16px;
    opacity: 0; pointer-events: none;
    transition: opacity .16s ease-out, transform .16s ease-out;
    box-shadow: 0 24px 60px -12px rgba(0,0,0,0.7);
    z-index: 100; text-align: left;
  }
  .pin:hover .pin-tip { opacity: 1; transform: translate(-50%, 0); }
  .pin-tip-name { font-weight: 700; font-size: 15px; letter-spacing: -0.005em; margin: 0 0 6px; }
  .pin-tip-def {
    font-size: 12.5px; line-height: 1.45;
    color: var(--soft);
    margin: 0 0 12px; padding-bottom: 10px;
    border-bottom: 1px solid var(--hair);
  }
  .pin-tip-label {
    font-family: var(--font-mono); font-size: 9px;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--dimmer);
    margin: 0 0 6px;
  }
  .pin-tip .tip-holdings li { grid-template-columns: 54px 1fr; gap: 10px; }
  .pin-tip .tip-tk { font-size: 10px; }
  .pin-tip .tip-nm { font-size: 12px; }

  /* ─────────────────── Gauge (Fear & Greed) ─────────────────── */
  .gauge { display: flex; align-items: center; gap: 40px; margin-top: 20px; }
  .gauge-big { text-align: center; flex: 0 0 auto; }
  .gauge-num {
    font-size: 76px; font-weight: 800; line-height: 0.9; letter-spacing: -0.03em;
    background: linear-gradient(120deg, #9B8CFA, #D66AE0);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .gauge-word {
    font-size: 11px; font-weight: 600;
    letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--accent-hi);
    margin-top: 8px;
  }
  .gauge-right { flex: 1; min-width: 0; padding-top: 22px; }
  .gauge-bar {
    position: relative; height: 14px; border-radius: 8px;
    background: linear-gradient(90deg, #2E8B6E, #8FC9A0, #E0C97A, #E09A5A, #D6503C);
    box-shadow: inset 0 1px 3px rgba(0,0,0,0.4);
  }
  .gauge-pointer {
    position: absolute; top: -26px;
    width: 2px; height: 30px;
    background: var(--text);
    transform: translateX(-50%);
    animation: gaugeDrop 0.8s cubic-bezier(.2,.6,.3,1.2) both .2s;
  }
  .gauge-pointer::before {
    content: ""; position: absolute; top: -7px; left: 50%;
    transform: translateX(-50%);
    border-left: 6px solid transparent; border-right: 6px solid transparent;
    border-top: 8px solid var(--text);
  }
  .gauge-scale {
    display: flex; justify-content: space-between;
    font-size: 10px; letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--dim);
    margin-top: 6px;
  }
  .gauge-tiles { display: flex; gap: 14px; margin-top: 18px; }
  .gauge-tile {
    flex: 1; text-align: center;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 10px;
    padding: 10px;
  }
  .gt-label { font-size: 11px; color: var(--dim); font-weight: 600; }
  .gt-val { font-size: 18px; font-weight: 700; color: var(--text); }

  @keyframes gaugeDrop {
    from { opacity: 0; transform: translateX(-50%) translateY(-10px); }
    to   { opacity: 1; transform: translateX(-50%) translateY(0); }
  }

  /* ─────────────────── Footer ─────────────────── */
  footer {
    display: flex; justify-content: space-between; align-items: baseline;
    gap: 10px; flex-wrap: wrap;
    font-family: var(--font-mono);
    font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--dim);
    border-top: 1px solid var(--hair);
    padding-top: 18px; margin-top: 4px;
  }
  footer .left { display: flex; gap: 12px; flex-wrap: wrap; }
  footer .dot { color: #3A3D45; }
  footer strong { color: var(--soft); font-weight: 600; }

  /* ─────────────────── Animations ─────────────────── */
  @keyframes rise { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes pinIn { from { opacity: 0; transform: translateX(-50%) translateY(-8px); } to { opacity: 1; transform: translateX(-50%) translateY(0); } }
  @keyframes rowIn { from { opacity: 0; } to { opacity: 1; } }
  .kicker        { animation: rise .55s ease-out .00s both; }
  .wordmark      { animation: rise .80s cubic-bezier(.2,.6,.2,1) .10s both; }
  .standfirst    { animation: rise .60s ease-out .35s both; }
  .masthead-side { animation: rise .50s ease-out .55s both; }

  @media (max-width: 900px) {
    body { padding: 16px 10px; }
    .shell { border-radius: 20px; }
    .inner { padding: 24px 18px 24px; gap: 16px; }
    .masthead { flex-direction: column; align-items: flex-start; }
    .masthead-side { text-align: left; }
    .card { padding: 20px 16px; border-radius: 18px; }
    .card-head { flex-direction: column; gap: 8px; }
    .card-aside { text-align: left; padding-top: 0; }
    .rank-head, .row { grid-template-columns: 26px 1.6fr 60px 1fr 40px; gap: 10px; }
    .rank-head > *:nth-child(6), .row > *:nth-child(6) { display: none; }
    .name { font-size: 13px; }
    .val { font-size: 13px; }
    .pct { font-size: 15px; }
    .gauge { flex-direction: column; gap: 24px; align-items: stretch; }
    .gauge-right { padding-top: 30px; }
    .gauge-tiles { flex-wrap: wrap; }
    .gauge-tile { flex: 1 1 40%; }
    .gauge-scale span:nth-child(even) { display: none; }
    /* strip: dots only — labels collide at percentile spacing on narrow screens */
    .strip-frame { height: 150px; }
    .pin-label { display: none; }
    .pin-row-0 { top: 6px; }
    .pin-row-1 { top: 34px; }
    .pin-row-2 { top: 62px; }
    .pin-row-3 { top: 90px; }
    .strip-labels { font-size: 9px; }
    .strip-labels span:nth-child(2) { display: none; }
  }
</style>
</head>
<body data-lens="forward">

<div class="shell">
  <div class="blobs">
    <i class="blob-1"></i><i class="blob-2"></i><i class="blob-3"></i>
    <i class="blob-4"></i><i class="blob-5"></i><i class="blob-6"></i>
  </div>

  <div class="inner">
    <!-- ═══ Masthead ═══ -->
    <header class="masthead">
      <div>
        <p class="kicker">AlphaLabX1 — Internal Research · Vol. II</p>
        <h1 class="wordmark">Valuation <em>&amp; Mood</em></h1>
        <p class="standfirst">The S&amp;P 500 and its eleven sectors, seen through two P/E lenses — and the market's mood, plotted against the price beneath it. <time>Updated __LATEST_LABEL__.</time></p>
      </div>
      <div class="masthead-side">
        <div class="lens" id="lens">
          <button data-lens="forward" class="active">Forward</button>
          <button data-lens="trailing">Trailing</button>
        </div>
        <div class="lens-note"></div>
      </div>
    </header>

    <!-- ═══ 01. Dot distribution ═══ -->
    <section class="card">
      <div class="card-head">
        <div class="card-title">
          <span class="card-num">01</span>
          <div>
            <h2>Where everyone stands today</h2>
            <p class="lede">Each dot is a sector's current P/E placed as a percentile of its own trailing five years. <strong>Right is expensive.</strong> A reading of 50 sits on the sector's own five-year median.</p>
          </div>
        </div>
        <div class="card-aside"><span class="lens-echo"></span></div>
      </div>

      <div class="view-forward">
        <div class="strip-frame" id="strip-forward" data-family="forward">
          <div class="strip-pins">__STRIP_FORWARD__</div>
          <div class="strip-labels"><span>Cheap vs own 5y</span><span>Median</span><span>Expensive vs own 5y</span></div>
        </div>
      </div>

      <div class="view-trailing">
        <div class="strip-frame" id="strip-trailing" data-family="trailing">
          <div class="strip-pins">__STRIP_TRAILING__</div>
          <div class="strip-labels"><span>Cheap vs own 5y</span><span>Median</span><span>Expensive vs own 5y</span></div>
        </div>
      </div>
    </section>

    <!-- ═══ 02. Rank table ═══ -->
    <section class="card">
      <div class="card-head">
        <div class="card-title">
          <span class="card-num">02</span>
          <div>
            <h2>Five-year rank</h2>
            <p class="lede">Sectors ordered richest → cheapest against their own history. <strong>Click any row</strong> to isolate it on the chart below.</p>
          </div>
        </div>
        <div class="card-aside"><span class="lens-echo"></span></div>
      </div>

      <div class="view-forward">
        <ul class="rank-table" data-family="forward">
          <li class="rank-head">
            <span></span><span>Sector</span><span style="text-align:right">P/E</span><span>5y percentile</span>
            <span style="text-align:right">Rank</span><span style="text-align:right">5y range</span>
          </li>
          __TABLE_FORWARD__
        </ul>
      </div>

      <div class="view-trailing">
        <ul class="rank-table" data-family="trailing">
          <li class="rank-head">
            <span></span><span>Sector</span><span style="text-align:right">P/E</span><span>5y percentile</span>
            <span style="text-align:right">Rank</span><span style="text-align:right">5y range</span>
          </li>
          __TABLE_TRAILING__
        </ul>
      </div>
    </section>

    <!-- ═══ 03. Historical chart ═══ -->
    <section class="card">
      <div class="card-head">
        <div class="card-title">
          <span class="card-num">03</span>
          <div>
            <h2>Historical path</h2>
            <p class="lede">Forward view shows 12-month analyst estimates (daily, since 2008). Trailing view uses reported TTM earnings (monthly, since 1995 for sectors — since 1871 for the index). The Y axis auto-scales to whichever window and series are visible.</p>
          </div>
        </div>
        <div class="card-aside"><span class="lens-echo"></span></div>
      </div>
      <div class="chart-controls">
        <div class="seg">
          <button data-range="all">All</button>
          <button data-range="10y">10Y</button>
          <button data-range="5y" class="active">5Y</button>
          <button data-range="3y">3Y</button>
          <button data-range="1y">1Y</button>
          <button data-range="ytd">YTD</button>
        </div>
        <div class="ctrl-group">
          <button class="obtn" id="only-index">Index</button>
          <button class="obtn" id="only-sectors">Sectors</button>
          <button class="obtn" id="show-all">Reset</button>
        </div>
      </div>
      <div class="chart-wrap"><div id="chart"></div></div>
    </section>

    <!-- ═══ 04. Fear & Greed gauge ═══ -->
    <section class="card">
      <div class="card-head">
        <div class="card-title">
          <span class="card-num">04</span>
          <div>
            <h2>Sentiment, at a glance</h2>
            <p class="lede">MacroMicro's Fear &amp; Greed composite reduces the market's mood to a single 0–100 reading. Under 25 is panicked fear; over 75 is euphoric greed.</p>
          </div>
        </div>
        <div class="card-aside">Composite · 0–100</div>
      </div>
      __GAUGE__
    </section>

    <!-- ═══ 05. F&G vs SPX chart ═══ -->
    <section class="card">
      <div class="card-head">
        <div class="card-title">
          <span class="card-num">05</span>
          <div>
            <h2>Mood against price</h2>
            <p class="lede">Sentiment on the left axis, S&amp;P 500 on the right. Bear phases bottom with fear readings below 25; tops tend to coincide with extreme-greed plateaus — not coincidence, but also not a tradable signal on its own.</p>
          </div>
        </div>
        <div class="card-aside">Dual axis · shared X</div>
      </div>
      <div class="chart-controls">
        <div class="seg">
          <button data-mood-range="all">All</button>
          <button data-mood-range="10y">10Y</button>
          <button data-mood-range="5y" class="active">5Y</button>
          <button data-mood-range="3y">3Y</button>
          <button data-mood-range="1y">1Y</button>
          <button data-mood-range="ytd">YTD</button>
        </div>
      </div>
      <div class="chart-wrap"><div id="mood-chart"></div></div>
    </section>

    <!-- ═══ 06. Valuation: price, multiple, and yield gap ═══ -->
    <section class="card">
      <div class="card-head">
        <div class="card-title">
          <span class="card-num">06</span>
          <div>
            <h2>Are we expensive?</h2>
            <p class="lede">Three stacked panels on one time axis. Top: the index. Middle: its forward P/E against the 20th–80th percentile band of the trailing five years, plus a 200-day moving average. Bottom: forward earnings yield (1 ÷ forward P/E) next to the 10-year Treasury yield, with bars showing the spread — bars near zero mean stocks no longer offer much premium over bonds.</p>
          </div>
        </div>
        <div class="card-aside">3 panels · shared X</div>
      </div>
      <div class="chart-controls">
        <div class="ctrl-group">
          <div class="seg">
            <button data-val-index="spy" class="active">SPY</button>
            <button data-val-index="qqq">QQQ</button>
          </div>
          <div class="seg">
            <button data-val-range="all">All</button>
            <button data-val-range="10y">10Y</button>
            <button data-val-range="5y" class="active">5Y</button>
            <button data-val-range="3y">3Y</button>
            <button data-val-range="1y">1Y</button>
            <button data-val-range="ytd">YTD</button>
          </div>
        </div>
      </div>
      <div class="chart-wrap"><div id="val-chart"></div></div>
    </section>

    <!-- ═══ Footer ═══ -->
    <footer>
      <div class="left">
        <span><strong>AlphaLabX1</strong> — internal research</span>
        <span class="dot">·</span>
        <span>P/E &amp; prices · Koyfin</span>
        <span class="dot">·</span>
        <span>Fear &amp; Greed · MacroMicro</span>
      </div>
      <span>as of __LATEST_ISO__ · 5-year window</span>
    </footer>
  </div>
</div>

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
    opacity: s.isIndex ? 1 : 0.85,
    hovertemplate: "<b>" + s.name + "</b>  %{y:.2f}<extra></extra>",
    visible: true,
    meta: s.id,
  }));
}

const MONO = '"Spline Sans Mono", monospace';
const BODY = '"Plus Jakarta Sans", sans-serif';
const TICK_FONT = { family: MONO, size: 10, color: "#6B7078" };

const baseLayout = {
  margin: { l: 56, r: 20, t: 10, b: 44 },
  hovermode: "x unified",
  hoverlabel: {
    font: { family: MONO, size: 11, color: "#EDEEF0" },
    bgcolor: "#16181F", bordercolor: "rgba(255,255,255,0.15)",
  },
  xaxis: {
    showgrid: false, linecolor: "rgba(255,255,255,0.12)", tickcolor: "rgba(255,255,255,0.25)",
    tickfont: TICK_FONT,
    type: "date",
  },
  yaxis: {
    gridcolor: "rgba(255,255,255,0.06)", zeroline: false,
    tickfont: TICK_FONT,
    tickcolor: "rgba(255,255,255,0.25)",
    title: { text: "P/E", font: { family: BODY, size: 11, color: "#6B7078" }, standoff: 14 },
  },
  legend: { orientation: "h", y: -0.18, font: { family: MONO, size: 10, color: "#9BA0AB" } },
  paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
  font: { family: BODY, size: 11, color: "#9BA0AB" },
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
  if (family) Plotly.restyle("chart", { opacity: family.series.map(s => s.isIndex ? 1 : 0.85) });
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
      line: { color: "#A78BFA", width: 2.4 },
      fill: "tozeroy", fillcolor: "rgba(139,124,246,0.10)",
      yaxis: "y2",
      hovertemplate: "<b>S&P 500</b>  %{y:.2f}<extra></extra>",
    },
    {
      x: fg.map(p => p[0]),
      y: fg.map(p => p[1]),
      type: "scattergl", mode: "lines",
      name: "Fear & Greed",
      line: { color: "#F87171", width: 1.3 },
      opacity: 0.8,
      yaxis: "y",
      hovertemplate: "<b>F&G</b>  %{y:.1f}<extra></extra>",
    },
  ];
  const moodLayout = Object.assign({}, baseLayout, {
    yaxis: {
      gridcolor: "rgba(255,255,255,0.06)", zeroline: false,
      tickfont: TICK_FONT,
      tickcolor: "rgba(255,255,255,0.25)",
      title: { text: "Fear & Greed", font: { family: BODY, size: 11, color: "#F87171" }, standoff: 14 },
      range: [0, 100],
      tickvals: [0, 25, 50, 75, 100],
    },
    yaxis2: {
      overlaying: "y", side: "right",
      gridcolor: "rgba(0,0,0,0)", zeroline: false,
      tickfont: TICK_FONT,
      tickcolor: "rgba(255,255,255,0.25)",
      title: { text: "S&P 500", font: { family: BODY, size: 11, color: "#A78BFA" }, standoff: 14 },
    },
    shapes: [
      { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: 25, y1: 25, line: { color: "rgba(52,211,153,0.55)", width: 1, dash: "dot" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: 75, y1: 75, line: { color: "rgba(248,113,113,0.55)", width: 1, dash: "dot" } },
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

// ═══ Section 06: Are we expensive — 3-panel valuation chart (SPY / QQQ toggle) ═══
(function renderValuationChart() {
  if (!DATA.valuation || !DATA.valuation.spy) return;

  const TITLE_FONT = { family: BODY, size: 11 };

  const xs = (pts) => pts.map(p => p[0]);
  const ys = (pts) => pts.map(p => p[1]);

  let currentIndex = "spy";
  let currentRange = "5y";

  function priceOf(idx) {
    return idx === "qqq" ? (DATA.qqq && DATA.qqq.price) || [] : DATA.spx.points;
  }
  function valOf(idx) { return DATA.valuation[idx]; }
  function priceLabel(idx) { return idx === "qqq" ? "QQQ" : "S&P 500 (SPY)"; }

  function buildTraces(idx) {
    const v = valOf(idx);
    const price = priceOf(idx);
    return [
      // ───── Panel 1: Index price (log) ─────
      {
        x: xs(price), y: ys(price),
        type: "scattergl", mode: "lines",
        name: priceLabel(idx),
        line: { color: "#A78BFA", width: 1.8 },
        yaxis: "y", xaxis: "x",
        hovertemplate: "<b>" + priceLabel(idx) + "</b> %{y:.2f}<extra></extra>",
      },
      // ───── Panel 2: forward P/E + 5Y rolling P20/P80 band + 200d SMA ─────
      {
        x: xs(v.band_p80), y: ys(v.band_p80),
        type: "scattergl", mode: "lines",
        name: "5Y P80", line: { color: "rgba(0,0,0,0)", width: 0 },
        yaxis: "y2", xaxis: "x", showlegend: false,
        hovertemplate: "P80 %{y:.2f}<extra></extra>",
      },
      {
        x: xs(v.band_p20), y: ys(v.band_p20),
        type: "scattergl", mode: "lines",
        name: "5Y P20–P80 band",
        line: { color: "rgba(0,0,0,0)", width: 0 },
        fill: "tonexty", fillcolor: "rgba(139,124,246,0.18)",
        yaxis: "y2", xaxis: "x",
        hovertemplate: "P20 %{y:.2f}<extra></extra>",
      },
      {
        x: xs(v.sma200), y: ys(v.sma200),
        type: "scattergl", mode: "lines",
        name: "200d SMA",
        line: { color: "#D66AE0", width: 1.2, dash: "dash" },
        yaxis: "y2", xaxis: "x",
        hovertemplate: "SMA200 %{y:.2f}<extra></extra>",
      },
      {
        x: xs(v.pe), y: ys(v.pe),
        type: "scattergl", mode: "lines",
        name: "Forward P/E",
        line: { color: "#5EEAD4", width: 2 },
        yaxis: "y2", xaxis: "x",
        hovertemplate: "<b>Fwd P/E</b> %{y:.2f}<extra></extra>",
      },
      // ───── Panel 3: Forward EY, 10Y yield, spread ─────
      {
        x: xs(v.us10y), y: ys(v.us10y),
        type: "scattergl", mode: "lines",
        name: "10Y Treasury",
        line: { color: "#6AA0E0", width: 1.4, dash: "dash" },
        yaxis: "y3", xaxis: "x",
        hovertemplate: "<b>10Y</b> %{y:.2f}%<extra></extra>",
      },
      {
        x: xs(v.ey), y: ys(v.ey),
        type: "scattergl", mode: "lines",
        name: "Forward EY (1÷PE)",
        line: { color: "#F0A868", width: 1.8 },
        yaxis: "y3", xaxis: "x",
        hovertemplate: "<b>Fwd EY</b> %{y:.2f}%<extra></extra>",
      },
      {
        x: xs(v.spread), y: ys(v.spread),
        type: "bar",
        name: "EY − 10Y",
        marker: { color: ys(v.spread).map(s => s >= 0 ? "rgba(52,211,153,0.45)" : "rgba(248,113,113,0.45)") },
        yaxis: "y4", xaxis: "x",
        hovertemplate: "<b>Spread</b> %{y:+.2f} pp<extra></extra>",
      },
    ];
  }

  function buildLayout(idx) {
    return {
      margin: { l: 56, r: 60, t: 16, b: 36 },
      hovermode: "x unified",
      hoverlabel: {
        font: { family: MONO, size: 11, color: "#EDEEF0" },
        bgcolor: "#16181F", bordercolor: "rgba(255,255,255,0.15)",
      },
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: BODY, size: 11, color: "#9BA0AB" },
      legend: { orientation: "h", y: -0.08, x: 0, font: { family: MONO, size: 10, color: "#9BA0AB" } },
      xaxis: {
        anchor: "y3", domain: [0, 1],
        showgrid: false, linecolor: "rgba(255,255,255,0.12)", tickcolor: "rgba(255,255,255,0.25)",
        tickfont: TICK_FONT, type: "date",
      },
      yaxis: {
        domain: [0.70, 1.0], type: "log",
        gridcolor: "rgba(255,255,255,0.06)", zeroline: false,
        tickfont: TICK_FONT, tickcolor: "rgba(255,255,255,0.25)",
        title: { text: (idx === "qqq" ? "QQQ" : "S&P 500") + " (log)", font: Object.assign({}, TITLE_FONT, { color: "#A78BFA" }), standoff: 12 },
      },
      yaxis2: {
        domain: [0.37, 0.66],
        gridcolor: "rgba(255,255,255,0.06)", zeroline: false,
        tickfont: TICK_FONT, tickcolor: "rgba(255,255,255,0.25)",
        title: { text: "Forward P/E", font: Object.assign({}, TITLE_FONT, { color: "#5EEAD4" }), standoff: 12 },
      },
      yaxis3: {
        domain: [0.0, 0.33],
        gridcolor: "rgba(255,255,255,0.06)", zeroline: false,
        tickfont: TICK_FONT, tickcolor: "rgba(255,255,255,0.25)",
        title: { text: "Yield (%)", font: Object.assign({}, TITLE_FONT, { color: "#F0A868" }), standoff: 12 },
      },
      yaxis4: {
        domain: [0.0, 0.33], overlaying: "y3", side: "right",
        showgrid: false, zeroline: true, zerolinecolor: "rgba(255,255,255,0.2)", zerolinewidth: 1,
        tickfont: TICK_FONT, tickcolor: "rgba(255,255,255,0.25)",
        title: { text: "Spread (pp)", font: Object.assign({}, TITLE_FONT, { color: "#34D399" }), standoff: 12 },
      },
    };
  }

  function applyValRange(key) {
    currentRange = key;
    const v = valOf(currentIndex);
    const price = priceOf(currentIndex);
    const now = new Date(LATEST);
    let start;
    if (key === "all") start = new Date(v.pe[0][0]);
    else if (key === "ytd") start = new Date(now.getFullYear(), 0, 1);
    else { const y = parseInt(key, 10); start = new Date(now); start.setFullYear(start.getFullYear() - y); }
    const startStr = start.toISOString().slice(0,10);
    const endStr = now.toISOString().slice(0,10);
    const startMs = start.getTime(), endMs = now.getTime();

    function rangeOf(pts, log) {
      let lo = Infinity, hi = -Infinity;
      for (let i = 0; i < pts.length; i++) {
        const t = Date.parse(pts[i][0]);
        if (t < startMs || t > endMs) continue;
        let val = pts[i][1];
        if (val == null) continue;
        if (log) { if (val <= 0) continue; val = Math.log10(val); }
        if (val < lo) lo = val;
        if (val > hi) hi = val;
      }
      if (!isFinite(lo)) return null;
      const pad = Math.max((hi - lo) * 0.08, 0.02);
      return [lo - pad, hi + pad];
    }

    const upd = {
      "xaxis.range": [startStr, endStr],
      "xaxis.autorange": false,
    };
    const r1 = rangeOf(price, true);
    if (r1) { upd["yaxis.range"] = r1; upd["yaxis.autorange"] = false; }
    const peAll = v.pe.concat(v.band_p20, v.band_p80, v.sma200);
    const r2 = rangeOf(peAll, false);
    if (r2) { upd["yaxis2.range"] = r2; upd["yaxis2.autorange"] = false; }
    const yieldAll = v.ey.concat(v.us10y);
    const r3 = rangeOf(yieldAll, false);
    if (r3) { upd["yaxis3.range"] = r3; upd["yaxis3.autorange"] = false; }
    const r4 = rangeOf(v.spread, false);
    if (r4) {
      const mag = Math.max(Math.abs(r4[0]), Math.abs(r4[1])) * 1.1;
      upd["yaxis4.range"] = [-mag, mag];
      upd["yaxis4.autorange"] = false;
    }
    Plotly.relayout("val-chart", upd);
  }

  function rerender() {
    Plotly.react("val-chart", buildTraces(currentIndex), buildLayout(currentIndex), chartConfig)
      .then(() => applyValRange(currentRange));
  }

  Plotly.newPlot("val-chart", buildTraces(currentIndex), buildLayout(currentIndex), chartConfig)
    .then(() => applyValRange("5y"));

  document.querySelectorAll("[data-val-range]").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-val-range]").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      applyValRange(btn.dataset.valRange);
    });
  });

  document.querySelectorAll("[data-val-index]").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-val-index]").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentIndex = btn.dataset.valIndex;
      rerender();
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
