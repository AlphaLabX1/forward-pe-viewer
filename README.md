# Valuation & Mood

A static dashboard of S&P 500 and sector valuations, market sentiment, and
breadth. GitHub Actions rebuilds it every weekday and GitHub Pages serves the
result.

**Live dashboard: <https://alphalabx1.github.io/forward-pe-viewer/>**

## What the page shows

The page is one self-contained `index.html` with nine numbered cards. Cards
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
| 09 | SPY and QQQ rebased to 100, against the share of S&P 500 stocks above their 50-day and 200-day averages |

The page date is the date of the latest Koyfin P/E reading. Each card shows
the date of its own data. A source that failed to fetch, or whose data is
older than the page date, turns red on its cards and is named in the
masthead. If the page itself is more than four days old, a banner says so.

## Pipeline

The workflow `.github/workflows/daily.yml` runs at 22:00 UTC, Monday to
Friday. Each step reads the committed files that the previous step wrote.

1. `fetch.py` downloads every series, validates it, and writes it to
   `data/`. It then writes `data/status.json`.
2. `python -m unittest discover -s tests` runs the test suite.
3. `commentary.py` asks a model for the section 01 findings and writes
   `data/insights.json`.
4. `build_html.py` reads `data/` and writes `index.html`.
5. `alerts.py` sends a Telegram message for failed or stale sources and for
   threshold crossings.
6. The workflow commits `data/` and `index.html` to `main`.

The production scripts use only the Python standard library.

### Sources

| Source | Series | Files |
| --- | --- | --- |
| Koyfin (`koyfin_pe`) | Forward (`f_pe`) and trailing (`f_peltm`) P/E for SPY and the 11 sector SPDR ETFs | `data/<id>_<name>.csv`, `data/trailing/<id>_<name>.csv` |
| Koyfin (`koyfin_prices`) | SPY price, QQQ price, QQQ forward P/E | `data/spx_price.csv`, `data/qqq_price.csv`, `data/qqq_forward_pe.csv` |
| Koyfin (`us10y`) | US 10-year Treasury yield | `data/us10y.csv` |
| MacroMicro (`fear_greed`) | CNN Fear & Greed index, chart 50108, stat 22748 | `data/fear_greed.csv` |
| MacroMicro (`breadth`) | % of S&P 500 above the 50-day and 200-day average, chart 81081, stats 18331 and 22718 | `data/sp500_breadth.csv` |

Koyfin is called through its unauthenticated web API. The instrument IDs are
in `fetch.py`. The numeric series IDs (20052 for the S&P 500, 20517 to 20527
for sectors) are legacy MacroMicro IDs and are kept as keys and filenames.

MacroMicro blocks datacenter IPs. In CI, `fetch.py` sends its MacroMicro
requests through [ScrapingAnt](https://scrapingant.com/) when the
`SCRAPINGANT_API_KEY` secret is set. Each chart needs two requests: the chart
page, which issues a token, and the data call that uses it. The session
cookie is carried between the two. Without the key, `fetch.py` uses
`curl_cffi`, which works from a residential IP.

### Validation

`fetch.py` defines every stored series in one `CATALOG`: its file, value
bounds, whether new data merges with or replaces the stored file, and a
`valid_from` date. Before a write, `check_series` rejects a reply that has a
value outside the bounds, ends earlier than the stored data, or has fewer
than 90% of the stored rows. A rejected or failed series keeps its previous
file, and the failure goes into `status.json`.

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
- A sector's forward P/E five-year percentile crossing 5 or 95.

With the `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` secrets set, it sends a
Telegram message. Without them, it prints the lines. It never fails the run.

## Run locally

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python fetch.py          # refresh data/ (needs network)
python3 build_html.py              # write index.html from data/
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
tests/               unittest suite
data/                committed CSVs, status.json, insights.json
index.html           the built page, served by GitHub Pages
.github/workflows/daily.yml
```

## Sector tickers

The UI uses `SPX` for the index and `IT`, `COMM`, `DISC`, `FIN`, `IND`,
`UTIL`, `EGY`, `RE`, `MAT`, `STPL`, `HLTH` for the 11 GICS sectors.

Internal dashboard for **AlphaLabX1**.
