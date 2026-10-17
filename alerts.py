"""Alert lamps (警報燈) for the board and the evening summary: rulebook §3 macro thinking order (2026-10-15).

Each lamp is a daily on/off state with separate on and off thresholds (hysteresis), so it does not flicker. The
build records whether it switched today ('new_on' = newly triggered, 'new_off' = improved) and since when it has held.
'kind' is 'risk' (on = warning) or 'relief' (on = the improvement the rulebook waits for). Texts carry changes, not
ICE BofA spread levels, so the same text can go on the public site and into the public summary.
Thresholds are research settings; 'tested' says which rest on a backtest and which do not.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / "data"


def _hyst(on_cond, off_cond):
    """Daily state from on/off conditions: switch on when on_cond holds, back off only when off_cond holds."""
    state, out = False, []
    for on, off in zip(on_cond.fillna(False), off_cond.fillna(False)):
        if not state and on:
            state = True
        elif state and off:
            state = False
        out.append(state)
    return pd.Series(out, index=on_cond.index)


def _meta(st):
    """Today's state, whether it switched today, and the date the current state began."""
    now, prev = bool(st.iloc[-1]), bool(st.iloc[-2]) if len(st) > 1 else bool(st.iloc[-1])
    change = None if now == prev else ("new_on" if now else "new_off")
    flips = st.ne(st.shift())
    since = st.index[flips][-1] if flips.any() else st.index[0]
    return now, change, since.strftime("%Y-%m-%d")


def _basket(close, members):
    rets = close[[m for m in members if m in close]].pct_change(fill_method=None)
    return (1 + rets.mean(axis=1).fillna(0)).cumprod()


def _sg(v, n=1):
    return "—" if v is None or pd.isna(v) else f"{v:+.{n}f}".replace("-", "−")


def build_alerts(close, px, pct50, pct200, baskets):
    """close: all daily closes (US trading days); px: S&P member closes; pct50/pct200: % of members above their
    50/200-day lines; baskets: AI_BASKETS. Writes data/alerts.json and returns the rows."""
    idx = close.index
    spy = close["SPY"]
    rates = pd.DataFrame(json.loads((DATA / "rates.json").read_text())).set_index("date")
    rates.index = pd.to_datetime(rates.index)
    rates = rates.reindex(idx.union(rates.index)).ffill(limit=10).reindex(idx)
    p5 = lambda s: (s / s.shift(5) - 1) * 100
    rows = []

    def add(lid, name, name_en, kind, st, text, text_en, rule, rule_en, tested, tested_en, **fields):
        now, change, since = _meta(st)
        rows.append({"id": lid, "name": name, "name_en": name_en, "kind": kind, "on": now, "change": change,
                     "since": since, "text": text, "text_en": text_en, "rule": rule, "rule_en": rule_en,
                     "tested": tested, "tested_en": tested_en,
                     **{k: (None if v is None or pd.isna(v) else round(float(v), 2)) for k, v in fields.items()}})

    # CCC–BB and index/member divergence are descriptive chart observations.
    # Removed their unvalidated binary alert thresholds (user, 2026-10-17).
    p50 = pct50.reindex(idx)

    # 3. AI credit proxy: the neocloud basket against the S&P (CDS and bond prices come from the evening collector)
    neo = _basket(close, baskets["AI_NEO"][1])
    rel = neo / (spy / spy.iloc[0])
    rel10 = (rel / rel.shift(10) - 1) * 100
    low20 = rel.rolling(20).min()
    new_low = rel <= low20 + 1e-12
    days_since = pd.Series(np.nan, index=idx)
    last = None
    for i in range(len(idx)):
        if new_low.iloc[i]:
            last = i
        days_since.iloc[i] = np.nan if last is None else i - last
    st = _hyst(rel10 <= -15, days_since >= 5)
    orcl5 = p5(close["ORCL"]) - p5(spy) if "ORCL" in close else pd.Series(np.nan, index=idx)
    add("ai_credit", "AI 信用錨（新雲籃子代理）", "AI credit anchor (neocloud basket proxy)", "risk", st,
        f"新雲籃子 10 日相對標普 {_sg(rel10.iloc[-1])}%，距上次 20 日相對新低 {int(days_since.iloc[-1])} 日；ORCL 5 日相對 {_sg(orcl5.iloc[-1])}%",
        f"neocloud basket {_sg(rel10.iloc[-1])}% vs the S&P over 10 days, {int(days_since.iloc[-1])} sessions since a 20-day relative low; ORCL {_sg(orcl5.iloc[-1])}% over 5 days",
        "觸發：10 日跑輸標普 ≥15%。好轉：連續 5 個交易日不創 20 日相對新低。CDS 及債價見每晚收集",
        "On: 15%+ behind the S&P over 10 days. Off: 5 sessions without a 20-day relative low. CDS and bond prices come from the evening collector",
        "未回測", "untested", rel10=rel10.iloc[-1], days_since_low=days_since.iloc[-1], orcl_rel5=orcl5.iloc[-1])

    # 4. rates: the 2-year over three months (the board's existing size-down rule)
    y2 = rates["nominal2"]
    c3 = (y2 - y2.shift(63)) * 100
    r3 = (rates["real10"] - rates["real10"].shift(63)) * 100
    st = _hyst(c3 >= 50, c3 < 30)
    add("rates", "利率壓力", "Rate pressure", "risk", st,
        f"2 年期三個月 {_sg(c3.iloc[-1], 0)} 基點，10 年期實質利率三個月 {_sg(r3.iloc[-1], 0)} 基點",
        f"2-year {_sg(c3.iloc[-1], 0)}bp in three months, 10-year real yield {_sg(r3.iloc[-1], 0)}bp",
        "觸發：2 年期三個月升 ≥50 基點。好轉：回落至 <30 基點",
        "On: 2-year up 50bp+ in three months. Off: under 30bp",
        "已回測：觸發後 40 日標普平均 −0.6% 至 −0.9%（2003 年起）", "tested: S&P −0.6% to −0.9% over the next 40 days (since 2003)",
        y2_3m_bp=c3.iloc[-1], real10_3m_bp=r3.iloc[-1])

    # 5. sector liquidation or market-wide deleveraging
    smh5 = p5(close["SMH"]) - p5(spy)
    vix = close["^VIX"]
    ig5 = (rates["ig_oas"] - rates["ig_oas"].shift(5)) * 100
    market = (vix >= 25) & (ig5 >= 15)
    st = _hyst((smh5 <= -5) | market, (smh5 > -2) & (vix < 20))
    kind_now = "全市場去槓桿" if market.iloc[-1] else "板塊清算" if smh5.iloc[-1] <= -5 else "—"
    kind_en = "market-wide deleveraging" if market.iloc[-1] else "sector liquidation" if smh5.iloc[-1] <= -5 else "—"
    add("liquidation", f"清算類型：{kind_now}", f"Liquidation type: {kind_en}", "risk", st,
        f"半導體 5 日相對標普 {_sg(smh5.iloc[-1])}%，VIX {vix.iloc[-1]:.1f}，投資級息差 5 日 {_sg(ig5.iloc[-1], 0)} 基點",
        f"semis {_sg(smh5.iloc[-1])}% vs the S&P over 5 days, VIX {vix.iloc[-1]:.1f}, IG spread {_sg(ig5.iloc[-1], 0)}bp in 5 days",
        "觸發：半導體 5 日跑輸 ≥5%（板塊清算）；VIX ≥25 而投資級息差 5 日擴闊 ≥15 基點（全市場）。好轉：跑輸收窄至 <2% 而 VIX <20",
        "On: semis 5%+ behind in 5 days (sector), or VIX ≥25 with IG 15bp+ wider in 5 days (market-wide). Off: gap under 2% and VIX under 20",
        "未回測", "untested", smh_rel5=smh5.iloc[-1], vix=vix.iloc[-1], ig_5d_bp=ig5.iloc[-1])

    # 6. crowded Korea unwind
    ewy5 = p5(close["EWY"])
    st = _hyst(ewy5 <= -10, ewy5 >= -3)
    add("korea", "韓國擠擁平倉", "Crowded Korea unwind", "risk", st,
        f"EWY 5 日 {_sg(ewy5.iloc[-1])}%", f"EWY {_sg(ewy5.iloc[-1])}% in 5 days",
        "觸發：5 日跌 ≥10%。好轉：5 日變化回到 ≥−3%", "On: down 10%+ in 5 days. Off: 5-day change back to −3% or better",
        "未回測", "untested", ewy5=ewy5.iloc[-1])

    # 7. AI capex scare fingerprint (rulebook §3): five legs on the last five sessions
    ipp = _basket(close, baskets["AI_IPP"][1])
    ipp5 = p5(ipp) - p5(spy)
    neo5 = p5(neo) - p5(spy)
    y10_5 = (rates["nominal10"] - rates["nominal10"].shift(5)) * 100
    legs = {
        "晶片": smh5 <= -5,
        "亞洲（韓、台）": (ewy5 <= -2) & (p5(close["EWT"]) <= -2),
        "AI 信用代理": (neo5 <= -10) | (orcl5 <= -5),
        "獨立發電": ipp5 <= -3,
        "債券上升": y10_5 < 0,
    }
    legs_en = {"晶片": "chips", "亞洲（韓、台）": "Asia (Korea, Taiwan)", "AI 信用代理": "AI credit proxy",
               "獨立發電": "IPPs", "債券上升": "bonds up"}
    count = sum(v.astype(int) for v in legs.values())
    st = _hyst(count >= 5, count <= 2)
    hit = [k for k, v in legs.items() if bool(v.iloc[-1])]
    add("fingerprint", "AI 資本開支恐慌指紋", "AI capex scare fingerprint", "risk", st,
        f"{int(count.iloc[-1])}/5 條腿：" + ("、".join(hit) if hit else "無"),
        f"{int(count.iloc[-1])}/5 legs: " + (", ".join(legs_en[k] for k in hit) if hit else "none"),
        "觸發：五條腿齊全。好轉：≤2 條", "On: all five legs. Off: two or fewer",
        "此五腿日頻版本未回測；舊週頻研究的市場、條件及回報口徑不同", "this daily five-leg version is untested; the older weekly study used different markets, conditions and return definitions",
        legs=count.iloc[-1])

    # 8. credit reversal checklist (relief lamp): the spike half given back and %>200-day near its pre-shock level
    hy = rates["hy_oas"]
    lo60, pk60 = hy.rolling(60).min(), hy.rolling(60).max()
    spike = (pk60 - lo60) * 100
    given = (pk60 - hy) / (pk60 - lo60).replace(0, np.nan)
    p200 = pct200.reindex(idx)
    pre = p200.shift(40)
    ok1 = (spike >= 40) & (given >= 0.5)
    ok2 = p200 >= pre - 3
    st = _hyst(ok1 & ok2, ~ok1)
    add("credit_reversal", "信用回轉清單（好轉燈）", "Credit reversal checklist (relief lamp)", "relief", st,
        f"高收益債 60 日內擴闊 {_sg(spike.iloc[-1], 0)} 基點、已回吐 {given.iloc[-1] * 100:.0f}%；高於 200 天線 {p200.iloc[-1]:.1f}%（約兩個月前 {pre.iloc[-1]:.1f}%）",
        f"high yield {_sg(spike.iloc[-1], 0)}bp wider within 60 days, {given.iloc[-1] * 100:.0f}% given back; {p200.iloc[-1]:.1f}% above the 200-day (about two months ago {pre.iloc[-1]:.1f}%)",
        "亮燈：擴闊 ≥40 基點後已回吐 ≥一半，而且高於 200 天線回到兩個月前 3 點內；第三項（成因是被迫平倉還是增長衝擊）要人手判斷",
        "On: after a 40bp+ widening, half or more given back, and %>200-day within 3 points of two months ago; the third check (forced unwind or a growth shock) is a judgement",
        "已回測：2024 年 8 月有效，2025 年 3 月成功過濾", "tested: worked in Aug 2024, filtered Mar 2025",
        hy_spike_bp=spike.iloc[-1], given_back=given.iloc[-1], pct200=p200.iloc[-1])

    asof = idx[-1].strftime("%Y-%m-%d")
    for r_ in rows:
        r_["asof"] = asof
    (DATA / "alerts.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n")
    try:  # the five-step read is a teaching aid built on the same series; never lose the lamps to it
        build_steps(dict(close=close, spy=spy, rates=rates, p5=p5, smh5=smh5, vix=vix, ig5=ig5, ewy5=ewy5,
                         rel10=rel10, days_since=days_since, orcl5=orcl5, neo=neo, fp=count, p50=p50, p200=p200,
                         baskets=baskets, lamps={r_["id"]: r_ for r_ in rows}, asof=asof))
    except Exception as e:
        print(f"  WARNING five-step read failed: {e}")
    return rows


# what steps ⑥ and ⑦ test, in plain words (user, 2026-10-15: explain each condition, no slogans)
LOOK_6 = ("延續條件（愈多愈表示賣壓未完）：①信用仍惡化——新雲籃子仍創 20 日相對新低，或 CCC−BB 5 日擴闊 ≥25 基點："
          "此為價格及息差條件，不能據此確認債主撤資。②恐慌指紋 ≥4 條腿——晶片、韓台股市、AI 信用代理、獨立發電、債券上升五個市場"
          "同時指向同一件事：是整條 AI 資本開支鏈的恐慌，不是個別股票。③清算燈亮——半導體 5 日跑輸標普 ≥5 點，或 VIX ≥25"
          " 而投資級息差 5 日擴闊 ≥15 基點：被迫沽售仍在進行。止跌條件（愈多愈表示賣壓接近完結）：①信用止跌——新雲籃子"
          "連續 5 日不創相對新低，而且 CCC−BB 5 日擴闊 <10 基點。②信用回轉清單亮燈——高收益債息差急擴 ≥40 基點後已回吐"
          "一半，而且高於 200 天線的成分股回到兩個月前 3 點內（2024 年 8 月亮燈後回升；2025 年 3 月未亮，擋住了未見底的"
          "反彈）。③市寬修復——高於 50 天線的成分股回到 55% 或以上。")
LOOK_6_EN = ("Continuation (more = selling not done): (1) credit still worsening: the neocloud basket at a new 20-day relative "
             "low, or CCC−BB 25bp+ wider in 5 days; (2) panic fingerprint with 4+ of 5 legs (chips, Korea/Taiwan, AI credit "
             "proxy, IPPs, bonds up): a chain-wide AI capex scare, not single names; (3) liquidation lamp on: semis 5+ pts "
             "behind in 5 days, or VIX 25+ with IG 15bp+ wider. Stopping (more = selling nearly done): (1) credit stops: 5 "
             "sessions without a relative low and CCC−BB under 10bp wider in 5 days; (2) credit reversal checklist on: high "
             "yield gave back half a 40bp+ spike and %>200-day is within 3 pts of two months ago; (3) breadth repaired: 55%+ "
             "above the 50-day.")
LOOK_7 = ("四項測試：①幅度——半導體 5 日相對變化達 2 個標準差或以上（約 5% 的時間才出現）。②持續——清算燈亮 3 日或以上，"
          "不是一日衝擊。③確認——恐慌指紋五條腿中 3 條或以上：晶片（半導體 5 日跑輸標普 ≥5 點）、亞洲（韓國 EWY 及台灣 EWT"
          " 5 日都跌 ≥2%）、AI 信用代理（新雲籃子 5 日跑輸 ≥10 點，或 ORCL 跑輸 ≥5 點）、獨立發電（籃子 5 日跑輸 ≥3 點）、"
          "債券上升（10 年期收益率 5 日下跌）。④信用同步惡化（見③信用）。0 至 1 項多半是噪音，2 項要再看，3 至 4 項是訊號。"
          "回測（2015 年起，半導體一週跑輸 ≥5%，共 17 次）：指紋不齊的 12 次，之後四週跑贏標普 65–83%，多數反彈；五條腿齊全的"
          " 5 次只有 40%，中位數跑輸 1.5 點。樣本少，信用及獨立發電兩條腿未回測；只說明不要假設很快反彈，不說明會再跌多少。")
LOOK_7_EN = ("Four tests: (1) size: a 5-day semis move of 2+ standard deviations; (2) persistence: the liquidation lamp on for 3+ "
             "sessions; (3) confirmation: 3+ of the five fingerprint legs — chips (semis 5+ pts behind in 5 days), Asia (EWY "
             "and EWT both down 2%+), AI credit proxy (neocloud basket 10+ pts behind or ORCL 5+), IPPs (basket 3+ pts "
             "behind), bonds up (10-year yield down over 5 days); (4) credit worsening too. 0–1 = probably noise, 2 = wait, "
             "3–4 = a signal. Since 2015 (17 weeks with semis 5%+ behind): without the full fingerprint (12) semis beat the "
             "S&P over 4 weeks 65–83% of the time; with all legs (5), 40%, median −1.5 pts. Small sample; the credit and IPP "
             "legs are untested; it says don't assume a quick bounce, not how far it falls.")


def _num(x):
    return None if x is None or pd.isna(x) else round(float(x), 2)


def _since(st):
    """The date the current value of a categorical daily series began."""
    flips = st.ne(st.shift())
    return (st.index[flips][-1] if flips.any() else st.index[0]).strftime("%Y-%m-%d")


def build_steps(v):
    """五步讀盤: the rulebook §3 thinking order applied to tonight's data, one row per question, plus a noise test.
    Each row: the question, a state label from fixed rules, the facts behind it, and what to watch (what would change
    the state). Facts and rule outputs only; the conclusion is the reader's. Local board and evening summary only
    (the public build drops it). Writes data/steps.json."""
    close, spy, rates, p5 = v["close"], v["spy"], v["rates"], v["p5"]
    idx, lamps = close.index, v["lamps"]
    smh5, vix, ig5, ewy5, p200 = v["smh5"], v["vix"], v["ig5"], v["ewy5"], v["p200"]
    bp5 = lambda s: (s - s.shift(5)) * 100
    rel5 = lambda s: p5(s) - p5(spy)
    last = lambda s: s.iloc[-1]
    sessions = lambda date: int((idx >= pd.Timestamp(date)).sum())  # sessions since a date, counting it
    steps = []

    def add(n, q, q_en, state, state_en, tone, facts, facts_en, look, look_en, since=None):
        steps.append({"n": n, "q": q, "q_en": q_en, "state": state, "state_en": state_en, "tone": tone,
                      "facts": facts, "facts_en": facts_en, "look": look, "look_en": look_en, "since": since,
                      "asof": v["asof"]})

    # 1. who is falling and who is not: one sector's liquidation, or market-wide deleveraging?
    spy5, rsp_gap = p5(spy), p5(close["RSP"]) - p5(spy)
    market = (vix >= 25) & (ig5 >= 15)
    kind = pd.Series(np.select([market, smh5 <= -5, spy5 <= -3], ["market", "sector", "index"], "none"), index=idx)
    k = last(kind)
    lab = {"market": ("全市場去槓桿", "market-wide deleveraging", "bad"), "sector": ("板塊清算", "sector liquidation", "mix"),
           "index": ("大市回吐", "broad pullback", "mix"), "none": ("沒有清算", "no liquidation", "good")}[k]
    add(1, "誰在跌、誰沒有跌", "Who is falling, who is not", lab[0], lab[1], lab[2],
        f"標普 5 日 {_sg(last(spy5))}%，等權相對市值加權 {_sg(last(rsp_gap))} 點，半導體相對標普 {_sg(last(smh5))} 點，"
        f"VIX {last(vix):.1f}，投資級息差 5 日 {_sg(last(ig5), 0)} 基點，高於 200 天線 {last(p200):.1f}%",
        f"S&P {_sg(last(spy5))}% in 5 days, equal weight {_sg(last(rsp_gap))} pts vs cap weight, semis {_sg(last(smh5))} pts "
        f"vs the S&P, VIX {last(vix):.1f}, IG spread {_sg(last(ig5), 0)}bp in 5 days, {last(p200):.1f}% above the 200-day",
        "板塊清算＝避開一個板塊（指數小跌、板塊大跌、等權跑贏、VIX 受控、投資級息差平穩）；全市場去槓桿＝全面減風險"
        "（VIX ≥25 加投資級息差 5 日擴闊 ≥15 基點）。",
        "Sector liquidation = step away from one sector (index down a little, the sector a lot, equal weight ahead, VIX "
        "contained, IG flat); market-wide deleveraging = cut risk everywhere (VIX 25+ with IG 15bp+ wider in 5 days).",
        _since(kind))

    steps[-1]["nums"] = dict(kind=k, spy5=_num(last(spy5)), rsp_gap=_num(last(rsp_gap)), smh5=_num(last(smh5)),
                             vix=_num(last(vix)), ig5=_num(last(ig5)), p200=_num(last(p200)))

    # 2. which leg is driving, each on its own anchor: rates, positioning, credit (earnings come from the collector)
    y2_5, r10_5 = bp5(rates["nominal2"]), bp5(rates["real10"])
    rate_leg = pd.Series(np.select([(y2_5 >= 10) | (r10_5 >= 10), (y2_5 <= -10) & (r10_5 <= 0)], ["push", "help"], "flat"), index=idx)
    pos_leg = pd.Series(np.select([ewy5 <= -5, ewy5 >= 3], ["push", "help"], "flat"), index=idx)
    # credit: worsening once the neocloud basket makes a deep new relative low or CCC−BB jumps, and until both stop
    # (5 sessions without a new low and CCC−BB no longer widening); healing once low-quality spreads narrow too.
    # Over 2024-12..2026-10 it read worsening on about a third of days (17 spells, median 6 sessions): a condition
    # that sizes the ladder, not a sell signal.
    ds, rel10 = v["days_since"], v["rel10"]
    cb = (rates["ccc_oas"] - rates["bb_oas"]) if {"ccc_oas", "bb_oas"} <= set(rates.columns) else pd.Series(np.nan, index=idx)
    cb5, cb21 = bp5(cb), (cb - cb.shift(21)) * 100
    worse = _hyst(((ds < 5) & (rel10 <= -10)) | (cb5 >= 25), (ds >= 5) & (cb5 < 10))
    heal = ~worse & (cb21 < 0) & (ds >= 10) & (rel10 > 0)
    credit = pd.Series(np.select([worse, heal], ["worse", "heal"], "pause"), index=idx)
    cred_leg = credit.map({"worse": "push", "heal": "help", "pause": "flat"})
    legs = {"利率": (rate_leg, "rates"), "倉位": (pos_leg, "positioning"), "信用": (cred_leg, "credit")}
    push = [z for z, (s, _) in legs.items() if last(s) == "push"]
    word = {"push": "推跌", "flat": "中性", "help": "支持"}
    word_en = {"push": "pushing down", "flat": "neutral", "help": "supportive"}
    add(2, "哪一條腿在推", "Which leg is driving", ("主導：" + "、".join(push)) if push else "沒有一條腿在推",
        ("driving: " + ", ".join(legs[z][1] for z in push)) if push else "no leg driving", "bad" if push else "good",
        f"利率{word[last(rate_leg)]}（2 年期 5 日 {_sg(last(y2_5), 0)} 基點，實質利率 5 日 {_sg(last(r10_5), 0)} 基點）；"
        f"倉位{word[last(pos_leg)]}（EWY 5 日 {_sg(last(ewy5))}%）；信用{word[last(cred_leg)]}（見第 ③ 項）；業績門檻見收集",
        f"rates {word_en[last(rate_leg)]} (2-year {_sg(last(y2_5), 0)}bp, real yield {_sg(last(r10_5), 0)}bp in 5 days); "
        f"positioning {word_en[last(pos_leg)]} (EWY {_sg(last(ewy5))}% in 5 days); credit {word_en[last(cred_leg)]} (see 3); "
        "the earnings bar comes from the collector",
        "每條腿用自己的錨：利率看 2 年期及實質利率；倉位看擁擠度及韓國散戶；信用看最高槓桿借款人的 CDS 及債價，不看高收益債指數。"
        "驅動會輪換，所以每次都重新問。",
        "Each leg on its own anchor: rates on the 2-year and real yields; positioning on crowding and Korean retail; credit "
        "on the most leveraged borrowers' CDS and bonds, not the HY index. The driver rotates, so ask every time.")

    steps[-1]["nums"] = dict(rate=last(rate_leg), pos=last(pos_leg), credit=last(cred_leg), y2_5=_num(last(y2_5)),
                             r10_5=_num(last(r10_5)), ewy5=_num(last(ewy5)))

    # 3. is the credit leg still worsening?
    cs = last(credit)
    lab = {"worse": ("惡化中", "worsening", "bad"), "pause": ("止跌中（未確認）", "pausing (unconfirmed)", "mix"),
           "heal": ("好轉", "healing", "good")}[cs]
    orcl5 = v["orcl5"]
    add(3, "信用這條腿是否仍在惡化", "Is the credit leg still worsening", lab[0], lab[1], lab[2],
        f"新雲籃子距上次 20 日相對新低 {int(last(ds))} 日（10 日相對標普 {_sg(last(rel10))}%）；CCC−BB 5 日 {_sg(last(cb5), 0)} 基點、"
        f"一個月 {_sg(last(cb21), 0)} 基點；ORCL 5 日相對 {_sg(last(orcl5))}%；投資級息差 5 日 {_sg(last(ig5), 0)} 基點",
        f"{int(last(ds))} sessions since the neocloud basket's last 20-day relative low ({_sg(last(rel10))}% vs the S&P over "
        f"10 days); CCC−BB {_sg(last(cb5), 0)}bp in 5 days, {_sg(last(cb21), 0)}bp in a month; ORCL {_sg(last(orcl5))}% "
        f"relative over 5 days; IG {_sg(last(ig5), 0)}bp in 5 days",
        "轉入惡化：新雲籃子 10 日跑輸 ≥10% 並創 20 日相對新低，或 CCC−BB 5 日擴闊 ≥25 基點。止跌：連續 5 日不創新低而 CCC−BB "
        "5 日擴闊 <10 基點。好轉：再加 CCC−BB 一個月收窄、10 日不創新低、10 日相對轉正。真正的確認在收集：CoreWeave CDS 及 "
        "2032 年債收益率、Oracle CDS 不再創新高。息差數據比股價遲一日。過去兩年約三分之一日子屬惡化（中位 6 日），所以它只決定"
        "可以用到哪一級，不是賣出訊號。",
        "Into worsening: the neocloud basket 10%+ behind over 10 days at a new 20-day relative low, or CCC−BB 25bp+ wider "
        "in 5 days. Pausing: 5 sessions without a new low and CCC−BB under 10bp wider in 5 days. Healing: CCC−BB narrower "
        "over a month as well, no new low for 10 sessions, the 10-day relative back above zero. The real confirmation is in "
        "the collector's lines: CoreWeave CDS and 2032 yield, Oracle CDS no longer making highs. Spreads lag a day. It read "
        "worsening on about a third of days over two years (median 6 sessions): it sizes the ladder, it is not a sell signal.",
        _since(credit))

    # semis on the same yardstick as the neocloud basket (the summary's leading-sector check)
    srel = close["SMH"] / spy
    s_new = srel <= srel.rolling(20).min() + 1e-12
    s_last = s_new[s_new].index
    smh_ds = int((idx > s_last[-1]).sum()) if len(s_last) else None
    smh_rel10 = (srel / srel.shift(10) - 1) * 100
    steps[-1]["nums"] = dict(state=cs, since=_since(credit), days_since_low=_num(last(ds)), rel10=_num(last(rel10)),
                             cb5=_num(last(cb5)), cb21=_num(last(cb21)), orcl5=_num(last(orcl5)), ig5=_num(last(ig5)),
                             smh_days_since_low=smh_ds, smh_rel10=_num(last(smh_rel10)))
    # the AI supply-chain stance, one calculation for the summary and the AI stage tab (rulebook §11a: no page computes
    # its own): defensive while credit worsens or the leading groups keep making relative lows
    nd = last(ds)
    lead = ("未確認" if smh_ds is None or pd.isna(nd) else "仍受壓" if min(nd, smh_ds) < 5
            else "初步改善" if (last(rel10) or 0) > 0 and (last(smh_rel10) or 0) > 0 else "暫穩")
    cchk = {"worse": "惡化中", "pause": "暫穩", "heal": "初步改善"}[cs]
    # These inputs are price/broad-credit proxies, not verified borrower CDS/OAS.
    # Keep the same model conditions without declaring an AI funding-risk all-clear.
    stance = ("偏防守" if cchk == "惡化中" or lead == "仍受壓"
              else "價格初步修復，借款人信用仍待確認" if cchk == "初步改善" and lead == "初步改善" else "暫穩，未轉強")
    why = ("信用仍在惡化，領跌板塊仍創相對新低" if cchk == "惡化中" and lead == "仍受壓" else "信用仍在惡化" if cchk == "惡化中"
           else "領跌板塊仍創相對新低" if lead == "仍受壓" else "價格及廣泛信用代理初步改善；借款人信用仍須確認" if cchk == "初步改善" and lead == "初步改善"
           else "信用及領跌板塊停止惡化，未見改善")
    steps[-1]["nums"].update(credit_check=cchk, lead_check=lead, ai_stance=stance, ai_stance_why=why)

    # 4. the transmission chain: who borrows to buy, and has the selling reached the receivers?
    bk = lambda key: _basket(close, v["baskets"][key][1])
    chain = [("新雲（借錢買）", "neoclouds (borrow to buy)", v["neo"]), ("超大型雲端（花錢方）", "hyperscalers (spenders)", bk("AI_HYPER")),
             ("伺服器 DELL／SMCI", "servers DELL/SMCI", _basket(close, ["DELL", "SMCI"])),
             ("運算晶片", "compute chips", bk("AI_CHIPS")), ("記憶體", "memory", bk("AI_MEM")), ("光通訊", "optics", bk("AI_OPT"))]
    r = [(a, b, last(rel5(s))) for a, b, s in chain]
    recv = [x for _, _, x in r[2:]]
    hit = sum(x <= -3 for x in recv)
    st = ("傳導已到下游", "reached the receivers", "bad") if hit >= 3 else \
         ("只限源頭", "contained at the source", "mix") if r[0][2] <= -5 else ("未見傳導", "no transmission", "good")
    gap = r[1][2] - float(np.mean(recv))
    add(4, "傳導鏈：誰借錢買貨", "Transmission: who borrows to buy", st[0], st[1], st[2],
        "5 日相對標普：" + "，".join(f"{a} {_sg(x)}" for a, _, x in r) + f"；花錢方減收錢方 {_sg(gap)} 點",
        "5 days vs the S&P: " + ", ".join(f"{b} {_sg(x)}" for _, b, x in r) + f"; spenders minus receivers {_sg(gap)} pts",
        "新雲融資不到 → 少要雲容量 → 少要伺服器 → 少要晶片、記憶體及光通訊。再問：你持有的公司有幾多需求來自借錢的買家？"
        "花錢方放慢時現金流反而改善，收錢方收入減少；沒有直接關係卻被連帶拋售的，可能是錯價。",
        "Neocloud funding dries up → less cloud capacity → fewer servers → fewer chips, memory and optics. Then ask how much "
        "of each holding's demand comes from borrowers. Spenders' cash flow improves when they slow; receivers lose "
        "revenue; names sold in sympathy with no direct link can be mispriced.")

    steps[-1]["nums"] = dict(state={"傳導已到下游": "reached", "只限源頭": "source"}.get(st[0], "none"),
                             chain={key: _num(x) for key, (_, _, x) in zip(("neo", "hyper", "servers", "chips", "mem", "opt"), r)},
                             gap=_num(gap))

    # 5. how price answers the news: bonds vs stocks over 5 days (earnings reactions come from the collector)
    y10_5, be5 = bp5(rates["nominal10"]), bp5(rates["be10"])
    down = (last(spy5) <= -1) or (last(smh5) <= -3)
    if down and last(y10_5) < 0:
        st = ("股跌債升：當作增長／信用風險", "stocks down, bonds up: read as growth/credit risk", "bad")
    elif down:
        st = ("股債齊跌：利率或通脹壓力", "stocks and bonds down: rate or inflation pressure", "bad")
    elif last(y10_5) > 0:
        st = ("股升息升：增長樂觀", "stocks up, yields up: growth optimism", "good")
    else:
        st = ("股債齊升：寬鬆", "stocks and bonds up: easing", "good")
    add(5, "價格怎樣回應消息", "How price answers the news", st[0], st[1], st[2],
        f"10 年期 5 日 {_sg(last(y10_5), 0)} 基點（實質 {_sg(last(r10_5), 0)}，通脹預期 {_sg(last(be5), 0)}），標普 5 日 {_sg(last(spy5))}%，"
        "半導體相對 " + _sg(last(smh5)) + " 點；業績後反應見摘要「信用錨（收集）」",
        f"10-year {_sg(last(y10_5), 0)}bp in 5 days (real {_sg(last(r10_5), 0)}, breakeven {_sg(last(be5), 0)}), S&P "
        f"{_sg(last(spy5))}%, semis {_sg(last(smh5))} pts relative; post-earnings reactions are in the summary's collected line",
        "好消息仍被賣＝倉位未清完；壞消息不再跌＝賣家接近用完。通脹數據熱但收益率由高位回落＝市場當增長風險，不是通脹問題。",
        "Good news still sold = positioning not yet cleared; bad news no longer hurts = sellers nearly done. A hot inflation "
        "print with yields falling from the high = the market reads growth risk, not inflation.")

    steps[-1]["nums"] = dict(state="growth" if st[0].startswith("股跌債升") else "rates" if st[0].startswith("股債齊跌")
                             else "optimism" if st[0].startswith("股升息升") else "easing",
                             y10_5=_num(last(y10_5)), r10_5=_num(last(r10_5)), be5=_num(last(be5)))

    # 6. valuation vs timing: price can fall through value because the short-term marginal seller ignores it
    keep = [("信用仍惡化", cs == "worse"), ("恐慌指紋 ≥4 條腿", last(v["fp"]) >= 4), ("清算燈亮", lamps.get("liquidation", {}).get("on", False))]
    stop = [("信用止跌 ≥5 日或好轉", cs == "heal" or (cs == "pause" and sessions(_since(credit)) >= 5)),
            ("信用回轉清單亮燈", lamps.get("credit_reversal", {}).get("on", False)),
            ("高於 50 天線回到 ≥55%", last(v["p50"]) >= 55)]
    gate = lamps.get("ai_credit", {}).get("on", False)
    nk, ns = sum(b for _, b in keep), sum(b for _, b in stop)
    add(6, "延續還是止跌", "Still selling or stopping",
        f"延續條件 {nk}/3，止跌條件 {ns}/3", f"continuation {nk}/3, stopping {ns}/3", "bad" if nk > ns else "mix" if nk else "good",
        "延續：" + "、".join(("✓" if b else "✗") + a for a, b in keep) + "。止跌：" + "、".join(("✓" if b else "✗") + a for a, b in stop) + "。",
        f"continuation {nk}/3, stopping {ns}/3",
        LOOK_6, LOOK_6_EN)

    steps[-1]["nums"] = dict(keep=[[a, bool(b)] for a, b in keep], stop=[[a, bool(b)] for a, b in stop], gate=bool(gate),
                             fp=_num(last(v["fp"])), p50=_num(last(v["p50"])))

    # 7. noise or signal: size against its own normal, persistence, independent confirmation, credit
    sd = smh5.rolling(252, min_periods=120).std()
    z = last(smh5) / last(sd) if last(sd) else np.nan
    liq = lamps.get("liquidation", {})
    held = liq.get("on") and sessions(liq["since"]) >= 3
    checks = [("幅度異常（|z| ≥2）", abs(z) >= 2), ("持續 ≥3 日", bool(held)), ("獨立市場確認 ≥3 條腿", last(v["fp"]) >= 3),
              ("信用同步惡化", cs == "worse")]
    n = sum(b for _, b in checks)
    st = ("多半是噪音", "probably noise", "good") if n <= 1 else ("未定，要再看", "undecided", "mix") if n == 2 else ("是訊號", "a signal", "bad")
    add(7, "噪音還是訊號（半導體）", "Noise or signal (semis)", f"{st[0]}（{n}/4）", f"{st[1]} ({n}/4)", st[2],
        f"半導體 5 日相對 {_sg(last(smh5))} 點＝{_sg(z)} 個標準差；" + "、".join(("✓" if b else "✗") + a for a, b in checks),
        f"semis {_sg(last(smh5))} pts vs the S&P in 5 days = {_sg(z)} standard deviations; "
        + ", ".join(("yes " if b else "no ") + a for a, b in checks),
        LOOK_7, LOOK_7_EN)

    steps[-1]["nums"] = dict(n=int(n), z=_num(z), smh5=_num(last(smh5)), checks=[[a, bool(b)] for a, b in checks],
                             liq_sessions=sessions(liq["since"]) if liq.get("on") else 0)
    (DATA / "steps.json").write_text(json.dumps(steps, ensure_ascii=False, indent=1) + "\n")
    return steps
