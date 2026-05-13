"""Fetch S&P 500 valuation data for the Forward P/E viewer.

Data sources (post 2026-05 migration):
  - Koyfin internal API (no auth) for all P/E and price data via the
    /api/v3p/data/graph (fundamental) and /api/v3/data/graph (price/yield)
    endpoints. KIDs captured 2026-05-12; verifiable via the public search
    endpoint `POST /api/v1/bfc/tickers/search`.
  - MacroMicro chart 50108 via ScrapingAnt for the CNN Fear & Greed index
    (Koyfin doesn't carry sentiment data).

Outputs:
  data/<sid>_<slug>.csv           — forward P/E (12 series)
  data/trailing/<sid>_<slug>.csv  — trailing P/E (12 series)
  data/spx_price.csv              — S&P 500 daily price (SPY ETF proxy)
  data/us10y.csv                  — US 10-year Treasury yield (%)
  data/fear_greed.csv             — CNN Fear & Greed Index
  data/raw.json                   — combined raw points, used by build_html.py
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
from pathlib import Path

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

# Output filenames for non-PE series.
EXTRA_FILES = {
    2: ("spx_price", "price"),
    46974: ("fear_greed", "value"),
    354: ("us10y", "yield"),
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


def fetch_fear_greed() -> list[list]:
    """Pull the CNN F&G daily series from MacroMicro chart 50108. Two calls:
    seed page → /charts/data/<id> with the page-issued stk token."""
    session = _MacroMicroSession()
    page_url = f"{MM_BASE}/charts/{MM_FG_CHART_ID}/{MM_FG_SLUG}"
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
    r2 = session.get(f"{MM_BASE}/charts/data/{MM_FG_CHART_ID}", headers=headers)
    r2.raise_for_status()
    payload = r2.json()
    if payload.get("success") != 1:
        raise RuntimeError(f"MacroMicro F&G chart returned non-success: {payload!r}")
    chart = payload["data"][f"c:{MM_FG_CHART_ID}"]
    # series[0] = CNN F&G index, series[1] = SPX (which we now get from Koyfin).
    return chart["series"][0]


# ──────────────────────────────────────────────────────────────────────────────
# CSV merge + write
# ──────────────────────────────────────────────────────────────────────────────

def _merge_with_existing_csv(path: Path, new_pts: list[list]) -> list[list]:
    """Merge new points into existing CSV by date — new wins on overlap, old
    dates not present in new are kept. Preserves deep history (e.g. SPX 1928+)."""
    by_date: dict[str, str] = {}
    if path.exists():
        with path.open() as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 2:
                    by_date[row[0]] = row[1]
    for d, v in new_pts:
        by_date[str(d)] = str(v)
    return [[d, by_date[d]] for d in sorted(by_date)]


def write_pe_csv(out_dir: Path, sid: int, name: str, pts: list[list], header_col: str):
    slug = name.lower().replace("&", "and").replace(" ", "_")
    path = out_dir / f"{sid}_{slug}.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", header_col])
        w.writerows(pts)


def write_extra_csv(out_dir: Path, stem: str, col: str, pts: list[list], merge: bool):
    """Write an extras CSV. If merge=True, preserves any prior history beyond
    what Koyfin/MacroMicro returns (e.g. SPX pre-1993 from the legacy source)."""
    path = out_dir / f"{stem}.csv"
    rows = _merge_with_existing_csv(path, pts) if merge else pts
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", col])
        w.writerows(rows)
    return len(rows)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    root = Path(__file__).parent
    out_dir = root / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    trailing_dir = out_dir / "trailing"
    trailing_dir.mkdir(parents=True, exist_ok=True)

    forward_points: dict[int, list] = {}
    trailing_points: dict[int, list] = {}

    print(f"[1/4] Koyfin forward + trailing P/E ({len(KOYFIN_PE_KIDS)} series) ...")
    for sid in SERIES:
        kid, tk = KOYFIN_PE_KIDS[sid]
        try:
            fwd = koy_fundamental(kid, "f_pe")
            trl = koy_fundamental(kid, "f_peltm")
        except Exception as e:
            print(f"  s:{sid:<6} {tk:<5} FAILED: {e}", file=sys.stderr)
            continue
        forward_points[sid] = fwd
        trailing_points[sid] = trl
        write_pe_csv(out_dir, sid, SERIES[sid], fwd, "forward_pe")
        write_pe_csv(trailing_dir, sid, SERIES[sid], trl, "trailing_pe")
        print(f"  s:{sid:<6} {tk:<5} fwd={len(fwd):>5}  trail={len(trl):>5}")

    print(f"[2/4] Koyfin SPX price + QQQ + 10Y yield ...")
    extras: dict[int, list] = {}
    spx = koy_price(KOYFIN_SPX_KID)
    extras[2] = spx
    n_spx = write_extra_csv(out_dir, "spx_price", "price", spx, merge=True)
    print(f"  spx_price  Koyfin {len(spx):>5} pts; merged → {n_spx} pts")

    qqq_pe = koy_fundamental(KOYFIN_QQQ_KID, "f_pe")
    qqq_price = koy_price(KOYFIN_QQQ_KID)
    n_qqq_pe = write_extra_csv(out_dir, "qqq_forward_pe", "forward_pe", qqq_pe, merge=False)
    n_qqq_p = write_extra_csv(out_dir, "qqq_price", "price", qqq_price, merge=True)
    print(f"  qqq_fwd_pe Koyfin {len(qqq_pe):>5} pts → {n_qqq_pe} pts")
    print(f"  qqq_price  Koyfin {len(qqq_price):>5} pts; merged → {n_qqq_p} pts")

    us10y = koy_price(KOYFIN_US10Y_KID)
    extras[354] = us10y
    n_10y = write_extra_csv(out_dir, "us10y", "yield", us10y, merge=True)
    print(f"  us10y      Koyfin {len(us10y):>5} pts; merged → {n_10y} pts")

    print(f"[3/4] MacroMicro CNN F&G (chart {MM_FG_CHART_ID}) ...")
    try:
        fg = fetch_fear_greed()
        extras[46974] = fg
        n_fg = write_extra_csv(out_dir, "fear_greed", "value", fg, merge=False)
        print(f"  fear_greed {n_fg:>5} pts (CNN, replace)")
    except Exception as e:
        print(f"  fear_greed FAILED: {e} — keeping prior CSV", file=sys.stderr)

    print(f"[4/4] writing raw.json ...")
    raw = {
        "forward": {str(sid): pts for sid, pts in forward_points.items()},
        "trailing": {str(sid): pts for sid, pts in trailing_points.items()},
        "spx": extras.get(2, []),
        "qqq_pe": qqq_pe,
        "qqq_price": qqq_price,
        "us10y": extras.get(354, []),
        "fg": extras.get(46974, []),
    }
    (out_dir / "raw.json").write_text(json.dumps(raw, indent=2))

    print(f"\nSummary:")
    print(f"  forward P/E   : {len(forward_points)}/{len(SERIES)} series")
    print(f"  trailing P/E  : {len(trailing_points)}/{len(SERIES)} series")
    print(f"  SPX price     : {len(extras.get(2, []))} new daily pts")
    print(f"  10Y yield     : {len(extras.get(354, []))} new daily pts")
    print(f"  CNN F&G       : {len(extras.get(46974, []))} pts")
    print(f"output: {out_dir}")


if __name__ == "__main__":
    main()
