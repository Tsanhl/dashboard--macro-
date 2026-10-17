# dashboard--macro-

板塊輪動與市寬儀表板 · Sector rotation & market breadth dashboard

A Traditional Chinese / English dashboard for US sectors, the AI chain, breadth, futures positioning, rates,
credit and model estimates. Prices and relative performance are not observed fund flows; CTA and volatility
control are estimates, not a census of institutional positions.

## Run locally

Python 3.12 or later on macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python refresh.py
python serve.py
```

Open http://localhost:8766. The **更新數據 / Update data** button fetches source data, recalculates indicators and
rebuilds the local board. Keep the server running. A plain static HTTP server can display the files but cannot
perform updates. The update endpoint accepts same-origin requests on loopback only; it is not a public backend.

The updater uses a process lock, a staged build, source-date validation and rollback backups. Concurrent clicks
share one update; an independently running command holds the same lock. A failed batch retains the previous
served board. Logs and prior snapshots stay local in the parent `board_updates/` folder (public runs use ignored
`.refresh/runs/`). They are intentionally retained for recovery, not committed.

A standalone clone works without personal files. Optional holdings, private summaries, borrower evidence and
AI-cycle tracker inputs live outside the repository; they are neither downloaded from this repo nor published.

## What the button updates

| Cadence | Content | Rule |
|---|---|---|
| Each completed US session | Prices, sector/AI returns, breadth, A/D, McClellan, highs/lows, relative strength, daily rotation, CTA/volatility models | Calendar-aware close plus a settling buffer; never label an unfinished bar as the close |
| Check daily for releases | Nominal/real Treasury yields, breakevens, curve, HY/IG/CCC/BB spreads | Keep each source's observation date; a carried value is not a new observation |
| Check daily for reference quotes, local only | Specified CoreWeave/Oracle BondTerminal bonds | Same ISIN, dated price/YTW and convention; gaps/conflicts remain unconfirmed. These are not CDS or OAS |
| New weekly report | CFTC positions, crowding, one-/four-week changes | Usually Friday release of Tuesday positions; unchanged between reports |
| Completed trading week | Confirmed rotation/leader ranks and consecutive weeks | Preserve the prior confirmation during the week; daily previews stay separate |
| New weekly release | Fed balance sheet / aligned net liquidity | Match the existing weekly observation basis |

Long lookbacks do not imply infrequent updates: a daily 20-day return is recalculated every completed session.
Research thresholds, dip statistics and recovery studies are not refitted by this button.

**The button does not rewrite AI analysis or the event calendar.** A narrative retains its own session date,
with an explicit notice when market data is newer. The existing independently configured after-close AI task
continues separately. After reviewing/saving a narrative, render it through the same lock without a new download:

```bash
python refresh.py --render-only
```

## Public website and GitHub

```bash
python refresh.py --public
```

This produces `site/`; local mode produces `site_private/`. Publishing the public build never replaces the private
site. The public button reloads **already published** data. An adjacent owner link opens the authenticated GitHub
Actions workflow; only repository users with the required write permission can run a fresh build there. No token
is embedded in the browser. Manual runs, pushes and the existing Tue–Sat 05:30 UTC job use the same updater.

The public build excludes personal holdings/picks, company valuations, position plans, private borrower evidence,
raw Yahoo price/index levels and ICE BofA spread levels. Derived returns, model directions and credit changes are
retained under the existing display policy. This filtering is not a redistribution licence from data providers.
Raw inputs, logs and generated sites are ignored by Git. The separately reviewed `summary.md` is the only public
narrative; neither the button nor GitHub Actions copies a private summary into it.

## Code map

| File | Responsibility |
|---|---|
| `refresh.py`, `market_clock.py` | Shared transaction, backups, validation and exchange calendar |
| `serve.py`, `refresh.js` | Local button/status API and public reload/owner entry |
| `build_data.py` | Fetch and calculate the board datasets; lower-level worker invoked by the updater |
| `flows.py`, `alerts.py` | Source observations, model calculations and existing risk rules |
| `bond_reference.py` | Fail-closed parsing of dated specified-bond price/YTW references |
| `daily_summary.py`, `daily_facts.py` | Reviewable facts from the same snapshot, not an independent market download |
| `build_site.py`, `index.html` | Private/public rendering and interactive charts |
| `.github/workflows/pages.yml` | Tests, public refresh and Pages deployment |
| `tests/` | Date/comparison, public-boundary, rollback, concurrency and endpoint checks |

`build_site.DATASETS` defines the required datasets. `refresh_meta` adds observation dates; optional private data
is separate. Active flow models are equity/Treasury CTA, volatility control and VIX term structure; historical
research files are not additional active updaters.

## Sources and pages

Sources: Yahoo Finance (`yfinance`, adjusted prices), CFTC, US Treasury, FRED, Cboe, ECB, Bank of England,
Japan's Ministry of Finance, public S&P constituent lists and delayed BondTerminal reference quotations.

Pages: **總覽**, **板塊輪動**, **AI階段**, **市寬**, **持倉與利率**, **宏觀與環球**, **讀法**.

Run checks with `python -m unittest discover -s tests -v`. Not investment advice.
