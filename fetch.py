"""Fetch S&P 500 forward P/E data (overall + 11 sectors) from MacroMicro."""

from __future__ import annotations

import base64
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path

_SCRAPINGANT_KEY = os.environ.get("SCRAPINGANT_API_KEY")

try:
    from curl_cffi import requests as _requests
    _USE_CFFI = True
except ImportError:
    import cloudscraper
    _USE_CFFI = False

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

# Additional series fetched in the same batch but written to their own CSVs.
# 2 = S&P 500 daily price (for F&G overlay), 46974 = MacroMicro Fear & Greed.
EXTRA_SERIES = {
    2: ("spx_price", "price"),
    46974: ("fear_greed", "value"),
}

BASE = "https://en.macromicro.me"
SEED_SERIES_ID = 20052
SEED_URL = f"{BASE}/series/{SEED_SERIES_ID}/sp500-forward-pe-ratio"
TOKEN_RE = re.compile(r'stk["\s]*[:=]["\s]*["\']([^"\']+)["\']')


class _ScrapingAntResponse:
    def __init__(self, status: int, body: bytes):
        self.status_code = status
        self.content = body
        self.text = body.decode("utf-8", "ignore")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text[:200]}")

    def json(self):
        # browser=true wraps non-HTML responses as `<html>...<pre>{json}</pre>...</html>`.
        body = self.text.strip()
        if body.startswith("<"):
            m = re.search(r"<pre[^>]*>(.*?)</pre>", body, re.DOTALL)
            if m:
                return json.loads(m.group(1))
        return json.loads(self.content)


class _ScrapingAntSession:
    """Route GET requests through the ScrapingAnt v2 API and persist cookies
    across calls. MacroMicro's `stk` token is bound to the PHPSESSID cookie
    set by the seed HTML response, so without cookie continuity the follow-up
    JSON API call returns `error #1165`."""

    ENDPOINT = "https://api.scrapingant.com/v2/general"

    def __init__(self, api_key: str):
        self._key = api_key
        self._cookies: dict[str, str] = {}

    # ScrapingAnt rotates residential IPs per request; some are pre-flagged
    # by Cloudflare. Their docs explicitly recommend retry on 423 detection.
    # Failed requests are not billed, so retries are free.
    MAX_ATTEMPTS = 4
    RETRY_BACKOFF_SEC = 3

    def get(self, url: str, headers: dict | None = None, timeout: int = 60):
        h = dict(headers or {})
        q = {
            "url": url,
            "x-api-key": self._key,
            "proxy_type": "residential",
            "browser": "true",
        }
        # `cookies` URL param injects into the headless Chrome session before
        # navigation. Ant-Cookie header alone does not survive a fresh browser.
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
                print(
                    f"[retry] ScrapingAnt {last_status} on attempt {attempt}, "
                    f"backing off {self.RETRY_BACKOFF_SEC}s",
                    file=sys.stderr,
                )
                time.sleep(self.RETRY_BACKOFF_SEC)

        set_cookie = (last_hdrs.get("Ant-Original-Header-Set-Cookie", "") if last_hdrs else "")
        if set_cookie:
            try:
                jar = SimpleCookie()
                jar.load(set_cookie)
                for name, morsel in jar.items():
                    self._cookies[name] = morsel.value
            except Exception:
                pass

        return _ScrapingAntResponse(last_status, last_body)

    def fetch_series_from_page(self, series_url: str, timeout: int = 120) -> list[list]:
        """Load a MacroMicro series page in headless Chrome, then read the
        chart data straight out of Highcharts' state — bypassing the
        /stats/data/* API entirely. Returns a list of [epoch_ms, value] pairs.

        Why: MacroMicro's API rejects our scraped fetches with 403 (level: 0)
        even though the page itself loads fine and the chart renders. By the
        time js_snippet runs, the page's own code has already populated
        `Highcharts.charts[0].series[0].options.data` with the full series.
        """
        js = r"""
const __out = {};
try {
  if (!window.Highcharts || !Array.isArray(window.Highcharts.charts) || !window.Highcharts.charts[0]) {
    throw new Error("Highcharts.charts[0] not present");
  }
  const __c = window.Highcharts.charts[0];
  const __s = __c.series && __c.series[0];
  if (!__s) throw new Error("chart has no series[0]");
  const __od = __s.options && __s.options.data;
  if (!Array.isArray(__od)) throw new Error("series[0].options.data is not an array");
  __out.name = __s.name;
  __out.data = __od;
  __out.location = location.href;
} catch (__e) {
  __out.error = String(__e);
  __out.stack = (__e && __e.stack) || null;
}
const __tag = document.createElement("pre");
__tag.id = "ant-result";
__tag.textContent = btoa(unescape(encodeURIComponent(JSON.stringify(__out))));
document.body.appendChild(__tag);
"""
        js_b64 = base64.b64encode(js.encode("utf-8")).decode("ascii")

        q = {
            "url": series_url,
            "x-api-key": self._key,
            "proxy_type": "residential",
            "browser": "true",
            "js_snippet": js_b64,
            "wait_for_selector": "#ant-result",
        }
        req = urllib.request.Request(f"{self.ENDPOINT}?{urllib.parse.urlencode(q)}")

        last_status, last_body = 0, b""
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    last_status, last_body = r.status, r.read()
            except urllib.error.HTTPError as e:
                last_status, last_body = e.code, e.read()
            if last_status < 400:
                break
            if attempt < self.MAX_ATTEMPTS:
                print(
                    f"[retry] {series_url} ScrapingAnt {last_status} on attempt {attempt}",
                    file=sys.stderr,
                )
                time.sleep(self.RETRY_BACKOFF_SEC)

        if last_status >= 400:
            raise RuntimeError(
                f"HTTP {last_status} for {series_url}: "
                f"{last_body[:200].decode('utf-8', 'ignore')}"
            )

        text = last_body.decode("utf-8", "ignore")
        m = re.search(r'<pre id="ant-result">([^<]*)</pre>', text)
        if not m:
            raise RuntimeError(f"ant-result tag missing for {series_url}")
        decoded = base64.b64decode(m.group(1)).decode("utf-8")
        out = json.loads(decoded)
        if "error" in out:
            raise RuntimeError(f"in-page extraction failed for {series_url}: {out['error']}")
        return out["data"]

    def fetch_macromicro_dataset(
        self, seed_url: str, all_ids: list[int], timeout: int = 120
    ) -> dict:
        """Legacy entry point — kept for reference but not used by main().
        See fetch_series_from_page() for the working flow.
        """
        ids = ",".join(str(i) for i in all_ids)
        # ScrapingAnt wraps the snippet in `async function() { ... }` and awaits
        # the result. Use top-level await directly — wrapping in our own async
        # IIFE returns a Promise the wrapper doesn't await, so the snapshot
        # would be taken before the fetch resolves.
        js = r"""
const __diag = {};
try {
  // Probe: enumerate top-level window keys (filter out browser builtins)
  // and any variables that might hold chart series data.
  const __builtins = new Set([
    "self", "window", "document", "name", "location", "customElements", "history",
    "locationbar", "menubar", "personalbar", "scrollbars", "statusbar", "toolbar",
    "status", "closed", "frames", "length", "top", "opener", "parent", "frameElement",
    "navigator", "origin", "external", "screen", "innerWidth", "innerHeight",
    "scrollX", "pageXOffset", "scrollY", "pageYOffset", "visualViewport",
    "screenX", "screenY", "outerWidth", "outerHeight", "devicePixelRatio",
    "clientInformation", "screenLeft", "screenTop", "styleMedia", "onsearch",
    "isSecureContext", "performance", "onappinstalled", "onbeforeinstallprompt",
    "crypto", "indexedDB", "sessionStorage", "localStorage", "onbeforexrselect",
    "onabort", "onbeforeinput", "onbeforematch", "onbeforetoggle", "onblur",
    "oncancel", "oncanplay", "oncanplaythrough", "onchange", "onclick", "onclose",
    "oncontentvisibilityautostatechange", "oncontextlost", "oncontextmenu",
    "oncontextrestored", "oncuechange", "ondblclick", "ondrag", "ondragend",
    "ondragenter", "ondragleave", "ondragover", "ondragstart", "ondrop",
    "ondurationchange", "onemptied", "onended", "onerror", "onfocus", "onformdata",
    "oninput", "oninvalid", "onkeydown", "onkeypress", "onkeyup", "onload",
    "onloadeddata", "onloadedmetadata", "onloadstart", "onmousedown", "onmouseenter",
    "onmouseleave", "onmousemove", "onmouseout", "onmouseover", "onmouseup",
    "onmousewheel", "onpause", "onplay", "onplaying", "onprogress", "onratechange",
    "onreset", "onresize", "onscroll", "onsecuritypolicyviolation", "onseeked",
    "onseeking", "onselect", "onslotchange", "onstalled", "onsubmit", "onsuspend",
    "ontimeupdate", "ontoggle", "onvolumechange", "onwaiting", "onwebkitanimationend",
    "onwebkitanimationiteration", "onwebkitanimationstart", "onwebkittransitionend",
    "onwheel", "onauxclick", "ongotpointercapture", "onlostpointercapture",
    "onpointerdown", "onpointermove", "onpointerrawupdate", "onpointerup",
    "onpointercancel", "onpointerover", "onpointerout", "onpointerenter",
    "onpointerleave", "onselectstart", "onselectionchange", "onanimationend",
    "onanimationiteration", "onanimationstart", "ontransitionrun", "ontransitionstart",
    "ontransitionend", "ontransitioncancel", "onafterprint", "onbeforeprint",
    "onbeforeunload", "onhashchange", "onlanguagechange", "onmessage", "onmessageerror",
    "onoffline", "ononline", "onpagehide", "onpageshow", "onpopstate", "onrejectionhandled",
    "onstorage", "onunhandledrejection", "onunload", "crossOriginIsolated",
    "scheduler", "alert", "atob", "blur", "btoa", "cancelAnimationFrame",
    "cancelIdleCallback", "captureEvents", "clearInterval", "clearTimeout", "close",
    "confirm", "createImageBitmap", "fetch", "find", "focus", "getComputedStyle",
    "getSelection", "matchMedia", "moveBy", "moveTo", "open", "postMessage", "print",
    "prompt", "queueMicrotask", "releaseEvents", "reportError", "requestAnimationFrame",
    "requestIdleCallback", "resizeBy", "resizeTo", "scroll", "scrollBy", "scrollTo",
    "setInterval", "setTimeout", "stop", "structuredClone", "webkitCancelAnimationFrame",
    "webkitRequestAnimationFrame", "chrome", "AbortController", "TextEncoder",
    "TextDecoder", "fetchLater", "trustedTypes", "speechSynthesis", "onpageswap",
    "onpagereveal", "onscrollend", "onscrollsnapchange", "onscrollsnapchanging",
    "documentPictureInPicture", "onbeforematch", "credentialless", "$", "jQuery"
  ]);
  const __pageKeys = Object.keys(window).filter(k => !__builtins.has(k));
  __diag.location = location.href;
  __diag.tokenLen = (document.documentElement.outerHTML.match(/stk["\s]*[:=]["\s]*["']([^"']+)["']/) || [])[1]?.length;
  __diag.pageKeys = __pageKeys.slice(0, 80);
  // Look for likely chart/data globals: any object with a "data" key holding arrays,
  // or any value that's an array of [date, number] tuples, or Highcharts/Chart instances.
  const __candidates = {};
  for (const k of __pageKeys) {
    try {
      const v = window[k];
      const t = typeof v;
      if (t === "object" && v !== null) {
        __candidates[k] = {
          ctor: v.constructor && v.constructor.name,
          keys: Object.keys(v).slice(0, 12),
        };
      } else if (t !== "function") {
        __candidates[k] = {type: t, preview: String(v).slice(0, 60)};
      }
    } catch (__e) { /* ignore */ }
  }
  __diag.candidates = __candidates;
  // Probe Highcharts series 0 in detail
  if (window.Highcharts && Array.isArray(window.Highcharts.charts) && window.Highcharts.charts[0]) {
    const __c = window.Highcharts.charts[0];
    const __s = __c.series && __c.series[0];
    if (__s) {
      __diag.firstSeriesName = __s.name;
      __diag.firstSeriesXDataLen = (__s.xData || []).length;
      __diag.firstSeriesYDataLen = (__s.yData || []).length;
      __diag.firstSeriesXDataSample = (__s.xData || []).slice(0, 3);
      __diag.firstSeriesYDataSample = (__s.yData || []).slice(0, 3);
      __diag.firstSeriesXDataLast = (__s.xData || []).slice(-3);
      __diag.firstSeriesYDataLast = (__s.yData || []).slice(-3);
      // Original options.data: this is what MacroMicro originally pushed in
      const __od = __s.options && __s.options.data;
      __diag.firstSeriesOptionsDataLen = Array.isArray(__od) ? __od.length : "not-array";
      __diag.firstSeriesOptionsDataSample = Array.isArray(__od) ? __od.slice(0, 3) : null;
      __diag.firstSeriesOptionsDataLast = Array.isArray(__od) ? __od.slice(-3) : null;
    }
  }
} catch (__e) {
  __diag.error = String(__e);
  __diag.stack = (__e && __e.stack) || null;
}
const __tag = document.createElement("pre");
__tag.id = "ant-result";
__tag.textContent = btoa(unescape(encodeURIComponent(JSON.stringify(__diag))));
document.body.appendChild(__tag);
""".replace("__IDS__", ids)
        js_b64 = base64.b64encode(js.encode("utf-8")).decode("ascii")

        q = {
            "url": seed_url,
            "x-api-key": self._key,
            "proxy_type": "residential",
            "browser": "true",
            "js_snippet": js_b64,
            "wait_for_selector": "#ant-result",
        }
        req = urllib.request.Request(f"{self.ENDPOINT}?{urllib.parse.urlencode(q)}")

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
                print(
                    f"[retry] ScrapingAnt {last_status} on attempt {attempt}, "
                    f"backing off {self.RETRY_BACKOFF_SEC}s",
                    file=sys.stderr,
                )
                time.sleep(self.RETRY_BACKOFF_SEC)

        if last_hdrs is not None:
            seed_set_cookie = last_hdrs.get("Ant-Original-Header-Set-Cookie", "")
            print(f"[diag] seed-page Set-Cookie: {seed_set_cookie!r}", file=sys.stderr)

        if last_status >= 400:
            raise RuntimeError(
                f"HTTP {last_status}: {last_body[:200].decode('utf-8', 'ignore')}"
            )

        text = last_body.decode("utf-8", "ignore")
        m = re.search(r'<pre id="ant-result">([^<]*)</pre>', text)
        if not m:
            anywhere = "ant-result" in text
            print(
                f"[debug] response len={len(text)} ant-result-substring-present={anywhere}",
                file=sys.stderr,
            )
            print(f"[debug] last 800 chars:\n{text[-800:]!r}", file=sys.stderr)
            raise RuntimeError(
                f"ant-result tag not found (substring present={anywhere}); "
                f"first 200: {text[:200]!r}"
            )
        try:
            decoded = base64.b64decode(m.group(1)).decode("utf-8")
        except Exception as e:
            raise RuntimeError(
                f"failed to b64-decode ant-result: {e}; raw: {m.group(1)[:200]!r}"
            )
        diag = json.loads(decoded)
        print(f"[diag] {json.dumps(diag, indent=2)}", file=sys.stderr)
        # PROBE MODE: not extracting real data yet. Force-fail so the workflow
        # logs the dump and we can read what page state looks like.
        raise RuntimeError("probe-only run — see [diag] above")


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


def get_token(scraper) -> str:
    r = scraper.get(SEED_URL, timeout=30)
    r.raise_for_status()
    m = TOKEN_RE.search(r.text)
    if not m:
        raise RuntimeError("token 'stk' not found in seed page HTML")
    return m.group(1)


def fetch_data(scraper, token: str) -> dict:
    all_ids = list(SERIES) + list(EXTRA_SERIES)
    ids = ",".join(str(i) for i in all_ids)
    url = f"{BASE}/stats/data/{ids}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Referer": SEED_URL,
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    r = scraper.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    payload = r.json()
    if payload.get("success") != 1:
        raise RuntimeError(f"API returned non-success: {payload!r}")
    return payload["data"]


def write_csvs(data: dict, out_dir: Path) -> dict[int, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[int, int] = {}
    for sid, name in SERIES.items():
        entry = data.get(f"s:{sid}")
        if not entry:
            print(f"[warn] missing s:{sid} ({name})", file=sys.stderr)
            continue
        points = entry["series"][0]
        slug = name.lower().replace("&", "and").replace(" ", "_")
        path = out_dir / f"{sid}_{slug}.csv"
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "forward_pe"])
            w.writerows(points)
        counts[sid] = len(points)
    return counts


def write_extras(data: dict, out_dir: Path) -> dict[int, int]:
    counts: dict[int, int] = {}
    for sid, (stem, col) in EXTRA_SERIES.items():
        entry = data.get(f"s:{sid}")
        if not entry:
            print(f"[warn] missing s:{sid} ({stem})", file=sys.stderr)
            continue
        points = entry["series"][0]
        path = out_dir / f"{stem}.csv"
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", col])
            w.writerows(points)
        counts[sid] = len(points)
    return counts


def write_combined(data: dict, out_path: Path) -> None:
    by_date: dict[str, dict[int, float]] = {}
    for sid in SERIES:
        entry = data.get(f"s:{sid}")
        if not entry:
            continue
        for date, value in entry["series"][0]:
            by_date.setdefault(date, {})[sid] = value
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + [SERIES[i] for i in SERIES])
        for date in sorted(by_date):
            row = by_date[date]
            w.writerow([date] + [row.get(i, "") for i in SERIES])


def _epoch_ms_to_date(ms: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d")


def _series_data_to_macromicro_format(epoch_data: list[list]) -> dict:
    """Convert [[epoch_ms, value], ...] to {"series": [[[date_str, value], ...]]}."""
    points = [[_epoch_ms_to_date(int(ts)), v] for ts, v in epoch_data]
    return {"series": [points]}


def main() -> None:
    out_dir = Path(__file__).parent / "data"
    scraper = _make_session()
    backend = _backend_name()
    n_total = len(SERIES) + len(EXTRA_SERIES)
    if isinstance(scraper, _ScrapingAntSession):
        # ScrapingAnt path: load each series page individually, read Highcharts state.
        # We only know the URL pattern for SEED_SERIES_ID for sure; for the rest we
        # try `/series/{id}/x` and rely on MacroMicro's permissive slug routing.
        # Failures on individual series are logged and skipped (write_csvs handles
        # missing entries gracefully).
        all_ids = list(SERIES) + list(EXTRA_SERIES)
        print(f"[1/3] fetching {len(all_ids)} series via {backend} ...")
        data: dict = {}
        for sid in all_ids:
            url = SEED_URL if sid == SEED_SERIES_ID else f"{BASE}/series/{sid}/x"
            try:
                epoch_data = scraper.fetch_series_from_page(url)
                data[f"s:{sid}"] = _series_data_to_macromicro_format(epoch_data)
                print(f"      s:{sid:<6} {len(epoch_data):>5} points  ({url})")
            except Exception as e:
                print(f"      s:{sid:<6} FAILED: {e}", file=sys.stderr)
    else:
        print(f"[1/3] resolving token (backend={backend}) ...")
        token = get_token(scraper)
        print(f"      token: {token[:12]}... ({len(token)} chars)")
        print(f"[2/3] fetching {n_total} series in one call ...")
        data = fetch_data(scraper, token)
    print("[3/3] writing CSVs ...")
    counts = write_csvs(data, out_dir)
    extra_counts = write_extras(data, out_dir)
    write_combined(data, out_dir / "combined.csv")
    (out_dir / "raw.json").write_text(json.dumps(data, indent=2))
    for sid, name in SERIES.items():
        print(f"  {sid} {name:<24} {counts.get(sid, 0):>5} points")
    for sid, (stem, _) in EXTRA_SERIES.items():
        print(f"  {sid} {stem:<24} {extra_counts.get(sid, 0):>5} points")
    print(f"output: {out_dir}")


if __name__ == "__main__":
    main()
