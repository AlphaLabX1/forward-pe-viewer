"""Fetch S&P 500 valuation data for the Forward P/E viewer.

Data sources (post 2026-05 migration):
  - Koyfin internal API (no auth) for all P/E and price data via the
    /api/v3p/data/graph (fundamental) and /api/v3/data/graph (price/yield)
    endpoints. KIDs captured 2026-05-12; verifiable via the public search
    endpoint `POST /api/v1/bfc/tickers/search`.
  - MacroMicro chart 50108 via ScrapingAnt for the CNN Fear & Greed index
    (Koyfin doesn't carry sentiment data).
  - MacroMicro chart 81081 for S&P 500 breadth (% of constituents above
    their 50-day and 200-day moving averages).

Outputs:
  data/<sid>_<slug>.csv           — forward P/E (12 series)
  data/trailing/<sid>_<slug>.csv  — trailing P/E (12 series)
  data/spx_price.csv              — S&P 500 daily price (SPY ETF proxy)
  data/us10y.csv                  — US 10-year Treasury yield (%)
  data/fear_greed.csv             — CNN Fear & Greed Index
  data/sp500_breadth.csv          — % of S&P 500 above 50d / 200d MA
  data/qqq_forward_pe.csv, data/qqq_price.csv — QQQ valuation + price

build_html.py reads only these committed CSVs, through the paths defined here.
"""

from __future__ import annotations

import base64
import csv
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Callable

# ──────────────────────────────────────────────────────────────────────────────
# Series catalog
# ──────────────────────────────────────────────────────────────────────────────

# Internal series IDs (originally MacroMicro stat IDs; kept for compatibility
# with existing CSV filenames and build_html.py SECTOR_* dicts).
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

# Koyfin KIDs (Koyfin's internal primary keys). Captured 2026-05-12 via the
# public POST /api/v1/bfc/tickers/search endpoint.
KOYFIN_PE_KIDS: dict[int, tuple[str, str]] = {
    20052: ("et-n5kqqt", "SPY"),    # S&P 500
    20517: ("et-xvw944", "XLK"),    # Information Technology
    20518: ("et-p8fjob", "XLC"),    # Communication Services (data from 2018-06)
    20519: ("et-dxhekb", "XLY"),    # Consumer Discretionary
    20520: ("et-z6uurv", "XLF"),    # Financials
    20521: ("et-aboaoc", "XLI"),    # Industrials
    20522: ("et-5lxlgf", "XLU"),    # Utilities
    20523: ("et-y6w6i1", "XLE"),    # Energy
    20524: ("et-j6123q", "XLRE"),   # Real Estate (data from 2016-01)
    20525: ("et-skcbat", "XLB"),    # Materials
    20526: ("et-ngtedv", "XLP"),    # Consumer Staples
    20527: ("et-3q29co", "XLV"),    # Health Care
}

KOYFIN_SPX_KID = "et-n5kqqt"    # SPY ETF — proxy for S&P 500 index price
KOYFIN_QQQ_KID = "et-gpvivq"    # QQQ ETF — proxy for NDX (Nasdaq-100)
KOYFIN_US10Y_KID = "bn-dm6gok"  # US 10Y Treasury (close = yield in %)

DATA_DIR = Path(__file__).parent / "data"
LENS_DIRS = {"forward": DATA_DIR, "trailing": DATA_DIR / "trailing"}
SPX_PRICE_CSV = DATA_DIR / "spx_price.csv"
QQQ_PE_CSV = DATA_DIR / "qqq_forward_pe.csv"
QQQ_PRICE_CSV = DATA_DIR / "qqq_price.csv"
US10Y_CSV = DATA_DIR / "us10y.csv"
FEAR_GREED_CSV = DATA_DIR / "fear_greed.csv"
BREADTH_CSV = DATA_DIR / "sp500_breadth.csv"
STATUS_JSON = DATA_DIR / "status.json"


def pe_csv(lens: str, sid: int) -> Path:
    slug = SERIES[sid].lower().replace("&", "and").replace(" ", "_")
    return LENS_DIRS[lens] / f"{sid}_{slug}.csv"


@dataclass(frozen=True)
class Series:
    """Where one stored series lives and what a sane reply for it looks like."""
    path: Path
    columns: tuple[str, ...]
    lo: float
    hi: float
    merge: bool = False     # keep stored dates the reply lacks
    valid_from: str = ""    # rows dated earlier are broken upstream and never stored
    despike: bool = False   # drop isolated one-day spikes that revert


# Trailing P/E legitimately runs into the hundreds when earnings collapse
# (Energy 2017, Materials 2009); forward never has outside Energy 2020 (213).
PE_HI = {"forward": 250.0, "trailing": 1000.0}

# Each cut is a regime the source got wrong, not a range we dislike:
#   XLF forward reads 165-500 every day until 2015-12-31, 13.8 on 2016-01-01.
#   XLC's launch-day print is 10.1 forward / 6.6 trailing against 21.7 / 18.9
#   the next day.
PE_VALID_FROM = {
    ("forward", 20520): "2016-01-01",
    ("forward", 20518): "2018-06-20",
    ("trailing", 20518): "2018-06-20",
}

CATALOG: dict[str, Series] = {
    **{
        f"{lens}/{sid}": Series(
            pe_csv(lens, sid), (f"{lens}_pe",), 0.01, PE_HI[lens],
            valid_from=PE_VALID_FROM.get((lens, sid), ""),
        )
        for sid in SERIES for lens in LENS_DIRS
    },
    # Before 1993-01-29 (SPY's launch) the file held S&P index levels, 10x SPY.
    "spx_price": Series(SPX_PRICE_CSV, ("price",), 0.01, 1e5, merge=True,
                        valid_from="1993-01-29", despike=True),
    # QQQ forward P/E swings between 0.2 and 25 from 2004-09 until 2011-10-24.
    "qqq_pe": Series(QQQ_PE_CSV, ("forward_pe",), 0.01, PE_HI["forward"], valid_from="2011-10-25"),
    "qqq_price": Series(QQQ_PRICE_CSV, ("price",), 0.01, 1e5, merge=True, despike=True),
    "us10y": Series(US10Y_CSV, ("yield",), 0.0, 25.0, merge=True),
    "fear_greed": Series(FEAR_GREED_CSV, ("value",), 0.0, 100.0, merge=True),
    "breadth": Series(BREADTH_CSV, ("above_50d", "above_200d"), 0.0, 100.0, merge=True),
}

# ──────────────────────────────────────────────────────────────────────────────
# Koyfin client
# ──────────────────────────────────────────────────────────────────────────────

KOYFIN_BASE = "https://app.koyfin.com"
KOYFIN_HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://app.koyfin.com/",
    "Origin": "https://app.koyfin.com",
    "User-Agent": "Mozilla/5.0",
}

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SEC = 4


def _today() -> str:
    return datetime.date.today().isoformat()


def _koyfin_post(path: str, body: dict, timeout: int = 60):
    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(
                f"{KOYFIN_BASE}{path}",
                data=json.dumps(body).encode(),
                headers=KOYFIN_HEADERS,
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'ignore')}"
        except Exception as e:
            last_err = str(e)
        if attempt < MAX_ATTEMPTS:
            print(f"[retry] Koyfin {path} attempt {attempt}: {last_err}", file=sys.stderr)
            time.sleep(RETRY_BACKOFF_SEC)
    raise RuntimeError(f"Koyfin {path} failed after {MAX_ATTEMPTS} attempts: {last_err}")


def koy_fundamental(kid: str, key: str, date_from: str = "2003-01-01") -> list[list]:
    """Pull a fundamental time series via /api/v3p/data/graph. Used for P/E."""
    body = {
        "id": kid, "key": key, "currency": "USD",
        "financialPeriodType": "LTM", "priceFormat": "standard",
        "dateFrom": date_from, "dateTo": _today(),
    }
    r = _koyfin_post("/api/v3p/data/graph?schema=packed", body)
    g = r["graph"]
    return [[d, v] for d, v in zip(g["date"], g["value"]) if v is not None]


def koy_price(kid: str, date_from: str = "1990-01-01") -> list[list]:
    """Pull daily close via /api/v3/data/graph p_candle_range. For ETF prices
    this is the trading price; for bond securities this is the yield in %."""
    body = {
        "id": kid, "key": "p_candle_range",
        "dateFrom": date_from, "dateTo": _today(),
        "priceFormat": "both",
    }
    r = _koyfin_post("/api/v3/data/graph?schema=packed", body)
    g = r["graph"]
    return [[d, c] for d, c in zip(g["date"], g["close"]) if c is not None]


# ──────────────────────────────────────────────────────────────────────────────
# CNN Fear & Greed fetcher (via MacroMicro chart 50108)
# ──────────────────────────────────────────────────────────────────────────────

_SCRAPINGANT_KEY = os.environ.get("SCRAPINGANT_API_KEY")
_TOKEN_RE = re.compile(r'stk["\s]*[:=]["\s]*["\']([^"\']+)["\']')
MM_FG_CHART_ID = 50108
MM_FG_SLUG = "cnn-fear-and-greed"
MM_FG_STAT = 22748   # the chart's other series is SPX (stat 2)
MM_BREADTH_CHART_ID = 81081
MM_BREADTH_SLUG = "S-P-500-Breadth"
MM_BREADTH_STATS = (18331, 22718)   # % above 50d MA, % above 200d MA
MM_BASE = "https://en.macromicro.me"


class _ScrapingAntResponse:
    def __init__(self, status: int, body: bytes):
        self.status_code = status
        self.content = body
        self.text = body.decode("utf-8", "ignore")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text[:200]}")

    def json(self):
        return json.loads(self.content)


class _MacroMicroSession:
    """Two-call client for MacroMicro chart pages. Routes through ScrapingAnt
    when SCRAPINGANT_API_KEY is set (CI / datacenter), falls back to direct
    curl_cffi otherwise (local / residential). Persists PHPSESSID across calls
    — required for MacroMicro's stk token to validate."""

    def __init__(self):
        self._cookies: dict[str, str] = {}
        self._curl_session = None
        if not _SCRAPINGANT_KEY:
            try:
                from curl_cffi import requests as _r
                self._curl_session = _r.Session(impersonate="chrome124")
            except ImportError as e:
                raise RuntimeError(
                    "Need SCRAPINGANT_API_KEY or curl_cffi installed"
                ) from e

    def get(self, url: str, headers: dict | None = None, timeout: int = 60):
        if _SCRAPINGANT_KEY:
            return self._scrapingant_get(url, headers=headers, timeout=timeout)
        return self._curl_session.get(url, headers=headers or {}, timeout=timeout)

    def _scrapingant_get(self, url: str, headers: dict | None = None, timeout: int = 60):
        from http.cookies import SimpleCookie
        q = {
            "url": url, "x-api-key": _SCRAPINGANT_KEY,
            "proxy_type": "residential", "browser": "false",
        }
        # Forward persisted cookies via the documented `cookies=` URL param.
        if self._cookies:
            q["cookies"] = ";".join(f"{k}={v}" for k, v in self._cookies.items())
        req = urllib.request.Request(
            f"https://api.scrapingant.com/v2/general?{urllib.parse.urlencode(q)}"
        )
        for k, v in (headers or {}).items():
            req.add_header(f"Ant-{k}", v)

        last_status, last_body, last_hdrs = 0, b"", None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    last_status, last_body, last_hdrs = r.status, r.read(), r.headers
            except urllib.error.HTTPError as e:
                last_status, last_body, last_hdrs = e.code, e.read(), e.headers
            if last_status < 400:
                break
            if attempt < MAX_ATTEMPTS:
                backoff = 15 if last_status == 409 else RETRY_BACKOFF_SEC
                print(
                    f"[retry] ScrapingAnt {last_status} attempt {attempt}, backing off {backoff}s",
                    file=sys.stderr,
                )
                time.sleep(backoff)

        # Capture any Set-Cookie from the target site (forwarded by ScrapingAnt).
        if last_hdrs is not None:
            sc = last_hdrs.get("Ant-Original-Header-Set-Cookie", "")
            if sc:
                try:
                    jar = SimpleCookie()
                    jar.load(sc)
                    for name, morsel in jar.items():
                        self._cookies[name] = morsel.value
                except Exception:
                    pass
        return _ScrapingAntResponse(last_status, last_body)


def fetch_mm_chart(chart_id: int, slug: str) -> dict:
    """Pull one MacroMicro chart (`info` + `series`). Two calls: seed page →
    /charts/data/<id> with the page-issued stk token."""
    session = _MacroMicroSession()
    page_url = f"{MM_BASE}/charts/{chart_id}/{slug}"
    r1 = session.get(page_url, timeout=60)
    r1.raise_for_status()
    m = _TOKEN_RE.search(r1.text)
    if not m:
        raise RuntimeError(f"stk token not found on {page_url}")
    token = m.group(1)

    headers = {
        "Authorization": f"Bearer {token}",
        "Referer": page_url,
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    r2 = session.get(f"{MM_BASE}/charts/data/{chart_id}", headers=headers)
    r2.raise_for_status()
    payload = r2.json()
    if payload.get("success") != 1:
        raise RuntimeError(f"MacroMicro chart {chart_id} returned non-success: {payload!r}")
    return payload["data"][f"c:{chart_id}"]


def _series_by_stat(chart: dict, stat_ids) -> list[list[list]]:
    """MacroMicro charts reorder their series; stat_id is the stable key."""
    configs = chart["info"]["chart_config"]["seriesConfigs"]
    by_stat = {cfg["stats"][0]["stat_id"]: i for i, cfg in enumerate(configs)}
    missing = [sid for sid in stat_ids if sid not in by_stat]
    if missing:
        raise RuntimeError(f"MacroMicro chart missing stat_id(s) {missing}; found {sorted(by_stat)}")
    return [chart["series"][by_stat[sid]] for sid in stat_ids]


def fetch_fear_greed() -> list[list]:
    """CNN F&G daily series from MacroMicro chart 50108."""
    return _series_by_stat(fetch_mm_chart(MM_FG_CHART_ID, MM_FG_SLUG), [MM_FG_STAT])[0]


def fetch_sp500_breadth() -> list[list]:
    """Rows [date, % above 50d MA, % above 200d MA], outer-joined; a missing
    cell is ""."""
    chart = fetch_mm_chart(MM_BREADTH_CHART_ID, MM_BREADTH_SLUG)
    rows: dict[str, list] = {}
    for col, pts in enumerate(_series_by_stat(chart, MM_BREADTH_STATS)):
        for d, v in pts:
            if v not in (None, ""):
                rows.setdefault(str(d), ["", ""])[col] = v
    return [[d, *rows[d]] for d in sorted(rows)]


# ──────────────────────────────────────────────────────────────────────────────
# Validation + storage
# ──────────────────────────────────────────────────────────────────────────────

class SeriesError(ValueError):
    pass


def check_series(name: str, pts: list[list], lo: float, hi: float,
                 prior_last_date: str | None, min_points: int) -> None:
    """Reject a reply that is out of range, older than what is stored, or
    implausibly short. Raises SeriesError; the caller keeps the prior CSV."""
    if not pts or len(pts) < min_points:
        raise SeriesError(f"{name}: {len(pts)} rows, expected at least {max(min_points, 1)}")
    bad = [row for row in pts for v in row[1:] if v not in (None, "") and not lo <= float(v) <= hi]
    if bad:
        raise SeriesError(f"{name}: {len(bad)} value(s) outside [{lo:g}, {hi:g}], first {bad[0]}")
    last = max(row[0] for row in pts)
    if prior_last_date and last < prior_last_date:
        raise SeriesError(f"{name}: reply ends {last}, stored data ends {prior_last_date}")


def drop_spikes(rows: list[list], ratio: float = 1.5) -> list[list]:
    """Drop single rows that sit `ratio`x above or below both neighbours."""
    keep = []
    for i, row in enumerate(rows):
        if 0 < i < len(rows) - 1:
            a, b, c = (float(r[1]) for r in (rows[i - 1], row, rows[i + 1]))
            if min(a, c) > b * ratio or max(a, c) * ratio < b:
                continue
        keep.append(row)
    return keep


def read_rows(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    with path.open() as f:
        reader = csv.reader(f)
        next(reader, None)
        return [row for row in reader if len(row) >= 2]


def clean_rows(spec: Series, rows: list[list]) -> list[list]:
    rows = [r for r in rows if r[0] >= spec.valid_from]
    return drop_spikes(rows) if spec.despike else rows


def store(name: str, reply: list[list]) -> list[list]:
    """Validate one fetched series and write its CSV; returns the stored rows.
    Raises SeriesError and leaves the file untouched when validation fails."""
    spec = CATALOG[name]
    prior = clean_rows(spec, read_rows(spec.path))
    reply = clean_rows(spec, [[str(r[0]), *r[1:]] for r in reply])
    check_series(name, reply, spec.lo, spec.hi,
                 prior[-1][0] if prior else None, int(len(prior) * 0.9))
    by_date: dict[str, list] = {r[0]: r[1:] for r in prior} if spec.merge else {}
    for d, *cells in reply:
        old = by_date.get(d, [""] * len(cells))
        by_date[d] = [new if new not in (None, "") else o for new, o in zip(cells, old)]
    rows = clean_rows(spec, [[d, *by_date[d]] for d in sorted(by_date)])
    with spec.path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", *spec.columns])
        w.writerows(rows)
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

# Source -> the series it feeds, each with the call that fetches it.
SOURCES: dict[str, dict[str, Callable[[], list[list]]]] = {
    "koyfin_pe": {
        f"{lens}/{sid}": partial(koy_fundamental, KOYFIN_PE_KIDS[sid][0], key)
        for sid in SERIES for lens, key in (("forward", "f_pe"), ("trailing", "f_peltm"))
    },
    "koyfin_prices": {
        "spx_price": partial(koy_price, KOYFIN_SPX_KID),
        "qqq_pe": partial(koy_fundamental, KOYFIN_QQQ_KID, "f_pe"),
        "qqq_price": partial(koy_price, KOYFIN_QQQ_KID),
    },
    "us10y": {"us10y": partial(koy_price, KOYFIN_US10Y_KID)},
    "fear_greed": {"fear_greed": fetch_fear_greed},
    "breadth": {"breadth": fetch_sp500_breadth},
}


def stored_as_of(source: str) -> str | None:
    """Latest date any of the source's stored series reaches."""
    dates = [rows[-1][0] for name in SOURCES[source] if (rows := read_rows(CATALOG[name].path))]
    return max(dates, default=None)


def run_status(failures: dict[str, dict[str, str]], prior_as_of: dict[str, str | None]) -> dict:
    """The committed record of this run, data/status.json:

    {"generated_at": ISO-8601 UTC,
     "sources": {source: {"ok": bool, "as_of": "YYYY-MM-DD" | None,
                          "prior_as_of": as_of before this run,
                          "error": str | None,
                          "missing": [series names], only when something failed}}}
    """
    sources = {}
    for source in SOURCES:
        failed = failures.get(source, {})
        entry = {
            "ok": not failed,
            "as_of": stored_as_of(source),
            "prior_as_of": prior_as_of[source],
            "error": "; ".join(failed.values()) or None,
        }
        if failed:
            entry["missing"] = sorted(failed)
        sources[source] = entry
    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "sources": sources,
    }


def load_status() -> dict:
    """status.json, or an all-ok status read off the CSVs when it is absent."""
    if STATUS_JSON.exists():
        return json.loads(STATUS_JSON.read_text())
    as_of = {source: stored_as_of(source) for source in SOURCES}
    return run_status({}, as_of)


def main() -> None:
    for d in LENS_DIRS.values():
        d.mkdir(parents=True, exist_ok=True)
    prior_as_of = {source: stored_as_of(source) for source in SOURCES}
    failures: dict[str, dict[str, str]] = {}
    for source, fetchers in SOURCES.items():
        print(f"[{source}]")
        for name, fetch_fn in fetchers.items():
            try:
                rows = store(name, fetch_fn())
            except Exception as e:
                failures.setdefault(source, {})[name] = str(e)[:300]
                print(f"  {name:<16} FAILED, keeping prior CSV: {e}", file=sys.stderr)
                continue
            print(f"  {name:<16} {len(rows):>6} rows, last {rows[-1][0]}")
    status = run_status(failures, prior_as_of)
    STATUS_JSON.write_text(json.dumps(status, indent=2) + "\n")
    for source, entry in status["sources"].items():
        print(f"{source:<14} ok={entry['ok']!s:<5} as_of={entry['as_of']} error={entry['error']}")


if __name__ == "__main__":
    main()
