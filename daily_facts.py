"""Facts for the evening summary (scheduled task evening-dashboard-update), in the user's daily format.

Prints Chinese lines with the latest US session's index, sector, theme, AI-basket, cross-asset and global moves, plus
Treasury, credit and breadth numbers from dashboard/data. Numbers only: the task adds holidays, gilt and Bund yields,
notable movers, news and the calendar. Run after the US close (16:30 ET or later); earlier, the last finished session
is used. Every number that isn't from that session carries its own date, and gaps are listed at the end.
"""
import json
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

from build_data import AI_BASKETS, at_us_close

warnings.filterwarnings("ignore")
DATA = Path(__file__).resolve().parent / "data"

INDEXES = [("^GSPC", "標普500", True), ("^NDX", "納指100", False), ("^DJI", "道指", False), ("^RUT", "羅素2000", False)]
SECTORS = {"XLE": "能源", "XLP": "必需消費", "XLC": "通訊服務", "XLRE": "房地產", "XLV": "醫療保健", "XLF": "金融",
           "XLU": "公用事業", "XLB": "原材料", "XLI": "工業", "XLY": "非必需消費", "XLK": "科技"}
THEMES = [("SMH", "半導體"), ("IGV", "軟件"), ("MTUM", "動量因子"), ("KRE", "地區銀行"), ("IWM", "小型股")]
AI_NAMES = {"AI_HYPER": "超大型雲端", "AI_CHIPS": "運算晶片", "AI_MEM": "記憶體", "AI_NET": "網絡與互連", "AI_OPT": "光通訊",
            "AI_PWR": "電力與散熱", "AI_IPP": "獨立發電", "AI_NEO": "新雲", "AI_CRYPTO": "加密相關", "AI_NUC": "鈾與核能"}
CROSS = ["BZ=F", "CL=F", "GC=F", "HG=F", "DX-Y.NYB"]
# spot FX and bitcoin: the last hourly quote before 16:00 ET on each session (Yahoo's daily bars skip days)
AT_CLOSE = ["JPY=X", "EURUSD=X", "CNH=X", "BTC-USD"]
# local indexes with a usable daily history; HSTECH, CSI 300 and ASML's Amsterdam line are patchy on Yahoo, so they use
# the quote's previous close and follow the session dates of the HSI, the Shanghai Composite and the Stoxx 600
GLOBAL = [("^N225", "日經"), ("^KS11", "KOSPI"), ("^KQ11", "Kosdaq"), ("^TWII", "台股"), ("^HSI", "恒指"),
          ("000001.SS", "上證"), ("^STOXX", "Stoxx 600")]
QUOTE_ONLY = [("HSTECH.HK", "恒生科技", "^HSI"), ("000300.SS", "CSI 300", "000001.SS"), ("ASML.AS", "ASML", "^STOXX")]


def sg(v, n=1):
    """Signed number with a real minus sign, as in the summary."""
    if v is None or pd.isna(v):
        return "—"
    s = f"{abs(v):,.{n}f}"
    return s if float(s.replace(",", "")) == 0 else ("+" if v > 0 else "−") + s


def md(s):
    """2026-10-09 -> 10月9日"""
    return f"{int(s[5:7])}月{int(s[8:])}日"


def closes(tickers):
    df = yf.download(tickers, period="1mo", interval="1d", auto_adjust=False, progress=False)["Close"]
    return df if isinstance(df, pd.DataFrame) else df.to_frame(tickers[0])


def ig_watch_line(rt):
    """The board's investment-grade spread watch line, only when a widening episode is open (same words as the board)."""
    st = rt.get("igw_state")
    if not st or st == "quiet":
        return None
    d = rt["igw_start"]
    since = f"{int(d[5:7])}月{int(d[8:])}日起擴闊 {rt['igw_rise']:.0f} 基點，"
    hy, days, off = sg(rt.get("igw_hy_rise"), 0), rt.get("igw_days"), rt.get("igw_off_peak") or 0
    text = {
        "supply": f"{since}高收益債只 {hy} 基點：似新債供應過多（較輕），留意發債高峰後會否回落",
        "stuck": f"{since}{days} 個交易日仍未回落（距高位 {off:.0f} 基點），高收益債 {hy} 基點："
                 "供應問題拖延，留意會否轉為機構不願承接",
        "demand": f"{since}高收益債同步擴闊 {hy} 基點：似機構不願承接（較嚴重）" + ("，由供應問題演變而來" if rt.get("igw_from_supply") else ""),
        "recovered": f"{since}已回吐 {off:.0f} 基點：已消化",
    }[st]
    p = rt.get("ig_yield_pctile")
    if p is not None and p >= 80 and st != "recovered":
        text += f"；收益率處三年第 {p:.0f} 百分位，高收益率下仍擴闊，訊號更強"
    return f"投資級債息差觀察：{text}。"


def main():
    gaps = []
    members = sorted({m for _, ms in AI_BASKETS.values() for m in ms})
    us = closes(["SPY", "RSP", "^SOX", "^VIX", *[t for t, _, _ in INDEXES], *SECTORS, *[t for t, _ in THEMES], *members, *CROSS])
    spy = us["SPY"].dropna()
    now_et = pd.Timestamp.now(tz="America/New_York")
    if spy.index[-1].date() == now_et.date() and (now_et.hour, now_et.minute) < (16, 30):
        spy = spy.iloc[:-1]  # the session isn't closed yet
    d, d0 = spy.index[-1], spy.index[-2]
    pct = lambda t: (us[t][d] / us[t][d0] - 1) * 100 if t in us and pd.notna(us[t].get(d)) and pd.notna(us[t].get(d0)) else None
    spy_r = pct("SPY")
    rel = lambda t: None if pct(t) is None else pct(t) - spy_r
    out = [f"（美股收市 {d.date()}，對比 {d0.date()}）", "", "指數："]

    idx = []
    for t, name, level in INDEXES:
        idx.append(f"{name} {us[t][d]:,.0f}（{sg(pct(t), 2)}%）" if level else f"{name} {sg(pct(t), 2)}%")
    out.append("* " + "、".join(idx) + "。")
    vix, vix0 = us["^VIX"][d], us["^VIX"][d0]
    out.append(f"* 等權 RSP {sg(pct('RSP'), 2)}%，費城半導體指數 {sg(pct('^SOX'), 2)}%，VIX {vix:.2f}（{sg(vix - vix0, 2)}）。")

    out += ["", "板塊相對標普（百分點）："]
    sec = sorted(SECTORS.items(), key=lambda kv: -(rel(kv[0]) or -99))
    out.append("* " + "、".join(f"{n} {sg(rel(t))}" for t, n in sec) + "。")
    out.append("* 主題和風格：" + "、".join(f"{n} {sg(rel(t))}" for t, n in THEMES) + "。")

    out += ["", "AI 籃子相對標普（百分點）："]
    ai = []
    for b, (_, ms) in AI_BASKETS.items():
        rs = [pct(m) for m in ms if pct(m) is not None]
        missing = [m for m in ms if pct(m) is None]
        if missing:
            gaps.append(f"{AI_NAMES[b]} 缺 {' '.join(missing)}")
        ai.append((AI_NAMES[b], sum(rs) / len(rs) - spy_r if rs else None))
    ai.sort(key=lambda x: -(x[1] if x[1] is not None else -99))
    out.append("* " + "、".join(f"{n} {sg(v)}" for n, v in ai[:6]) + "。")
    out.append("* " + "、".join(f"{n} {sg(v)}" for n, v in ai[6:]) + "。")

    # cross-asset: futures, FX and bitcoin trade on other calendars; take the last bar on or before each session date
    def at(t):
        s = us[t].dropna() if t in us else pd.Series(dtype=float)
        a, b = s.loc[:d], s.loc[:d0]
        if a.empty or b.empty:
            gaps.append(f"{t} 無數據")
            return None, None, ""
        note = "" if (a.index[-1] == d and b.index[-1] == d0) else f"（{a.index[-1].date()} 對比 {b.index[-1].date()}）"
        return a.iloc[-1], (a.iloc[-1] / b.iloc[-1] - 1) * 100, note
    x = {t: at(t) for t in CROSS}
    same_day = datetime.now().date() == d.date()
    h = at_us_close(AT_CLOSE, period="1mo")
    for t in AT_CLOSE:
        s = h[t].dropna() if t in h else pd.Series(dtype=float)
        if d in s.index and d0 in s.index:
            x[t] = (s[d], (s[d] / s[d0] - 1) * 100, "")
        else:
            x[t] = (None, None, "")
            gaps.append(f"{t} 無 {d.date()} 美股收市時的報價")
    v = lambda t, f: "—" if x[t][0] is None else f.format(x[t][0])
    c = lambda t: sg(x[t][1])
    out += ["", "跨資產："]
    out.append(f"* 油：Brent {v('BZ=F', '${:.2f}')}（{c('BZ=F')}%），WTI {v('CL=F', '${:.2f}')}（{c('CL=F')}%）。")
    out.append(f"* 金 {c('GC=F')}%，銅 {c('HG=F')}%，比特幣 {c('BTC-USD')}%。")
    out.append(f"* 美匯指數 {v('DX-Y.NYB', '{:.2f}')}（{sg(x['DX-Y.NYB'][1], 2)}%），美元兌日圓 {v('JPY=X', '{:.2f}')}，"
               f"歐元兌美元 {v('EURUSD=X', '{:.4f}')}，美元兌離岸人民幣 {v('CNH=X', '{:.2f}')}。")
    for t in CROSS:
        if x[t][2]:
            gaps.append(f"{t} 日期不同步 {x[t][2]}")
    try:
        rt = json.load(open(DATA / "rates_now.json"))[0]
        when = "" if rt["date"] == str(d.date()) else f"（{md(rt['date'])}數字）"
        out.append(f"* 美債：10年期 {rt['nominal10']:.2f}%（{sg(rt.get('nominal10_1d'), 0)} 基點）、2年期 {rt['nominal2']:.2f}%"
                   f"（{sg(rt.get('nominal2_1d'), 0)} 基點）{when}；高收益債息差 {rt['hy_oas']:.2f}%（{md(rt['hy_date'])}）；"
                   f"投資級債息差 {rt['ig_oas']:.2f}%（一年 {sg(rt.get('ig_oas_1y'), 0)} 基點），"
                   f"投資級債收益率 {rt['ig_yield']:.2f}%（三年第 {rt['ig_yield_pctile']:.0f} 百分位）。")
    except (OSError, KeyError, IndexError):
        gaps.append("rates_now.json 讀取失敗")

    # global: a market with no bar on the US session date was shut (holiday) or Yahoo has a gap; the task checks which
    gl = closes([t for t, _ in GLOBAL])
    g = {}
    for t, name in GLOBAL:
        s = gl[t].dropna() if t in gl else pd.Series(dtype=float)
        s = s.loc[:d]
        if s.empty or s.index[-1] != d or len(s) < 2:
            g[t] = None
            gaps.append(f"{name} 無 {d.date()} 數據（最後 {s.index[-1].date() if not s.empty else '—'}）：假期或 Yahoo 缺數")
        else:
            g[t] = (s.iloc[-1] / s.iloc[-2] - 1) * 100
    for t, name, follow in QUOTE_ONLY:
        g[t] = None
        if g.get(follow) is None:
            continue
        if not same_day:
            gaps.append(f"{name}：只有即時報價，非收市當晚執行，需核實")
            continue
        try:
            fi = yf.Ticker(t).fast_info
            g[t] = (fi["lastPrice"] / fi["previousClose"] - 1) * 100
        except Exception:
            gaps.append(f"{name} 報價失敗")
    p = lambda t: "休市或缺數" if g.get(t) is None else f"{sg(g[t], 2)}%"
    out += ["", "環球："]
    out.append(f"* 日經 {p('^N225')}，KOSPI {p('^KS11')}，Kosdaq {p('^KQ11')}；台股 {p('^TWII')}。")
    out.append(f"* 恒指 {p('^HSI')}，恒生科技 {p('HSTECH.HK')}；CSI 300 {p('000300.SS')}。")
    out.append(f"* Stoxx 600 {p('^STOXX')}，ASML {p('ASML.AS')}。")

    try:
        b = json.load(open(DATA / "breadth_now.json"))[0]
        when = "" if b["asof"] == str(d.date()) else f"（看板數據 {b['asof']}）"
        out += ["", "看板讀數（另加頁首的「大環境」及 thrust 行）：",
                f"* 高於 50 天線的成分股 {b['pct50']:.1f}%（{sg(b['pct50'] - b['pct50_1d'])}）；麥克連 {sg(b['mcclellan'], 0)}{when}。"]
    except (OSError, KeyError, IndexError):
        gaps.append("breadth_now.json 讀取失敗")
    try:
        line = ig_watch_line(json.load(open(DATA / "rates_now.json"))[0])
        if line:
            out.append(f"* {line}")
    except (OSError, KeyError, IndexError):
        gaps.append("投資級債息差觀察讀取失敗")

    # systematic-flow models (持倉與利率 tab) and COT extremes: model estimates and weekly filings, numbers only
    lv = lambda v: "—" if v is None else f"{v:,.0f}"  # an index level, or a dash when the model has none
    try:
        f = json.load(open(DATA / "flowmodel_now.json"))[0]
        if f["date"] != str(d.date()):
            gaps.append(f"資金流向模型是 {f['date']} 的數據")
        if f.get("vix_date") and f["vix_date"] != str(d.date()):
            gaps.append(f"VIX 期限結構是 {f['vix_date']} 的數據")
        out += ["", "持倉與流向（模型估算）："]
        fl = (f.get("cta_flips") or [{}])[0]
        flat = f.get("cta_flat") or {}
        out.append(f"* CTA 模型 {sg(f['cta'], 0)}（一日 {sg(f['cta_1d'])}、五日 {sg(f['cta_5d'])}）；1個月趨勢轉負位 "
                   f"{lv(fl.get('level'))}（{sg(fl.get('dist'))}%）"
                   + (f"，整體轉平 {lv(flat.get('level'))}（{sg(flat.get('dist'))}%）" if flat else "") + "。")
        out.append(f"* Vol control 為上限的 {f['vc']:.0f}%（五日 {sg(f['vc_5d'])}、一個月 {sg(f['vc_21d'])}）；"
                   f"實現波幅 1個月 {f['rv1']:.1f}%、3個月 {f['rv3']:.1f}%。")
        short = {"us": "美", "de": "德", "uk": "英", "jp": "日"}
        out.append("* CTA 國債信號：" + "、".join(f"{short[b['key']]} {sg(b['signal'], 2)}" for b in f["bonds"])
                   + f"；VIX/VIX3M {f['vix_ratio']:.2f}，SKEW {f['skew']:.1f}。")
    except (OSError, KeyError, IndexError, TypeError, ValueError):
        gaps.append("flowmodel_now.json 讀取失敗")
    try:
        pos = json.load(open(DATA / "positioning_now.json"))
        ext = [f"{p['name']}{'資產管理' if w == 'am' else ('槓桿基金' if p['has_am'] else '管理資金')} {sg(p[w + '_z'])}"
               for p in pos for w in ("am", "lev") if p.get(w + "_z") is not None and abs(p[w + "_z"]) >= 2]
        out.append(f"* COT 52週 z 達 ±2（截至 {md(pos[0]['date'])}）：" + ("、".join(ext) if ext else "無") + "。")
    except (OSError, KeyError, IndexError):
        gaps.append("positioning_now.json 讀取失敗")

    if gaps:
        out += ["", "缺數或要核實（不要放入摘要，先處理）："] + [f"- {s}" for s in gaps]
    print("\n".join(out))


if __name__ == "__main__":
    main()
