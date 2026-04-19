"""Fetch S&P 500 forward P/E data (overall + 11 sectors) from MacroMicro."""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import cloudscraper

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

BASE = "https://en.macromicro.me"
SEED_SERIES_ID = 20052
SEED_URL = f"{BASE}/series/{SEED_SERIES_ID}/sp500-forward-pe-ratio"
TOKEN_RE = re.compile(r'stk["\s]*[:=]["\s]*["\']([^"\']+)["\']')


def get_token(scraper: cloudscraper.CloudScraper) -> str:
    r = scraper.get(SEED_URL, timeout=30)
    r.raise_for_status()
    m = TOKEN_RE.search(r.text)
    if not m:
        raise RuntimeError("token 'stk' not found in seed page HTML")
    return m.group(1)


def fetch_data(scraper: cloudscraper.CloudScraper, token: str) -> dict:
    ids = ",".join(str(i) for i in SERIES)
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


def main() -> None:
    out_dir = Path(__file__).parent / "data"
    scraper = cloudscraper.create_scraper()
    print("[1/3] resolving token ...")
    token = get_token(scraper)
    print(f"      token: {token[:12]}... ({len(token)} chars)")
    print("[2/3] fetching 12 series in one call ...")
    data = fetch_data(scraper, token)
    print("[3/3] writing CSVs ...")
    counts = write_csvs(data, out_dir)
    write_combined(data, out_dir / "combined.csv")
    (out_dir / "raw.json").write_text(json.dumps(data, indent=2))
    for sid, name in SERIES.items():
        print(f"  {sid} {name:<24} {counts.get(sid, 0):>5} points")
    print(f"output: {out_dir}")


if __name__ == "__main__":
    main()
