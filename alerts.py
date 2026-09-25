"""Threshold alerts, run by CI after build_html.py.

Reads data/status.json and reports:
  - Any source that failed to fetch or trails the page's P/E date
  - Fear & Greed crossing 25 (extreme fear) or 75 (extreme greed), either way
  - Any sector's forward-P/E 5y percentile crossing 95 (rich) or 5 (cheap)

Crossings compare the last two observations, so they are checked only for a
series that gained a new date this run; a re-run never repeats them.

Sends one Telegram message when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are
set (repo secrets in CI); otherwise just prints the lines. Exits 0 on any
error — alerting must never fail the data-refresh workflow.
"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request

from build_html import (
    FEAR_GREED_CSV,
    SECTOR_TICKERS,
    SOURCE_LABELS,
    _load_csv_points,
    compute_5y,
    freshness,
    load_pe,
)
from fetch import load_status

FG_THRESHOLDS = (25, 75)
PCT_THRESHOLDS = (5, 95)


def _crossed(prev: float, cur: float, thr: float) -> bool:
    return (prev < thr) != (cur < thr)


def _advanced(entry: dict) -> bool:
    return bool(entry.get("as_of")) and entry.get("as_of") != entry.get("prior_as_of")


def collect_alerts(status: dict) -> list[str]:
    lines: list[str] = []
    sources = status["sources"]
    pe = sources["koyfin_pe"]

    for source, f in freshness(status, pe["as_of"] or "").items():
        if f["stale"]:
            err = sources[source].get("error")
            lines.append(f"{SOURCE_LABELS.get(source, source)} not current: {f['why']}, "
                         f"last {f['as_of']}" + (f" ({err[:160]})" if err else ""))

    fg = _load_csv_points(FEAR_GREED_CSV)
    if len(fg) >= 2 and _advanced(sources["fear_greed"]):
        (_, prev), (d, cur) = fg[-2], fg[-1]
        for thr in FG_THRESHOLDS:
            if _crossed(prev, cur, thr):
                arrow = "fell below" if cur < thr else "rose above"
                lines.append(f"Fear & Greed {arrow} {thr}: {prev:.0f} → {cur:.0f} ({d})")

    fresh_pe = _advanced(pe)
    for sid, pts in load_pe("forward").items():
        if not fresh_pe or len(pts) < 2 or pts[-1][0] != pe["as_of"]:
            continue
        cur5, prev5 = compute_5y(pts), compute_5y(pts[:-1])
        if not cur5 or not prev5:
            continue
        prev_r, cur_r = prev5["rank"], cur5["rank"]
        for thr in PCT_THRESHOLDS:
            if _crossed(prev_r, cur_r, thr):
                arrow = "fell below" if cur_r < thr else "rose above"
                lines.append(
                    f"{SECTOR_TICKERS[sid]} forward-P/E 5y percentile {arrow} {thr}: "
                    f"{prev_r:.0f} → {cur_r:.0f} (P/E {cur5['current']:.1f}, {pts[-1][0]})"
                )
    return lines


def send(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — printing only.")
        print(text)
        return
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=urllib.parse.urlencode({"chat_id": chat, "text": text}).encode(),
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()
    print("alert sent to Telegram")


def main() -> None:
    lines = collect_alerts(load_status())
    if not lines:
        print("no threshold crossings today")
        return
    send("Valuation & Mood alerts\n" + "\n".join(f"• {ln}" for ln in lines)
         + "\nhttps://alphalabx1.github.io/forward-pe-viewer/")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # alerting must never break the daily refresh
        print(f"::warning::alerts.py failed: {exc}")
