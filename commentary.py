"""Generate section 01's machine read: three findings about the P/E tables,
written by a model at build time.

Run between fetch.py and build_html.py. Writes data/insights.json, which
build_html.py renders if present and dated to the current build, and
silently skips otherwise. The file is committed, so every day's wording
lands in git history and the page still builds when this script is skipped
or the API is down.

Model access goes through the CoreServices Worker (an OpenRouter proxy) on its
free tier, identified by a fixed machine ID. No API key is involved, so
nothing secret is needed in CI.

Every number the model writes is checked against the brief; a finding with an
unfamiliar figure is dropped. The interpretation around the numbers is not
checked, and the page says so.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from build_html import DATA, table_brief

INSIGHTS_OUT = DATA / "insights.json"

PROXY = "https://coreservices-proxy.ycczkl91.workers.dev/v1/chat/completions"
MACHINE_ID = "forward-pe-viewer-ci"
MODEL = "~deepseek/deepseek-v4-flash-latest"   # free tier on the proxy
MODEL_FALLBACK = "openai/gpt-5.6-luna-pro"     # if the proxy has not allowed the above yet
TIMEOUT = 90

INSIGHTS_SYSTEM = """You find what is worth noticing in a table of sector
valuations, for readers who already know what a P/E is.

Return a JSON array of exactly 3 objects, each {"title": ..., "body": ...}.
Nothing else — no prose around it, no markdown fence.

title: 4-7 words naming the specific finding. Not a category label.
body: 2-3 sentences, at most 3 figures total.

The point of a finding is the inference, not the figures. Cite the minimum
number of figures needed to make the reader see it, then spend the rest of the
body saying what it means — something the numbers do not literally state.

GOOD — figures set up a claim about the world:
  "Health Care trades at 19.63x forward but 30.71x trailing. That 36 percent
   compression is analysts betting on sharp margin expansion, which is an
   unusual thing to underwrite at the top of a five-year range."

BAD — a sentence that only restates the brief:
  "Health Care has a forward P/E of 19.63 at percentile 97 versus a trailing
   P/E of 30.71 at percentile 69, a percentile gap of 28 points."

Never write the pattern "has a forward P/E of X at percentile Y versus a
trailing P/E of..." — that is reciting, not reporting.

What makes a finding worth reporting, in order:
- The two lenses disagree sharply about the same sector
- A sector's valuation and its implied growth do not fit each other
- A large percentile move that the multiple has not followed
- A sector at an extreme of its own five-year range

Hard rules:
- Quote figures ONLY as they appear in the brief. Never add, subtract, divide,
  or round them. Every derived figure you might want is already computed for
  you; if one is missing, describe the relationship in words instead.
- Each finding must be about a different sector.
- Dry and declarative. No hedging, no advice, no predictions, no "investors
  should". Never recommend buying or selling."""


def generate_insights() -> None:
    brief, allowed, as_of = table_brief()
    if not brief:
        print("no table data — skipping insights")
        return
    raw_out = call_model(
        INSIGHTS_SYSTEM,
        f"Today's table:\n\n{brief}\n\nReturn the 3 findings as JSON.",
        max_tokens=2000,
    )
    if not raw_out:
        print("no insights generated — leaving previous file untouched")
        return

    text = raw_out.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        print(f"rejected insights — not JSON: {text[:200]}")
        return
    if not isinstance(items, list) or not items:
        print(f"rejected insights — unexpected shape: {text[:200]}")
        return

    clean = []
    for it in items[:3]:
        if not isinstance(it, dict):
            continue
        title = " ".join(str(it.get("title", "")).split())
        body = " ".join(str(it.get("body", "")).split())
        if not title or not body:
            continue
        bad = unknown_numbers(f"{title} {body}", allowed)
        if bad:
            print(f"dropped finding — figures not in brief {bad}: {title}")
            continue
        clean.append({"title": title, "body": body})

    if not clean:
        print("rejected insights — nothing survived number checking")
        return
    INSIGHTS_OUT.write_text(json.dumps({
        "findings": clean,
        "model": USED_MODEL,
        "as_of": as_of,
    }, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {INSIGHTS_OUT.name}: {len(clean)} finding(s)")
    for c in clean:
        print(f"  · {c['title']}")


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


# deepseek-v4-flash thinks before it writes, and on the structured findings
# prompt the thinking does not converge: given 4,500 tokens it spends 4,506,
# given 6,500 it spends 6,559, and never reaches the answer. Capping the
# reasoning budget does not help — only switching it off does, after which the
# model answers immediately. Raising max_tokens alone is not a fix here.
REASONING_MODELS = ("~deepseek/",)


def _post(model: str, system: str, user: str, max_tokens: int) -> str | None:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.4,
    }
    if model.startswith(REASONING_MODELS):
        payload["reasoning"] = {"enabled": False}
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
        print(f"proxy error ({model}): {body['error']}")
        return None
    return (body["choices"][0]["message"]["content"] or "").strip()


USED_MODEL = MODEL   # whichever model actually produced the last response


def call_model(system: str, user: str, max_tokens: int = 400) -> str | None:
    """Preferred model, falling back if the proxy has not been redeployed with
    it on the allow-list yet. Records which one answered, so the page credits
    the model that actually wrote the text."""
    global USED_MODEL
    try:
        out = _post(MODEL, system, user, max_tokens)
        if out:
            USED_MODEL = MODEL
            return out
    except urllib.error.HTTPError as exc:
        if exc.code not in (400, 403):
            raise
        print(f"{MODEL} rejected by proxy ({exc.code}) — using {MODEL_FALLBACK}")
    out = _post(MODEL_FALLBACK, system, user, max_tokens)
    if out:
        USED_MODEL = MODEL_FALLBACK
    return out


def main() -> None:
    generate_insights()


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        # Commentary is decorative — never fail the daily refresh over it.
        print(f"::warning::commentary.py failed: {exc}")
