# Valuation & Mood

A static dashboard of S&P 500 and sector valuations, market sentiment, and
breadth. GitHub Actions rebuilds it every weekday and GitHub Pages serves the
result.

**Live dashboard: <https://alphalabx1.github.io/forward-pe-viewer/>**

## What the page shows

The page is one self-contained `index.html` with fourteen numbered cards. Cards
01 to 04 have a Forward / Trailing lens toggle.

| Card | Content |
| --- | --- |
| 01 | Each sector's P/E as a percentile of its own last five years, the model's findings about the table, and an "ask the table" panel |
| 02 | Ranked table: P/E, implied earnings growth, five-year percentile and range |
| 03 | Change in five-year percentile over one week and one month |
| 04 | P/E history per series, or its rolling five-year percentile |
| 05 | CNN Fear & Greed gauge with 1W / 1M / 3M / 1Y readings |
| 06 | Fear & Greed against SPY |
| 07 | SPY returns 3, 6 and 12 months after extreme-fear and extreme-greed readings |
| 08 | SPY or QQQ price, forward P/E with its 5-year P20 to P80 band and 200-day average, earnings yield against the 10-year Treasury yield |
| 09 | SPY and QQQ rebased to 100, against the share of S&P 500 or Nasdaq-100 stocks above their 50-day and 200-day averages |
| 10 | Each day's SPY distance below its 52-week high against breadth, colored by the next three months' return, with stats for days near the high while under half of stocks are above their average |
| 11 | Equal-weight against cap-weight price ratios for RSP / SPY and QQEW / QQQ, with recent changes and five-year percentiles |
| 12 | SPY, the cumulative S&P 500 advance/decline line, daily net advancers, and the McClellan Oscillator |
| 13 | VIX / VIX3M against SPY with backwardation bands, a 9D to 6M term-curve snapshot, and forward SPY returns by curve state and exit |
| 14 | Daily default v2 VX calendar-spread and Z1 short-UVXY rule outputs, next actions, and last five closed trades |

The page date is the date of the latest Koyfin P/E reading. Each card shows
the date of its own data. A source that failed to fetch, or whose data is
older than the page date, turns red on its cards and is named in the
masthead. Cboe closes and MacroMicro member counts may trail by one session
before being marked stale. If the page itself is more than four days old, a
banner says so.

## Pipeline

The workflow `.github/workflows/daily.yml` runs at 07:00 UTC, Tuesday to
Saturday, after each US trading day and after Cboe posts the VX settles. Each step reads the committed files
that the previous step wrote.

1. `fetch.py` downloads every series, validates it, and writes it to
   `data/`. It then writes `data/status.json`.
2. `python -m unittest discover -s tests` runs the test suite.
3. `commentary.py` asks a model for the section 01 findings and writes
   `data/insights.json`.
4. `build_html.py` reads `data/` and writes `index.html`; `vix_signals.py`
   prints the daily states and next-session actions.
5. `alerts.py` sends a Telegram message for failed or stale sources and for
   threshold crossings.
6. The workflow commits `data/` and `index.html` to `main`.

The production scripts use only the Python standard library.

### Sources

| Source | Series | Files |
| --- | --- | --- |
| Koyfin (`koyfin_pe`) | Forward (`f_pe`) and trailing (`f_peltm`) P/E for SPY and the 11 sector SPDR ETFs | `data/<id>_<name>.csv`, `data/trailing/<id>_<name>.csv` |
| Koyfin (`koyfin_prices`) | SPY, RSP, QQQ and QQEW prices, plus QQQ forward P/E | `data/spx_price.csv`, `data/rsp_price.csv`, `data/qqq_price.csv`, `data/qqew_price.csv`, `data/qqq_forward_pe.csv` |
| Koyfin (`us10y`) | US 10-year Treasury yield | `data/us10y.csv` |
| MacroMicro (`fear_greed`) | CNN Fear & Greed index, chart 50108, stat 22748 | `data/fear_greed.csv` |
| MacroMicro (`breadth`) | % of S&P 500 above the 50-day and 200-day average, chart 81081, stats 18331 and 22718; % of Nasdaq-100 above those averages, chart 96064, stats 18332 and 25229 | `data/sp500_breadth.csv`, `data/ndx_breadth.csv` |
| Cboe (`cboe_vix`) | Daily VIX, VIX3M, VIX9D and VIX6M closes | `data/vix.csv`, `data/vix3m.csv`, `data/vix9d.csv`, `data/vix6m.csv` |
| Cboe (`cboe_vx`) | Monthly VX daily settles, expiry window from 400 calendar days ago through the fifth upcoming monthly | `data/vx_settle.csv` (`date,expiry,settle`; merged history) |
| MacroMicro (`advance_decline`) | S&P 500 advancers, unchanged and decliners, chart 136200, stats 76090, 76092 and 76091 | `data/sp500_ad.csv` |

Koyfin is called through its unauthenticated web API. The instrument IDs are
in `fetch.py`. The numeric series IDs (20052 for the S&P 500, 20517 to 20527
for sectors) are legacy MacroMicro IDs and are kept as keys and filenames.

MacroMicro blocks datacenter IPs. In CI, `fetch.py` sends its MacroMicro
requests through [ScrapingAnt](https://scrapingant.com/) when the
`SCRAPINGANT_API_KEY` secret is set. One run-scoped session loads a chart page
once to obtain a token, then uses that token for every chart data call. This is
one seed request plus one request per chart. A failed chart data call does not
stop the other charts. Without the key, `fetch.py` uses `curl_cffi`, which
works from a residential IP.

### Validation

`fetch.py` defines every stored series in one `CATALOG`: its file, value
bounds, whether new data merges with or replaces the stored file, and a
`valid_from` date. Before a write, `check_series` rejects a reply that has a
value outside the bounds, ends earlier than the stored data, or has fewer
than 90% of the stored rows. Rolling-window sources can define a fixed minimum
reply length instead. The advance/decline chart uses a 200-row minimum because
MacroMicro serves only its latest year while the stored file grows by merging
new windows. VX settles use the same 200-row floor and merge on both date
and expiry. A rejected or failed series keeps its previous file, and the
failure goes into `status.json`.

Some upstream history is wrong and is filtered out on every write:

- Financials forward P/E before 2016-01-01 reads 165 to 500.
- QQQ forward P/E before 2011-10-25 swings between 0.2 and 25.
- Communication Services has a bad first reading on its launch day,
  2018-06-19.
- `spx_price.csv` holds SPY prices from 1993-01-29. Isolated one-day price
  spikes that revert are dropped.

### status.json

```json
{
  "generated_at": "2026-09-24T22:10:03+00:00",
  "sources": {
    "fear_greed": {
      "ok": false,
      "as_of": "2026-09-23",
      "prior_as_of": "2026-09-23",
      "error": "HTTP 423: ...",
      "missing": ["fear_greed"]
    }
  }
}
```

`as_of` is the latest date stored for the source after the run, and
`prior_as_of` is the date before it. `missing` lists the failed series and
appears only when a series failed. `alerts.py` checks threshold crossings
only for series whose date advanced in this run, so a second run on the same
day does not repeat an alert.

### VIX spread signals

`vix_signals.py` replays all stored data on each run, with no saved position state.
VX dates require the first four upcoming monthly contracts from the expiry
calendar, including holiday shifts. Incomplete ladders are dropped. Missing
sessions reset arming and cancel unfilled entries; a gap during an open trade
reports an error rather than inventing a fill.
Default v2 ports `forex/qc/b1_main.py` and `DECISION_DEFAULT_V2.md`: the active
near/far pair switches when fewer than 10 sessions remain, arming requires
S > 1.50, and a 0.60 drop from that pair's high fires the signal. On switch
days the old pair decides; the new pair inherits its own high since arming.
At the next settle, choose the pair again, short near and buy the contract
after far. Exits use near minus far: target −1.00, stop entry S + 3.00,
return to entry S after touching zero, or 21 sessions. They fill at the next
settle, except the reference's expiry exit at the last pre-expiry settle.
Net trade points include the 0.20 round-trip cost.

Z1 uses only `data/vix.csv`: when flat, a close above 30 after a close at or
below 30 signals a 2%-of-equity UVXY short at the next close. Only weekdays
outside the v2 holiday calendar count for signals and the hold clock. Cover 84
sessions after the fill and ignore triggers while short. The cover date is
an estimate using regular exchange holidays; unscheduled closures can shift
it. UVXY prices and returns are not inferred from VIX. Z1 ignores the
author's discretionary event filter and option structures. These are rule
outputs, not advice.

`vx_settle.csv` contains live Cboe settles through 2026-11-06. The current
expiry window starts at 2025-10-22, so its first complete ladder is
2025-09-17. Backfill once with `python3 fetch.py --backfill-vx`: this fetches
every monthly contract from June 2013 through the daily window's fifth
upcoming contract and merges the result into the stored file. The daily run
keeps its 400-day expiry window and merges, retaining older history. The
backfill is still pending because sandbox DNS blocked `cdn.cboe.com`; no
replacement fixture was created. Backtest context refers to the full research
sample, not just the recent file.

Z1 reference parity starts flat on 2011-10-04, matching the research window.
The continuous daily replay starts with the full VIX history: its August
2011 position remains open until December 5, so it ignores the October 17
trigger and next signals on December 8, exiting on 2012-04-12. Both replays
are flat before the next trigger on 2015-08-24. From that trade onward, all
12 reference trades match on signal, entry, and exit dates. Starting flat
at the reference window matches all 13 trades on those dates.

### Commentary

The masthead's "Today's read" is a template in `build_html.standfirst()`. It
names the richest and cheapest sector against their own five years and the
largest one-week move. It says "richest in five years" only at the 95th
percentile or above.

The section 01 findings come from a model. `commentary.py` sends the table
brief to a personal OpenRouter proxy (a Cloudflare Worker) and keeps only
findings whose numbers all appear in the brief. The page states that the
interpretation is the model's and is not checked. The "ask the table" panel
calls the same proxy from the browser. No API key is involved.

### Alerts

`alerts.py` reports:

- A source that failed or is older than the P/E date.
- Fear & Greed crossing 25 or 75.
- VIX / VIX3M crossing above 1 into backwardation or returning to 1 or below.
- v2 transitions into ARMED, SIGNAL or EXIT_DUE; Z1 into SIGNAL or COVER_DUE, only when the source date advances.
- A sector's forward P/E five-year percentile crossing 5 or 95.

With the `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` secrets set, it sends a
Telegram message. Without them, it prints the lines. It never fails the run.

## Run locally

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python fetch.py          # refresh data/ (needs network)
python3 build_html.py              # write index.html from data/
python3 vix_signals.py             # daily states and next-session actions
python3 -m unittest discover -s tests
open index.html
```

`requirements.txt` lists `curl_cffi`, which only the local MacroMicro
fallback needs. `build_html.py` and the tests need nothing beyond Python 3.10
or later, and build from the committed `data/` without fetching.

## Layout

```
fetch.py             sources, series catalog, validation, status.json
build_html.py        page build: payload, rendered fragments, HTML/CSS/JS template
commentary.py        section 01 findings (model, number-checked)
alerts.py            Telegram alerts
vix_signals.py       pure v2/Z1 replays and daily signal script
tests/               unittest suite
data/                committed CSVs, status.json, insights.json
index.html           the built page, served by GitHub Pages
.github/workflows/daily.yml
```

## Sector tickers

The UI uses `SPX` for the index and `IT`, `COMM`, `DISC`, `FIN`, `IND`,
`UTIL`, `EGY`, `RE`, `MAT`, `STPL`, `HLTH` for the 11 GICS sectors.

Internal dashboard for **AlphaLabX1**.
