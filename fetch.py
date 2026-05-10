"""Fetch S&P 500 forward P/E data (overall + 11 sectors) from MacroMicro.

Uses MacroMicro's per-chart endpoint /charts/data/<chart_id> which returns all
series for a chart in one response, instead of /stats/data/<series_ids> which
the site started rejecting from non-residential / replay sessions in April 2026.

Two HTTP calls per refresh:
  1. GET the chart page (sets PHPSESSID, embeds the stk token in HTML)
  2. GET /charts/data/<chart_id> with `Authorization: Bearer <stk>`

Backends:
  - curl_cffi (Chrome impersonation) when run from a residential IP
  - ScrapingAnt residential proxy when run on GitHub Actions (datacenter IPs
    are blocked by Cloudflare)
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_SCRAPINGANT_KEY = os.environ.get("SCRAPINGANT_API_KEY")

try:
    from curl_cffi import requests as _requests
    _USE_CFFI = True
except ImportError:
    import cloudscraper
    _USE_CFFI = False

# Output series — the 12 forward-PE series come from one MacroMicro chart.
SERIES = {
    20052: "S&P 500",
    20517: "Information Technology",
    20518: "Communication Services",
    20519: "Consumer Discretionary",
    20520: "Financials",
    20521: "Industrials",
    20522: "Utilities",
    20523: "Energy",
    20524: "Real Estate",
    20525: "Materials",
    20526: "Consumer Staples",
    20527: "Health Care",
}

# Auxiliary series written to their own CSVs.
#   2     = S&P 500 daily price (F&G overlay + valuation chart)
#   46974 = CNN Fear & Greed (sentiment gauge)
#   354   = US 10-year Treasury bond yield (valuation chart — equity risk premium)
EXTRA_SERIES = {
    2: ("spx_price", "price"),
    46974: ("fear_greed", "value"),
    354: ("us10y", "yield"),
}

BASE = "https://en.macromicro.me"
TOKEN_RE = re.compile(r'stk["\s]*[:=]["\s]*["\']([^"\']+)["\']')

# Each entry: (chart_id, slug, [(series_index_in_chart, our_series_id), ...])
# Chart 48243 ("US - S&P 500 Forward PE Ratio by Sector") embeds 12 series in
# one response. The order of `series[i]` matches the order in `chart_config.
# seriesConfigs[i].name_en` — confirmed by inspection on 2026-05-10.
# Chart 50108 ("US - CNN Fear and Greed Index") gives SPX daily price (stat 2)
# and CNN's F&G index (stat 22748). The MacroMicro-original F&G (stat 46974)
# isn't reachable via any public chart anymore — its CSV stays stale.
CHART_SOURCES: list[tuple[int, str, list[tuple[int, int]]]] = [
    (
        48243,
        "s5cond-forward-pe-ratio",
        [
            (0, 20052),   # S&P 500
            (1, 20517),   # Info Tech
            (2, 20518),   # Comm Svcs
            (3, 20521),   # Industrials
            (4, 20520),   # Financials
            (5, 20525),   # Materials
            (6, 20523),   # Energy
            (7, 20524),   # Real Estate
            (8, 20519),   # Cons Disc
            (9, 20526),   # Cons Staples
            (10, 20527),  # Health Care
            (11, 20522),  # Utilities
        ],
    ),
    (
        3919,
        "sp500-10y-yield",
        [
            (0, 354),     # US 10-year Treasury bond yield (daily, since 1962)
            (1, 2),       # SPX daily price (daily, since 1962 — longer than chart 142681)
        ],
    ),
    (
        50108,
        "cnn-fear-and-greed",
        [
            (0, 46974),   # CNN F&G index — replaces the deprecated MacroMicro
                          # Investor F&G (also id 46974). Different methodology
                          # and only 5 years of history, but it's the only
                          # publicly fetchable F&G chart on the site now.
        ],
    ),
]


class _ScrapingAntResponse:
    def __init__(self, status: int, body: bytes):
        self.status_code = status
        self.content = body
        self.text = body.decode("utf-8", "ignore")
        self.cookies: dict = {}  # populated by session

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text[:200]}")

    def json(self):
        return json.loads(self.content)


class _ScrapingAntSession:
    """Route GET through ScrapingAnt's v2 API with residential proxy and
    browser=false (raw HTML, no JS — cheaper and avoids the page's natural
    fetch consuming our token).

    Cost: ~50 credits per request × 2 per refresh = 100 credits/day,
    well inside the 10,000/month free tier even at daily cadence."""

    ENDPOINT = "https://api.scrapingant.com/v2/general"
    MAX_ATTEMPTS = 4
    RETRY_BACKOFF_SEC = 5

    def __init__(self, api_key: str):
        self._key = api_key
        self._cookies: dict[str, str] = {}

    def get(self, url: str, headers: dict | None = None, timeout: int = 60):
        h = dict(headers or {})
        q = {
            "url": url,
            "x-api-key": self._key,
            "proxy_type": "residential",
            "browser": "false",
        }
        if self._cookies:
            q["cookies"] = ";".join(f"{k}={v}" for k, v in self._cookies.items())
        req = urllib.request.Request(f"{self.ENDPOINT}?{urllib.parse.urlencode(q)}")
        for k, v in h.items():
            req.add_header(f"Ant-{k}", v)

        last_status, last_body, last_hdrs = 0, b"", None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    last_status, last_body, last_hdrs = r.status, r.read(), r.headers
            except urllib.error.HTTPError as e:
                last_status, last_body, last_hdrs = e.code, e.read(), e.headers
            if last_status < 400:
                break
            if attempt < self.MAX_ATTEMPTS:
                # 409 = concurrency limit on free tier; sleep longer.
                backoff = 15 if last_status == 409 else self.RETRY_BACKOFF_SEC
                print(
                    f"[retry] ScrapingAnt {last_status} on attempt {attempt} for "
                    f"{url}, backing off {backoff}s",
                    file=sys.stderr,
                )
                time.sleep(backoff)

        if last_hdrs is not None:
            from http.cookies import SimpleCookie
            set_cookie = last_hdrs.get("Ant-Original-Header-Set-Cookie", "")
            if set_cookie:
                try:
                    jar = SimpleCookie()
                    jar.load(set_cookie)
                    for name, morsel in jar.items():
                        self._cookies[name] = morsel.value
                except Exception:
                    pass

        return _ScrapingAntResponse(last_status, last_body)


def _make_session():
    if _SCRAPINGANT_KEY:
        return _ScrapingAntSession(_SCRAPINGANT_KEY)
    if _USE_CFFI:
        return _requests.Session(impersonate="chrome124")
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )


def _backend_name() -> str:
    if _SCRAPINGANT_KEY:
        return "scrapingant"
    return "curl_cffi" if _USE_CFFI else "cloudscraper"


def fetch_chart(scraper, chart_id: int, slug: str) -> dict:
    """Two-step pull for one MacroMicro chart:
       1. GET the chart page (sets PHPSESSID, embeds the stk token in HTML)
       2. GET /charts/data/<id> with Authorization: Bearer <stk>
    Returns the parsed JSON payload (`{"data": {"c:<id>": {...}}, ...}`).
    """
    page_url = f"{BASE}/charts/{chart_id}/{slug}"
    r1 = scraper.get(page_url, timeout=60)
    r1.raise_for_status()
    m = TOKEN_RE.search(r1.text)
    if not m:
        raise RuntimeError(f"stk token not found on /charts/{chart_id}/{slug}")
    token = m.group(1)

    headers = {
        "Authorization": f"Bearer {token}",
        "Referer": page_url,
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    r2 = scraper.get(f"{BASE}/charts/data/{chart_id}", headers=headers, timeout=60)
    r2.raise_for_status()
    payload = r2.json()
    if payload.get("success") != 1:
        raise RuntimeError(f"chart {chart_id} returned non-success: {payload!r}")
    return payload


def collect_series(scraper) -> dict[int, list[list]]:
    """Walk CHART_SOURCES, fetch each chart once, distribute the series array
    into our flat `{series_id: [[date_str, value], ...]}` map."""
    out: dict[int, list[list]] = {}
    for chart_id, slug, mapping in CHART_SOURCES:
        print(f"[1/2] fetching chart {chart_id} ({slug}) — {len(mapping)} series ...")
        payload = fetch_chart(scraper, chart_id, slug)
        chart_entry = payload["data"][f"c:{chart_id}"]
        chart_series = chart_entry["series"]
        for series_idx, our_id in mapping:
            if series_idx >= len(chart_series):
                print(f"    [warn] chart {chart_id} series[{series_idx}] missing", file=sys.stderr)
                continue
            pts = chart_series[series_idx]
            if not isinstance(pts, list):
                print(f"    [warn] chart {chart_id} series[{series_idx}] not a list", file=sys.stderr)
                continue
            out[our_id] = pts
            print(f"      s:{our_id:<6} {len(pts):>5} points")
    return out


def write_csvs(series_map: dict[int, list[list]], out_dir: Path) -> dict[int, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[int, int] = {}
    for sid, name in SERIES.items():
        pts = series_map.get(sid)
        if not pts:
            print(f"[warn] missing s:{sid} ({name})", file=sys.stderr)
            continue
        slug = name.lower().replace("&", "and").replace(" ", "_")
        path = out_dir / f"{sid}_{slug}.csv"
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "forward_pe"])
            w.writerows(pts)
        counts[sid] = len(pts)
    return counts


def _merge_with_existing_csv(path: Path, new_pts: list[list], col: str) -> list[list]:
    """Merge new daily points into the existing CSV at `path`. New values
    win on overlapping dates; older dates not present in `new_pts` are kept.
    Returns the merged sorted list. Used to preserve deep history (e.g. the
    pre-1999 SPX prices) when a fresh chart endpoint serves only recent data.
    """
    by_date: dict[str, str] = {}
    if path.exists():
        with path.open() as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for row in reader:
                if len(row) >= 2:
                    by_date[row[0]] = row[1]
    for date, value in new_pts:
        by_date[str(date)] = str(value)
    return [[d, by_date[d]] for d in sorted(by_date)]


def write_extras(series_map: dict[int, list[list]], out_dir: Path) -> dict[int, int]:
    counts: dict[int, int] = {}
    for sid, (stem, col) in EXTRA_SERIES.items():
        pts = series_map.get(sid)
        path = out_dir / f"{stem}.csv"
        if not pts:
            print(f"[warn] missing s:{sid} ({stem}) — keeping prior CSV", file=sys.stderr)
            continue
        # F&G (id 46974): source switched from MacroMicro-proprietary to CNN
        # — different methodology, do NOT mix the two; replace outright.
        # Everything else (SPX price, 10Y yield, ...): same series across runs,
        # merge to preserve any prior history beyond what the chart serves.
        rows = pts if sid == 46974 else _merge_with_existing_csv(path, pts, col)
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", col])
            w.writerows(rows)
        counts[sid] = len(rows)
    return counts


def write_combined(series_map: dict[int, list[list]], out_path: Path) -> None:
    by_date: dict[str, dict[int, float]] = {}
    for sid in SERIES:
        pts = series_map.get(sid)
        if not pts:
            continue
        for date, value in pts:
            by_date.setdefault(date, {})[sid] = value
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + [SERIES[i] for i in SERIES])
        for date in sorted(by_date):
            row = by_date[date]
            w.writerow([date] + [row.get(i, "") for i in SERIES])


def main() -> None:
    out_dir = Path(__file__).parent / "data"
    scraper = _make_session()
    print(f"backend: {_backend_name()}")
    series_map = collect_series(scraper)

    print("[2/2] writing CSVs ...")
    counts = write_csvs(series_map, out_dir)
    extra_counts = write_extras(series_map, out_dir)
    write_combined(series_map, out_dir / "combined.csv")

    raw_path = out_dir / "raw.json"
    raw_path.write_text(json.dumps({str(k): v for k, v in series_map.items()}, indent=2))

    for sid, name in SERIES.items():
        print(f"  {sid} {name:<24} {counts.get(sid, 0):>5} points")
    for sid, (stem, _) in EXTRA_SERIES.items():
        print(f"  {sid} {stem:<24} {extra_counts.get(sid, 0):>5} points")
    print(f"output: {out_dir}")


if __name__ == "__main__":
    main()
