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
           "XLU": "公用事業", "XLB": "原材料", "XLI": "工業", "XLY": "非必需消費", "XLK": "科技",
           "GDX": "貴金屬"}  # gold miners, listed with the sectors (user, 2026-10-15)
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


LAYER_SHORT = {"L5_MEM": "記憶體", "L4_ASIC": "互連與定制晶片", "L3_OPT": "光模組與網絡", "L2_IP": "晶片 IP", "L1_PWR": "電力",
               "NEO": "新雲", "SPEND": "付款方（超大型雲端）", "ALT": "私募信貸管理人", "BDC": "BDC"}


# Daily summary implementation; weekly/CFTC helpers below are no longer emitted on the homepage.
from daily_summary import overview


def cot_line(pos):
    """期貨持倉 in one sentence: this week's and four weeks' biggest changes (scaled by each series' own weekly swing,
    core macro markets only) and any clear turn from a deep extreme (build_data.positioning_reads)."""
    hold = lambda p, w: "資產管理機構" if w == "am" else ("槓桿基金" if p["has_am"] else "管理資金")
    legs = [(p, w) for p in pos for w in ("lev", "am") if p.get("core", True) and p.get(w + "_z1") is not None]
    wk = sorted(legs, key=lambda t: -abs(t[0][t[1] + "_z1"]))[:2]
    m4 = [t for t in sorted(legs, key=lambda t: -abs(t[0][t[1] + "_z4"] or 0)) if t not in wk][:2]
    nm = lambda t: f"{t[0]['name'].removesuffix('期貨')}（{hold(*t)}）"
    side = lambda v: "long" if v > 0 else "short"
    line = (f"* 期貨持倉｜每週，截至 {md(pos[0]['date'])}：本週淨持倉變化最大是"
            + "及".join(f"{nm(t)} {sg(t[0][t[1] + '_chg_1w'])} 點" for t in wk) + "；四週累計，"
            + "、".join(f"{nm(t)}偏向 {side(t[0][t[1] + '_chg_4w'])}（{sg(t[0][t[1] + '_chg_4w'])} 點）" for t in m4) + "。")
    turns = [(p, w) for p in pos for w in ("lev", "am") if p.get(w + "_turn")]
    if turns:
        line += "持倉開始轉向：" + "、".join(
            f"{nm(t)}由三年{'高' if t[0][t[1] + '_turn']['from'] == 'high' else '低'}位{'回落' if t[0][t[1] + '_turn']['from'] == 'high' else '回升'}"
            for t in turns) + "。"
    return line


QNAME = {"leading": "領先", "improving": "改善", "weakening": "轉弱", "lagging": "落後"}
TURN = {"confirmed": "確認", "early": "初步", "unconfirmed": "未確認"}


def fw_state(r, leader):
    """The board's weekly state (index.html fwState) on the last completed week's fields (_fw): entry, watch, hold,
    noadd, warn, exit, weak or lag. Exit signals (rulebook §10a step 6): weakening with the RS-ratio down two weeks, or
    a drop into lagging; for a leader only a one-week drop from leading into lagging at −8% or worse vs the S&P 500."""
    q, q1, chg = r["q_fw"], r.get("q1_fw"), r.get("chg_fw") or 0
    if (q == "weakening" and chg < 0 and (r.get("chg_prev_fw") or 0) < 0) or (q == "lagging" and q1 in ("weakening", "leading")):
        if not leader:
            return "exit"
        return "exit" if q == "lagging" and q1 == "leading" and (r.get("relw_fw") or 0) <= -8 else "warn"
    if q == "leading":
        return "hold" if chg > 0 else "noadd"
    if q == "improving":
        return "entry" if r.get("turn_fw") == "confirmed" else "watch"
    return "weak" if q == "weakening" else "lag"


def rotation_weekly():
    """板塊輪動 and 股票 lines from the last completed week only (user, 2026-10-15: weekly, updated on the Friday close;
    rulebook §10a: weekday readings are previews, not signals). Same leaders and states as the board's leaders card
    (index.html leaderPool / fwState): AI baskets top three by 26-week strength plus new entrants, S&P sectors top two."""
    groups = json.load(open(DATA / "sectors.json"))
    stocks = json.load(open(DATA / "stocks.json"))
    week = groups[0]["week_fw"]

    def glabel(g):
        st = fw_state(g, False)
        if st in ("entry", "watch"):
            return f"改善（{TURN.get(g.get('turn_fw'), '未確認')}）"
        return {"hold": "領先", "noadd": "領先（強度回落）", "exit": "出場", "warn": "轉弱", "weak": "轉弱", "lag": "落後"}[st]

    order = ["領先", "領先（強度回落）", "改善（確認）", "改善（初步）", "改善（未確認）", "轉弱", "落後", "出場"]

    def by_state(gs):
        b = {}
        for g in gs:
            b.setdefault(glabel(g), []).append(g["name"])
        return "；".join(f"{k} {'、'.join(b[k])}" for k in order if k in b)

    sec = [g for g in groups if g["group"] == "sector"]
    ai = [g for g in groups if g["group"] == "ai"]
    moved = [f"{g['name']} {QNAME[g['q1_fw']]} → {QNAME[g['q_fw']]}" for g in sec + ai
             if g.get("q1_fw") and g["q1_fw"] != g["q_fw"]]
    out = ["", f"板塊輪動（週線；週結 {md(week)}收市，只在週五收市後更新）：", "",
           f"* 標普板塊：{by_state(sec)}。",
           f"* 比上週：{'；'.join(moved) if moved else '象限沒有變化'}。",
           f"* AI 子板塊：{by_state(ai)}。"]

    # leaders, as index.html leaderPool(): a name in two groups is kept once (AI first)
    strength = lambda r: r.get("rel26w_fw") if r.get("rel26w_fw") is not None else r.get("rel6m")
    pool, seen = [], set()
    for g in ai + sec:
        if g["group"] == "ai":
            ms = [x for x in stocks if g["ticker"] in (x.get("baskets") or "").split()]
        else:
            ms = [x for x in stocks if x.get("sector") == g["ticker"]]
        ms = sorted([x for x in ms if strength(x) is not None], key=lambda x: -strength(x))
        top = ms[:3] if g["group"] == "ai" else ms[:2]
        new = [x for x in ms[3:] if (x.get("rel4w_fw") or 0) >= 15 and (x.get("chg_fw") or 0) > 0] if g["group"] == "ai" else []
        for x, leader in [(x, True) for x in top] + [(x, False) for x in new]:
            if x["ticker"] not in seen:
                seen.add(x["ticker"])
                pool.append((x, fw_state(x, leader)))

    def names(state):
        xs = []
        for x, st in pool:
            if st != state:
                continue
            notes = []
            if state == "watch":
                notes.append(TURN.get(x.get("turn_fw"), "未確認"))
            if (x.get("chg_fw") or 0) >= 5 and (x.get("relw_fw") or 0) >= 8:
                notes.append("急升")
            if x["q_fw"] == "leading" and x.get("gap_fw") is not None and x["gap_fw"] < 8:
                notes.append("重返，新注碼減半")
            xs.append(x["ticker"] + (f"（{'，'.join(notes)}）" if notes else ""))
        return "、".join(xs) or "無"

    out += ["", "股票（龍頭名單；規則讀數，不是建議）：", "",
            f"* 入場候選（改善＋確認）：{names('entry')}。",
            f"* 觀察（改善但未確認）：{names('watch')}。",
            f"* 持有（領先、強度上升）：{names('hold')}。",
            f"* 不加注（領先、強度回落）：{names('noadd')}。",
            f"* 轉弱守止蝕：{names('warn')}。",
            f"* 出場訊號：{names('exit')}。"]
    return out


def main():
    from daily_summary import build
    print(build())
    print("\n缺數或要核實（不要放入摘要，先處理）：")
    print("- 大市取態須使用同一收市快照的既有風險模型；不可把單日改善稱為已確認轉強。")
    print("- 檢查AI融資壓力資料；只有新的可核對CDS／指定債券息差才更新結論及圖表，缺報價仍未確認。")
    print("- 保留三個圖表標記；每日重寫市場含意，移除環球、EWY/EWT、CFTC、週線清單及個股交易標籤。")
    print("- 日期須逐項核對；未完成交易日不能當收市，舊報價不能當連續改善。")


if __name__ == "__main__":
    main()
