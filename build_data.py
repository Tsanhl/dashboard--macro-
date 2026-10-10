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
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf


OUT = Path(__file__).resolve().parent / "data"
OUT.mkdir(exist_ok=True)

# ticker: (繁體名, group)
ETFS = {
    "XLK": ("科技", "sector"), "XLC": ("通訊服務", "sector"), "XLY": ("非必需消費", "sector"),
    "XLF": ("金融", "sector"), "XLI": ("工業", "sector"), "XLE": ("能源", "sector"),
    "XLB": ("原材料", "sector"), "XLU": ("公用事業", "sector"), "XLP": ("必需消費", "sector"),
    "XLV": ("醫療保健", "sector"), "XLRE": ("房地產", "sector"),
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
    "^IRX": ("美國3個月國庫券收益率", "%"), "DX-Y.NYB": ("美元指數", ""),
    "CL=F": ("WTI原油", "$"), "HG=F": ("銅", "$"), "GC=F": ("黃金", "$"),
    "^VIX": ("VIX波動率", ""), "^MOVE": ("MOVE債市波動率", ""), "USDJPY=X": ("美元兌日圓", ""),
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
}
# Extra single names to make searchable beyond the S&P 500 and the baskets
WATCH_EXTRA = ["CBRS", "INTC", "TSLA", "AAPL", "PLTR"]
# Company names for tickers outside the S&P 500 member list
EXTRA_NAMES = {
    "TSM": "Taiwan Semiconductor", "CRWV": "CoreWeave", "NBIS": "Nebius", "IREN": "IREN", "APLD": "Applied Digital",
    "CRDO": "Credo Technology", "ALAB": "Astera Labs", "COHR": "Coherent", "LITE": "Lumentum", "CIEN": "Ciena",
    "FN": "Fabrinet", "AAOI": "Applied Optoelectronics", "SNDK": "SanDisk", "TLN": "Talen Energy", "BE": "Bloom Energy",
    "MSTR": "Strategy", "HOOD": "Robinhood", "COIN": "Coinbase", "CBRS": "Cerebras", "MRVL": "Marvell",
}

# English for every Chinese name, label and meaning written to the data files (繁體/English switch)
EN = {
    "科技": "Technology", "通訊服務": "Communication services", "非必需消費": "Consumer discretionary",
    "金融": "Financials", "工業": "Industrials", "能源": "Energy", "原材料": "Materials",
    "公用事業": "Utilities", "必需消費": "Consumer staples", "醫療保健": "Health care", "房地產": "Real estate",
    "半導體": "Semiconductors", "軟件": "Software", "地區銀行": "Regional banks", "生物科技": "Biotech",
    "住宅建築": "Homebuilders", "航天國防": "Aerospace & defense", "標普等權": "S&P 500 equal weight",
    "羅素2000小型股": "Russell 2000 small caps", "納指100": "Nasdaq-100", "動量因子": "Momentum factor",
    "低波動因子": "Low-volatility factor",
    "日本": "Japan", "歐洲": "Europe", "中國大型股": "China large caps", "香港": "Hong Kong", "台灣": "Taiwan",
    "南韓": "South Korea", "印度": "India", "新興市場": "Emerging markets", "黃金": "Gold", "比特幣ETF": "Bitcoin ETF",
    "標普500": "S&P 500",
    "美國10年期國債收益率": "US 10-year Treasury yield", "美國5年期國債收益率": "US 5-year Treasury yield",
    "美國3個月國庫券收益率": "US 3-month bill yield", "美元指數": "US dollar index", "WTI原油": "WTI crude",
    "銅": "Copper", "VIX波動率": "VIX", "MOVE債市波動率": "MOVE bond volatility", "美元兌日圓": "USD/JPY",
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
    "加密相關": "Crypto-linked",
    "股票指數": "Equity index", "美債": "Treasuries", "波動率": "Volatility", "外匯": "FX", "商品": "Commodities",
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


def download(tickers):
    df = yf.download(sorted(set(tickers)), period="2y", interval="1d", auto_adjust=True,
                     progress=False, threads=True, group_by="column")
    return df["Close"], df["High"], df["Low"]


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
    tickers = (list(ETFS) + list(GLOBAL) + list(MACRO) + ["SPY", "HYG", "IEF"] + list(mem["Symbol"])
               + basket_members + WATCH_EXTRA)
    print(f"downloading {len(set(tickers))} tickers…")
    close, high, low = download(tickers)
    close = close.dropna(how="all")
    asof = close["SPY"].dropna().index[-1]
    # US trading days only (futures and FX also print on US holidays)
    close = close.loc[close["SPY"].notna()].loc[:asof]
    stamp = {"asof": asof.strftime("%Y-%m-%d"),
             "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    spy = close["SPY"].dropna()
    one_year = close.index[close.index > asof - pd.Timedelta(days=366)]

    # ---- member stats (breadth) ----
    stocks = [s for s in mem["Symbol"] if s in close and close[s].notna().sum() > 260]
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

    breadth = []
    for d in one_year:
        breadth.append({
            "date": d.strftime("%Y-%m-%d"), "pct20": r(pct20[d], 1), "pct50": r(pct50[d], 1),
            "pct200": r(pct200[d], 1), "adv": int(adv[d]), "dec": int(dec[d]),
            "ad_line": int(ad_line[d]), "new_highs": int(nh[d]), "new_lows": int(nl[d]),
            "mcclellan": r(mcclellan[d], 1), "ew_index": r(ew_index[d]), "spy_index": r(spy_index.get(d)),
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
    weekly = close.resample("W-FRI").last().loc[:asof]
    smh = close["SMH"].dropna()

    def quadrant(rr, rm):
        if rr is None or rm is None or pd.isna(rr) or pd.isna(rm):
            return None
        return "leading" if rr >= 100 and rm >= 100 else "weakening" if rr >= 100 else "improving" if rm >= 100 else "lagging"

    def rrg(t, bench):
        """Weekly RS-ratio (vs its 10-week average) and momentum (vs 4 weeks earlier), against a benchmark."""
        rs = (weekly[t] / weekly[bench]).dropna()
        ratio = 100 * rs / rs.rolling(10).mean()
        return ratio, 100 * ratio / ratio.shift(4)

    def rotation(t):
        """Return fields and relative rotation vs the S&P 500 and vs semis (SMH)."""
        s = close[t].dropna()
        row = {"r1w": r(pct(s, 5)), "r1m": r(pct(s, 21)), "r3m": r(pct(s, 63)), "r6m": r(pct(s, 126)), "rytd": r(ytd(s)),
               "vs_50d": r((s.iloc[-1] / s.rolling(50).mean().iloc[-1] - 1) * 100) if len(s) >= 50 else None,
               "off_high": r((s.iloc[-1] / s.iloc[-252:].max() - 1) * 100)}
        tail = {}
        for suffix, bench, bs in (("", "SPY", spy), ("_smh", "SMH", smh)):
            for n, k in ((5, "rel1w"), (21, "rel1m"), (63, "rel3m"), (126, "rel6m")):
                a_, b_ = pct(s, n), pct(bs, n)
                row[k + suffix] = None if a_ is None or b_ is None else r(a_ - b_)
            ratio, mom = rrg(t, bench)
            ok = len(mom.dropna()) > 0
            row["rs_ratio" + suffix] = r(ratio.iloc[-1]) if ok else None
            row["rs_mom" + suffix] = r(mom.iloc[-1]) if ok else None
            row["quadrant" + suffix] = quadrant(ratio.iloc[-1], mom.iloc[-1]) if ok else None
            row["quadrant_4w" + suffix] = quadrant(ratio.iloc[-5], mom.iloc[-5]) if len(mom.dropna()) > 4 else None
            for d in ratio.index[-10:]:
                tail.setdefault(d, {})["rs_ratio" + suffix] = r(ratio[d])
                tail[d]["rs_mom" + suffix] = r(mom[d])
        tails_out = [{"ticker": t, "week": d.strftime("%Y-%m-%d"), **v} for d, v in tail.items()]
        return row, tails_out

    sectors, tails = [], []
    universe = [(t, name, group) for t, (name, group) in ETFS.items()] + [(b, n, "ai") for b, (n, _) in AI_BASKETS.items()]
    for t, name, group in universe:
        s = close[t].dropna()
        row, tl = rotation(t)
        row = {"ticker": t, "name": name, "group": group, "price": r(s.iloc[-1]), **row,
               "vs_200d": r((s.iloc[-1] / s.rolling(200).mean().iloc[-1] - 1) * 100)}
        if group == "ai":
            row["members_list"] = " ".join(AI_BASKETS[t][1])
        p = participation.get(t, {})
        row.update({"members": p.get("members"), "m_pct50": p.get("pct50"), "m_pct50_1w": p.get("pct50_1w"), "m_pct50_1m": p.get("pct50_1m"),
                    "m_pct200": p.get("pct200"), "m_pct_up_1m": p.get("pct_up_1m"),
                    "m_median_1m": p.get("median_1m"), "m_new_highs_5d": p.get("new_highs_5d")})
        sectors.append(row)
        tails += tl
    write("sectors", sectors)
    write("rrg_tails", tails)

    # ---- single stocks: S&P 500 members, basket members and the watch list ----
    names = dict(zip(mem["Symbol"], mem["Security"])) | EXTRA_NAMES
    in_basket = {}
    for bid, (n, ms) in AI_BASKETS.items():
        for m in ms:
            in_basket.setdefault(m, []).append(bid)
    stock_rows, stock_tails = [], []
    for t in sorted(set(mem["Symbol"]) | set(basket_members) | set(WATCH_EXTRA)):
        if t not in close or close[t].dropna().shape[0] < 30:
            continue
        row, tl = rotation(t)
        stock_rows.append({"ticker": t, "name": names.get(t, t), "baskets": " ".join(in_basket.get(t, [])), **row})
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
                              "chg5d": r(c5), "chg20d": r(c20), "chg60d": r(pct(line, 60)),
                              "vs_50d": r((line.iloc[-1] / sma50_l.iloc[-1] - 1) * 100), "trend": trend})
    write("ratios", ratio_rows)
    write("ratio_summary", ratio_summary)

    # ---- macro ----
    macro = []
    for t, (name, unit) in MACRO.items():
        s = close[t].dropna()
        if s.empty:
            continue
        is_yield = unit == "%"
        def delta(n):
            if len(s) <= n:
                return None
            return (s.iloc[-1] - s.iloc[-1 - n]) * 100 if is_yield else pct(s, n)
        macro.append({"ticker": t, "name": name, "unit": "bp" if is_yield else "%", "last": r(s.iloc[-1], 3 if is_yield else 2),
                      "chg1w": r(delta(5), 1), "chg1m": r(delta(21), 1), "chg3m": r(delta(63), 1),
                      "date": s.index[-1].strftime("%Y-%m-%d")})
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
    build_positioning()
    build_rates()
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
        lev_p, am_p = pctile("lev_pct"), pctile("am_pct")
        # 3-year extreme of a holder's net position: "high" = most long / least short, "low" = the reverse
        extreme = lambda p: None if p is None else "high" if p >= 90 else "low" if p <= 10 else None
        now.append({"market": code, "name": name, "group_name": group, "date": df.index[-1].strftime("%Y-%m-%d"),
                    "am_pct": r(df["am_pct"].iloc[-1], 1), "am_chg_1w": chg("am_pct"), "am_chg_4w": chg("am_pct", 4),
                    "am_pctile": am_p, "lev_pct": r(df["lev_pct"].iloc[-1], 1), "lev_chg_1w": chg("lev_pct"),
                    "lev_chg_4w": chg("lev_pct", 4), "lev_pctile": lev_p,
                    "lev_net": int(df["lev_net"].iloc[-1]), "oi": int(df["oi"].iloc[-1]),
                    "am_extreme": extreme(am_p), "lev_extreme": extreme(lev_p),
                    "has_am": ds == "tff"})
    write("positioning", rows)
    write("positioning_now", now)


# ---- FRED: yield decomposition, credit, curve, liquidity ----
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}&cosd=2022-01-01"
RATE_SERIES = {"DGS10": "nominal10", "DFII10": "real10", "T10YIE": "be10", "THREEFYTP10": "tp10",
               "T10Y2Y": "curve", "BAMLH0A0HYM2": "hy_oas", "BAMLC0A0CM": "ig_oas"}
LIQ_SERIES = {"WALCL": "fed_bs", "WTREGEN": "tga", "RRPONTSYD": "rrp", "WRESBAL": "reserves"}


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


def build_rates():
    start = pd.Timestamp.today() - pd.DateOffset(years=3)
    rates = pd.DataFrame({k: fred(sid) for sid, k in RATE_SERIES.items()})
    rates = rates[rates.index >= start - pd.DateOffset(days=120)]
    filled = rates.ffill(limit=10)  # term premium and OAS publish on slightly different days
    out = filled[filled.index >= start].dropna(subset=["nominal10"])
    write("rates", [{"date": d.strftime("%Y-%m-%d"), **{k: r(v, 3) for k, v in x.items()}} for d, x in out.iterrows()])

    liq = pd.DataFrame({k: fred(sid) for sid, k in LIQ_SERIES.items()})
    liq = liq.ffill(limit=7).loc[liq["fed_bs"].dropna().index]  # the Fed's weekly (Wednesday) balance-sheet dates
    liq["fed_bs"] /= 1000; liq["tga"] /= 1000; liq["reserves"] /= 1000  # millions -> billions ($bn); RRP is already $bn
    liq["net_liq"] = liq["fed_bs"] - liq["tga"] - liq["rrp"]
    liq = liq[liq.index >= start].dropna(subset=["net_liq"])
    write("liquidity", [{"date": d.strftime("%Y-%m-%d"), **{k: r(v, 0) for k, v in x.items()}} for d, x in liq.iterrows()])

    def delta(col, days, bp=True):
        s = rates[col].dropna()
        past = s[s.index <= s.index[-1] - pd.Timedelta(days=days)]
        return None if past.empty else r((s.iloc[-1] - past.iloc[-1]) * (100 if bp else 1), 0)
    now = {"date": rates["nominal10"].dropna().index[-1].strftime("%Y-%m-%d"),
           "tp_date": rates["tp10"].dropna().index[-1].strftime("%Y-%m-%d")}
    for col in RATE_SERIES.values():
        now[col] = r(rates[col].dropna().iloc[-1], 3)
        now[col + "_1w"] = delta(col, 7)
        now[col + "_1m"] = delta(col, 30)
        now[col + "_3m"] = delta(col, 91)
    nl = liq["net_liq"]
    now.update({"net_liq": r(nl.iloc[-1], 0), "net_liq_1w": r(nl.iloc[-1] - nl.iloc[-2], 0),
                "net_liq_4w": r(nl.iloc[-1] - nl.iloc[-5], 0),
                "net_liq_13w": r(nl.iloc[-1] - nl.iloc[-14], 0), "liq_date": nl.index[-1].strftime("%Y-%m-%d")})
    write("rates_now", [now])


if __name__ == "__main__":
    if sys.argv[1:] == ["positioning"]:
        build_positioning(); build_rates()
    else:
        sys.exit(main())
