# dashboard--macro-

板塊輪動與市寬儀表板 · Sector rotation & market breadth dashboard

A Claude Dashboard artifact that tracks US sector and AI-chain rotation, market breadth, futures positioning, rates,
credit, liquidity and model flow estimates, with a 繁體 / English switch and a built-in reading guide (the 讀法 /
How to read tab).

## Files

| File | What it is |
|---|---|
| `build_data.py` | Pulls the data and writes 18 JSON files to `data/` (AI baskets, typical dips and the watch list are defined at the top) |
| `flows.py` | Flow models for the 資金流向 tab (dealer gamma, CTA, vol control, VIX term structure); called at the end of `build_data.py`, and the build carries on without it if it fails |
| `daily_facts.py` | Prints the numbers for the evening facts summary from `data/` and Yahoo (used by the local evening task) |
| `index.html` | The dashboard page (runs inside the Claude Dashboard artifact type, which supplies `dash` and `d3`) |
| `build_site.py` | Builds the public website into `site/`: wraps `index.html` with a stand-in `dash` API, d3 and colour tokens, and copies a filtered copy of the data. `--private` builds a full-data local copy into `site_private/` |
| `.github/workflows/pages.yml` | Rebuilds the data and the site Tuesday to Saturday at 05:30 UTC and publishes it to GitHub Pages |
| `data/`, `site/`, `site_private/` | Generated output, not committed |

The 18 data files: `sectors`, `rrg_tails`, `stocks`, `stock_tails` (returns and relative rotation for sectors, themes,
styles, the AI baskets and single stocks), `ratios`, `ratio_summary` (the eight ratio lines), `breadth`, `breadth_now`,
`macro`, `global`, `positioning`, `positioning_now` (CFTC), `rates`, `rates_now`, `liquidity`, and `flowmodel`,
`flowmodel_now`, `gamma` (the flow models). `data/_gamma_hist.json` keeps the nightly gamma history between builds; it is
not a dataset.

## Two versions

| | Local / Claude dashboard | Public website |
|---|---|---|
| CFTC positioning, Treasury and FRED yields, breakevens, term premium, Fed balance sheet | Shown | Shown |
| Indicators derived from Yahoo prices (returns, ratios, rotation, breadth) | Shown | Shown |
| Raw Yahoo price levels (the "Last" column in macro) | Shown | Hidden; changes only |
| Index levels behind the flow models (SPX spot, gamma strikes, flip, CTA trigger levels, VIX levels) | Shown | Hidden; gamma by % distance from spot, flip as a distance |
| ICE BofA credit-spread levels | Shown | Hidden; change in bp and 3-year percentile |
| Rotation playbook, leaders list, size-down line, held and watch-list marks, nightly gamma history | Shown | Hidden |

## Private local build

`python3 build_site.py --private` builds `site_private/` with the full data plus two datasets read from the project
folder one level up: `summary` (the newest `../daily_summaries/<date>.md`, shown as the board's top section while it is
not older than the board's data) and `holdings` (`../holdings.json`, `{"updated": ..., "held": [tickers], "picks":
[tickers]}`: the positions held, which set the ● marks, and the user's watch list, which becomes the 自選股 default and
the ★ marks). Neither is ever written to `data/`, `site/` or this repo. Elsewhere (the artifact, the public site) the
holdings dataset is missing: no marks, and 自選股 starts from a neutral list of large AI-chain and tech names.

## Data sources

- Prices: Yahoo Finance via `yfinance` (dividend-adjusted)
- S&P 500 members: [datasets/s-and-p-500-companies](https://github.com/datasets/s-and-p-500-companies)
- Futures positioning: CFTC public reporting API (Traders in Financial Futures and Disaggregated reports)
- Treasury yields and real yields: the US Treasury's daily par curves first (published the same evening), FRED for
  older days and as the fallback
- Credit spreads, term premium (Kim-Wright), Fed balance sheet: FRED
- Other 10-year yields for the bond CTA signals: the ECB (euro-area AAA curve, for Germany), the Bank of England and
  Japan's Ministry of Finance
- SPX dealer gamma: Cboe delayed option quotes

## Build

```bash
pip3 install -r requirements.txt
python3 build_data.py      # full data, for the local / Claude dashboard
python3 build_site.py      # public site in site/
python3 build_site.py --private   # full-data local copy in site_private/ (never published)
python3 -m http.server 8765 --directory site   # preview at http://localhost:8765
```

`python3 build_data.py positioning` rebuilds only the CFTC, Treasury and FRED files.

## Tabs

1. **總覽 Overview**: two-timeframe regime reading, money in and out, rotation inside the AI chain, credit and curve reads, positioning changes, six headline figures, eight ratio lines
2. **板塊輪動 Rotation**: relative rotation graph and return rankings for 11 sectors, themes, styles and factors, 10 AI-chain baskets (hyperscalers plus nine supplier baskets) and up to sixteen picked stocks, against the S&P 500 or semis (SMH)
3. **AI 階段 AI stages**: how far each AI sub-sector and name has pulled back from its 60-day high against the sub-sector's typical dip, and each name's rotation stage
4. **市寬 Breadth**: share above moving averages, equal weight vs index, A-D line, McClellan, new highs minus lows, sector participation
5. **持倉與利率 Positioning & rates**: who added and cut, crowding and turns from extremes (weekly CFTC data), 10-year yield decomposition, credit vs stocks, curve, net liquidity
6. **資金流向 Flows**: model estimates of who may be forced to trade: dealer gamma, CTA (S&P 500 and 10-year bonds), vol control, VIX term structure, and what tomorrow's moves would do to them
7. **宏觀與環球 Macro & global**: macro drivers and a global sweep
8. **讀法 How to read**: what every indicator means and what to look for

Not investment advice.
