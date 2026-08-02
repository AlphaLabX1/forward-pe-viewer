"""Generate the dashboard's daily standfirst — the two-sentence read at the top
of the masthead — from the numbers the build already computed.

Run between fetch.py and build_html.py. Writes data/commentary.json, which
build_html.py renders if present and silently skips if not. The file is
committed, so every day's wording lands in git history and the page still
builds when this script is skipped or the API is down.

Model access goes through the CoreServices Worker (an OpenRouter proxy) on its
free tier, identified by a fixed machine ID — no API key involved, so nothing
secret is needed in CI.

The model is given a compact brief of today's readings and asked for prose.
Every number it writes back is checked against that brief; a single unfamiliar
figure rejects the whole response and the page falls back to static copy.
Sentences about numbers are worth generating — the numbers themselves are not.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from build_html import (
    DATA,
    SECTOR_TICKERS,
    _load_csv_points,
    _round_series,
    build_family_payload,
    compute_5y,
    _fg_word,
)
from fetch import SERIES

ROOT = Path(__file__).parent
OUT = DATA / "commentary.json"

PROXY = "https://coreservices-proxy.ycczkl91.workers.dev/v1/chat/completions"
MACHINE_ID = "forward-pe-viewer-ci"
MODEL = "openai/gpt-5.6-luna-pro"   # free tier on the proxy
TIMEOUT = 90

SYSTEM = """You write the standfirst for a daily equity-valuation dashboard.

House voice: dry, concrete, declarative. The publication already sounds like
"Right is expensive" and "Descriptive, not a strategy". Match that.

Hard rules:
- Exactly two sentences. Under 55 words total.
- Say what is unusual today and what it means. No hedging, no throat-clearing,
  no "it's important to note", no advice, no predictions.
- Use ONLY figures that appear in the brief. Never compute, round, or invent a
  number. If you are unsure of a figure, describe it in words instead.
- No greeting, no headline, no markdown, no quotes around the output.
- Never tell the reader to buy, sell, or wait."""


def _pct_asof(points, asof: date):
    asof_str = asof.isoformat()
    for i in range(len(points) - 1, -1, -1):
        if points[i][0] <= asof_str:
            five = compute_5y(points[: i + 1])
            return five["rank"] if five else None
    return None


def build_brief() -> tuple[str, set[str]]:
    """Return (brief text for the model, set of number tokens it may use)."""
    raw = json.loads((DATA / "raw.json").read_text())
    fwd_raw = raw.get("forward", {})
    forward_points = {
        sid: _round_series(fwd_raw[str(sid)], 4)
        for sid in SERIES
        if str(sid) in fwd_raw
    }
    fam = build_family_payload(forward_points)
    rows = fam["summary"]

    fg = _load_csv_points(DATA / "fear_greed.csv")
    allowed: set[str] = set()

    def num(v, nd=0):
        """Format a number and register it as quotable."""
        s = f"{v:.{nd}f}"
        allowed.add(s.lstrip("+-"))
        return s

    lines = [f"Date: {fam['latest_date']}", "", "Sector forward P/E, 5-year percentile (0=cheapest, 100=richest):"]
    for r in rows:
        d1w = f", 1w change {r['d1w']:+.0f}" if r.get("d1w") is not None else ""
        if r.get("d1w") is not None:
            allowed.add(f"{abs(r['d1w']):.0f}")
        lines.append(
            f"  {r['name']} ({SECTOR_TICKERS[r['id']]}): "
            f"percentile {num(r['rank_5y'])}, P/E {num(r['latest'], 1)}{d1w}"
        )

    if fg:
        cur_d, cur_v = fg[-1]
        lines += ["", f"Fear & Greed: {num(cur_v)} ({_fg_word(cur_v)}) on {cur_d}"]
        cur_date = date.fromisoformat(cur_d)
        for label, days in (("1 week ago", 7), ("1 month ago", 30)):
            hit = next((v for d, v in reversed(fg) if d <= (cur_date - timedelta(days=days)).isoformat()), None)
            if hit is not None:
                lines.append(f"  {label}: {num(hit)}")

    # Percentile-space movers give the model something to actually say.
    movers = [r for r in rows if r.get("d1w") is not None]
    if movers:
        movers.sort(key=lambda r: -abs(r["d1w"]))
        lines += ["", "Biggest percentile moves this week:"]
        for r in movers[:3]:
            direction = "richer" if r["d1w"] > 0 else "cheaper"
            lines.append(f"  {r['name']}: {r['d1w']:+.0f} points {direction}")

    return "\n".join(lines), allowed


NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# Small integers read as ordinary prose ("two sectors", "the 500"), not as data.
PROSE_NUMBERS = {str(n) for n in range(0, 13)} | {"100", "500"}


def unknown_numbers(text: str, allowed: set[str]) -> list[str]:
    """Numbers the model wrote that were not in the brief.

    Matching is numeric, not textual: a token is accepted only if it equals an
    allowed figure exactly or is that figure rounded to a whole number (47 for
    47.3). Prefix matching would let almost anything through — "23.7" shares a
    leading digit with some allowed value nearly every day.
    """
    allowed_vals = []
    for a in allowed:
        try:
            allowed_vals.append(float(a))
        except ValueError:
            continue
    bad = []
    for tok in NUM_RE.findall(text):
        if tok in allowed or tok in PROSE_NUMBERS:
            continue
        try:
            v = float(tok)
        except ValueError:
            bad.append(tok)
            continue
        if any(abs(v - a) < 1e-9 or abs(v - round(a)) < 1e-9 for a in allowed_vals):
            continue
        bad.append(tok)
    return bad


def call_model(brief: str) -> str | None:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Today's readings:\n\n{brief}\n\nWrite the standfirst."},
        ],
        "max_tokens": 400,
        "temperature": 0.4,
    }
    req = urllib.request.Request(
        PROXY,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Machine-ID": MACHINE_ID,
            # Cloudflare in front of the Worker 403s the default Python-urllib UA.
            "User-Agent": "forward-pe-viewer/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        body = json.loads(resp.read())
    if "error" in body:
        print(f"proxy error: {body['error']}")
        return None
    return (body["choices"][0]["message"]["content"] or "").strip()


def main() -> None:
    brief, allowed = build_brief()
    text = call_model(brief)
    if not text:
        print("no commentary generated — leaving previous file untouched")
        return

    text = " ".join(text.split())
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()

    bad = unknown_numbers(text, allowed)
    if bad:
        print(f"rejected — figures not in brief: {bad}\n  text: {text}")
        return
    if len(text.split()) > 75:
        print(f"rejected — too long ({len(text.split())} words): {text}")
        return

    latest = json.loads((DATA / "raw.json").read_text()).get("forward", {}).get("20052", [])
    OUT.write_text(json.dumps({
        "text": text,
        "model": MODEL,
        "as_of": latest[-1][0] if latest else "",
    }, indent=2) + "\n")
    print(f"wrote {OUT.name}: {text}")


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        # Commentary is decorative — never fail the daily refresh over it.
        print(f"::warning::commentary.py failed: {exc}")
