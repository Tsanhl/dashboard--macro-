"""Local daily macro summary, from the board's same completed-session snapshot.

Presentation only. Confirmed weekly models and trading thresholds are unchanged.
The canonical selection/update policy is analysis_lens.md §11a.
"""
import json
import math
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
JOINT = "共同改善：市寬及領跌板塊持續修復，AI借款人的CDS／債券息差亦同步收窄，便重新評估AI的防守取態；缺報價維持未確認。大市取態另看廣泛信用、市寬及利率。"


def load(name):
    return json.loads((DATA / f"{name}.json").read_text())


def sg(x, digits=1):
    if x is None:
        return "未確認"
    return ("+" if x > 0 else "−" if x < 0 else "") + f"{abs(x):.{digits}f}"


def md(x):
    return f"{int(x[5:7])}月{int(x[8:10])}日" if x else "日期未確認"


def reference_reading(issuer, asof):
    """Dated vendor price/YTW comparisons only; never a CDS/OAS confirmation.

    An explicit superseded flag preserves revisions without comparing two versions
    of the same day. Unresolved duplicate dates or differing quote bases fail closed.
    """
    def valid(q):
        try:
            d = q.get("price_date", "")
            return (not q.get("superseded") and date.fromisoformat(d).isoformat() == d and d <= asof
                    and all(q.get(k) for k in ("isin", "currency", "yield_type", "provider", "price_basis"))
                    and q.get("source", "").startswith("https://")
                    and all(isinstance(q.get(k), (int, float)) and not isinstance(q[k], bool)
                            and math.isfinite(q[k]) for k in ("clean_price_per_100", "yield_pct"))
                    and q["clean_price_per_100"] > 0)
        except (ValueError, TypeError):
            return False
    rows = sorted([q for q in issuer.get("reference_quotes", []) if valid(q)], key=lambda q: q["price_date"])
    if not rows:
        return None
    last = rows[-1]
    out = {"latest": last, "prior": None, "price_change": None, "price_return_pct": None,
           "yield_change_bp": None, "status": "延遲第三方參考；信用改善未確認"}
    if sum(q["price_date"] == last["price_date"] for q in rows) != 1:
        return {**out, "latest": None, "status": "同日報價有衝突，暫不比較"}
    earlier = [q for q in rows if q["price_date"] < last["price_date"]]
    if not earlier:
        return out
    prior = earlier[-1]
    keys = ("isin", "currency", "yield_type", "provider", "price_basis")
    if sum(q["price_date"] == prior["price_date"] for q in earlier) != 1 or any(prior[k] != last[k] for k in keys):
        out["status"] = "前次報價口徑不一或有衝突，暫不比較"
        return out
    if prior.get("settlement_basis") != last.get("settlement_basis"):
        out["status"] = "結算假設不一致或前值未記錄，變化未確認"
        return out
    change = last["clean_price_per_100"] - prior["clean_price_per_100"]
    out.update(prior=prior, price_change=round(change, 6),
               price_return_pct=round(change / prior["clean_price_per_100"] * 100, 6),
               yield_change_bp=round((last["yield_pct"] - prior["yield_pct"]) * 100, 6))
    return out


def credit_change(rt, cols):
    dates = [rt.get(c + "_date") for c in cols]
    prev = [rt.get(c + "_prev_date") for c in cols]
    values = [rt.get(c + "_1d") for c in cols]
    if (not all(dates + prev) or len(set(dates)) != 1 or len(set(prev)) != 1
            or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in values)):
        return None, None, None
    try:
        if any(date.fromisoformat(d).isoformat() != d for d in dates + prev) or prev[0] >= dates[0]:
            return None, None, None
    except (ValueError, TypeError):
        return None, None, None
    return values[0] - sum(values[1:]), dates[0], prev[0]


def daily_context(asof, sectors):
    pts = load("check_prices")
    if not pts or pts[-1]["date"] != asof:
        raise ValueError("每日相對走勢資料未與摘要收市日期對齊")
    spy = pts[-1]["spy_1d"]
    return {r["ticker"]: {**r, "rel_day": None if r.get("r1d") is None else r["r1d"] - spy} for r in sectors}


def borrower_reading(asof):
    path = DATA.parent.parent / "board_evidence" / "borrower_credit.json"
    doc = json.loads(path.read_text()) if path.is_file() else {}
    lines, available = [], set()
    for issuer in doc.get("issuers", []):
        for s in issuer.get("series", []):
            if not (s.get("comparable") is True and s.get("metric") in ("cds_bp", "bond_oas_bp")
                    and s.get("quote_type") in ("close_mid", "close_trade")
                    and all(s.get(k) for k in ("instrument", "currency", "series_id"))):
                continue
            def valid_date(value):
                try:
                    return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
                except ValueError:
                    return False
            obs = [o for o in s.get("observations", []) if o.get("verified") is True
                   and isinstance(o.get("value"), (int, float)) and not isinstance(o["value"], bool) and math.isfinite(o["value"])
                   and o.get("source", "").startswith("https://") and o.get("observed_at", "")[:10] == o.get("date")
                   and valid_date(o.get("date")) and o["date"] <= asof]
            counts = {o["date"]: sum(x["date"] == o["date"] for x in obs) for o in obs}
            obs = sorted([o for o in obs if counts[o["date"]] == 1], key=lambda o: o["date"])
            if len(obs) < 2:
                continue
            last, prior = obs[-1], obs[-2]
            change = last["value"] - prior["value"]
            lines.append(f"{issuer['name']} {s['instrument']} {sg(change, 1)} 基點（{md(prior['date'])}至{md(last['date'])}）")
            if last["date"] == asof:
                available.add(issuer["id"])
    if available >= {"CRWV", "ORCL"}:
        return "已有同日可比較報價，持續改善仍待確認", "；".join(lines) + "。要看之後能否持續收窄。"
    evidence = "；".join(lines) + "。" if lines else ""
    has_reference = any(reference_reading(i, asof) for i in doc.get("issuers", []))
    reference = "已有延遲第三方債價／YTW及可比較日期的變化，見下方展開資料；它們不等於已確認信用息差。" if has_reference else ""
    return "信用改善未確認", evidence + reference + "CoreWeave、Oracle尚未同時具備最新可比較收市CDS／指定債券息差。"


def backdrop(sectors, rt):
    # Same thresholds as index.html sizeDownWhy, not a new daily trading model.
    by = {r['ticker']: r for r in sectors}
    mean = lambda ids: sum(by[t]['r1m'] for t in ids) / len(ids)
    reasons = []
    if mean(['XLU','XLP','XLV']) - mean(['XLY','XLI','XLF','XLB']) >= 3:
        reasons.append("防守板塊中期仍較強")
    if (rt.get('nominal2_3m') or 0) >= 50:
        reasons.append("中期利率壓力仍在")
    if (rt.get('hy_oas_1m') or 0) >= 40:
        reasons.append("廣泛信用尚未修復")
    return ("偏防守（" + "、".join(reasons) + "）；風險條件仍未解除") if reasons else "原有減風險條件未觸發；仍看市寬及信用是否持續改善"


def overview(asof, al, stp, lt, br, rt, sectors, pos_date=None):
    # Keep the existing entry point; weekly position date and layer names are not daily-summary inputs.
    if br[-1]["date"] != asof or stp[0]["asof"] != asof:
        raise ValueError("摘要資料不是同一個已完成交易日")
    S = {r["n"]: r["nums"] for r in stp}
    a, c = S[1], S[3]
    sector = daily_context(asof, sectors)
    latest, prior = br[-1], br[-2]
    p50, change = latest["pct50"], latest["pct50"] - prior["pct50"]
    adv, dec = latest["adv"], latest["dec"]
    cb, cd, cp = credit_change(rt, ["ccc_oas", "bb_oas"])
    ig, igd, igp = credit_change(rt, ["ig_oas"])
    rsp, smh, neo = [sector[t] for t in ("RSP", "SMH", "AI_NEO")]
    borrower_state, borrower_text = borrower_reading(asof)
    spy_day = rsp['r1d'] - rsp['rel_day']
    repair = change > 0 and adv > dec and rsp["rel_day"] > 0
    headline = ("市寬回升、等權股跑贏，大市出現修復" if repair else
                "市寬轉弱、下跌股票較多，大市修復受阻" if change < 0 and dec > adv else "市寬與等權表現分歧，大市方向仍待確認")
    semi = "半導體當日仍跑輸" if smh["rel_day"] < 0 else "半導體當日跑贏"
    cred = "低質信用最新變化未確認" if cb is None else "低質信用息差仍擴闊" if cb > 0 else "低質信用息差開始收窄" if cb < 0 else "低質信用息差最新持平"
    assessment = ("大市改善、AI壓力未解" if repair and (smh["rel_day"] < 0 or neo["rel1w"] < 0) else
                  "修復範圍擴大，仍待信用確認" if repair else "風險尚未解除，繼續觀察修復能否延續")
    out = [f"總結（{md(asof)}收市）：", "",
           f"一句摘要：{headline}；{semi}，{cred}（{md(cd)}）。目前是「{assessment}」。", "",
           "取態：", "",
           f"* 大市：{backdrop(sectors, rt)}。",
           f"* AI：{c['ai_stance']}；領跌板塊{c['lead_check']}；借款人：{borrower_state}。", "",
           "今日有甚麼改變：", "",
           f"* 大市：標普 {sg(spy_day)}%、等權 {sg(rsp['r1d'])}%，VIX {a['vix']:.1f}。",
           f"* 參與度：高於50天線由 {prior['pct50']:.1f}% → {p50:.1f}%（{sg(change)} 個百分點）；{adv} 隻升、{dec} 隻跌。"
           + ("更多股票參與反彈。" if repair else "留意改善是否集中在少數股票。"),
           f"* AI反彈是否跟上：新雲當日相對標普 {sg(neo['rel_day'])} 點，但近5日 {sg(neo['rel1w'])} 點；半導體當日 {sg(smh['rel_day'])} 點、近5日 {sg(smh['rel1w'])} 點。"
           + ("新雲的一日反彈仍未扭轉近期弱勢。" if neo['rel_day'] > 0 and neo['rel1w'] < 0 else "看兩者能否持續跑贏。"), ""]
    ydate = rt.get("nominal2_date")
    out += [f"利率與廣泛信用：2年期較上一報價 {sg(rt.get('nominal2_1d'), 0)} 基點（{md(ydate)}）、10年期實質利率 {sg(rt.get('real10_1d'), 0)} 基點（{md(rt.get('real10_date'))}）；"
            f"投資級息差 {sg(ig, 0)} 基點（{md(igp)}至{md(igd)}）。"
            + ("利率當日回落，但不能單憑此確認信用風險解除。" if (rt.get('nominal2_1d') or 0) < 0 else "留意融資壓力是否擴散。"), "",
            "下一次改變取態，看三件事：", "",
            f"1. AI融資壓力：{borrower_state}", "",
            f"CCC−BB 最新 {sg(cb, 0)} 基點（{md(cp)}至{md(cd)}），反映低質信用分化。{borrower_text}股價反彈不能代替信用改善。",
            "[[check:credit]]", "",
            "2. 市寬：" + ("初步修復，未確認轉強" if repair else "改善尚未形成共識"), "",
            "要看升跌線與50天線市寬持續一起改善；55%是現有觀察門檻，單日觸及不等於全面轉強。",
            "[[check:breadth]]", "",
            f"3. 領跌板塊：{c['lead_check']}", "",
            f"新雲{'今日再創相對新低' if c['days_since_low'] == 0 else '已'+str(int(c['days_since_low']))+'個交易日未創相對新低'}；"
            f"半導體{'今日再創相對新低' if c['smh_days_since_low'] == 0 else '已'+str(c['smh_days_since_low'])+'個交易日未創相對新低'}。"
            "暫穩先看5個交易日不創新低，其後看相對表現回升能否維持。",
            "[[check:leaders]]", "",
            JOINT, "",
            "再次惡化：領跌板塊再創相對新低、借款人息差再創高位，提示AI風險加劇；若投資級信用與大市市寬一起轉差，重新評估全市場風險。", "",
            f"資料日期：股價／市寬 {md(asof)}；2年期美債 {md(ydate)}、10年期實質利率 {md(rt.get('real10_date'))}；CCC−BB {md(cd)}、投資級信用 {md(igd)}；借款人報價狀態見第1項。"]
    return out


def rotation_daily(asof, sectors):
    rows = daily_context(asof, sectors)
    groups = [r for r in rows.values() if r["group"] == "sector" and r["rel_day"] is not None]
    ordered = sorted(groups, key=lambda r: r["rel_day"], reverse=True)
    winners = [r for r in ordered if r["rel_day"] > 0][:3]
    losers = [r for r in reversed(ordered) if r["rel_day"] < 0][:3]
    describe = lambda xs: "、".join(f"{r['name']} {sg(r['rel_day'])}" for r in xs) or "沒有"
    out = ["", f"板塊輪動（{md(asof)}當日，相對標普百分點）：", "",
           f"* 跑贏較多：{describe(winners)}。", f"* 跑輸較多：{describe(losers)}。",
           "* 相對強弱表示價格表現，並非已確認資金流入／流出；一日排名不代表趨勢已轉向。"]
    return out


def model_daily(asof, f):
    if f.get("date") != asof:
        return ["", "持倉與流向：模型未有同日數據，暫不判讀。"]
    flip = (f.get("cta_flips") or [{}])[0]
    out = ["", "持倉與流向（每日模型估算）：", "",
           f"* CTA {sg(f['cta'], 0)}，今日 {sg(f.get('cta_1d'))}：模型{'多倉減少' if f.get('cta_1d',0) < 0 else '倉位增加'}，不是實際基金買賣統計。"
           + (f"標普若再跌約 {abs(flip['dist']):.1f}%，一個月趨勢會轉負，須留意進一步機械減倉壓力。" if flip.get('dist') is not None and flip['dist'] < 0 else ""),
           f"* 波幅控制模型倉位為上限的 {f['vc']:.0f}%，今日 {sg(f.get('vc_1d'))} 點；"
           + ("此模型今日沒有同步減倉。" if f.get('vc_1d') is not None and f['vc_1d'] >= 0 else "此模型今日亦減少風險。")]
    return out


def detail_daily(asof, sectors, f):
    rows = daily_context(asof, sectors)
    out = ["", "[[details:每日板塊及模型數據]]", "", f"板塊相對標普（{md(asof)}當日，百分點）：", ""]
    for group, label in [("sector", "標普板塊及貴金屬"), ("theme", "主題"), ("style", "風格"), ("ai", "AI籃子")]:
        xs = sorted([r for r in rows.values() if r["group"] == group and r["rel_day"] is not None], key=lambda r: -r["rel_day"])
        out.append(f"* {label}：" + "、".join(f"{r['name']} {sg(r['rel_day'])}" for r in xs) + "。")
    if f.get("date") == asof:
        flip = (f.get("cta_flips") or [{}])[0]; flat = f.get("cta_flat") or {}
        out += ["", "每日模型詳情：", "",
                f"* CTA 今日 {sg(f.get('cta_1d'))}、近5日 {sg(f.get('cta_5d'))}；一個月趨勢轉負參考 {flip.get('level', '未確認')}，整體轉平 {flat.get('level', '未確認')}。",
                f"* 波幅控制：近5日 {sg(f.get('vc_5d'))}、近21日 {sg(f.get('vc_21d'))} 點；實現波幅1個月 {f['rv1']:.1f}%、3個月 {f['rv3']:.1f}%。",
                f"* VIX／VIX3M {f['vix_ratio']:.2f}、SKEW {f['skew']:.1f}（{md(f.get('vix_date'))}）。"]
        fresh = [b for b in f.get("bonds", []) if b.get("date") == asof]
        if fresh:
            out.append("* 國債CTA模型（同日觀察）：" + "、".join(f"{b['name']} {sg(b['signal'],2)}" for b in fresh) + "。")
    out += ["", "[[/details]]"]
    return out


def build():
    br, rt, sectors, stp, al, f = [load(k) for k in ("breadth", "rates_now", "sectors", "steps", "alerts", "flowmodel_now")]
    asof, rt, f = br[-1]["date"], rt[0], f[0]
    out = overview(asof, al, stp, None, br, rt, sectors)
    out += ["", "---", ""] + rotation_daily(asof, sectors) + model_daily(asof, f) + detail_daily(asof, sectors, f)
    return "\n".join(out) + "\n"
