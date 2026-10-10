# dashboard--macro-

板塊輪動與市寬儀表板 · Sector rotation & market breadth dashboard

A Claude Dashboard artifact that tracks US sector rotation, market breadth, futures positioning, rates, credit and
liquidity, with a 繁體 / English switch and a built-in reading guide (the 讀法 / How to read tab).

## Files

| File | What it is |
|---|---|
| `build_data.py` | Pulls the data and writes 13 JSON files to `data/` |
| `index.html` | The dashboard page (runs inside the Claude Dashboard artifact type, which supplies `dash` and `d3`) |
| `data/` | Generated output, not committed |

## Data sources

- Prices: Yahoo Finance via `yfinance` (dividend-adjusted)
- S&P 500 members: [datasets/s-and-p-500-companies](https://github.com/datasets/s-and-p-500-companies)
- Futures positioning: CFTC public reporting API (Traders in Financial Futures and Disaggregated reports)
- Yields, breakevens, term premium, credit spreads, Fed balance sheet: FRED

## Build

```bash
pip3 install yfinance pandas numpy
python3 build_data.py
```

`python3 build_data.py positioning` rebuilds only the CFTC and FRED files.

## Tabs

1. **總覽 Overview**: two-timeframe regime reading, money in and out, six headline figures, eight ratio lines
2. **板塊輪動 Rotation**: relative rotation graph (vs S&P 500) and return rankings
3. **市寬 Breadth**: share above moving averages, equal weight vs index, A-D line, McClellan, new highs minus lows, sector participation
4. **持倉與利率 Positioning & rates**: futures positioning changes and crowding, 10-year yield decomposition, credit spreads, curve, net liquidity
5. **宏觀與環球 Macro & global**: macro drivers and a global sweep
6. **讀法 How to read**: what every indicator means and what to look for

Not investment advice.
