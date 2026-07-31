"""Threshold alerts, run by CI after build_html.py.

Compares the last two observations of each series and reports:
  - Fear & Greed crossing 25 (extreme fear) or 75 (extreme greed), either way
  - Any sector's forward-P/E 5y percentile crossing 95 (rich) or 5 (cheap)

Sends one Telegram message when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are
set (repo secrets in CI); otherwise just prints the lines. Exits 0 on any
error — alerting must never fail the data-refresh workflow.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from build_html import DATA, SECTOR_TICKERS, _load_csv_points, compute_5y
from fetch import SERIES

FG_THRESHOLDS = (25, 75)
PCT_THRESHOLDS = (5, 95)


def _crossed(prev: float, cur: float, thr: float) -> bool:
    return (prev < thr) != (cur < thr)


def collect_alerts() -> list[str]:
    lines: list[str] = []

    fg = _load_csv_points(DATA / "fear_greed.csv")
    if len(fg) >= 2:
        (_, prev), (d, cur) = fg[-2], fg[-1]
        for thr in FG_THRESHOLDS:
            if _crossed(prev, cur, thr):
                arrow = "fell below" if cur < thr else "rose above"
                lines.append(f"Fear & Greed {arrow} {thr}: {prev:.0f} → {cur:.0f} ({d})")

    fwd = json.loads((DATA / "raw.json").read_text()).get("forward", {})
    for sid in SERIES:
        pts = fwd.get(str(sid)) or []
        if len(pts) < 2:
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
    lines = collect_alerts()
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
