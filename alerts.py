"""Threshold alerts, run by CI after build_html.py.

Reads data/status.json and reports:
  - Any source that failed to fetch or trails the page's P/E date
  - Fear & Greed crossing 25 (extreme fear) or 75 (extreme greed), either way
  - VIX / VIX3M entering backwardation above 1 or returning to 1 or below
  - Any sector's forward-P/E 5y percentile crossing 95 (rich) or 5 (cheap)
  - v2 and U1 state changes, with the next-session action on the newest date.
    v2 warns on ARMED, NEAR_TRIGGER (S within 0.30 of its trigger), SIGNAL and EXIT_DUE,
    including the day before an expiry close. U1 warns on WATCH (VX backwardation),
    SIGNAL, SHORT (with target and stop prices), NEAR_TARGET (within 5% of the target,
    which fills at the same close), COVER_DUE and FLAT.

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
    ratio_series,
)
from fetch import UVXY_PRICE_CSV, VIX_CSV, VIX3M_CSV, VX_SETTLE_CSV, load_status
from vix_signals import State, load_uvxy, load_vx, u1_replay, v2_replay

FG_THRESHOLDS = (25, 75)
PCT_THRESHOLDS = (5, 95)


def _crossed(prev: float, cur: float, thr: float) -> bool:
    return (prev < thr) != (cur < thr)


def _advanced(entry: dict) -> bool:
    return bool(entry.get("as_of")) and entry["as_of"] > (entry.get("prior_as_of") or "")


V2_ALERTS = {State.ARMED, State.NEAR_TRIGGER, State.SIGNAL, State.EXIT_DUE}
U1_ALERTS = {State.WATCH, State.SIGNAL, State.SHORT, State.NEAR_TARGET, State.COVER_DUE, State.FLAT}


def _window(sources: dict, names: tuple[str, ...]) -> tuple[str | None, str]:
    """(prior, as_of) across the sources a signal reads: it is current only as far as the slowest one."""
    entries = [sources.get(n, {}) for n in names]
    if not all(e.get("as_of") for e in entries):
        return None, ""
    priors = [e.get("prior_as_of") for e in entries]
    return (None if None in priors else min(priors)), min(e["as_of"] for e in entries)


def signal_alerts(sources: dict) -> list[str]:
    lines = []
    for name, names, watched in (("v2", ("cboe_vx",), V2_ALERTS),
                                 ("U1", ("cboe_vx", "koyfin_prices"), U1_ALERTS)):
        prior, as_of = _window(sources, names)
        if not as_of or (prior and as_of <= prior):
            continue
        days, px = load_vx(VX_SETTLE_CSV)
        result = v2_replay(days, px) if name == "v2" else u1_replay(days, px, load_uvxy(UVXY_PRICE_CSV))
        if str(result.state.as_of) != as_of:
            continue
        for transition in result.transitions:
            d = str(transition.date)
            if transition.state not in watched or not (prior < d <= as_of if prior else d == as_of):
                continue
            detail = f": {result.state.action}" if d == as_of else ""
            lines.append(f"{name} → {transition.state.value} ({d}){detail}")
    return lines


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

    vix_source = sources.get("cboe_vix", {})
    if _advanced(vix_source):
        ratios = ratio_series(_load_csv_points(VIX_CSV), _load_csv_points(VIX3M_CSV))
        if len(ratios) >= 2 and ratios[-1][0] == vix_source["as_of"]:
            (_, prev), (d, cur) = ratios[-2], ratios[-1]
            if (prev > 1) != (cur > 1):
                state = "entered backwardation" if cur > 1 else "returned to contango"
                lines.append(f"VIX / VIX3M {state}: {prev:.2f} → {cur:.2f} ({d})")

    lines.extend(signal_alerts(sources))

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
