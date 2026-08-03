"""Generate the self-contained dashboard: forward + trailing P/E (with lens
toggle), plus a Fear & Greed gauge and an F&G / SPX dual-axis chart."""

from __future__ import annotations

import csv
import json
import statistics
from datetime import date, datetime, timedelta
from html import escape as html_escape
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


def pct_asof(points, asof: date):
    """5y percentile rank of the series as it stood on `asof` (using only
    points dated on or before it), or None if no data reaches back that far."""
    asof_str = asof.isoformat()
    for i in range(len(points) - 1, -1, -1):
        if points[i][0] <= asof_str:
            five = compute_5y(points[: i + 1])
            return five["rank"] if five else None
    return None


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


def render_table(rows, growth=None):
    growth = growth or {}
    parts = []
    for i, r in enumerate(rows, 1):
        pct = r["rank_5y"]
        heat = "hot" if pct >= 85 else "cold" if pct <= 45 else "mid"
        index_cls = " is-index" if r["isIndex"] else ""
        definition, holdings_html = _holdings_html(r["id"])
        g = growth.get(r["id"])
        if g is None:
            growth_html = '<span class="grw mono na">–</span>'
        else:
            g_cls = "pos" if g > 0.005 else "neg" if g < -0.005 else "na"
            growth_html = (
                f'<span class="grw mono {g_cls}" title="Trailing P/E ÷ forward P/E − 1: '
                f'the next-12-month earnings growth analyst estimates imply">{g:+.0%}</span>'
            )
        parts.append(f'''
<li class="row heat-{heat}{index_cls}" data-id="{r["id"]}">
  <span class="rank-num">{i:02d}</span>
  <span class="name-col">
    <span class="swatch" style="background:{r["color"]}"></span>
    <span class="name">{r["name"]}</span>
    <span class="ticker">{r["ticker"]}</span>
  </span>
  <span class="val mono">{r["latest"]:.2f}</span>
  {growth_html}
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


def render_movers(rows):
    """Sector rows sorted by 1-week percentile move, richer-drifting first,
    with a diverging bar centered on zero."""
    usable = [r for r in rows if r.get("d1w") is not None]
    if not usable:
        return ""
    usable = sorted(usable, key=lambda r: -r["d1w"])
    scale = max(max(abs(r["d1w"]) for r in usable), 1.0)
    parts = []
    for r in usable:
        d1w, d1m = r["d1w"], r["d1m"]
        cls = "up" if d1w > 0.5 else "dn" if d1w < -0.5 else "flat"
        width = abs(d1w) / scale * 50
        anchor = "left:50%" if d1w >= 0 else "right:50%"
        bar_cls = "up" if d1w >= 0 else "dn"
        d1m_html = (
            f'<span class="mv-d mv-d1m mono {"up" if d1m > 0.5 else "dn" if d1m < -0.5 else "flat"}">{d1m:+.0f}</span>'
            if d1m is not None else '<span class="mv-d mv-d1m mono flat">–</span>'
        )
        parts.append(
            f'<li class="mv-row" data-id="{r["id"]}">'
            f'<span class="name-col">'
            f'<span class="swatch" style="background:{r["color"]}"></span>'
            f'<span class="name">{r["name"]}</span>'
            f'<span class="ticker">{r["ticker"]}</span>'
            f'</span>'
            f'<span class="mv-now mono">{r["rank_5y"]:.0f}</span>'
            f'<span class="mv-bar"><span class="mv-fill {bar_cls}" style="{anchor};width:{width:.1f}%"></span></span>'
            f'<span class="mv-d mono {cls}">{d1w:+.0f}</span>'
            f'{d1m_html}'
            f'</li>'
        )
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
        latest_d = date.fromisoformat(latest_date_str)
        p1w = pct_asof(points, latest_d - timedelta(days=7))
        p1m = pct_asof(points, latest_d - timedelta(days=30))
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
            "d1w": five["rank"] - p1w if p1w is not None else None,
            "d1m": five["rank"] - p1m if p1m is not None else None,
        })
    summary_rows.sort(key=lambda r: -r["rank_5y"])
    strip_rows = assign_rows(sorted(summary_rows, key=lambda r: r["rank_5y"]))
    latest_date_str = max((r["latest_date"] for r in summary_rows), default="")
    return {
        "series": series_payload,
        "summary": summary_rows,
        "strip_html": render_strip(strip_rows),
        "movers_html": render_movers(summary_rows),
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


def fg_stats_payload(fg_points, spx_points):
    """Forward SPX price returns conditioned on the F&G reading of the day.
    Horizons are counted in trading sessions on the price series (63/126/252
    ≈ 3/6/12 months). Overlapping windows — descriptive stats, not a backtest."""
    if not fg_points or not spx_points:
        return None
    from bisect import bisect_right
    pdates = [d for d, _ in spx_points]
    pvals = [v for _, v in spx_points]
    horizons = [("3M", 63), ("6M", 126), ("12M", 252)]
    buckets = [
        ("fear", "Extreme fear", "F&G below 25", lambda v: v < 25),
        ("greed", "Extreme greed", "F&G above 75", lambda v: v > 75),
        ("all", "Any reading", "every day since 2021", lambda v: True),
    ]
    rets = {b: {h: [] for h, _ in horizons} for b, *_ in buckets}
    days = {b: 0 for b, *_ in buckets}
    for d, v in fg_points:
        i = bisect_right(pdates, d) - 1
        if i < 0 or (date.fromisoformat(d) - date.fromisoformat(pdates[i])).days > 7:
            continue
        for key, _, _, cond in buckets:
            if cond(v):
                days[key] += 1
        for h, n in horizons:
            j = i + n
            if j >= len(pvals):
                continue
            r = pvals[j] / pvals[i] - 1
            for key, _, _, cond in buckets:
                if cond(v):
                    rets[key][h].append(r)
    out = []
    for key, label, sub, _ in buckets:
        cells = []
        for h, _ in horizons:
            rr = rets[key][h]
            cells.append({
                "h": h,
                "n": len(rr),
                "median": statistics.median(rr) * 100 if rr else None,
                "win": sum(1 for x in rr if x > 0) / len(rr) * 100 if rr else None,
            })
        out.append({"key": key, "label": label, "sub": sub, "days": days[key], "cells": cells})
    return {"rows": out, "since": fg_points[0][0]}


def render_fg_stats(stats):
    if not stats:
        return "<p style='color:var(--dim)'>Not enough data for conditional stats.</p>"
    head = "".join(f"<th>{h} later</th>" for h in ("3M", "6M", "12M"))
    body = []
    for row in stats["rows"]:
        cells = []
        for c in row["cells"]:
            if c["median"] is None:
                cells.append('<td><span class="sig-med">–</span></td>')
                continue
            m_cls = "pos" if c["median"] > 0 else "neg"
            cells.append(
                f'<td><span class="sig-med {m_cls}">{c["median"]:+.1f}%</span>'
                f'<span class="sig-win">{c["win"]:.0f}% up · n={c["n"]}</span></td>'
            )
        base_cls = ' class="sig-base"' if row["key"] == "all" else ""
        body.append(
            f'<tr{base_cls}><td>{row["label"]}'
            f'<span class="sig-cond-sub">{row["sub"]}</span></td>'
            f'<td><span class="sig-med">{row["days"]}</span>'
            f'<span class="sig-win">days</span></td>'
            f'{"".join(cells)}</tr>'
        )
    return (
        '<div class="sig-wrap"><table class="sig-table">'
        f'<thead><tr><th>Condition</th><th>Sample</th>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def render_commentary(latest_date_str: str) -> str:
    """The generated daily read, written by commentary.py into
    data/commentary.json. Absent file, unreadable file, or one describing an
    older build all render nothing — the masthead simply loses a block rather
    than showing yesterday's take under today's date."""
    path = DATA / "commentary.json"
    if not path.exists():
        return ""
    try:
        c = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return ""
    text = (c.get("text") or "").strip()
    if not text or c.get("as_of") != latest_date_str:
        return ""
    model = (c.get("model") or "").split("/")[-1]
    attr = f"Written from today's readings by {model}. Figures checked against the data." if model else ""
    # Model output is untrusted text — escape before it reaches the page.
    return (
        '<div class="read">'
        '<p class="read-kicker">Today\'s read</p>'
        f'<p class="read-body">{html_escape(text)}</p>'
        f'<p class="read-attr">{html_escape(attr)}</p>'
        '</div>'
    )


def _families():
    """Forward and trailing summaries keyed by series id, plus the build date."""
    raw = json.loads((DATA / "raw.json").read_text())
    out = {}
    for lens in ("forward", "trailing"):
        src = raw.get(lens, {})
        pts = {sid: _round_series(src[str(sid)], 4) for sid in SERIES if str(sid) in src}
        fam = build_family_payload(pts) if pts else None
        out[lens] = fam
    return out


def table_brief() -> tuple[str, set[str], str]:
    """Brief covering both lenses for the section-01 findings.

    Every figure the model might reasonably cite — including derived ones like
    the forward-vs-trailing percentile gap and the multiple compression — is
    computed here and listed. The model is told to quote, never to calculate,
    because a number it computes itself cannot be checked against the data.
    """
    fams = _families()
    fwd, trl = fams["forward"], fams["trailing"]
    if not fwd:
        return "", set(), ""
    trl_by_id = {r["id"]: r for r in (trl["summary"] if trl else [])}

    allowed: set[str] = set()

    def num(v, nd=0):
        s = f"{v:.{nd}f}"
        allowed.add(s.lstrip("+-"))
        return s

    lines = [
        f"Date: {fwd['latest_date']}",
        "",
        "Per sector — forward lens (12-month analyst estimates) and trailing lens",
        "(reported TTM earnings). Percentile = rank within that sector's own",
        "trailing five years, 0 cheapest to 100 richest.",
        "",
    ]
    for r in fwd["summary"]:
        t = trl_by_id.get(r["id"])
        tk = SECTOR_TICKERS[r["id"]]
        seg = [
            f"{r['name']} ({tk}):",
            f"  forward P/E {num(r['latest'], 2)}, percentile {num(r['rank_5y'])}",
        ]
        if r.get("d1w") is not None:
            seg.append(f"  forward percentile change: 1 week {num(abs(r['d1w']))} points "
                       f"{'richer' if r['d1w'] > 0 else 'cheaper'}")
        if r.get("d1m") is not None:
            seg.append(f"  forward percentile change: 1 month {num(abs(r['d1m']))} points "
                       f"{'richer' if r['d1m'] > 0 else 'cheaper'}")
        if t:
            seg.append(f"  trailing P/E {num(t['latest'], 2)}, percentile {num(t['rank_5y'])}")
            gap = r["rank_5y"] - t["rank_5y"]
            seg.append(f"  percentile gap between lenses: {num(abs(gap))} points "
                       f"({'forward richer' if gap > 0 else 'trailing richer'})")
            if t["latest"] and r["latest"]:
                growth = (t["latest"] / r["latest"] - 1) * 100
                seg.append(f"  implied next-12m earnings growth: {num(abs(growth))} percent"
                           f"{'' if growth >= 0 else ' decline'}")
                comp = (1 - r["latest"] / t["latest"]) * 100
                if comp > 0:
                    seg.append(f"  multiple compression forward vs trailing: {num(comp)} percent")
        lines.append("\n".join(seg))

    fg = _load_csv_points(DATA / "fear_greed.csv")
    if fg:
        lines += ["", f"Fear & Greed: {num(fg[-1][1])} ({_fg_word(fg[-1][1])})"]
    return "\n".join(lines), allowed, fwd["latest_date"]


def ask_chips(rows) -> tuple[str, str]:
    """Suggested questions and the input placeholder. Two are evergreen; the
    rest name whichever sectors are actually at the extremes today, so the
    panel opens on something worth asking rather than a blank prompt."""
    if not rows:
        return "", "Ask about any sector in the table"
    richest = rows[0]["name"]
    cheapest = rows[-1]["name"]
    qs = [
        "What's the cleanest cheap-vs-expensive pair?",
        "Where do the two lenses disagree most?",
        f"What would change my mind on {richest}?",
        f"Is {cheapest} cheap or just falling?",
    ]
    chips = "".join(
        f'<button type="button" data-q="{html_escape(q, quote=True)}">{html_escape(q)}</button>'
        for q in qs
    )
    placeholder = f"Why is {richest} at the {rows[0]['rank_5y']:.0f}th percentile?"
    return chips, placeholder


def render_insights(latest_date_str: str) -> str:
    """Section 01's machine read — findings written at build time by
    commentary.py. Same staleness contract as the masthead read: anything
    missing, malformed, or dated to an older build renders nothing."""
    path = DATA / "insights.json"
    if not path.exists():
        return ""
    try:
        d = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return ""
    findings = d.get("findings") or []
    if not findings or d.get("as_of") != latest_date_str:
        return ""
    model = (d.get("model") or "").split("/")[-1]
    items = "".join(
        f'<div class="finding">'
        f'<h3>{html_escape(str(f.get("title", "")))}</h3>'
        f'<p>{html_escape(str(f.get("body", "")))}</p>'
        f'</div>'
        for f in findings
    )
    attr = f"Written from both P/E tables by {model}. Figures are the page's own."
    return (
        '<div class="findings">'
        f'{items}'
        f'<p class="findings-attr">{html_escape(attr)}</p>'
        '</div>'
    )


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

    # Implied next-12M earnings growth per sector: trailing P/E ÷ forward P/E − 1.
    growth = {}
    if trailing:
        fwd_latest = {r["id"]: r["latest"] for r in forward["summary"]}
        for r in trailing["summary"]:
            f = fwd_latest.get(r["id"])
            if f:
                growth[r["id"]] = r["latest"] / f - 1

    # Sentiment: Fear & Greed + SPX price.
    fg_points = _round_series(_load_csv_points(DATA / "fear_greed.csv"), 2)
    spx_points = _round_series(_load_csv_points(DATA / "spx_price.csv"), 2)
    us10y_points = _round_series(_load_csv_points(DATA / "us10y.csv"), 3)
    gauge = gauge_payload(fg_points)
    fg_stats = fg_stats_payload(fg_points, spx_points)

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

    # The exact brief the build-time model read, handed to the browser so the
    # "ask the table" panel is grounded in the same text — what the page shows
    # and what the model sees can never drift apart.
    brief_text, _, _ = table_brief()
    chips_html, placeholder = ask_chips(forward["summary"])

    payload = json.dumps({
        "brief": brief_text,
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
        .replace("__STRIP_TRAILING__", trailing["strip_html"] if trailing else "")
        .replace("__TABLE_FORWARD__", render_table(forward["summary"], growth))
        .replace("__TABLE_TRAILING__", render_table(trailing["summary"], growth) if trailing else "")
        .replace("__MOVERS_FORWARD__", forward["movers_html"])
        .replace("__MOVERS_TRAILING__", trailing["movers_html"] if trailing else "")
        .replace("__FG_STATS__", render_fg_stats(fg_stats))
        .replace("__COMMENTARY__", render_commentary(forward["latest_date"]))
        .replace("__INSIGHTS__", render_insights(forward["latest_date"]))
        .replace("__ASK_CHIPS__", chips_html)
        .replace("__ASK_PLACEHOLDER__", html_escape(placeholder, quote=True))
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

  /* ── Generated daily read. Deliberately unlike the authored standfirst
        above it: rule, mono kicker, attribution. Machine-written text should
        never be able to pass for editorial copy. ── */
  .read {
    margin: 18px 0 0;
    padding: 2px 0 2px 16px;
    border-left: 2px solid rgba(139,124,246,0.5);
    max-width: 640px;
    animation: rise .6s ease-out .45s both;
  }
  .read-kicker {
    font-family: var(--font-mono);
    font-size: 9.5px; letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--accent-hi);
    margin: 0 0 6px;
  }
  .read-body { margin: 0; font-size: 15px; line-height: 1.55; color: var(--text-2); }
  .read-attr {
    margin: 8px 0 0;
    font-family: var(--font-mono); font-size: 10px;
    letter-spacing: 0.04em; color: var(--dimmer);
  }

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
  body[data-lens="trailing"] .lens-echo::before { content: "trailing · daily"; }

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
    grid-template-columns: 34px 2.2fr 84px 74px 1.9fr 54px 120px;
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

  /* ─────────────────── Section 01 AI block ─────────────────── */
  .ai-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 30px;
    margin-top: 26px; padding-top: 24px;
    border-top: 1px solid var(--hair);
  }
  .ai-col { min-width: 0; display: flex; flex-direction: column; }
  .ai-col-head {
    display: flex; justify-content: space-between; align-items: baseline;
    gap: 12px; margin-bottom: 16px; min-height: 20px;
  }
  .ai-label {
    font-family: var(--font-mono);
    font-size: 9.5px; letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--accent-hi);
  }
  .ai-note {
    font-family: var(--font-mono); font-size: 10px;
    color: var(--dimmer); letter-spacing: 0.04em;
  }

  .findings { display: flex; flex-direction: column; gap: 18px; }
  .finding { padding-left: 14px; border-left: 2px solid rgba(139,124,246,0.5); }
  .finding h3 {
    margin: 0 0 6px; font-size: 14.5px; font-weight: 700;
    letter-spacing: -0.005em; color: var(--text);
  }
  .finding p { margin: 0; font-size: 13.5px; line-height: 1.55; color: var(--soft); }
  .findings-attr {
    margin: 4px 0 0;
    font-family: var(--font-mono); font-size: 10px;
    letter-spacing: 0.04em; color: var(--dimmer);
  }

  /* Ask panel */
  .ask {
    background: var(--inset);
    border: 1px solid var(--inset-border);
    border-radius: 14px;
    padding: 18px 20px 18px;
  }
  .ask-body {
    flex: 1; min-height: 150px; max-height: 340px; overflow-y: auto;
    display: flex; flex-direction: column; gap: 12px;
    margin-bottom: 14px;
  }
  .ask-intro, .ask-answer, .ask-q {
    font-size: 13.5px; line-height: 1.55;
    border-radius: 12px; padding: 13px 15px;
  }
  .ask-intro {
    color: var(--soft);
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.05);
  }
  .ask-q {
    align-self: flex-end; max-width: 85%;
    background: rgba(139,124,246,0.18);
    border: 1px solid rgba(139,124,246,0.28);
    color: var(--text);
    font-weight: 500;
  }
  .ask-answer {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.06);
    color: var(--text-2);
  }
  .ask-answer.err { border-color: rgba(248,113,113,0.35); color: #F8A6A6; }
  .ask-answer p { margin: 0 0 9px; }
  .ask-answer p:last-child { margin-bottom: 0; }
  .ask-thinking { display: inline-flex; gap: 4px; align-items: center; }
  .ask-thinking i {
    width: 5px; height: 5px; border-radius: 50%;
    background: var(--accent); display: inline-block;
    animation: askPulse 1.1s ease-in-out infinite;
  }
  .ask-thinking i:nth-child(2) { animation-delay: .16s; }
  .ask-thinking i:nth-child(3) { animation-delay: .32s; }
  @keyframes askPulse { 0%,100% { opacity: .25; } 50% { opacity: 1; } }

  /* Opt-in web lookup: the fast answer is data-only, this goes to the web. */
  .ask-why {
    margin-top: 11px; padding-top: 10px;
    border-top: 1px solid rgba(255,255,255,0.07);
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  }
  .ask-why button {
    border: 1px solid rgba(139,124,246,0.4); background: transparent;
    color: var(--accent-hi);
    padding: 5px 11px; border-radius: 8px;
    font-family: var(--font-mono); font-size: 10.5px; font-weight: 600;
    cursor: pointer; transition: background .12s, color .12s;
  }
  .ask-why button:hover { background: rgba(139,124,246,0.16); color: #FFF; }
  .ask-why span { font-family: var(--font-mono); font-size: 10px; color: var(--dimmer); }
  .ask-cites { margin: 10px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 5px; }
  .ask-cites li { font-size: 11.5px; line-height: 1.4; }
  .ask-cites a { color: var(--accent-hi); }
  .ask-src-label {
    font-family: var(--font-mono); font-size: 9px;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--dimmer); margin: 12px 0 0;
  }

  .ask-chips { display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 12px; }
  .ask-chips button {
    border: 1px solid rgba(255,255,255,0.1); background: rgba(255,255,255,0.02);
    color: var(--lede);
    padding: 6px 11px; border-radius: 8px;
    font-family: var(--font-mono); font-size: 10.5px;
    cursor: pointer; text-align: left;
    transition: border-color .12s, color .12s;
  }
  .ask-chips button:hover { border-color: var(--accent); color: var(--text); }

  .ask-form { display: flex; gap: 8px; }
  .ask-form input {
    flex: 1; min-width: 0;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 10px;
    padding: 10px 13px;
    color: var(--text);
    font-family: var(--font-body); font-size: 13.5px;
  }
  .ask-form input::placeholder { color: var(--dimmer); }
  .ask-form input:focus { outline: none; border-color: var(--accent); }
  .ask-form button {
    flex: 0 0 auto;
    background: linear-gradient(120deg, #8B7CF6, #6C5CE7);
    border: 0; border-radius: 10px;
    color: #FFF; font-family: var(--font-body); font-size: 13px; font-weight: 700;
    padding: 10px 20px; cursor: pointer;
  }
  .ask-form button:disabled { opacity: .5; cursor: default; }

  /* ─────────────────── Movers ─────────────────── */
  .mv-table { list-style: none; margin: 12px 0 0; padding: 0; }
  .mv-head, .mv-row {
    display: grid;
    grid-template-columns: 2.2fr 54px 1.9fr 74px 74px;
    gap: 16px;
    align-items: center;
  }
  .mv-head {
    padding: 0 8px 10px;
    font-family: var(--font-mono);
    font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--dim);
    border-bottom: 1px solid var(--hair);
  }
  .mv-row {
    padding: 10px 8px;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    border-radius: 10px;
    cursor: pointer;
    animation: rowIn 0.45s ease-out both;
    transition: background .15s;
  }
  .mv-row:hover { background: rgba(255,255,255,0.04); }
  .mv-row:last-child { border-bottom: 0; }
  .mv-now { font-size: 13px; text-align: right; color: var(--soft); font-variant-numeric: tabular-nums; }
  .mv-bar { position: relative; height: 14px; display: block; }
  .mv-bar::before {
    content: ""; position: absolute; left: 0; right: 0; top: 6px;
    height: 2px; border-radius: 2px; background: rgba(255,255,255,0.08);
  }
  .mv-bar::after {
    content: ""; position: absolute; left: 50%; top: 1px; bottom: 1px; width: 1px;
    background: rgba(255,255,255,0.22);
  }
  .mv-fill { position: absolute; top: 5px; height: 4px; border-radius: 2px; }
  .mv-fill.up { background: linear-gradient(90deg, #FB9B8B, #F87171); }
  .mv-fill.dn { background: linear-gradient(90deg, #34D399, #5EEAB4); }
  .mv-d { font-size: 15px; font-weight: 700; text-align: right; font-variant-numeric: tabular-nums; }
  .mv-d.up { color: var(--hot); }
  .mv-d.dn { color: var(--cold); }
  .mv-d.flat { color: var(--dim); }
  .mv-d1m { font-size: 12.5px; font-weight: 600; }

  /* ─────────────────── Implied-growth column ─────────────────── */
  .grw { font-size: 13px; font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }
  .grw.pos { color: var(--cold); }
  .grw.neg { color: var(--hot); }
  .grw.na { color: var(--dimmer); }

  /* ─────────────────── Conditional-returns table ─────────────────── */
  .sig-wrap { overflow-x: auto; margin-top: 16px; }
  .sig-table { width: 100%; border-collapse: collapse; font-family: var(--font-mono); min-width: 540px; }
  .sig-table th {
    text-align: right; padding: 0 10px 10px;
    font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--dim); font-weight: 600;
    border-bottom: 1px solid var(--hair);
  }
  .sig-table th:first-child { text-align: left; }
  .sig-table td {
    padding: 13px 10px; text-align: right;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    font-size: 14px; color: var(--text-2);
    font-variant-numeric: tabular-nums;
    vertical-align: top;
  }
  .sig-table tr:last-child td { border-bottom: 0; }
  .sig-table td:first-child {
    text-align: left; font-family: var(--font-body);
    font-weight: 600; font-size: 14px; color: var(--text-2);
  }
  .sig-cond-sub { display: block; font-family: var(--font-mono); font-size: 10.5px; font-weight: 400; color: var(--dim); margin-top: 3px; }
  .sig-med { font-weight: 700; }
  .sig-med.pos { color: var(--cold); }
  .sig-med.neg { color: var(--hot); }
  .sig-win { display: block; font-size: 10.5px; color: var(--dim); margin-top: 3px; }
  .sig-base td { opacity: 0.75; }

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
    .rank-head > *:nth-child(4), .row > *:nth-child(4),
    .rank-head > *:nth-child(7), .row > *:nth-child(7) { display: none; }
    .ai-grid { grid-template-columns: 1fr; gap: 24px; }
    .ask { padding: 16px 14px; }
    .ask-body { max-height: none; }
    .ask-chips button { font-size: 10px; }
    .mv-head, .mv-row { grid-template-columns: 1.6fr 1fr 48px; gap: 10px; }
    .mv-head > *:nth-child(2), .mv-row > *:nth-child(2),
    .mv-head > *:nth-child(5), .mv-row > *:nth-child(5) { display: none; }
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
        __COMMENTARY__
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

      <div class="ai-grid">
        <div class="ai-col">
          <div class="ai-col-head">
            <span class="ai-label">Machine read · both lenses</span>
          </div>
          __INSIGHTS__
        </div>

        <div class="ai-col ask" id="ask">
          <div class="ai-col-head">
            <span class="ai-label">Ask the table</span>
            <span class="ai-note">grounded in this page's data</span>
          </div>
          <div class="ask-body" id="ask-body">
            <div class="ask-intro" id="ask-intro">
              I've read both P/E tables — 12 sectors, current multiples, five-year
              percentiles and one-week / one-month drift. Ask me anything about what's
              rich, what's cheap, and where the two lenses disagree.
            </div>
          </div>
          <div class="ask-chips" id="ask-chips">__ASK_CHIPS__</div>
          <form class="ask-form" id="ask-form">
            <input id="ask-input" type="text" autocomplete="off"
                   placeholder="__ASK_PLACEHOLDER__" aria-label="Ask about the table">
            <button type="submit" id="ask-send">Ask</button>
          </form>
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
            <span></span><span>Sector</span><span style="text-align:right">P/E</span>
            <span style="text-align:right" title="Trailing P/E ÷ forward P/E − 1: the next-12-month earnings growth analyst estimates imply">Impl. growth</span>
            <span>5y percentile</span>
            <span style="text-align:right">Rank</span><span style="text-align:right">5y range</span>
          </li>
          __TABLE_FORWARD__
        </ul>
      </div>

      <div class="view-trailing">
        <ul class="rank-table" data-family="trailing">
          <li class="rank-head">
            <span></span><span>Sector</span><span style="text-align:right">P/E</span>
            <span style="text-align:right" title="Trailing P/E ÷ forward P/E − 1: the next-12-month earnings growth analyst estimates imply">Impl. growth</span>
            <span>5y percentile</span>
            <span style="text-align:right">Rank</span><span style="text-align:right">5y range</span>
          </li>
          __TABLE_TRAILING__
        </ul>
      </div>
    </section>

    <!-- ═══ 03. Movers ═══ -->
    <section class="card">
      <div class="card-head">
        <div class="card-title">
          <span class="card-num">03</span>
          <div>
            <h2>What moved</h2>
            <p class="lede">Each sector's five-year percentile against where it stood one week and one month ago, biggest richward drift first. <strong>Red grows right — getting richer;</strong> green grows left — getting cheaper.</p>
          </div>
        </div>
        <div class="card-aside"><span class="lens-echo"></span></div>
      </div>

      <div class="view-forward">
        <ul class="mv-table">
          <li class="mv-head">
            <span>Sector</span><span style="text-align:right">Now</span><span>1w swing</span>
            <span style="text-align:right">Δ 1w</span><span style="text-align:right">Δ 1m</span>
          </li>
          __MOVERS_FORWARD__
        </ul>
      </div>

      <div class="view-trailing">
        <ul class="mv-table">
          <li class="mv-head">
            <span>Sector</span><span style="text-align:right">Now</span><span>1w swing</span>
            <span style="text-align:right">Δ 1w</span><span style="text-align:right">Δ 1m</span>
          </li>
          __MOVERS_TRAILING__
        </ul>
      </div>
    </section>

    <!-- ═══ 04. Historical chart ═══ -->
    <section class="card">
      <div class="card-head">
        <div class="card-title">
          <span class="card-num">04</span>
          <div>
            <h2>Historical path</h2>
            <p class="lede">Forward view shows 12-month analyst estimates; trailing view uses reported TTM earnings — both daily since 2003 (Real Estate 2016, Communication Services 2018). The percentile view replots every series as its rolling five-year rank, 0–100. The Y axis auto-scales to whichever window and series are visible.</p>
          </div>
        </div>
        <div class="card-aside"><span class="lens-echo"></span></div>
      </div>
      <div class="chart-controls">
        <div class="ctrl-group">
        <div class="seg">
          <button data-view="pe" class="active">P/E</button>
          <button data-view="pct">5y percentile</button>
        </div>
        <div class="seg">
          <button data-range="all">All</button>
          <button data-range="10y">10Y</button>
          <button data-range="5y" class="active">5Y</button>
          <button data-range="3y">3Y</button>
          <button data-range="1y">1Y</button>
          <button data-range="ytd">YTD</button>
        </div>
        </div>
        <div class="ctrl-group">
          <button class="obtn" id="only-index">Index</button>
          <button class="obtn" id="only-sectors">Sectors</button>
          <button class="obtn" id="show-all">Reset</button>
        </div>
      </div>
      <div class="chart-wrap"><div id="chart"></div></div>
    </section>

    <!-- ═══ 05. Fear & Greed gauge ═══ -->
    <section class="card">
      <div class="card-head">
        <div class="card-title">
          <span class="card-num">05</span>
          <div>
            <h2>Sentiment, at a glance</h2>
            <p class="lede">MacroMicro's Fear &amp; Greed composite reduces the market's mood to a single 0–100 reading. Under 25 is panicked fear; over 75 is euphoric greed.</p>
          </div>
        </div>
        <div class="card-aside">Composite · 0–100</div>
      </div>
      __GAUGE__
    </section>

    <!-- ═══ 06. F&G vs SPX chart ═══ -->
    <section class="card">
      <div class="card-head">
        <div class="card-title">
          <span class="card-num">06</span>
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

    <!-- ═══ 07. F&G conditional forward returns ═══ -->
    <section class="card">
      <div class="card-head">
        <div class="card-title">
          <span class="card-num">07</span>
          <div>
            <h2>Has the mood meant anything?</h2>
            <p class="lede">S&amp;P 500 price return (SPY, ex-dividends) 3, 6, and 12 months after each daily Fear &amp; Greed reading. <strong>Descriptive, not a strategy:</strong> windows overlap heavily, the sample starts in 2021 and spans a single cycle — one bear market, one long bull run.</p>
          </div>
        </div>
        <div class="card-aside">Median · % positive</div>
      </div>
      __FG_STATS__
    </section>

    <!-- ═══ 08. Valuation: price, multiple, and yield gap ═══ -->
    <section class="card">
      <div class="card-head">
        <div class="card-title">
          <span class="card-num">08</span>
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

// Rolling 5y percentile of each point within its own trailing window,
// mirroring compute_5y in build_html.py. Lazy — computed on first use of the
// percentile view, then cached on the series object.
function computePctSeries(s) {
  if (s._pct) return s._pct;
  const pts = s.points, ts = s._t;
  const WINDOW = 1825 * 86400e3, MIN_SPAN = 365 * 86400e3;
  const win = [];   // sorted window values
  const idx = [];   // window point indices, oldest first
  const out = [];
  for (let i = 0; i < pts.length; i++) {
    const v = pts[i][1];
    let lo = 0, hi = win.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (win[m] <= v) lo = m + 1; else hi = m; }
    win.splice(lo, 0, v);
    idx.push(i);
    const cutoff = ts[i] - WINDOW;
    while (ts[idx[0]] < cutoff) {
      const ov = pts[idx.shift()][1];
      let l = 0, h = win.length;
      while (l < h) { const m = (l + h) >> 1; if (win[m] < ov) l = m + 1; else h = m; }
      win.splice(l, 1);
    }
    if (ts[i] - ts[idx[0]] < MIN_SPAN) continue;
    let le = 0, he = win.length;
    while (le < he) { const m = (le + he) >> 1; if (win[m] <= v) le = m + 1; else he = m; }
    out.push([pts[i][0], Math.round(le / win.length * 1000) / 10]);
  }
  s._pct = out;
  return out;
}

function makeTraces(family) {
  const pctView = chartView === "pct";
  return family.series.map(s => {
    const pts = pctView ? computePctSeries(s) : s.points;
    return {
      x: pts.map(p => p[0]),
      y: pts.map(p => p[1]),
      type: "scattergl",
      mode: "lines",
      name: s.name,
      line: { color: s.color, width: s.isIndex ? 2.6 : 1.4 },
      opacity: s.isIndex ? 1 : 0.85,
      hovertemplate: "<b>" + s.name + "</b>  %{y:" + (pctView ? ".1f" : ".2f") + "}<extra></extra>",
      visible: true,
      meta: s.id,
    };
  });
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

// ═══ Section 04 chart with lens / view switching ═══
let currentLens = "forward";
let currentRange = "5y";
let chartView = "pe";   // "pe" | "pct"
let soloId = null;

function yRangeForWindow(startMs, endMs) {
  if (chartView === "pct") return null;  // pct view keeps a fixed 0-100 axis
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
  if (!family) return Promise.resolve();
  const traces = makeTraces(family);
  const layout = JSON.parse(JSON.stringify(baseLayout));
  if (chartView === "pct") {
    layout.yaxis.title.text = "5y percentile";
    layout.yaxis.range = [0, 102];
    layout.yaxis.autorange = false;
    layout.yaxis.tickvals = [0, 25, 50, 75, 100];
    layout.shapes = [
      { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: 50, y1: 50,
        line: { color: "rgba(255,255,255,0.18)", width: 1, dash: "dot" } },
    ];
  }
  return Plotly.react("chart", traces, layout, chartConfig).then(() => applyRange(currentRange));
}

function applySolo() {
  if (soloId == null) return;
  const family = DATA[currentLens];
  if (!family) return;
  const idx = family.series.findIndex(s => s.id === soloId);
  if (idx < 0) { soloId = null; return; }
  const vis = family.series.map((_, i) => i === idx ? true : "legendonly");
  Plotly.restyle("chart", { visible: vis }).then(rescaleY);
}

// ═══ URL hash state: #lens=trailing&view=pct&range=3y&solo=20523 ═══
const VALID_RANGES = ["all", "10y", "5y", "3y", "1y", "ytd"];
function updateHash() {
  const parts = [];
  if (currentLens !== "forward") parts.push("lens=" + currentLens);
  if (chartView !== "pe") parts.push("view=" + chartView);
  if (currentRange !== "5y") parts.push("range=" + currentRange);
  if (soloId != null) parts.push("solo=" + soloId);
  history.replaceState(null, "", parts.length ? "#" + parts.join("&") : location.pathname + location.search);
}
(function initFromHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  if (h.get("lens") === "trailing" && DATA.trailing) {
    currentLens = "trailing";
    document.body.setAttribute("data-lens", "trailing");
    document.querySelectorAll(".lens button").forEach(b => b.classList.toggle("active", b.dataset.lens === "trailing"));
  }
  if (h.get("view") === "pct") {
    chartView = "pct";
    document.querySelectorAll("[data-view]").forEach(b => b.classList.toggle("active", b.dataset.view === "pct"));
  }
  const r = h.get("range");
  if (r && VALID_RANGES.includes(r)) {
    currentRange = r;
    document.querySelectorAll("[data-range]").forEach(b => b.classList.toggle("active", b.dataset.range === r));
  }
  const solo = parseInt(h.get("solo") || "", 10);
  if (!isNaN(solo)) soloId = solo;
})();

renderChart().then(applySolo);

document.querySelectorAll("[data-view]").forEach(btn => {
  btn.addEventListener("click", () => {
    if (btn.dataset.view === chartView) return;
    chartView = btn.dataset.view;
    document.querySelectorAll("[data-view]").forEach(b => b.classList.toggle("active", b === btn));
    renderChart().then(applySolo);
    updateHash();
  });
});

function rescaleY() {
  if (chartView === "pct") return;
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
    updateHash();
  });
});

function setVisible(fn) {
  const family = DATA[currentLens];
  if (!family) return;
  const vis = family.series.map(s => fn(s) ? true : "legendonly");
  Plotly.restyle("chart", { visible: vis }).then(rescaleY);
}
document.getElementById("only-index").addEventListener("click", () => { soloId = null; updateHash(); setVisible(s => s.isIndex); });
document.getElementById("only-sectors").addEventListener("click", () => { soloId = null; updateHash(); setVisible(s => !s.isIndex); });
document.getElementById("show-all").addEventListener("click", () => { soloId = null; updateHash(); setVisible(() => true); });

// ═══ Lens toggle ═══
document.querySelectorAll(".lens button").forEach(btn => {
  btn.addEventListener("click", () => {
    const newLens = btn.dataset.lens;
    if (newLens === currentLens) return;
    if (newLens === "trailing" && !DATA.trailing) return;
    currentLens = newLens;
    document.body.setAttribute("data-lens", newLens);
    document.querySelectorAll(".lens button").forEach(b => b.classList.toggle("active", b === btn));
    renderChart().then(applySolo);
    wireSectorInteractions();
    updateHash();
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
  // Movers row click
  document.querySelectorAll(".mv-row").forEach((row, i) => {
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
  soloId = id;
  const vis = family.series.map((_, i) => i === idx ? true : "legendonly");
  Plotly.restyle("chart", { visible: vis, opacity: family.series.map(() => 1) }).then(rescaleY);
  document.getElementById("chart").scrollIntoView({ behavior: "smooth", block: "center" });
  updateHash();
}

// ═══ Section 01: ask the table ═══
// Two stages by design. The default answer is written only from DATA.brief —
// the same text the build-time model read — which is fast, costs ~$0.0002 and
// cannot cite anything the page does not contain. A web lookup costs ~90x that
// and pulls in claims this page cannot check, so it stays behind a click.
(function askPanel() {
  const form = document.getElementById("ask-form");
  if (!form || !DATA.brief) return;
  const input = document.getElementById("ask-input");
  const send = document.getElementById("ask-send");
  const body = document.getElementById("ask-body");
  const intro = document.getElementById("ask-intro");
  const chips = document.getElementById("ask-chips");

  const ENDPOINT = "https://coreservices-proxy.ycczkl91.workers.dev/v1/chat/completions";
  const MODELS = ["~deepseek/deepseek-v4-flash-latest", "openai/gpt-5.6-luna-pro"];
  const MACHINE_ID = "forward-pe-viewer-web";

  const GROUND_RULES =
    "You answer questions about one specific table of S&P 500 sector valuations.\n\n" +
    "The table below is your only source. Quote its figures exactly; never " +
    "compute new ones. If the answer is not in the table, say so in one " +
    "sentence and stop — do not guess and do not fall back on general " +
    "knowledge about these companies.\n\n" +
    "Two to four sentences. Dry and concrete. No hedging, no disclaimers, no " +
    "advice, and never suggest buying or selling anything.\n\n" +
    "THE TABLE:\n" + DATA.brief;

  const WEB_RULES =
    "You explain what actually happened in the market to produce a reading the " +
    "reader is looking at.\n\n" +
    "You MUST search the web and report what you find there. Do not restate " +
    "the figures — the reader already has them on screen. Lead with the cause: " +
    "earnings, guidance, policy, rates, a specific event. Name companies and " +
    "dates where the reporting supports it.\n\n" +
    "Three sentences at most. If the reporting is thin or contradictory, say " +
    "that plainly instead of manufacturing a tidy story.\n\n" +
    "No advice, no price targets, never suggest buying or selling.\n\n" +
    "Reference only — the on-screen table:\n" + DATA.brief;

  let busy = false;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function scroll() { body.scrollTop = body.scrollHeight; }

  // Models emit markdown even when told not to. Escape everything, then allow
  // exactly one construct back in: **bold**. Nothing else survives as markup.
  function inline(raw) {
    const esc = raw
      // Web answers trail each claim with ([domain](url)); the sources are
      // listed under the answer, so drop them from the prose.
      .replace(/\s*\(\[[^\]]*\]\([^)]*\)\)/g, "")
      .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return esc
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s.,;:)]|$)/g, "$1$2");
  }

  function paragraphs(node, text) {
    const chunks = text.split(/\n{2,}/).map(c => c.trim()).filter(Boolean);
    (chunks.length ? chunks : [text]).forEach(chunk => {
      const p = document.createElement("p");
      p.innerHTML = inline(chunk);
      node.appendChild(p);
    });
  }

  async function callModel(system, question, plugins) {
    let lastErr;
    for (const model of MODELS) {
      const payload = {
        model,
        messages: [
          { role: "system", content: system },
          { role: "user", content: question },
        ],
        max_tokens: 700,
        temperature: 0.3,
      };
      if (plugins) payload.plugins = plugins;
      let resp;
      try {
        resp = await fetch(ENDPOINT, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Machine-ID": MACHINE_ID },
          body: JSON.stringify(payload),
        });
      } catch (e) {
        throw new Error("Could not reach the model service.");
      }
      if (resp.status === 429) throw new Error("QUOTA");
      if (!resp.ok) {
        // A model the proxy has not allow-listed 400s; try the next one.
        lastErr = new Error("The model service returned " + resp.status + ".");
        continue;
      }
      const data = await resp.json();
      if (data.error) { lastErr = new Error(String(data.error)); continue; }
      const msg = (data.choices && data.choices[0] && data.choices[0].message) || {};
      return { text: (msg.content || "").trim(), annotations: msg.annotations || [] };
    }
    throw lastErr || new Error("No model available.");
  }

  function addWhy(answerNode, question, answerText) {
    const wrap = el("div", "ask-why");
    const btn = el("button", null, "Look up why →");
    btn.type = "button";
    const note = el("span", "searches the web · slower");
    note.textContent = "searches the web · slower";
    wrap.appendChild(btn);
    wrap.appendChild(note);
    answerNode.appendChild(wrap);

    btn.addEventListener("click", async () => {
      if (busy) return;
      busy = true;
      wrap.innerHTML = "";
      const dots = el("div", "ask-thinking");
      dots.innerHTML = "<i></i><i></i><i></i>";
      wrap.appendChild(dots);
      scroll();
      try {
        // Re-aim the question at the world. Forwarding the original wording
        // gets the table restated, because that is what it asked for.
        const webQuestion =
          "A reader is looking at a sector-valuation dashboard and asked: \"" +
          question + "\"\n\nThe on-screen answer was:\n" + answerText +
          "\n\nSearch recent news and explain what actually drove this in the " +
          "market over the past weeks or months.";
        const r = await callModel(WEB_RULES, webQuestion, [{ id: "web", max_results: 4 }]);
        wrap.innerHTML = "";
        paragraphs(wrap, r.text || "Nothing conclusive turned up.");
        const cites = (r.annotations || [])
          .map(a => a && a.url_citation).filter(Boolean);
        if (cites.length) {
          wrap.appendChild(el("p", "ask-src-label", "Sources"));
          const ul = el("ul", "ask-cites");
          cites.slice(0, 4).forEach(c => {
            const li = document.createElement("li");
            const a = document.createElement("a");
            a.href = c.url; a.target = "_blank"; a.rel = "noopener noreferrer";
            a.textContent = c.title || c.url;
            li.appendChild(a);
            ul.appendChild(li);
          });
          wrap.appendChild(ul);
        } else {
          // The web plugin does not always return annotations. Without them
          // there is nothing for the reader to check, and saying so is the
          // only honest option.
          wrap.appendChild(el("p", "ask-src-label", "No sources returned — unverified"));
        }
      } catch (e) {
        wrap.innerHTML = "";
        wrap.appendChild(el("p", null,
          e.message === "QUOTA"
            ? "Daily lookup limit reached — the answer above still stands."
            : "The lookup failed. The answer above still stands."));
      } finally {
        busy = false;
        scroll();
      }
    });
  }

  async function ask(question) {
    if (busy || !question.trim()) return;
    busy = true;
    send.disabled = true;
    if (intro && intro.parentNode) intro.remove();
    if (chips) chips.style.display = "none";

    body.appendChild(el("div", "ask-q", question));
    const answer = el("div", "ask-answer");
    const dots = el("div", "ask-thinking");
    dots.innerHTML = "<i></i><i></i><i></i>";
    answer.appendChild(dots);
    body.appendChild(answer);
    scroll();

    try {
      const r = await callModel(GROUND_RULES, question);
      answer.innerHTML = "";
      const text = r.text || "No answer came back.";
      paragraphs(answer, text);
      addWhy(answer, question, text);
    } catch (e) {
      answer.innerHTML = "";
      answer.classList.add("err");
      answer.appendChild(el("p", null,
        e.message === "QUOTA"
          ? "This dashboard's shared monthly question limit is used up. The findings on the left are generated at build time and are unaffected."
          : e.message));
    } finally {
      busy = false;
      send.disabled = false;
      scroll();
    }
  }

  form.addEventListener("submit", e => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    input.value = "";
    ask(q);
  });
  if (chips) {
    chips.querySelectorAll("button").forEach(b => {
      b.addEventListener("click", () => ask(b.dataset.q));
    });
  }
})();

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
