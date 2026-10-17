"""Build the data files for the sector-rotation and market-breadth dashboard.

Run:  python3 dashboard/build_data.py
Writes JSON row files to dashboard/data/. Prices come from Yahoo Finance (yfinance),
the S&P 500 member list from github.com/datasets/s-and-p-500-companies, futures
positioning from the CFTC public reporting API, rates/credit/liquidity from FRED.
Breadth uses today's members over the whole window (survivorship bias, small).
"""
import http.client
import io
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from pandas.tseries.holiday import USFederalHolidayCalendar
from market_clock import completed_session, week_complete


OUT = Path(__file__).resolve().parent / "data"
OUT.mkdir(exist_ok=True)

# ticker: (繁體名, group)
ETFS = {
    "XLK": ("科技", "sector"), "XLC": ("通訊服務", "sector"), "XLY": ("非必需消費", "sector"),
    "XLF": ("金融", "sector"), "XLI": ("工業", "sector"), "XLE": ("能源", "sector"),
    "XLB": ("原材料", "sector"), "XLU": ("公用事業", "sector"), "XLP": ("必需消費", "sector"),
    "XLV": ("醫療保健", "sector"), "XLRE": ("房地產", "sector"),
    "GDX": ("貴金屬", "sector"),  # gold miners, shown with the 11 sectors (user, 2026-10-15); no S&P members of its own
    "SMH": ("半導體", "theme"), "IGV": ("軟件", "theme"), "KRE": ("地區銀行", "theme"),
    "XBI": ("生物科技", "theme"), "XHB": ("住宅建築", "theme"), "ITA": ("航天國防", "theme"),
    "RSP": ("標普等權", "style"), "IWM": ("羅素2000小型股", "style"), "QQQ": ("納指100", "style"),
    "MTUM": ("動量因子", "style"), "USMV": ("低波動因子", "style"),
}
GLOBAL = {
    "EWJ": "日本", "VGK": "歐洲", "FXI": "中國大型股", "EWH": "香港", "EWT": "台灣",
    "EWY": "南韓", "INDA": "印度", "EEM": "新興市場", "GLD": "黃金", "IBIT": "比特幣ETF",
}
MACRO = {
    "^TNX": ("美國10年期國債收益率", "%"), "^FVX": ("美國5年期國債收益率", "%"),
    "^IRX": ("美國3個月國庫券貼現率", "%"), "DX-Y.NYB": ("美元指數", ""),
    "CL=F": ("WTI原油", "$"), "HG=F": ("銅", "$"), "GC=F": ("黃金", "$"),
    "^VIX": ("VIX波動率", ""), "^MOVE": ("MOVE債市波動率", ""), "USDJPY=X": ("美元兌日圓", ""),
    "CNH=X": ("美元兌離岸人民幣", ""),  # offshore: onshore CNY is shut at the US close and over China's holidays
}
# pair id: (numerator, denominator, 繁體 label, what rising means)
RATIOS = {
    "XLY_XLP": ("XLY", "XLP", "非必需／必需消費", "風險胃納上升"),
    "RSP_SPY": ("RSP", "SPY", "等權／市值加權", "市寬擴闊"),
    "IWM_QQQ": ("IWM", "QQQ", "小型股／納指100", "資金由龍頭擴散"),
    "XLK_XLU": ("XLK", "XLU", "科技／公用事業", "偏好增長"),
    "KRE_XLF": ("KRE", "XLF", "地區銀行／金融", "信貸健康改善"),
    "SMH_SPY": ("SMH", "SPY", "半導體／標普500", "AI鏈升溫"),
    "HYG_IEF": ("HYG", "IEF", "高收益債／中期國債", "信貸風險胃納上升"),
    "HG_GC": ("HG=F", "GC=F", "銅／金", "增長預期上升"),
}
# AI capex chain sub-sectors as equal-weight baskets (rulebook §5): id -> (繁體名, members)
AI_BASKETS = {
    "AI_HYPER": ("超大型雲端", ["MSFT", "AMZN", "GOOGL", "META", "ORCL"]),
    "AI_CHIPS": ("運算晶片", ["NVDA", "AVGO", "AMD", "TSM"]),
    "AI_MEM": ("記憶體", ["MU", "WDC", "STX", "SNDK"]),
    "AI_NET": ("網絡與互連", ["ANET", "MRVL", "CRDO", "ALAB"]),
    "AI_OPT": ("光通訊", ["COHR", "LITE", "CIEN", "GLW", "FN", "AAOI"]),
    "AI_PWR": ("電力與散熱", ["VRT", "ETN", "GEV", "PWR"]),
    "AI_IPP": ("獨立發電與現場供電", ["VST", "CEG", "TLN", "NRG", "BE"]),
    "AI_NEO": ("新雲", ["CRWV", "NBIS", "IREN", "APLD"]),
    "AI_CRYPTO": ("加密相關", ["COIN", "MSTR", "HOOD"]),
    "AI_NUC": ("鈾與核能", ["CCJ", "UEC", "LEU", "OKLO", "SMR", "BWXT"]),
}
# Typical dip after a quick pump (+30% in 20 sessions), by AI basket: (median, worst quarter) % from the peak.
# From backtests/results/22_pump_dip.txt (2020-07..2026); the monthly review reruns it. Reference only.
# Written into the AI basket rows of sectors.json (typ_dip, typ_worst) for the AI stages tab.
TYPICAL_DIP = {
    "AI_HYPER": (-16.0, -18.3), "AI_CHIPS": (-14.8, -22.0), "AI_MEM": (-19.5, -25.9), "AI_NET": (-22.5, -30.1),
    "AI_OPT": (-23.0, -40.0), "AI_PWR": (-16.5, -22.1), "AI_IPP": (-22.0, -35.3), "AI_NEO": (-40.8, -48.6),
    "AI_CRYPTO": (-29.0, -37.0), "AI_NUC": (-30.2, -45.0),
}
# PHLX Semiconductor Index (SOX) members for the semis-breadth gauge: the SOXQ holdings filed for 2026-05-31 (30
# names; SOX reconstitutes each September and the newer list was not public). Refresh after each reconstitution.
SOX_MEMBERS = ["MU", "NVDA", "AVGO", "AMD", "INTC", "MRVL", "QCOM", "TXN", "LRCX", "KLAC", "AMAT", "NXPI", "ADI", "MPWR",
               "TSM", "ASML", "COHR", "TER", "ALAB", "MCHP", "ARM", "ON", "GFS", "CRDO", "MTSI", "ENTG", "NVMI", "RMBS",
               "SWKS", "QRVO"]
# Extra single names to make searchable beyond the S&P 500 and the baskets
WATCH_EXTRA = ["CBRS", "INTC", "TSLA", "AAPL", "PLTR", "URA", "SPCX"]
# Company names for tickers outside the S&P 500 member list
EXTRA_NAMES = {
    "TSM": "Taiwan Semiconductor", "CRWV": "CoreWeave", "NBIS": "Nebius", "IREN": "IREN", "APLD": "Applied Digital",
    "CRDO": "Credo Technology", "ALAB": "Astera Labs", "COHR": "Coherent", "LITE": "Lumentum", "CIEN": "Ciena",
    "FN": "Fabrinet", "AAOI": "Applied Optoelectronics", "SNDK": "SanDisk", "TLN": "Talen Energy", "BE": "Bloom Energy",
    "MSTR": "Strategy", "HOOD": "Robinhood", "COIN": "Coinbase", "CBRS": "Cerebras", "MRVL": "Marvell",
    "CCJ": "Cameco", "UEC": "Uranium Energy", "LEU": "Centrus Energy", "OKLO": "Oklo", "SMR": "NuScale Power",
    "BWXT": "BWX Technologies", "URA": "Global X Uranium ETF", "SPCX": "SpaceX",
}

# English for every Chinese name, label and meaning written to the data files (繁體/English switch)
EN = {
    "科技": "Technology", "通訊服務": "Communication services", "非必需消費": "Consumer discretionary",
    "金融": "Financials", "工業": "Industrials", "能源": "Energy", "原材料": "Materials",
    "公用事業": "Utilities", "貴金屬": "Precious metals", "必需消費": "Consumer staples", "醫療保健": "Health care", "房地產": "Real estate",
    "半導體": "Semiconductors", "軟件": "Software", "地區銀行": "Regional banks", "生物科技": "Biotech",
    "住宅建築": "Homebuilders", "航天國防": "Aerospace & defense", "標普等權": "S&P 500 equal weight",
    "羅素2000小型股": "Russell 2000 small caps", "納指100": "Nasdaq-100", "動量因子": "Momentum factor",
    "低波動因子": "Low-volatility factor",
    "日本": "Japan", "歐洲": "Europe", "中國大型股": "China large caps", "香港": "Hong Kong", "台灣": "Taiwan",
    "南韓": "South Korea", "印度": "India", "新興市場": "Emerging markets", "黃金": "Gold", "比特幣ETF": "Bitcoin ETF",
    "標普500": "S&P 500",
    "美國10年期國債收益率": "US 10-year Treasury yield", "美國5年期國債收益率": "US 5-year Treasury yield",
    "美國3個月國庫券貼現率": "US 3-month bill (discount rate)", "美元指數": "US dollar index", "WTI原油": "WTI crude",
    "銅": "Copper", "VIX波動率": "VIX", "MOVE債市波動率": "MOVE bond volatility", "美元兌日圓": "USD/JPY", "美元兌離岸人民幣": "USD/CNH (offshore)",
    "非必需／必需消費": "Discretionary / staples", "風險胃納上升": "risk appetite rising",
    "等權／市值加權": "Equal weight / cap weight", "市寬擴闊": "breadth widening",
    "小型股／納指100": "Small caps / Nasdaq-100", "資金由龍頭擴散": "money spreading from leaders",
    "科技／公用事業": "Tech / utilities", "偏好增長": "preference for growth",
    "地區銀行／金融": "Regional banks / financials", "信貸健康改善": "credit health improving",
    "半導體／標普500": "Semis / S&P 500", "AI鏈升溫": "AI chain heating up",
    "高收益債／中期國債": "High yield / Treasuries", "信貸風險胃納上升": "credit risk appetite rising",
    "銅／金": "Copper / gold", "增長預期上升": "growth expectations rising",
    "標普500期貨": "S&P 500 futures", "納指100期貨": "Nasdaq-100 futures", "羅素2000期貨": "Russell 2000 futures",
    "2年期美債期貨": "2-year T-note futures", "10年期美債期貨": "10-year T-note futures",
    "超長期美債期貨": "Ultra T-bond futures", "VIX期貨": "VIX futures", "美元指數期貨": "US dollar index futures",
    "日圓期貨": "Japanese yen futures", "WTI原油期貨": "WTI crude futures", "銅期貨": "Copper futures",
    "黃金期貨": "Gold futures",
    "超大型雲端": "Hyperscalers", "運算晶片": "Compute chips", "記憶體": "Memory", "網絡與互連": "Networking & interconnect",
    "光通訊": "Optical", "電力與散熱": "Power & cooling", "獨立發電與現場供電": "IPPs & on-site power", "新雲": "Neoclouds",
    "加密相關": "Crypto-linked", "鈾與核能": "Uranium & nuclear",
    "股票指數": "Equity index", "美債": "Treasuries", "波動率": "Volatility", "外匯": "FX", "商品": "Commodities",
    "MSCI新興市場期貨": "MSCI EM futures", "MSCI EAFE期貨": "MSCI EAFE futures", "5年期美債期貨": "5-year T-note futures",
    "超長10年期美債期貨": "Ultra 10-year T-note futures", "長期美債期貨": "T-bond futures",
    "3個月SOFR期貨": "3-month SOFR futures", "聯邦基金利率期貨": "Fed funds futures", "歐元期貨": "Euro futures",
    "英鎊期貨": "British pound futures", "澳元期貨": "Australian dollar futures", "加元期貨": "Canadian dollar futures",
    "瑞士法郎期貨": "Swiss franc futures", "比特幣期貨": "Bitcoin futures", "天然氣期貨": "Natural gas futures",
    "汽油期貨": "Gasoline futures", "白銀期貨": "Silver futures", "大豆期貨": "Soybean futures", "粟米期貨": "Corn futures",
    "小麥期貨": "Wheat futures", "短期利率": "Short rates", "加密貨幣": "Crypto",
}

GICS_TO_ETF = {
    "Information Technology": "XLK", "Communication Services": "XLC", "Consumer Discretionary": "XLY",
    "Financials": "XLF", "Industrials": "XLI", "Energy": "XLE", "Materials": "XLB",
    "Utilities": "XLU", "Consumer Staples": "XLP", "Health Care": "XLV", "Real Estate": "XLRE",
}


def r(x, n=2):
    return None if x is None or pd.isna(x) else round(float(x), n)


def pct(series, days):
    s = series.dropna()
    if len(s) <= days:
        return None
    return (s.iloc[-1] / s.iloc[-1 - days] - 1) * 100


def ytd(series):
    s = series.dropna()
    prior = s[s.index.year < s.index[-1].year]
    return None if prior.empty else (s.iloc[-1] / prior.iloc[-1] - 1) * 100


# Tickers whose Yahoo daily bar is normally a session behind the rest (^MOVE: on 2026-10-12 its last bar was 10-09): a
# lagging last bar is normal for them, so the stale re-fetch in download() skips them rather than waiting ~30s for nothing.
LAGGING = {"^MOVE"}


def download(tickers, tries=3):
    tickers = sorted(set(tickers))
    df = yf.download(tickers, period="2y", interval="1d", auto_adjust=True,
                     progress=False, threads=True, group_by="column")
    close, high, low = df["Close"].copy(), df["High"].copy(), df["Low"].copy()
    # A big batch can come back with a few empty columns (rate limits) and nothing says so: on 2026-10-12 VRT and LEN
    # went missing and the power basket flipped to leading. A column can also stop a day short (HUBB kept a stale last
    # bar). Fetch those again, one at a time: empty columns, and non-FX tickers whose last bar is before SPY's (spot FX
    # comes from hourly quotes in main(); LAGGING tickers are always a day behind).
    for attempt in range(tries):
        spy_last = close["SPY"].last_valid_index() if "SPY" in close else None
        stale = lambda t: (spy_last is not None and not t.endswith("=X") and t not in LAGGING
                           and close[t].last_valid_index() < spy_last)
        missing = [t for t in tickers if t not in close or close[t].dropna().empty or stale(t)]
        if not missing:
            break
        print(f"  retry {attempt + 1}: {len(missing)} empty or stale: {' '.join(missing)}")
        time.sleep(5 * (attempt + 1))
        for t in missing:
            one = yf.download([t], period="2y", interval="1d", auto_adjust=True,
                              progress=False, threads=False, group_by="column")
            if not one.empty and not one["Close"][t].dropna().empty:
                close[t], high[t], low[t] = one["Close"][t], one["High"][t], one["Low"][t]
    return close, high, low


def at_us_close(tickers, period="6mo"):
    """Last hourly quote before 16:00 ET on each day, for markets that trade round the clock. Yahoo's daily bars for
    spot FX and bitcoin skip days (USD/JPY had no bar for 2026-10-12) and close at midnight London, not at the US close.
    Returns a frame indexed by date (naive), one column per ticker; empty if the download fails."""
    try:
        h = yf.download(sorted(set(tickers)), period=period, interval="1h", auto_adjust=False, progress=False)["Close"]
    except Exception as e:  # keep the daily bars rather than fail the build
        print(f"  WARNING hourly quotes failed: {e}")
        return pd.DataFrame()
    if isinstance(h, pd.Series):
        h = h.to_frame(tickers[0])
    h.index = h.index.tz_convert("America/New_York")
    h = h[h.index.hour < 16]
    out = h.groupby(h.index.date).last()
    out.index = pd.to_datetime(out.index)
    return out


def members():
    url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
    raw = urllib.request.urlopen(url, timeout=30).read().decode()
    df = pd.read_csv(io.StringIO(raw))
    df["Symbol"] = df["Symbol"].str.replace(".", "-", regex=False)
    return df[["Symbol", "Security", "GICS Sector"]]


def write(name, rows):
    for row in rows:
        for key in ("name", "label", "meaning", "group_name"):
            if isinstance(row.get(key), str):
                row[key + "_en"] = EN.get(row[key], row[key])
    (OUT / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
    print(f"  {name}.json  {len(rows)} rows")


def main():
    mem = members()
    basket_members = sorted({m for _, ms in AI_BASKETS.values() for m in ms})
    tickers = (list(ETFS) + list(GLOBAL) + list(MACRO) + ["SPY", "HYG", "IEF"] + list(mem["Symbol"]) + SOX_MEMBERS
               + basket_members + WATCH_EXTRA)
    print(f"downloading {len(set(tickers))} tickers…")
    close, high, low = download(tickers)
    empty = lambda ts: [t for t in ts if t not in close or close[t].dropna().empty]
    # no build rather than a wrong one: a missing basket member or ETF changes the rotation reads
    # spot FX comes from hourly quotes below (Yahoo's daily FX history is patchy, CNH's nearly empty), so don't require it here
    fx = [t for t in MACRO if t.endswith("=X")]
    required = empty(list(ETFS) + list(GLOBAL) + [t for t in MACRO if t not in fx] + ["SPY", "HYG", "IEF"] + basket_members)
    if required:
        sys.exit(f"still missing after retries: {' '.join(required)}")
    for label, ts in (("S&P 500 members", list(mem["Symbol"])), ("watch list", WATCH_EXTRA)):
        if empty(ts):
            print(f"  WARNING {label} without data: {' '.join(empty(ts))}")
    close = close.dropna(how="all")
    # a build while US markets are open gets today's unfinished bar from Yahoo; leave it out until the close
    now_et = pd.Timestamp.now(tz="America/New_York")
    cutoff = os.environ.get('BOARD_ASOF') or completed_session()
    close = close.loc[:cutoff]
    high, low = high.loc[:cutoff], low.loc[:cutoff]
    asof = close["SPY"].dropna().index[-1]
    if os.environ.get('BOARD_STRICT_REFRESH') and asof.strftime('%Y-%m-%d') != cutoff:
        raise ValueError(f'Latest completed session {cutoff} is missing; keep the previous snapshot')
    missing_latest = [t for t in set(ETFS) | set(basket_members) if pd.isna(close.at[asof, t])]
    if missing_latest:
        raise ValueError(f'Incomplete latest basket/ETF prices: {missing_latest}')
    # US trading days only (futures and FX also print on US holidays)
    close = close.loc[close["SPY"].notna()].loc[:asof]
    # spot FX at the US close instead of Yahoo's daily bars (see at_us_close); older rows keep the daily bars
    hourly = at_us_close(fx)
    for t in fx:
        if t in hourly:
            s = hourly[t].dropna()
            s = s[s.index.isin(close.index)]
            close.loc[s.index, t] = s.values
    stamp = {"asof": asof.strftime("%Y-%m-%d"),
             "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    spy = close["SPY"].dropna()
    one_year = close.index[close.index > asof - pd.Timedelta(days=366)]

    # ---- member stats (breadth) ----
    stocks = [s for s in mem["Symbol"] if s in close and close[s].notna().sum() > 260]
    if os.environ.get('BOARD_STRICT_REFRESH'):
        absent = [s for s in stocks if pd.isna(close.at[asof, s])]
        if absent:
            raise ValueError(f'Incomplete latest breadth constituents: {absent}')
    px = close[stocks].ffill(limit=3)
    # same US trading days as the closes; futures and FX add holiday rows that would thin the 252-day window
    hi = high[stocks].reindex(close.index)
    lo = low[stocks].reindex(close.index)
    sma20, sma50, sma200 = px.rolling(20).mean(), px.rolling(50).mean(), px.rolling(200).mean()
    hi52, lo52 = hi.rolling(252, min_periods=240).max(), lo.rolling(252, min_periods=240).min()
    chg = px.pct_change(fill_method=None)
    adv, dec = (chg > 0).sum(axis=1), (chg < 0).sum(axis=1)
    valid = px.notna().sum(axis=1)
    pct20 = (px > sma20).sum(axis=1) / sma20.notna().sum(axis=1) * 100
    pct50 = (px > sma50).sum(axis=1) / sma50.notna().sum(axis=1) * 100
    pct200 = (px > sma200).sum(axis=1) / sma200.notna().sum(axis=1) * 100
    nh = (hi >= hi52).sum(axis=1)
    nl = (lo <= lo52).sum(axis=1)
    ratio_adv = (adv - dec) / (adv + dec).replace(0, np.nan)
    mcclellan = (ratio_adv.ewm(span=19, adjust=False).mean() - ratio_adv.ewm(span=39, adjust=False).mean()) * 1000
    ad_line = (adv - dec).loc[one_year].cumsum()
    ew_index = (1 + chg.mean(axis=1).fillna(0)).loc[one_year].cumprod() * 100
    spy_index = spy.loc[one_year] / spy.loc[one_year].iloc[0] * 100

    # semis breadth (user, 2026-10-15): SOX members below their 50-day line. 24 of 30 (80%+) is the "flush" level
    # quoted in the trading chat; tested 2010–2026 in rulebook §5b (a mild 20-day edge, not a bottom signal)
    sx = close[[t for t in SOX_MEMBERS if t in close]].ffill(limit=3)
    sx50 = sx.rolling(50).mean()
    sx_ok = sx.notna() & sx50.notna()
    sox_below, sox_n = ((sx < sx50) & sx_ok).sum(axis=1), sx_ok.sum(axis=1)
    breadth = []
    for d in one_year:
        breadth.append({
            "date": d.strftime("%Y-%m-%d"), "pct20": r(pct20[d], 1), "pct50": r(pct50[d], 1),
            "pct200": r(pct200[d], 1), "adv": int(adv[d]), "dec": int(dec[d]),
            "ad_line": int(ad_line[d]), "new_highs": int(nh[d]), "new_lows": int(nl[d]),
            "mcclellan": r(mcclellan[d], 1), "ew_index": r(ew_index[d]), "spy_index": r(spy_index.get(d)),
            "sox_below50": int(sox_below[d]), "sox_n": int(sox_n[d]),
        })
    write("breadth", breadth)

    def ago(s, n):
        s = s.dropna()
        return s.iloc[-1 - n] if len(s) > n else None

    def nh_nl_sum(s, n=5):
        return int(s.iloc[-n:].sum())

    breadth_now = [{
        **stamp, "members": int(valid.iloc[-1]),
        "pct20": r(pct20.iloc[-1], 1), "pct50": r(pct50.iloc[-1], 1), "pct200": r(pct200.iloc[-1], 1),
        "pct20_1w": r(ago(pct20, 5), 1), "pct50_1w": r(ago(pct50, 5), 1), "pct200_1w": r(ago(pct200, 5), 1),
        "pct50_1m": r(ago(pct50, 21), 1),
        "pct50_1d": r(ago(pct50, 1), 1), "pct200_1d": r(ago(pct200, 1), 1), "mcclellan_1d": r(ago(mcclellan, 1), 1),
        "adv": int(adv.iloc[-1]), "dec": int(dec.iloc[-1]),
        "adv_5d": int(adv.iloc[-5:].sum()), "dec_5d": int(dec.iloc[-5:].sum()),
        "new_highs": int(nh.iloc[-1]), "new_lows": int(nl.iloc[-1]),
        "new_highs_5d": nh_nl_sum(nh), "new_lows_5d": nh_nl_sum(nl),
        "mcclellan": r(mcclellan.iloc[-1], 1), "mcclellan_1w": r(ago(mcclellan, 5), 1),
        "mcclellan_1m": r(ago(mcclellan, 21), 1), "ad_1w": int((adv - dec).iloc[-5:].sum()),
        "new_highs_1m": int(nh.iloc[-21:].sum()), "new_lows_1m": int(nl.iloc[-21:].sum()),
        "ew_1m": r(pct(ew_index, 21)), "spy_1m": r(pct(spy, 21)),
        "ad_1m": int((adv - dec).iloc[-21:].sum()),
    }]
    write("breadth_now", breadth_now)

    # ---- sector participation (members inside each sector) ----
    above50_now = (px.iloc[-1] > sma50.iloc[-1])
    above200_now = (px.iloc[-1] > sma200.iloc[-1])
    above50_1w = (px.iloc[-6] > sma50.iloc[-6])
    above50_1m = (px.iloc[-22] > sma50.iloc[-22])
    r1m_stock = (px.iloc[-1] / px.iloc[-22] - 1) * 100
    participation = {}
    for gics, etf in GICS_TO_ETF.items():
        syms = [s for s in mem.loc[mem["GICS Sector"] == gics, "Symbol"] if s in stocks]
        participation[etf] = {
            "members": len(syms),
            "pct50": r(above50_now[syms].mean() * 100, 1), "pct50_1w": r(above50_1w[syms].mean() * 100, 1),
            "pct50_1m": r(above50_1m[syms].mean() * 100, 1),
            "pct200": r(above200_now[syms].mean() * 100, 1),
            "pct_up_1m": r((r1m_stock[syms] > 0).mean() * 100, 1), "median_1m": r(r1m_stock[syms].median()),
            "new_highs_5d": int((hi[syms].iloc[-5:] >= hi52[syms].iloc[-5:]).any().sum()),
        }

    # ---- sectors, themes, styles, AI-chain baskets: returns + relative rotation ----
    # AI baskets: equal-weight, rebalanced daily, from the members that have data that day
    for bid, (_, ms) in AI_BASKETS.items():
        rets = close[[m for m in ms if m in close]].pct_change(fill_method=None).mean(axis=1, skipna=True)
        close[bid] = (1 + rets.fillna(0)).cumprod() * 100
    weekly = close.resample("W-FRI").last()
    # a midweek build labels the unfinished week with the coming Friday; stamp it with its last trading day instead
    # (cutting at asof dropped it, and the line below then dropped the last completed week as well)
    if weekly.index[-1] > asof:
        weekly = weekly.rename(index={weekly.index[-1]: asof})
    smh = close["SMH"].dropna()
    # The playbook acts on the Friday close, so it also gets tags from completed weeks only: a midweek build drops the
    # unfinished week; a build on the weekend (or after a Friday close) keeps it.
    week_done = week_complete(asof)
    weekly_fw = weekly if week_done else weekly.iloc[:-1]
    week_fw = close.index[close.index <= weekly_fw.index[-1]][-1].strftime("%Y-%m-%d")

    def quadrant(rr, rm):
        if rr is None or rm is None or pd.isna(rr) or pd.isna(rm):
            return None
        return "leading" if rr >= 100 and rm >= 100 else "weakening" if rr >= 100 else "improving" if rm >= 100 else "lagging"

    def rrg(t, bench):
        """Weekly RS-ratio (vs its 10-week average) and momentum (vs 4 weeks earlier), against a benchmark."""
        rs = (weekly[t] / weekly[bench]).dropna()
        ratio = 100 * rs / rs.rolling(10).mean()
        return ratio, 100 * ratio / ratio.shift(4), rs

    def week_fields(t, wk=None, sfx="_fw"):
        """Rotation tags vs the S&P 500 on completed weeks (suffix _fw): this week's and last week's quadrant and tag,
        RS-ratio changes, 1-, 4- and 26-week relative returns, and the leading spell. With wk=weekly and sfx="_lv" the
        same fields count the unfinished week as this week (the leaders list updates daily; equal to _fw once the
        week is done)."""
        wk = weekly_fw if wk is None else wk
        rs = (wk[t] / wk["SPY"]).dropna()
        if len(rs) < 16:
            return {}
        ratio = 100 * rs / rs.rolling(10).mean()
        mom = 100 * ratio / ratio.shift(4)
        qs = [quadrant(a_, b_) for a_, b_ in zip(ratio, mom)]
        if qs[-1] is None or qs[-2] is None:
            return {}
        w = rs.pct_change() * 100

        def tag(i):
            c = ratio.iloc[i] - ratio.iloc[i - 1]
            return "confirmed" if c > 0 and w.iloc[i] > 0 and w.iloc[i - 1] > 0 else "early" if c > 0 and w.iloc[i] > 0 else "unconfirmed"
        # consecutive completed weeks with the RS-ratio falling (rulebook §10a step 6: 1-2 pullback, 3+ rotation)
        dr_ = ratio.diff().dropna().values
        down = 0
        while down < len(dr_) and dr_[-1 - down] < 0:
            down += 1
        lead = 0
        while lead < len(qs) and qs[-1 - lead] == "leading":
            lead += 1
        gap = None
        if lead:
            start = len(qs) - lead
            prev = [i for i in range(start - 1, -1, -1) if qs[i] == "leading"]
            gap = (start - 1 - prev[0]) if prev else None
        out = {"q": qs[-1], "q1": qs[-2], "turn": tag(-1), "turn1": tag(-2),
               "chg": r(ratio.iloc[-1] - ratio.iloc[-2]), "chg_prev": r(ratio.iloc[-2] - ratio.iloc[-3]),
               "relw": r(w.iloc[-1]), "rel4w": r((rs.iloc[-1] / rs.iloc[-5] - 1) * 100),
               "rel26w": r((rs.iloc[-1] / rs.iloc[-27] - 1) * 100) if len(rs) > 27 else None,
               "lead": lead, "gap": gap, "down_wks": down}
        return {k + sfx: v for k, v in out.items()}

    def rotation(t):
        """Return fields and relative rotation vs the S&P 500 and vs semis (SMH)."""
        s = close[t].dropna()
        row = {"r1w": r(pct(s, 5)), "r1m": r(pct(s, 21)), "r3m": r(pct(s, 63)), "r6m": r(pct(s, 126)), "rytd": r(ytd(s)),
               "vs_50d": r((s.iloc[-1] / s.rolling(50).mean().iloc[-1] - 1) * 100) if len(s) >= 50 else None,
               "vs_200d": r((s.iloc[-1] / s.rolling(200).mean().iloc[-1] - 1) * 100) if len(s) >= 200 else None,
               "off_high": r((s.iloc[-1] / s.iloc[-252:].max() - 1) * 100)}
        # pullback stage (AI stage tab): distance from the 60-session closing high, sessions since it, and the latest
        # quick pump (+30% in 20 sessions) within the last 120 sessions
        if len(s) >= 60:
            w60 = s.iloc[-60:]
            row["off_hi60"] = r((s.iloc[-1] / w60.max() - 1) * 100)
            row["hi60_days"] = int(len(w60) - 1 - int(np.argmax(w60.values)))
            row["hi60_date"] = w60.idxmax().strftime("%Y-%m-%d")
        # daily state (AI stage tab, user 2026-10-15): yesterday's readings so "today crossed" is only said when it did,
        # the 52-week high and its date (a fixed reference; a 60-day high can roll out of its window without any
        # recovery), and sessions since the last 20-day low of the price relative to the S&P 500
        if len(s) >= 61:
            sma50 = s.rolling(50).mean()
            row["vs_50d_prev"] = r((s.iloc[-2] / sma50.iloc[-2] - 1) * 100)
            row["off_hi60_prev"] = r((s.iloc[-2] / s.iloc[-61:-1].max() - 1) * 100)
            row["hi52_date"] = s.iloc[-252:].idxmax().strftime("%Y-%m-%d")
            row["r1d"] = r(pct(s, 1))
            rel_ = (s / spy.reindex(s.index)).dropna()
            new_low = rel_ <= rel_.rolling(20).min() + 1e-12
            lows = np.flatnonzero(new_low.values)
            row["rel_low_days"] = int(len(rel_) - 1 - lows[-1]) if len(lows) else None
            r20 = (s / s.shift(20) - 1).iloc[-120:]
            hit = r20[r20 >= 0.30]
            if len(hit):
                row["pump_days"] = int(len(s.loc[hit.index[-1]:]) - 1)
                row["vs_pump"] = r((s.iloc[-1] / s.loc[hit.index[-1]] - 1) * 100)
        # stop distance (rulebook §10a step 5): the wider of 15% and 3 x the 14-day ATR, as % of the close
        if t in high and t in low:
            h, l_ = high[t].reindex(s.index), low[t].reindex(s.index); pc = s.shift(1)
            tr = pd.concat([h - l_, (h - pc).abs(), (l_ - pc).abs()], axis=1).max(axis=1, skipna=False)
            atr = tr.rolling(14).mean().iloc[-1]
            if pd.notna(atr):
                row["atr_pct"] = r(atr / s.iloc[-1] * 100)
                row["stop_pct"] = r(max(15.0, 3 * atr / s.iloc[-1] * 100))
        tail = {}
        for suffix, bench, bs in (("", "SPY", spy), ("_smh", "SMH", smh)):
            for n, k in ((5, "rel1w"), (21, "rel1m"), (63, "rel3m"), (126, "rel6m")):
                a_, b_ = pct(s, n), pct(bs, n)
                row[k + suffix] = None if a_ is None or b_ is None else r(a_ - b_)
            ratio, mom, rs = rrg(t, bench)
            ok = len(mom.dropna()) > 0
            row["rs_ratio" + suffix] = r(ratio.iloc[-1]) if ok else None
            row["rs_mom" + suffix] = r(mom.iloc[-1]) if ok else None
            row["quadrant" + suffix] = quadrant(ratio.iloc[-1], mom.iloc[-1]) if ok else None
            row["quadrant_4w" + suffix] = quadrant(ratio.iloc[-5], mom.iloc[-5]) if len(mom.dropna()) > 4 else None
            # Next week, solved exactly: RS-ratio' = 1000·y / (S9 + y), where y is next week's relative strength and
            # S9 the sum of the last nine; momentum' compares RS-ratio' with the ratio three weeks before the latest.
            rsv = rs.dropna()
            if ok and len(rsv) >= 10 and len(ratio.dropna()) >= 4:
                s9, last, base = rsv.iloc[-9:].sum(), rsv.iloc[-1], ratio.iloc[-4]
                need = lambda c: None if c >= 1000 else r((c * s9 / (1000 - c) / last - 1) * 100)
                flat_ratio = 1000 * last / (s9 + last)
                row["need_mom" + suffix] = need(base)       # relative return next week for momentum >= 100
                row["need_ratio" + suffix] = need(100.0)    # relative return next week for RS-ratio >= 100
                row["next_flat" + suffix] = quadrant(flat_ratio, 100 * flat_ratio / base)  # quadrant if it just matches
            # Is a turn real or a base effect? confirmed = RS-ratio rising and beating the benchmark two weeks running;
            # early = RS-ratio rising and beat it this week only; unconfirmed = anything else
            if ok and len(rsv) >= 3 and len(ratio.dropna()) >= 2:
                chg = ratio.iloc[-1] - ratio.iloc[-2]
                w1, w2 = (rsv.iloc[-1] / rsv.iloc[-2] - 1) * 100, (rsv.iloc[-2] / rsv.iloc[-3] - 1) * 100
                row["ratio_chg" + suffix], row["rel_w1" + suffix], row["rel_w2" + suffix] = r(chg), r(w1), r(w2)
                row["turn" + suffix] = ("confirmed" if chg > 0 and w1 > 0 and w2 > 0
                                        else "early" if chg > 0 and w1 > 0 else "unconfirmed")
            # Where it is in its run: weeks in the current leading spell, the gap since the previous one (a short gap
            # means a late re-entry), last week's quadrant, and whether the RS-ratio fell two weeks running.
            if ok and len(ratio.dropna()) >= 3:
                qs = [quadrant(a_, b_) for a_, b_ in zip(ratio, mom)]
                lead = 0
                while lead < len(qs) and qs[-1 - lead] == "leading":
                    lead += 1
                gap = None
                if lead:
                    start = len(qs) - lead
                    prev = [i for i in range(start - 1, -1, -1) if qs[i] == "leading"]
                    gap = (start - 1 - prev[0]) if prev else None
                row["lead_weeks" + suffix], row["gap_before" + suffix] = lead, gap
                row["quadrant_1w" + suffix] = qs[-2]
                row["ratio_chg_prev" + suffix] = r(ratio.iloc[-2] - ratio.iloc[-3])
            for d in ratio.index[-10:]:
                tail.setdefault(d, {})["rs_ratio" + suffix] = r(ratio[d])
                tail[d]["rs_mom" + suffix] = r(mom[d])
        row.update(week_fields(t))
        row.update(week_fields(t, weekly, "_lv"))
        tails_out = [{"ticker": t, "week": d.strftime("%Y-%m-%d"), **v} for d, v in tail.items()]
        return row, tails_out

    def daily_rrg(t):
        """Daily rotation (user, 2026-10-15; beside the weekly graph): RS-ratio against its 20-session average
        and momentum against 5 sessions earlier, vs the S&P 500 and vs semis; the last 20 sessions as a tail. Noisier
        than the weekly graph; the rotation rules (§10a) stay weekly."""
        fields, pts = {}, {}
        for suffix, bench in (("", "SPY"), ("_smh", "SMH")):
            rs_d = (close[t] / close[bench]).dropna()
            ratio_d = 100 * rs_d / rs_d.rolling(20).mean()
            mom_d = 100 * ratio_d / ratio_d.shift(5)
            ok = mom_d.dropna()
            if len(ok) < 6:
                continue
            fields["quadrant_d" + suffix] = quadrant(ratio_d.iloc[-1], mom_d.iloc[-1])
            fields["quadrant_d5" + suffix] = quadrant(ratio_d.iloc[-6], mom_d.iloc[-6])
            fields["rs_ratio_d" + suffix], fields["rs_mom_d" + suffix] = r(ratio_d.iloc[-1]), r(mom_d.iloc[-1])
            for d in ratio_d.index[-20:]:
                pts.setdefault(d, {})["rs_ratio" + suffix] = r(ratio_d[d])
                pts[d]["rs_mom" + suffix] = r(mom_d[d])
        return fields, [{"ticker": t, "date": d.strftime("%Y-%m-%d"), **v} for d, v in pts.items()]

    sectors, tails, dtails = [], [], []
    universe = [(t, name, group) for t, (name, group) in ETFS.items()] + [(b, n, "ai") for b, (n, _) in AI_BASKETS.items()]
    for t, name, group in universe:
        s = close[t].dropna()
        row, tl = rotation(t)
        row = {"ticker": t, "name": name, "group": group, "price": r(s.iloc[-1]), **row,
               "vs_200d": r((s.iloc[-1] / s.rolling(200).mean().iloc[-1] - 1) * 100),
               "week_fw": week_fw, "week_done": week_done}
        if group == "ai":
            row["members_list"] = " ".join(AI_BASKETS[t][1])
            row["typ_dip"], row["typ_worst"] = TYPICAL_DIP.get(t, (None, None))
        dfields, dtl = daily_rrg(t)
        row.update(dfields)
        dtails += dtl
        p = participation.get(t, {})
        row.update({"members": p.get("members"), "m_pct50": p.get("pct50"), "m_pct50_1w": p.get("pct50_1w"), "m_pct50_1m": p.get("pct50_1m"),
                    "m_pct200": p.get("pct200"), "m_pct_up_1m": p.get("pct_up_1m"),
                    "m_median_1m": p.get("median_1m"), "m_new_highs_5d": p.get("new_highs_5d")})
        sectors.append(row)
        tails += tl
    write("sectors", sectors)
    write("rrg_tails", tails)
    write("rrg_daily", dtails)
    from summary_checks import check_prices
    write("check_prices", check_prices(close, AI_BASKETS["AI_NEO"][1], asof))

    # ---- single stocks: S&P 500 members, basket members and the watch list ----
    names = dict(zip(mem["Symbol"], mem["Security"])) | EXTRA_NAMES
    gics = dict(zip(mem["Symbol"], mem["GICS Sector"]))
    in_basket = {}
    for bid, (n, ms) in AI_BASKETS.items():
        for m in ms:
            in_basket.setdefault(m, []).append(bid)
    stock_rows, stock_tails = [], []
    for t in sorted(set(mem["Symbol"]) | set(basket_members) | set(WATCH_EXTRA)):
        if t not in close or close[t].dropna().shape[0] < 30:
            continue
        row, tl = rotation(t)
        stock_rows.append({"ticker": t, "name": names.get(t, t), "baskets": " ".join(in_basket.get(t, [])),
                           "sector": GICS_TO_ETF.get(gics.get(t)), "price": r(close[t].dropna().iloc[-1]), **row})
        stock_tails += tl
    write("stocks", stock_rows)
    write("stock_tails", stock_tails)

    # ---- ratio lines ----
    ratio_rows, ratio_summary = [], []
    for pid, (a, b, label, meaning) in RATIOS.items():
        line = (close[a] / close[b]).dropna()
        sma50_l = line.rolling(50).mean()
        base = line.loc[one_year].iloc[0]
        for d in line.loc[one_year].index:
            ratio_rows.append({"date": d.strftime("%Y-%m-%d"), "pair": pid, "value": r(line[d] / base * 100),
                               "sma50": r(sma50_l[d] / base * 100)})
        c20, c5 = pct(line, 20), pct(line, 5)
        above = line.iloc[-1] > sma50_l.iloc[-1]
        slope = sma50_l.iloc[-1] > sma50_l.iloc[-11]
        trend = "up" if above and slope else "down" if not above and not slope else "turning_up" if above else "turning_down"
        ratio_summary.append({"pair": pid, "num": a, "den": b, "label": label, "meaning": meaning,
                              "chg1d": r(pct(line, 1)), "chg5d": r(c5), "chg20d": r(c20), "chg60d": r(pct(line, 60)),
                              "vs_50d": r((line.iloc[-1] / sma50_l.iloc[-1] - 1) * 100), "trend": trend})
    write("ratios", ratio_rows)
    write("ratio_summary", ratio_summary)

    # rates first: their print dates are the bond-market calendar for the Yahoo yields below
    build_positioning()
    prints, treasury_ok = build_rates()
    fed_holidays = USFederalHolidayCalendar().holidays(close.index[0], close.index[-1])

    def bond_days(s):
        """Yahoo prints yields on days the Treasury market is shut (Columbus Day, Veterans Day). Keep the days the 10-year
        printed (FRED and Treasury). After its latest print, drop federal holidays at once, and other days only once
        Treasury has had time to post them (usually by 18:00 ET; FRED alone lags), so an early-evening build keeps
        today's yields."""
        if not len(prints):
            return s
        printed, last = set(prints), prints[-1]
        posted = treasury_ok and (now_et.hour >= 18)

        def keep(d):
            if d <= last:
                return d < prints[0] or d in printed
            if d in fed_holidays:
                return False
            return not (treasury_ok and (d.date() < now_et.date() or posted))
        return s[[keep(d) for d in s.index]]

    # ---- macro ----
    macro = []
    for t, (name, unit) in MACRO.items():
        s = close[t].dropna()
        is_yield = unit == "%"
        if is_yield:
            s = bond_days(s)
        if s.empty:
            continue
        def delta(n):
            if len(s) <= n:
                return None
            return (s.iloc[-1] - s.iloc[-1 - n]) * 100 if is_yield else pct(s, n)
        base = lambda n: s.index[-1 - n].strftime("%Y-%m-%d") if len(s) > n else None
        macro.append({"ticker": t, "name": name, "unit": "bp" if is_yield else "%", "last": r(s.iloc[-1], 3 if is_yield else 2),
                      "chg1d": r(delta(1), 1), "chg1w": r(delta(5), 1), "chg1m": r(delta(21), 1), "chg3m": r(delta(63), 1),
                      "date": s.index[-1].strftime("%Y-%m-%d"), "base1w": base(5), "base1m": base(21), "base3m": base(63),
                      # sessions in the last five with an unchanged quote: a local holiday (CNY over China's National
                      # Day week) freezes the price and makes the week's change stale
                      "flat_1w": int((s.diff().iloc[-5:] == 0).sum())})
    write("macro", macro)

    # ---- global sweep ----
    glob = []
    for t, name in GLOBAL.items():
        s = close[t].dropna()
        if s.empty:
            continue
        glob.append({"ticker": t, "name": name, "r1w": r(pct(s, 5)), "r1m": r(pct(s, 21)), "r3m": r(pct(s, 63)),
                     "rel1m": r(pct(s, 21) - pct(spy, 21)), "rel3m": r(pct(s, 63) - pct(spy, 63)),
                     "vs_50d": r((s.iloc[-1] / s.rolling(50).mean().iloc[-1] - 1) * 100)})
    glob.append({"ticker": "SPY", "name": "標普500", "r1w": r(pct(spy, 5)), "r1m": r(pct(spy, 21)), "r3m": r(pct(spy, 63)),
                 "rel1m": 0.0, "rel3m": 0.0, "vs_50d": r((spy.iloc[-1] / spy.rolling(50).mean().iloc[-1] - 1) * 100)})
    write("global", glob)
    try:  # alert lamps (警報燈, rulebook §3): a trial; never lose the rest of the build to them
        from alerts import build_alerts
        build_alerts(close, px, pct50, pct200, AI_BASKETS)
    except Exception as e:
        if os.environ.get('BOARD_STRICT_REFRESH'): raise
        print(f"  WARNING alert lamps failed: {e}")
    try:
        from flows import build_flows  # imports this module, so loaded here rather than at the top
        build_flows(asof)
    except Exception as e:  # the flow models are a trial; never lose the rest of the build to them
        if os.environ.get('BOARD_STRICT_REFRESH'): raise
        print(f"  WARNING flow models failed: {e}")
    print("as of", stamp["asof"])


# ---- CFTC futures positioning ----
CFTC = "https://publicreporting.cftc.gov/resource/{ds}.json"
# code: (繁體名, group, dataset) ; tff = Traders in Financial Futures, dis = Disaggregated (managed money)
POSITIONS = {
    "13874A": ("標普500期貨", "股票指數", "tff"), "209742": ("納指100期貨", "股票指數", "tff"),
    "239742": ("羅素2000期貨", "股票指數", "tff"), "042601": ("2年期美債期貨", "美債", "tff"),
    "043602": ("10年期美債期貨", "美債", "tff"), "020604": ("超長期美債期貨", "美債", "tff"),
    "1170E1": ("VIX期貨", "波動率", "tff"), "098662": ("美元指數期貨", "外匯", "tff"),
    "097741": ("日圓期貨", "外匯", "tff"), "067651": ("WTI原油期貨", "商品", "dis"),
    "085692": ("銅期貨", "商品", "dis"), "088691": ("黃金期貨", "商品", "dis"),
    # added 2026-10-13: the rest of the weekly COT sweep (global equity, the curve, short rates, FX, crypto, energy, grains)
    "244042": ("MSCI新興市場期貨", "股票指數", "tff"), "244041": ("MSCI EAFE期貨", "股票指數", "tff"),
    "044601": ("5年期美債期貨", "美債", "tff"), "043607": ("超長10年期美債期貨", "美債", "tff"),
    "020601": ("長期美債期貨", "美債", "tff"), "134741": ("3個月SOFR期貨", "短期利率", "tff"),
    "045601": ("聯邦基金利率期貨", "短期利率", "tff"), "099741": ("歐元期貨", "外匯", "tff"),
    "096742": ("英鎊期貨", "外匯", "tff"), "232741": ("澳元期貨", "外匯", "tff"), "090741": ("加元期貨", "外匯", "tff"),
    "092741": ("瑞士法郎期貨", "外匯", "tff"), "133741": ("比特幣期貨", "加密貨幣", "tff"),
    "023651": ("天然氣期貨", "商品", "dis"), "111659": ("汽油期貨", "商品", "dis"), "084691": ("白銀期貨", "商品", "dis"),
    "005602": ("大豆期貨", "商品", "dis"), "002602": ("粟米期貨", "商品", "dis"), "001602": ("小麥期貨", "商品", "dis"),
}


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())


def build_positioning():
    since = (pd.Timestamp.today() - pd.DateOffset(years=4)).strftime("%Y-%m-%d")
    rows, now = [], []
    for code, (name, group, ds) in POSITIONS.items():
        dsid = "gpe5-46if" if ds == "tff" else "72hh-3qpy"
        q = (f"?$limit=5000&$order=report_date_as_yyyy_mm_dd&cftc_contract_market_code={code}"
             f"&$where=report_date_as_yyyy_mm_dd>='{since}'")
        raw = pd.DataFrame(fetch_json(CFTC.format(ds=dsid) + q.replace(" ", "%20").replace("'", "%27")))
        f = lambda c: pd.to_numeric(raw[c], errors="coerce")
        df = pd.DataFrame({"date": pd.to_datetime(raw["report_date_as_yyyy_mm_dd"]), "oi": f("open_interest_all")})
        if ds == "tff":
            df["am_net"] = f("asset_mgr_positions_long") - f("asset_mgr_positions_short")
            df["lev_net"] = f("lev_money_positions_long") - f("lev_money_positions_short")
        else:  # commodities: managed money is the speculative "fund" leg; no asset-manager split
            df["am_net"] = np.nan
            df["lev_net"] = f("m_money_positions_long_all") - f("m_money_positions_short_all")
        df = df.drop_duplicates("date").sort_values("date").set_index("date")
        df["am_pct"] = df["am_net"] / df["oi"] * 100
        df["lev_pct"] = df["lev_net"] / df["oi"] * 100
        df["am_chg"] = df["am_pct"].diff()
        df["lev_chg"] = df["lev_pct"].diff()
        last3 = df[df.index > df.index[-1] - pd.DateOffset(years=3)]
        for d, x in last3.iterrows():
            rows.append({"date": d.strftime("%Y-%m-%d"), "market": code, "am_pct": r(x.am_pct, 1), "lev_pct": r(x.lev_pct, 1),
                         "am_chg": r(x.am_chg, 1), "lev_chg": r(x.lev_chg, 1),
                         "am_net": None if pd.isna(x.am_net) else int(x.am_net), "lev_net": int(x.lev_net), "oi": int(x.oi)})
        def pctile(col):
            s = last3[col].dropna()
            return None if s.empty else r((s < s.iloc[-1]).mean() * 100, 0)
        def chg(col, weeks=1):
            s = df[col].dropna()
            return None if len(s) <= weeks else r(s.iloc[-1] - s.iloc[-1 - weeks], 1)
        def zscore(col):
            # 52-week z-score of net contracts, the way the weekly COT tables quote it
            s = df[col].dropna().iloc[-52:]
            return None if len(s) < 20 or s.std() == 0 else r((s.iloc[-1] - s.mean()) / s.std(), 1)
        lev_p, am_p = pctile("lev_pct"), pctile("am_pct")
        # 3-year extreme of a holder's net position: "high" = most long / least short, "low" = the reverse
        extreme = lambda p: None if p is None else "high" if p >= 90 else "low" if p <= 10 else None
        now.append({"market": code, "name": name, "group_name": group, "date": df.index[-1].strftime("%Y-%m-%d"),
                    "am_pct": r(df["am_pct"].iloc[-1], 1), "am_chg_1w": chg("am_pct"), "am_chg_4w": chg("am_pct", 4),
                    "am_pctile": am_p, "lev_pct": r(df["lev_pct"].iloc[-1], 1), "lev_chg_1w": chg("lev_pct"),
                    "lev_chg_4w": chg("lev_pct", 4), "lev_pctile": lev_p,
                    "lev_net": int(df["lev_net"].iloc[-1]), "oi": int(df["oi"].iloc[-1]),
                    "am_extreme": extreme(am_p), "lev_extreme": extreme(lev_p),
                    "am_z": zscore("am_net"), "lev_z": zscore("lev_net"),
                    "has_am": ds == "tff"})
    positioning_reads(rows, now)
    write("positioning", rows)
    write("positioning_now", now)


NON_CORE_COT = {"天然氣期貨", "汽油期貨", "大豆期貨", "粟米期貨", "小麥期貨"}  # left out of the one-line weekly read


def positioning_reads(rows, now):
    """Adds to each positioning_now row, per holder ('am', 'lev'): the 1- and 4-week change scaled by that series' own
    weekly swing (z1, z4: comparable across markets), and a turn from a deep extreme only when it is clear (user,
    2026-10-15: show 「開始轉向」 only when really spotted). A turn needs all four: 2–4 weeks ago at or beyond the
    3-year 5th/95th percentile; two straight weekly moves against it; the 2-week move at least 1.5 times the
    series' typical weekly change (52 weeks); the percentile now 20+ points off that extreme. Over the year to
    2026-10 this marked about 40 turns across 62 series (under one a year per series); the old rule (any one-week
    move against a 10th/90th extreme) marked 242."""
    hist = pd.DataFrame(rows)
    hist["date"] = pd.to_datetime(hist["date"])
    for x in now:
        x["core"] = x["name"] not in NON_CORE_COT
        g = hist[hist["market"] == x["market"]].set_index("date").sort_index()
        for w in ("am", "lev"):
            x[w + "_z1"] = x[w + "_z4"] = x[w + "_turn"] = None
            s_ = g[w + "_pct"].dropna()
            if len(s_) < 60:
                continue
            ch = s_.diff()
            sd = ch.iloc[-52:].std()
            if sd and not np.isnan(sd):
                x[w + "_z1"] = round(float(ch.iloc[-1] / sd), 1)
                x[w + "_z4"] = round(float((s_.iloc[-1] - s_.iloc[-5]) / (2 * sd)), 1)
            pct = lambda i: (s_.iloc[: i + 1].iloc[-156:] < s_.iloc[i]).mean() * 100  # 3-year percentile as of week i
            n = len(s_) - 1
            past = [pct(i) for i in (n - 4, n - 3, n - 2)]
            c1, c2, now_p = ch.iloc[-1], ch.iloc[-2], pct(n)
            big = abs(c1 + c2) >= 1.5 * sd
            if max(past) >= 95 and c1 < 0 and c2 < 0 and big and now_p <= max(past) - 20:
                x[w + "_turn"] = {"from": "high", "peak": round(max(past)), "now": round(now_p), "move_2w": round(float(c1 + c2), 1)}
            elif min(past) <= 5 and c1 > 0 and c2 > 0 and big and now_p >= min(past) + 20:
                x[w + "_turn"] = {"from": "low", "peak": round(min(past)), "now": round(now_p), "move_2w": round(float(c1 + c2), 1)}


# ---- FRED: yield decomposition, credit, curve, liquidity ----
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}&cosd=2022-01-01"
RATE_SERIES = {"DGS10": "nominal10", "DGS2": "nominal2", "DFII10": "real10", "T10YIE": "be10", "THREEFYTP10": "tp10",
               "T10Y2Y": "curve", "BAMLH0A0HYM2": "hy_oas", "BAMLC0A0CM": "ig_oas",
               "BAMLH0A3HYC": "ccc_oas", "BAMLH0A1HYBB": "bb_oas",  # credit-quality split for the alert lamps
               "BAMLC0A0CMEY": "ig_yield"}
# all Wednesday levels, like WALCL (WTREGEN and WRESBAL are week averages: mixing them put the 2026-10-07 weekly change
# in net liquidity at +$82bn instead of +$112bn)
LIQ_SERIES = {"WALCL": "fed_bs", "WDTGAL": "tga", "RRPONTSYD": "rrp", "WRBWFRBL": "reserves"}


def fred(series_id, tries=4):
    for attempt in range(tries):
        try:
            # FRED stalls on browser-like user agents from scripts; a curl agent is answered at once
            req = urllib.request.Request(FRED.format(series_id), headers={"User-Agent": "curl/8.7.1"})
            text = urllib.request.urlopen(req, timeout=90).read().decode()
            break
        except (TimeoutError, OSError, http.client.HTTPException):
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    df = pd.read_csv(io.StringIO(text))
    df.columns = ["date", "v"]
    df["date"] = pd.to_datetime(df["date"])
    return pd.to_numeric(df.set_index("date")["v"], errors="coerce").dropna()


TREASURY = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
            "{y}/all?type={t}&field_tdr_date_value={y}&page&_format=csv")


def treasury(kind, year):
    """Treasury's daily par yields, published the same evening (FRED's DGS10/DGS2/DFII10 copy them a day or more
    later)."""
    req = urllib.request.Request(TREASURY.format(y=year, t=kind), headers={"User-Agent": "curl/8.7.1"})
    df = pd.read_csv(io.StringIO(urllib.request.urlopen(req, timeout=60).read().decode()))
    df["Date"] = pd.to_datetime(df["Date"])
    return df.set_index("Date").sort_index()


def treasury_latest():
    """nominal10, nominal2, real10 and be10 from Treasury for this year (and last year in January)."""
    today = pd.Timestamp.today()
    frames = []
    for y in sorted({today.year, (today - pd.Timedelta(days=40)).year}):
        nom = treasury("daily_treasury_yield_curve", y)
        real = treasury("daily_treasury_real_yield_curve", y)
        frames.append(pd.DataFrame({"nominal10": nom["10 Yr"], "nominal2": nom["2 Yr"], "real10": real["10 YR"]}))
    t = pd.concat(frames).sort_index()
    t["be10"] = t["nominal10"] - t["real10"]
    return t


# ---- investment-grade spread watch: is a widening a supply jam (passes) or buyers stepping back (serious)? ----
# An episode opens when the IG spread is 10bp above its one-month low. High yield joining (25bp above its level at the
# episode's start, held until it gives back to 15bp) means buyers stepping back; IG alone means new-issue supply. A
# supply widening that hasn't retraced after 15 sessions is "stuck", the step before buyers step back. Half the rise
# given back is "recovered", shown for 5 sessions before the episode closes. First set 2026-10-13, untested (rulebook §13).
IGW_OPEN, IGW_HY_ON, IGW_HY_OFF, IGW_STUCK_DAYS, IGW_STUCK_GAP, IGW_SHOW_RECOVERED = 0.10, 0.25, 0.15, 15, 0.03, 5


def ig_watch(df):
    """One row per day: state (quiet / supply / stuck / demand / recovered) and the open episode's numbers."""
    d = df[["ig_oas", "hy_oas"]].ffill().dropna()
    ig, hy = d["ig_oas"], d["hy_oas"]
    widen = (ig - ig.rolling(22).min()) >= IGW_OPEN - 1e-9
    rows, ep = [], None
    for i, (t, on) in enumerate(widen.items()):
        if ep is None and on:
            j = ig.iloc[max(0, i - 21):i + 1].idxmin()
            ep = {"start": t, "i0": i, "base": ig[j], "hy_base": hy.loc[j:t].min(), "peak": ig[t], "hy_on": False,
                  "was_supply": False, "rec_i": None}
        state = "quiet"
        if ep:
            ep["peak"] = max(ep["peak"], ig[t])
            hy_up = hy[t] - ep["hy_base"]
            ep["hy_on"] = hy_up >= IGW_HY_ON - 1e-9 or (ep["hy_on"] and hy_up >= IGW_HY_OFF - 1e-9)
            if ep["hy_on"]:
                state = "demand"
            elif ig[t] <= ep["base"] + (ep["peak"] - ep["base"]) / 2:
                state = "recovered"
            elif i - ep["i0"] >= IGW_STUCK_DAYS and ig[t] >= ep["peak"] - IGW_STUCK_GAP:
                state = "stuck"
            else:
                state = "supply"
            ep["was_supply"] |= state in ("supply", "stuck")
            row = {"state": state, "start": ep["start"], "days": i - ep["i0"],
                   "rise": (ep["peak"] - ep["base"]) * 100, "off_peak": (ep["peak"] - ig[t]) * 100,
                   "hy_rise": hy_up * 100, "from_supply": ep["was_supply"] and state == "demand"}
            if state == "recovered":
                ep["rec_i"] = i if ep["rec_i"] is None else ep["rec_i"]
                if i - ep["rec_i"] >= IGW_SHOW_RECOVERED:
                    ep, row = None, {"state": "quiet"}
            else:
                ep["rec_i"] = None
        else:
            row = {"state": "quiet"}
        rows.append({"date": t, **row})
    return pd.DataFrame(rows).set_index("date")


def build_rates():
    start = pd.Timestamp.today() - pd.DateOffset(years=3)
    rates = pd.DataFrame({k: fred(sid) for sid, k in RATE_SERIES.items()})
    # FRED's Treasury yields lag a day or more; fill the days it hasn't caught up on from Treasury itself (same series)
    fred_last = rates["nominal10"].dropna().index[-1]
    try:
        t = treasury_latest()
        t = t[t.index > fred_last]
        rates = rates.combine_first(t)
        src, treasury_ok = ("treasury" if len(t) else "fred"), True
    except Exception as e:  # keep FRED alone if Treasury is down
        print(f"  Treasury yields unavailable ({e}); FRED only")
        src, treasury_ok = "fred", False
    rates = rates[rates.index >= start - pd.DateOffset(days=120)]
    # the curve from the same day's 10-year and 2-year (T10Y2Y can print a day ahead of DGS10/DGS2 and then disagree
    # with the yields shown beside it)
    rates["curve"] = rates["nominal10"] - rates["nominal2"]
    filled = rates.ffill(limit=10)  # term premium and OAS publish on slightly different days
    # only days the 10-year printed: a breakeven printing a day early used to add a filled-forward 10-year point
    out = filled[(filled.index >= start) & rates["nominal10"].notna()]
    write("rates", [{"date": d.strftime("%Y-%m-%d"), **{k: r(v, 3) for k, v in x.items()}} for d, x in out.iterrows()])

    liq = pd.DataFrame({k: fred(sid) for sid, k in LIQ_SERIES.items()})
    liq = liq.ffill(limit=7).loc[liq["fed_bs"].dropna().index]  # the Fed's weekly (Wednesday) balance-sheet dates
    liq["fed_bs"] /= 1000; liq["tga"] /= 1000; liq["reserves"] /= 1000  # millions -> billions ($bn); RRP is already $bn
    liq["net_liq"] = liq["fed_bs"] - liq["tga"] - liq["rrp"]
    liq = liq[liq.index >= start].dropna(subset=["net_liq"])
    write("liquidity", [{"date": d.strftime("%Y-%m-%d"), **{k: r(v, 0) for k, v in x.items()}} for d, x in liq.iterrows()])

    # every reading as of the 10-year's last print, so real yield + breakeven add up to the nominal shown beside them
    last = rates["nominal10"].dropna().index[-1]
    cut = rates.loc[:last]

    def past(col, days):
        s = cut[col].dropna()
        return s, s[s.index <= s.index[-1] - pd.Timedelta(days=days)]

    def delta(col, days, bp=True):
        s, p = past(col, days)
        return None if p.empty else r((s.iloc[-1] - p.iloc[-1]) * (100 if bp else 1), 0)

    def base(days):
        p = past("nominal10", days)[1]
        return None if p.empty else p.index[-1].strftime("%Y-%m-%d")
    now = {"date": last.strftime("%Y-%m-%d"), "tp_date": cut["tp10"].dropna().index[-1].strftime("%Y-%m-%d"),
           "hy_date": cut["hy_oas"].dropna().index[-1].strftime("%Y-%m-%d"), "yield_src": src,
           "base1w": base(7), "base1m": base(30), "base3m": base(91)}
    for col in RATE_SERIES.values():
        now[col] = r(cut[col].dropna().iloc[-1], 3)
        s1 = cut[col].dropna()
        now[col + "_date"] = s1.index[-1].strftime("%Y-%m-%d")
        now[col + "_prev_date"] = s1.index[-2].strftime("%Y-%m-%d") if len(s1) > 1 else None
        now[col + "_1d"] = r((s1.iloc[-1] - s1.iloc[-2]) * 100, 0) if len(s1) > 1 else None  # since the last print
        now[col + "_1w"] = delta(col, 7)
        now[col + "_1m"] = delta(col, 30)
        now[col + "_3m"] = delta(col, 91)
        now[col + "_1y"] = delta(col, 365)
    ys = cut["ig_yield"].dropna()
    ys = ys[ys.index >= ys.index[-1] - pd.DateOffset(years=3)]
    now["ig_yield_pctile"] = r((ys.iloc[:-1] < ys.iloc[-1]).mean() * 100, 0)
    w = ig_watch(cut).iloc[-1]
    now["igw_state"] = w["state"]
    if w["state"] != "quiet":
        now.update({"igw_start": w["start"].strftime("%Y-%m-%d"), "igw_days": int(w["days"]), "igw_rise": r(w["rise"], 0),
                    "igw_off_peak": r(w["off_peak"], 0), "igw_hy_rise": r(w["hy_rise"], 0),
                    "igw_from_supply": bool(w["from_supply"])})
    nl = liq["net_liq"]
    now.update({"net_liq": r(nl.iloc[-1], 0), "net_liq_1w": r(nl.iloc[-1] - nl.iloc[-2], 0),
                "net_liq_4w": r(nl.iloc[-1] - nl.iloc[-5], 0),
                "net_liq_13w": r(nl.iloc[-1] - nl.iloc[-14], 0), "liq_date": nl.index[-1].strftime("%Y-%m-%d")})
    write("rates_now", [now])
    # the days the 10-year printed (FRED and Treasury), and whether Treasury answered: the bond-market calendar that
    # main() applies to the Yahoo yields in the macro table
    return rates["nominal10"].dropna().index, treasury_ok


if __name__ == "__main__":
    if sys.argv[1:] == ["positioning"]:
        build_positioning(); build_rates()
    else:
        sys.exit(main())
