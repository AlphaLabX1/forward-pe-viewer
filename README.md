# Valuation & Mood — S&P 500 dashboard

A single-page viewer covering:

1. **Forward P/E** (12-month analyst estimates) for the S&P 500 + its 11 GICS
   sectors, with a 5-year percentile rank per sector
2. **Trailing P/E** (reported TTM earnings) for the same 12 series, as a
   toggleable second lens
3. **Fear & Greed** composite with an S&P 500 price overlay

**Live dashboard → <https://alphalabx1.github.io/forward-pe-viewer/>**

## What it does

- Pulls daily forward P/E (~4,600 points/series × 12) + daily Fear & Greed +
  daily SPX price from **MacroMicro** in two HTTP calls
- Pulls monthly trailing P/E (~375 points/series × 12, SPX back to 1871) from
  **worldperatio.com** as a second independent source
- Computes each sector's current P/E as a percentile of its own trailing
  5-year distribution, under both lenses
- Renders a single self-contained `index.html` with:
  - A pin-strip and ranked table with Forward / Trailing lens toggle
  - An interactive Plotly line chart with auto-rescaling Y axis
  - A Fear & Greed gauge showing current reading + 1W/1M/3M/1Y markers
  - A dual-axis F&G / SPX chart

## Stack

- `fetch.py` — scrapes MacroMicro (forward P/E, SPX price, F&G) through
  ScrapingAnt when `SCRAPINGANT_API_KEY` is set, else `curl_cffi` /
  `cloudscraper`
- `fetch_trailing.py` — scrapes worldperatio.com sector pages; the monthly
  time series is embedded directly in each page as a `detailPE_data` JS array
- `build_html.py` — reads both data sources, computes percentile ranks,
  renders the dashboard to `index.html`
- GitHub Pages serves `index.html` off the `main` branch root
- GitHub Actions runs daily at 22:00 UTC (06:00 Asia/Shanghai)

## Run locally

```bash
pip install -r requirements.txt
python fetch.py           # MacroMicro: forward P/E + F&G + SPX price
python fetch_trailing.py  # worldperatio: trailing P/E (monthly)
python build_html.py      # writes index.html
open index.html
```

## Layout

```
forward-pe-viewer/
├── fetch.py              # MacroMicro batch (14 series, 1 API call)
├── fetch_trailing.py     # worldperatio.com (12 HTML pages)
├── build_html.py         # merges both sources → self-contained HTML
├── requirements.txt
├── data/
│   ├── *.csv             # forward P/E per sector + spx_price.csv + fear_greed.csv
│   ├── raw.json          # MacroMicro raw batch response
│   └── trailing/
│       ├── *.csv         # one trailing P/E CSV per sector
│       └── raw.json      # worldperatio parsed output
├── index.html            # the dashboard (committed, served by Pages)
└── .github/workflows/daily.yml
```

## Daily update in CI

MacroMicro's Cloudflare blocks GitHub Actions datacenter IPs on sight, so
`fetch.py` routes its two requests through [ScrapingAnt](https://scrapingant.com/)'s
proxy API when the `SCRAPINGANT_API_KEY` repo secret is set. Each run costs
2 credits out of the 10,000/month free tier — ~0.6% monthly usage. Cookies
(specifically `PHPSESSID`) are preserved across the seed + API calls so the
`stk` token validates on the JSON hop.

`fetch_trailing.py` hits worldperatio.com directly — that host is on plain
Apache, no bot protection, no proxy needed.

Locally without `SCRAPINGANT_API_KEY` set, `fetch.py` falls back to
`curl_cffi` / `cloudscraper`, which works fine from a residential IP.

## Sector taxonomy

GICS 11. Tickers used in the UI: `IT`, `COMM`, `DISC`, `FIN`, `IND`, `UTIL`,
`EGY`, `RE`, `MAT`, `STPL`, `HLTH`, plus `SPX` for the index.

## Sources

- **Forward P/E, Fear & Greed, SPX daily price** — [MacroMicro](https://en.macromicro.me/)
  series `20052`, `20517–20527`, `2`, `46974`. Ultimately sourced by MacroMicro
  from S&P Dow Jones Indices. Note: S&P's "forward EPS" column retroactively
  overwrites historical forecasts with realized TTM earnings, so old P/E
  values for sectors with earnings shocks (e.g. Energy in 2020) can look
  distorted.
- **Trailing P/E** — [worldperatio.com](https://worldperatio.com/sp-500-sectors/);
  monthly series embedded in each sector's detail page as a JS array,
  starting 1995 for sectors and 1871 for the index (Shiller series).

Internal dashboard for **AlphaLabX1**.
