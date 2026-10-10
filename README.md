# dashboard--macro-

板塊輪動與市寬儀表板 · Sector rotation & market breadth dashboard

A Claude Dashboard artifact that tracks US sector rotation, market breadth, futures positioning, rates, credit and
liquidity, with a 繁體 / English switch and a built-in reading guide (the 讀法 / How to read tab).

## Files

| File | What it is |
|---|---|
| `build_data.py` | Pulls the data and writes 15 JSON files to `data/` (AI baskets and the watch list are defined at the top) |
| `index.html` | The dashboard page (runs inside the Claude Dashboard artifact type, which supplies `dash` and `d3`) |
| `build_site.py` | Builds the public website into `site/`: wraps `index.html` with a stand-in `dash` API, d3 and colour tokens, and copies a filtered copy of the data |
| `.github/workflows/pages.yml` | Rebuilds the data and the site Tuesday to Saturday at 05:30 UTC and publishes it to GitHub Pages |
| `data/`, `site/` | Generated output, not committed |

## Two versions

| | Local / Claude dashboard | Public website |
|---|---|---|
| CFTC positioning, FRED yields, breakevens, term premium, Fed balance sheet | Shown | Shown |
| Indicators derived from Yahoo prices (returns, ratios, rotation, breadth) | Shown | Shown |
| Raw Yahoo price levels (the "Last" column in macro) | Shown | Hidden; changes only |
| ICE BofA credit-spread levels | Shown | Hidden; change in bp and 3-year percentile |

## Data sources

- Prices: Yahoo Finance via `yfinance` (dividend-adjusted)
- S&P 500 members: [datasets/s-and-p-500-companies](https://github.com/datasets/s-and-p-500-companies)
- Futures positioning: CFTC public reporting API (Traders in Financial Futures and Disaggregated reports)
- Yields, breakevens, term premium, credit spreads, Fed balance sheet: FRED

## Build

```bash
pip3 install -r requirements.txt
python3 build_data.py      # full data, for the local / Claude dashboard
python3 build_site.py      # public site in site/
python3 -m http.server 8765 --directory site   # preview at http://localhost:8765
```

`python3 build_data.py positioning` rebuilds only the CFTC and FRED files.

## Tabs

1. **總覽 Overview**: two-timeframe regime reading, money in and out, rotation inside the AI chain, credit and curve reads, positioning changes, six headline figures, eight ratio lines
2. **板塊輪動 Rotation**: relative rotation graph and return rankings for 11 sectors, themes, styles and factors, nine AI-chain baskets and up to ten picked stocks, against the S&P 500 or semis (SMH)
3. **市寬 Breadth**: share above moving averages, equal weight vs index, A-D line, McClellan, new highs minus lows, sector participation
4. **持倉與利率 Positioning & rates**: who added and cut, crowding and turns from extremes (weekly CFTC data), 10-year yield decomposition, credit vs stocks, curve, net liquidity
5. **宏觀與環球 Macro & global**: macro drivers and a global sweep
6. **讀法 How to read**: what every indicator means and what to look for

Not investment advice.
