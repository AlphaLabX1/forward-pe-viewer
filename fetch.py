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

    def discover_series_links(self, seed_url: str, timeout: int = 120) -> dict[int, str]:
        """Load the seed page and harvest every <a href="/series/{id}/{slug}">
        URL it links to. Returns {series_id: full_url}.
        """
        js = r"""
const __out = {links: []};
try {
  const __anchors = Array.from(document.querySelectorAll('a[href*="/series/"]'));
  const __seen = new Set();
  for (const __a of __anchors) {
    const __h = __a.getAttribute("href") || "";
    const __m = __h.match(/\/series\/(\d+)\/([^/?#]+)/);
    if (__m) {
      const __key = __m[1];
      if (!__seen.has(__key)) {
        __seen.add(__key);
        __out.links.push({id: parseInt(__m[1], 10), slug: __m[2], href: __h});
      }
    }
  }
} catch (__e) {
  __out.error = String(__e);
}
const __tag = document.createElement("pre");
__tag.id = "ant-result";
__tag.textContent = btoa(unescape(encodeURIComponent(JSON.stringify(__out))));
document.body.appendChild(__tag);
"""
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
                # 409 = concurrency limit hit; sleep longer before retrying.
                backoff = 15 if last_status == 409 else self.RETRY_BACKOFF_SEC
                print(
                    f"[retry] discover_series_links {last_status} on attempt {attempt}, "
                    f"backing off {backoff}s",
                    file=sys.stderr,
                )
                time.sleep(backoff)
        if last_status >= 400:
            raise RuntimeError(
                f"HTTP {last_status} discovering series links: "
                f"{last_body[:200].decode('utf-8', 'ignore')}"
            )
        text = last_body.decode("utf-8", "ignore")
        m = re.search(r'<pre id="ant-result">([^<]*)</pre>', text)
        if not m:
            raise RuntimeError("ant-result missing in discover_series_links")
        decoded = base64.b64decode(m.group(1)).decode("utf-8")
        out = json.loads(decoded)
        if "error" in out:
            raise RuntimeError(f"discover_series_links error: {out['error']}")
        result = {}
        for link in out["links"]:
            sid = link["id"]
            slug = link["slug"]
            if sid not in result:
                result[sid] = f"{BASE}/series/{sid}/{slug}"
        return result

    def probe_ssr_data(self, seed_url: str, timeout: int = 180) -> dict:
        """One-shot probe: dump page's internal data-fetching method source code
        and try invoking them. Single ScrapingAnt request (~125 credits)."""
        js = r"""
const __out = {};
try {
  // Dump source code of the candidate methods
  __out.fnSrc = {};
  const __toProbe = [
    ["App.getChartData", () => window.App && window.App.getChartData],
    ["App.getStatData", () => window.App && window.App.getStatData],
    ["ChartApp.getStatData", () => window.ChartApp && window.ChartApp.getStatData],
    ["ChartApp.getChartData", () => window.ChartApp && window.ChartApp.getChartData],
    ["ChartApp.preloadStats", () => window.ChartApp && window.ChartApp.preloadStats],
    ["ChartApp.preloadCharts", () => window.ChartApp && window.ChartApp.preloadCharts],
    ["ChartApp.drawStat", () => window.ChartApp && window.ChartApp.drawStat],
  ];
  for (const [__name, __getter] of __toProbe) {
    try {
      const __fn = __getter();
      if (typeof __fn === "function") {
        __out.fnSrc[__name] = __fn.toString().slice(0, 1500);
      } else {
        __out.fnSrc[__name] = "not-a-function (type=" + typeof __fn + ")";
      }
    } catch (__e) {
      __out.fnSrc[__name] = "error: " + String(__e);
    }
  }
  // Try invoking ChartApp.getStatData(20052) — this is what the page's own
  // chart code presumably calls to get the same data we want.
  __out.invocations = {};
  if (window.ChartApp && typeof window.ChartApp.getStatData === "function") {
    try {
      const __r = window.ChartApp.getStatData(20052);
      __out.invocations["ChartApp.getStatData(20052)"] = {
        type: typeof __r,
        isPromise: __r && typeof __r.then === "function",
        ctor: __r && __r.constructor && __r.constructor.name,
      };
      if (__r && typeof __r.then === "function") {
        try {
          const __resolved = await Promise.race([
            __r,
            new Promise((_, __rej) => setTimeout(() => __rej(new Error("timeout-15s")), 15000)),
          ]);
          __out.invocations["ChartApp.getStatData(20052)"].resolved = {
            type: typeof __resolved,
            isArray: Array.isArray(__resolved),
            keys: __resolved && typeof __resolved === "object" ? Object.keys(__resolved).slice(0, 10) : null,
            preview: JSON.stringify(__resolved).slice(0, 500),
          };
        } catch (__e) {
          __out.invocations["ChartApp.getStatData(20052)"].rejection = String(__e);
        }
      }
    } catch (__e) {
      __out.invocations["ChartApp.getStatData(20052)"] = "throw: " + String(__e);
    }
  }
  // Try ChartApp.preloadStats with all 14 IDs
  if (window.ChartApp && typeof window.ChartApp.preloadStats === "function") {
    try {
      const __r2 = window.ChartApp.preloadStats([20052, 20517, 20518, 20519, 20520, 20521, 20522, 20523, 20524, 20525, 20526, 20527, 2, 46974]);
      __out.invocations["ChartApp.preloadStats(14ids)"] = {
        type: typeof __r2,
        isPromise: __r2 && typeof __r2.then === "function",
      };
      if (__r2 && typeof __r2.then === "function") {
        try {
          const __resolved2 = await Promise.race([
            __r2,
            new Promise((_, __rej) => setTimeout(() => __rej(new Error("timeout-20s")), 20000)),
          ]);
          __out.invocations["ChartApp.preloadStats(14ids)"].resolved = {
            type: typeof __resolved2,
            isArray: Array.isArray(__resolved2),
            keys: __resolved2 && typeof __resolved2 === "object" ? Object.keys(__resolved2).slice(0, 20) : null,
            preview: JSON.stringify(__resolved2).slice(0, 500),
          };
        } catch (__e) {
          __out.invocations["ChartApp.preloadStats(14ids)"].rejection = String(__e);
        }
      }
    } catch (__e) {
      __out.invocations["ChartApp.preloadStats(14ids)"] = "throw: " + String(__e);
    }
  }
} catch (__e) {
  __out.fatal = String(__e);
  __out.stack = (__e && __e.stack) || null;
}
const __tag = document.createElement("pre");
__tag.id = "ant-result";
__tag.textContent = btoa(unescape(encodeURIComponent(JSON.stringify(__out))));
document.body.appendChild(__tag);
"""
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
                backoff = 15 if last_status == 409 else self.RETRY_BACKOFF_SEC
                time.sleep(backoff)
        if last_status >= 400:
            raise RuntimeError(f"HTTP {last_status}: {last_body[:200].decode('utf-8', 'ignore')}")
        text = last_body.decode("utf-8", "ignore")
        m = re.search(r'<pre id="ant-result">([^<]*)</pre>', text)
        if not m:
            raise RuntimeError("ant-result missing in probe_ssr_data")
        decoded = base64.b64decode(m.group(1)).decode("utf-8")
        return json.loads(decoded)

    def fetch_all_series_in_one_page(
        self, seed_url: str, all_ids: list[int], timeout: int = 180
    ) -> dict[int, list[list]]:
        """[disabled — see probe_ssr_data]"""
        ids_json = json.dumps(all_ids)
        js = r"""
const __out = {};
const __series = {};
const __errors = {};
try {
  const __html = document.documentElement.outerHTML;
  const __m = __html.match(/stk["\s]*[:=]["\s]*["']([^"']+)["']/);
  if (!__m) throw new Error("token 'stk' not found in DOM");
  const __token = __m[1];
  __out.tokenPrefix = __token.slice(0, 12);
  const __ids = __IDS_JSON__;
  const __fetchOne = async (__id) => {
    try {
      const __r = await fetch("/stats/data/" + __id, {
        method: "GET",
        headers: {
          "Authorization": "Bearer " + __token,
          "Accept": "application/json, text/plain, */*",
          "X-Requested-With": "XMLHttpRequest"
        },
        credentials: "include"
      });
      const __text = await __r.text();
      let __payload;
      try { __payload = JSON.parse(__text); }
      catch { __errors[__id] = "non-JSON " + __r.status + ": " + __text.slice(0, 80); return; }
      if (__payload && __payload.success === 1) {
        const __entry = __payload.data && __payload.data["s:" + __id];
        const __pts = __entry && __entry.series && __entry.series[0];
        if (Array.isArray(__pts)) __series[__id] = __pts;
        else __errors[__id] = "no series[0] in payload";
      } else {
        __errors[__id] = "non-success: " + JSON.stringify(__payload).slice(0, 150);
      }
    } catch (__e) {
      __errors[__id] = "exception: " + String(__e);
    }
  };
  await Promise.all(__ids.map(__fetchOne));
  __out.series = __series;
  __out.errors = __errors;
  __out.successCount = Object.keys(__series).length;
  __out.errorCount = Object.keys(__errors).length;
} catch (__e) {
  __out.fatal = String(__e);
  __out.stack = (__e && __e.stack) || null;
}
const __tag = document.createElement("pre");
__tag.id = "ant-result";
__tag.textContent = btoa(unescape(encodeURIComponent(JSON.stringify(__out))));
document.body.appendChild(__tag);
""".replace("__IDS_JSON__", ids_json)
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
                backoff = 15 if last_status == 409 else self.RETRY_BACKOFF_SEC
                print(
                    f"[retry] in-one-page ScrapingAnt {last_status} attempt {attempt}, "
                    f"backing off {backoff}s",
                    file=sys.stderr,
                )
                time.sleep(backoff)
        if last_status >= 400:
            raise RuntimeError(
                f"HTTP {last_status}: {last_body[:200].decode('utf-8', 'ignore')}"
            )
        text = last_body.decode("utf-8", "ignore")
        m = re.search(r'<pre id="ant-result">([^<]*)</pre>', text)
        if not m:
            raise RuntimeError("ant-result tag missing for in-one-page fetch")
        decoded = base64.b64decode(m.group(1)).decode("utf-8")
        out = json.loads(decoded)
        if "fatal" in out:
            raise RuntimeError(f"in-page extraction fatal: {out['fatal']}")
        print(
            f"[diag] in-one-page tokenPrefix={out.get('tokenPrefix')} "
            f"successCount={out.get('successCount')} "
            f"errorCount={out.get('errorCount')}",
            file=sys.stderr,
        )
        for sid, err in (out.get("errors") or {}).items():
            print(f"[diag]   s:{sid} -> {err}", file=sys.stderr)
        return {int(k): v for k, v in (out.get("series") or {}).items()}

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
                backoff = 15 if last_status == 409 else self.RETRY_BACKOFF_SEC
                print(
                    f"[retry] {series_url} ScrapingAnt {last_status} on attempt {attempt}, "
                    f"backing off {backoff}s",
                    file=sys.stderr,
                )
                time.sleep(backoff)

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
        print(f"[probe] SSR data probe via {backend} ...")
        probe = scraper.probe_ssr_data(SEED_URL)
        print(f"[probe] result:\n{json.dumps(probe, indent=2)}", file=sys.stderr)
        raise RuntimeError("probe-only run; see [probe] output above")
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
