# Forward P/E — S&P 500 sector viewer

Daily 12-month forward P/E for the S&P 500 and its 11 GICS sectors, with a
5-year percentile rank per sector as the headline feature.

**Live dashboard → <https://alphalabx1.github.io/forward-pe-viewer/>**

## What it does

- Pulls ~4,600 daily data points per series (12 series total) from MacroMicro
  in two HTTP calls
- Computes each sector's current forward P/E as a percentile of its own
  trailing 5-year distribution
- Renders a single self-contained `index.html` with:
  - A pin-strip showing all 12 sectors on a 0–100 valuation axis
  - A ranked table sorted from richest to cheapest, with sector definitions
    and top-5 constituents on hover
  - An interactive Plotly line chart with auto-rescaling Y axis

## Stack

- `fetch.py` — scrapes MacroMicro using `curl_cffi` (Chrome TLS
  impersonation), falls back to `cloudscraper`
- `build_html.py` — reads `data/raw.json`, computes ranks, renders the
  dashboard to `index.html`
- GitHub Pages serves `index.html` off the `main` branch root
- GitHub Actions runs daily at 22:00 UTC (06:00 Asia/Shanghai) —
  [see caveat below](#daily-update-caveat)

## Run locally

```bash
pip install -r requirements.txt
python fetch.py       # writes data/raw.json + data/*.csv
python build_html.py  # writes index.html
open index.html
```

## Layout

```
forward-pe-viewer/
├── fetch.py              # 2 HTTP calls, 12 series
├── build_html.py         # data → self-contained HTML
├── requirements.txt
├── data/
│   ├── *.csv             # one per series + combined.csv
│   └── raw.json          # gitignored, regenerated on each fetch
├── index.html            # the dashboard (committed, served by Pages)
└── .github/workflows/daily.yml
```

## Daily update caveat

GitHub Actions runners are issued datacenter IPs that MacroMicro's Cloudflare
hard-403s on sight — neither `cloudscraper` nor TLS-impersonating `curl_cffi`
gets through the IP block. The scheduled workflow therefore **exits 0 with a
warning** when the fetch fails, so the Page keeps serving the last-known-good
snapshot instead of breaking.

For actual daily refresh, run the fetch from a residential IP:

- **Home cron** on a machine you keep on — simplest, free
- **PythonAnywhere** free tier (1 scheduled task/day), then `git push`
- **Self-hosted GitHub Actions runner** on a home machine
- **A residential-proxy service** (ScraperAPI, ScrapingBee, BrightData) in
  front of the existing workflow

## Sector taxonomy

GICS 11. Tickers used in the UI: `IT`, `COMM`, `DISC`, `FIN`, `IND`, `UTIL`,
`EGY`, `RE`, `MAT`, `STPL`, `HLTH`, plus `SPX` for the index.

## Source

Data: [MacroMicro](https://en.macromicro.me/) series
`20052, 20517–20527`.

Internal dashboard for **AlphaLabX1**.
