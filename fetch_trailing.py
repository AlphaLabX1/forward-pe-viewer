"""Fetch monthly trailing P/E for S&P 500 + 11 GICS sectors from worldperatio.com.

Each sector / index page embeds its full monthly time series as a JS variable
  detailPE_data = [[Date.UTC(Y, M, D), value], ...];
M is JS-style zero-indexed. SPX goes back to 1871 (Shiller series); sectors
start around 1995. Apache host, no Cloudflare — no proxy needed."""

from __future__ import annotations

import csv
import json
import re
import sys
import urllib.request
from pathlib import Path

from fetch import SERIES

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "data" / "trailing"

SLUGS = {
    20052: "index/sp-500",
    20517: "sector/sp-500-information-technology",
    20518: "sector/sp-500-communication-services",
    20519: "sector/sp-500-consumer-discretionary",
    20520: "sector/sp-500-financials",
    20521: "sector/sp-500-industrials",
    20522: "sector/sp-500-utilities",
    20523: "sector/sp-500-energy",
    20524: "sector/sp-500-real-estate",
    20525: "sector/sp-500-materials",
    20526: "sector/sp-500-consumer-staples",
    20527: "sector/sp-500-health-care",
}
BASE = "https://worldperatio.com"
ARRAY_RE = re.compile(r"detailPE_data\s*=\s*(\[[\s\S]+?\])\s*;")
POINT_RE = re.compile(r"Date\.UTC\((\d+),\s*(\d+),\s*(\d+)\)\s*,\s*(-?[\d.]+)")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "ignore")


def _parse_points(html: str) -> list[tuple[str, float]]:
    m = ARRAY_RE.search(html)
    if not m:
        raise RuntimeError("detailPE_data array not found on page")
    out = []
    for y, mo, d, v in POINT_RE.findall(m.group(1)):
        # JS months are 0-indexed; convert to ISO (1-indexed) month.
        out.append((f"{int(y):04d}-{int(mo) + 1:02d}-{int(d):02d}", float(v)))
    out.sort()
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw: dict[int, list[tuple[str, float]]] = {}
    for sid, slug in SLUGS.items():
        name = SERIES[sid]
        url = f"{BASE}/{slug}/"
        try:
            html = _fetch(url)
            points = _parse_points(html)
        except Exception as e:
            print(f"[warn] {sid} {name}: {e}", file=sys.stderr)
            continue
        raw[sid] = points
        fname_slug = name.lower().replace("&", "and").replace(" ", "_")
        path = OUT_DIR / f"{sid}_{fname_slug}.csv"
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "trailing_pe"])
            w.writerows(points)
        print(f"  {sid} {name:<24} {len(points):>5} points  {points[-1]}")
    (OUT_DIR / "raw.json").write_text(
        json.dumps({str(sid): points for sid, points in raw.items()}, indent=2)
    )
    print(f"output: {OUT_DIR}")


if __name__ == "__main__":
    main()
