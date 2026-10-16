"""Build the public website version of the dashboard into site/.

Run after build_data.py:  python3 build_site.py
- Copies data/*.json to site/data/, leaving out what should not be republished:
  raw Yahoo price levels (ETF and stock prices, the "last" column of the macro table) and
  ICE BofA credit-spread levels (replaced by changes in bp and a 3-year percentile).
- Wraps index.html (written for the Claude Dashboard artifact) in a standalone page with
  a small stand-in for the artifact's `dash` API, d3 from cdnjs and the colour tokens.
The full data stays local and in the Claude dashboard.
With --private, the newest ../daily_summaries/<date>.md is added as the "summary" dataset and ../holdings.json (positions
held and the user's picks) as the "holdings" dataset (local board only; neither ever goes into site/ or the dashboard
repo).
With --summary, writes summary.md (committed, so the public site can show it): the newest daily summary without the
private 大環境 line and without the user's held and picked tickers in the 業績 lines. The public build shows summary.md.
A missing data file is left out with a warning; the page then shows 未能載入 for that dataset.
"""
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA, SITE = ROOT / "data", ROOT / "site"
SUMMARIES = ROOT.parent / "daily_summaries"  # the evening facts summaries, <session date>.md; local board only
HOLDINGS = ROOT.parent / "holdings.json"  # {"updated": ..., "held": [tickers], "picks": [tickers]}; local board only
PUBLIC_SUMMARY = ROOT / "summary.md"  # the cleaned public copy; first line "session: <date>", then the summary
DATASETS = ["sectors", "rrg_tails", "ratios", "ratio_summary", "breadth", "breadth_now", "macro", "global",
            "positioning", "positioning_now", "rates", "rates_now", "liquidity", "stocks", "stock_tails",
            "flowmodel", "flowmodel_now", "alerts", "steps", "rrg_daily", "check_prices"]
# summary sections the public copy leaves out (local only, user 2026-10-15): the 總結 overview, the lamps, the AI layers,
# the weekly rotation and leader lists (trading lines are private, like the board's 輪動操作)
LOCAL_SECTIONS = ("總結", "警報燈：", "五步讀盤：", "AI 周期分層（價格）：", "板塊輪動（", "股票（龍頭名單")
LOCAL_ONLY = {"steps", "ai_layers", "check_prices", "borrower_credit"}
BORROWER_CREDIT = ROOT.parent / "board_evidence" / "borrower_credit.json"
AI_LAYERS = ROOT.parent / "ai_cycle_watch" / "data"  # track.py's latest.json and history.json (local board only)
CREDIT = ["hy_oas", "ig_oas", "ig_yield", "ccc_oas", "bb_oas"]  # ICE BofA levels: percentiles and changes only on the public site


def load(name):
    """A data file's rows, or None when the file is missing (it is then left out, not fatal)."""
    path = DATA / f"{name}.json"
    if not path.exists():
        print(f"  WARNING {path.name} missing; left out")
        return None
    return json.loads(path.read_text())


def load_all():
    return {name: rows for name in DATASETS if (rows := load(name)) is not None}


def latest_summary():
    """The newest daily summary as one row, or None when there is none (the board then keeps its own reading)."""
    files = sorted(SUMMARIES.glob("20??-??-??.md")) if SUMMARIES.is_dir() else []
    return [{"session": files[-1].stem, "text": files[-1].read_text()}] if files else None


def write_public_summary():
    """The newest daily summary, cleaned for the public site, into summary.md. Drops the 大環境 bullet (the board's
    backdrop call is private) and the user's own tickers from the 業績 lines (they would give the holdings away)."""
    rows = latest_summary()
    if not rows:
        print("no daily summary to publish")
        return
    h = json.loads(HOLDINGS.read_text()) if HOLDINGS.is_file() else {}
    own = {t.upper() for t in h.get("held", []) + h.get("picks", [])}
    text = rows[0]["text"]
    # the 總結 has its own sub-headings (取態：, 推手：, ...), so drop everything above its 「---」 divider in one go
    if text.lstrip().startswith("總結") and "\n---\n" in text:
        text = text.split("\n---\n", 1)[1].lstrip("\n")
    out, skip = [], False
    for line in text.split("\n"):
        body = line.lstrip(" *•-")
        # a heading or one-line label (「警報燈：」, 「假期：…」) opens a section; the local ones are left out, and so is
        # the divider under the 總結
        if line and not line.startswith((" ", "*", "•", "-")):
            skip = line.startswith(LOCAL_SECTIONS)
        if skip or line.strip() == "---" or body.startswith(("大環境：", "警報燈轉變：")):
            continue
        if body.startswith("業績："):
            # A ticker followed by a date/comment is still private, not just an exact bare-token match.
            names = [n for n in body[len("業績："):].rstrip("。").split("、")
                     if not any(re.search(r"(?<![A-Z0-9])" + re.escape(t) + r"(?![A-Z0-9])", n.upper()) for t in own)]
            if not names:
                continue
            line = line[: len(line) - len(body)] + "業績：" + "、".join(names) + ("。" if body.endswith("。") else "")
        # The daily detail now includes raw CTA trigger prices and SKEW. The public copy retains
        # changes/ratios, consistent with public_data(), never the raw index levels.
        if body.startswith("CTA 今日"):
            line = line.split("；一個月趨勢轉負參考", 1)[0].rstrip("。") + "。"
        if body.startswith("VIX／VIX3M"):
            line = re.sub(r"、SKEW [^（\n]+", "", line)
        if line.strip() or (out and out[-1].strip()):  # one blank line where a dropped section was
            out.append(line)
    PUBLIC_SUMMARY.write_text(f"session: {rows[0]['session']}\n" + "\n".join(out))
    print(f"summary.md written for {rows[0]['session']}")


def public_summary():
    """summary.md as the "summary" dataset for the public site, or None."""
    if not PUBLIC_SUMMARY.is_file():
        return None
    first, _, text = PUBLIC_SUMMARY.read_text().partition("\n")
    return [{"session": first.removeprefix("session: ").strip(), "text": text}]


def holdings():
    """Positions held and the user's watch list, as rows [{"ticker", "kind": "held" | "pick"}], or None when there is no
    holdings file (local board only; the public page and the repo carry neither)."""
    if not HOLDINGS.is_file():
        return None
    h = json.loads(HOLDINGS.read_text())
    # long-term core = a name in the long-term tracker (company_tracking/active_registry.json): the AI stage tab shows
    # it a price-risk note and leaves exits to the company card instead of weekly trading labels (user, 2026-10-15)
    reg = ROOT.parent / "company_tracking" / "active_registry.json"
    core = set()
    if reg.is_file():
        for c in json.loads(reg.read_text()).get("companies", []):
            core |= {str(a).upper() for a in [c.get("id", "")] + c.get("aliases", [])}
    return ([{"ticker": t, "kind": "held", "core": t.upper() in core} for t in h.get("held", [])]
            + [{"ticker": t, "kind": "pick"} for t in h.get("picks", [])])


def public_data():
    out = load_all()
    for row in out.get("sectors", []) + out.get("stocks", []):
        row.pop("price", None)
    for row in out.get("macro", []):
        row.pop("last", None)
    # flow models: keep the model readings and distances, drop the Yahoo index and VIX levels behind them
    for row in out.get("flowmodel", []):
        for k in ("spx", "vix", "vix3m", "skew"):
            row.pop(k, None)
    for f in out.get("flowmodel_now", []):
        for k in ("spx", "vix", "vix9d", "vix3m", "skew"):
            f.pop(k, None)
        for x in f.get("cta_flips", []) + [f.get("cta_flat") or {}]:
            x.pop("level", None)
    for k in LOCAL_ONLY:
        out.pop(k, None)
    # alert lamps: the public site shows the lamps only (no readings, no ICE BofA levels)
    for row in out.get("alerts", []):
        for k in list(row):
            if k not in ("id", "name", "name_en", "kind", "on", "change", "since", "asof"):
                row.pop(k)
    rates = out.get("rates", [])
    now = (out.get("rates_now") or [{}])[0]
    for k in CREDIT:
        series = [r[k] for r in rates if r.get(k) is not None]
        latest = now.get(k)
        now[k + "_pctile"] = round(sum(v < latest for v in series) / len(series) * 100) if series and latest is not None else None
        base = series[0] if series else None
        for r in rates:  # change in bp since the first row; the page rebases it to the chosen range
            v = r.pop(k, None)
            r[k + "_cum"] = None if v is None or base is None else round((v - base) * 100, 1)
        now.pop(k, None)
    return out


SHIM = """
(() => {
  const DATASETS = __DATASETS__;
  window.RB_PUBLIC = __PUBLIC__;
  const store = {}, calcs = [], listeners = [];
  const params = { lang: 'zh' };
  try { const saved = localStorage.getItem('rb-lang'); if (saved) params.lang = saved; } catch (e) {}
  const fire = () => {
    for (const c of calcs) {
      const ins = c.inputs.map((id) => store[id]);
      if (ins.every((x) => x && x.status === 'ok')) {
        try { store[c.id] = { status: 'ok', data: c.fn(...ins.map((x) => x.data)) }; }
        catch (e) { store[c.id] = { status: 'error', data: [] }; console.error(c.id, e); }
      } else if (ins.some((x) => x && x.status === 'error')) store[c.id] = { status: 'error', data: [] };
    }
    for (const f of listeners) { try { f(); } catch (e) { console.error(e); } }
  };
  window.dash = {
    data: (id) => store[id] || { status: 'loading', data: [] },
    calc: (id, spec) => { calcs.push({ id, inputs: spec.inputs, fn: spec.fn }); },
    onData: (fn) => { listeners.push(fn); fn(); },
    params: () => ({ ...params }),
    setParams: (p) => { Object.assign(params, p); try { localStorage.setItem('rb-lang', params.lang); } catch (e) {} fire(); },
    resetParams: () => { params.lang = 'zh'; fire(); },
    setLink: (id) => { try { history.replaceState(null, '', '#' + id); } catch (e) {} },
    colors: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => `var(--series-${i})`),
  };
  Promise.all(DATASETS.map((id) => fetch(`data/${id}.json`, { cache: 'no-cache' })
    .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then((d) => { store[id] = { status: 'ok', data: d }; })
    .catch(() => { store[id] = { status: 'error', data: [] }; }))).then(fire);
  let t;
  addEventListener('resize', () => { clearTimeout(t); t = setTimeout(fire, 150); });
})();
"""

TOKENS = """
:root {
  color-scheme: light;
  --font-anthropic-sans: "IBM Plex Sans", "Noto Sans TC", system-ui, -apple-system, "PingFang TC", sans-serif;
  --font-anthropic-serif: "Source Serif 4", "Noto Serif TC", Georgia, "Songti TC", serif;
  --cds-font-size-caption: 0.8125rem; --cds-font-size-body: 0.9375rem; --cds-font-size-heading: 1.0625rem; --cds-font-size-title: 1.75rem;
  --cds-font-weight-medium: 600;
  --cds-gap-xs: 4px; --cds-gap-sm: 8px; --cds-gap-md: 14px; --cds-gap-lg: 22px;
  --cds-pad-xs: 4px; --cds-pad-sm: 8px; --cds-pad-md: 12px; --cds-pad-lg: 18px;
  --cds-radius: 8px; --cds-dur-slow: 320ms; --cds-ease-out: cubic-bezier(.2, .7, .2, 1);
  --color-bg: #f4f6f8; --color-panel: #ffffff; --color-fg: #1a1f26; --color-fg-muted: #5d6874; --color-border-line: #dfe4ea;
  --color-ok: #17804c; --color-warn: #b0600c; --color-bad: #c0352b;
  --cds-text-secondary: #4d6a8a; --cds-border: #d3dae2; --cds-surface-popover: #ffffff; --cds-shadow-popover: 0 6px 20px rgba(20, 30, 45, .14);
  --cds-chart-grid: #e9edf1; --cds-chart-axis: #b9c2cc; --cds-chart-reference: #8e99a5; --cds-chart-muted: #a3adb8;
  --series-1: #2c6bd6; --series-2: #d9822b; --series-3: #7b5cd6; --series-4: #179c8c; --series-5: #c43f7b; --series-6: #6c8f1f; --series-7: #8d6b1c; --series-8: #4b6fa6;
}
@media (prefers-color-scheme: dark) { :root {
  color-scheme: dark;
  --color-bg: #11151a; --color-panel: #181d24; --color-fg: #e6eaef; --color-fg-muted: #9aa5b1; --color-border-line: #2a313a;
  --color-ok: #4cc28a; --color-warn: #e39a4a; --color-bad: #f0796c;
  --cds-text-secondary: #9db7d4; --cds-border: #333b45; --cds-surface-popover: #1f252d; --cds-shadow-popover: 0 6px 20px rgba(0, 0, 0, .5);
  --cds-chart-grid: #232a32; --cds-chart-axis: #46505b; --cds-chart-reference: #6f7a86; --cds-chart-muted: #5f6a76;
  --series-1: #6b9dff; --series-2: #f2a65a; --series-3: #a58cf0; --series-4: #3cc2b0; --series-5: #ea6fa5; --series-6: #9bc24a; --series-7: #c9a24a; --series-8: #86a5d6;
} }
html, body { margin: 0; background: var(--color-bg); color: var(--color-fg); font-family: var(--font-anthropic-sans); font-size: 15px; }
[hidden] { display: none !important; }
#dash-root { padding: 28px clamp(16px, 4vw, 40px) 12px; }
.dash-skeleton { position: relative; min-height: 1em; border-radius: var(--cds-radius); background: linear-gradient(90deg, var(--cds-chart-grid), var(--color-panel), var(--cds-chart-grid)); background-size: 200% 100%; animation: skel 1.4s linear infinite; color: transparent !important; }
@keyframes skel { to { background-position: -200% 0; } }
@media (prefers-reduced-motion: reduce) { .dash-skeleton { animation: none; } }
.site-foot { max-width: 80rem; margin: 0 auto; padding: 8px clamp(16px, 4vw, 40px) 32px; color: var(--color-fg-muted); font-size: 0.8125rem; line-height: 1.6; }
"""

PAGE = """<!doctype html>
<html lang="zh-Hant-HK">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>板塊輪動與市寬</title>
<meta name="description" content="US sector rotation, market breadth, futures positioning, rates and credit. 美股板塊輪動、市寬、期貨持倉、利率與信貸。">
<meta property="og:title" content="板塊輪動與市寬 · Sector rotation &amp; breadth">
<meta property="og:description" content="美股板塊與AI產業鏈輪動、市寬、期貨持倉、利率與信貸，逢交易日更新。US sector and AI-chain rotation, breadth, positioning, rates and credit, updated each trading day.">
<meta property="og:type" content="website">
<meta name="theme-color" content="#f4f6f8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&family=Source+Serif+4:wght@400&family=Noto+Sans+TC:wght@400;600&family=Noto+Serif+TC:wght@400&display=swap">
<style>__TOKENS__</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<script>__SHIM__</script>
</head>
<body>
<main id="dash-root">
__BODY__
</main>
<footer class="site-foot">
  僅供參考，不構成投資建議。數據來自 Yahoo Finance、CFTC、FRED、美國財政部、Cboe、歐洲央行、英倫銀行及日本財務省，逢星期二至星期六更新；此公開版本不顯示原始價格及指數水平，亦不顯示 ICE BofA 信貸息差數值。<br>
  For information only, not investment advice. Data from Yahoo Finance, the CFTC, FRED, the US Treasury, Cboe, the ECB, the Bank of England and Japan's MoF, refreshed Tuesday to Saturday; this public version omits raw price and index levels and ICE BofA spread levels.
</footer>
</body>
</html>
"""


def main(private=False):
    """Public site into site/; with --private, a full-data local copy into site_private/ (never published)."""
    global SITE
    if private:
        SITE = ROOT / "site_private"
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "data").mkdir(parents=True)
    data = load_all() if private else public_data()
    if private:
        data["borrower_credit"] = [json.loads(BORROWER_CREDIT.read_text())] if BORROWER_CREDIT.is_file() else [{"issuers": []}]
        from daily_summary import reference_reading
        asof = (data.get("breadth") or [{}])[-1].get("date", "")
        for issuer in data["borrower_credit"][0].get("issuers", []):
            issuer["reference_display"] = reference_reading(issuer, asof)
    if private and (AI_LAYERS / "latest.json").is_file():
        lt = json.loads((AI_LAYERS / "latest.json").read_text())
        hist = json.loads((AI_LAYERS / "history.json").read_text()) if (AI_LAYERS / "history.json").is_file() else []
        lt["history_start"] = hist[0]["asof"] if hist else None
        data["ai_layers"] = [lt]
    summary = latest_summary() if private else public_summary()
    held = holdings() if private else None
    if summary:
        data["summary"] = summary
    if held is not None:
        data["holdings"] = held
    for name, rows in data.items():
        (SITE / "data" / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
    # the page asks for every dataset, so a missing file shows as 未能載入 rather than loading forever
    ids = [i for i in dict.fromkeys(DATASETS + list(data)) if private or i not in LOCAL_ONLY]
    body = (ROOT / "index.html").read_text()
    page = (PAGE.replace("__TOKENS__", TOKENS)
                .replace("__SHIM__", SHIM.replace("__DATASETS__", json.dumps(ids)).replace("__PUBLIC__", "false" if private else "true"))
                .replace("__BODY__", body))
    if private:
        page = page.replace("逢星期二至星期六更新；此公開版本不顯示原始價格及指數水平，亦不顯示 ICE BofA 信貸息差數值。",
                            "按已完成交易日更新；此為本地完整資料版本，各來源實際觀察日期見摘要及圖表。")
        page = page.replace("refreshed Tuesday to Saturday; this public version omits raw price and index levels and ICE BofA spread levels.",
                            "updated after completed sessions; this is the full local version. Source observation dates are shown in the summary and charts.")
    (SITE / "index.html").write_text(page)
    (SITE / ".nojekyll").write_text("")
    print(f"{SITE.name}/ built:", len(data), "data files" + (f", summary {summary[0]['session']}" if summary else "")
          + (f", holdings {len(held)}" if held is not None else ""))


if __name__ == "__main__":
    import sys
    if "--summary" in sys.argv:
        write_public_summary()
    else:
        main(private="--private" in sys.argv)
