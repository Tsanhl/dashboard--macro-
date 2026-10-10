"""Build the public website version of the dashboard into site/.

Run after build_data.py:  python3 build_site.py
- Copies data/*.json to site/data/, leaving out what should not be republished:
  raw Yahoo price levels (ETF prices, the "last" column of the macro table) and
  ICE BofA credit-spread levels (replaced by changes in bp and a 3-year percentile).
- Wraps index.html (written for the Claude Dashboard artifact) in a standalone page with
  a small stand-in for the artifact's `dash` API, d3 from cdnjs and the colour tokens.
The full data stays local and in the Claude dashboard.
"""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA, SITE = ROOT / "data", ROOT / "site"
DATASETS = ["sectors", "rrg_tails", "ratios", "ratio_summary", "breadth", "breadth_now", "macro", "global",
            "positioning", "positioning_now", "rates", "rates_now", "liquidity", "stocks", "stock_tails"]
CREDIT = ["hy_oas", "ig_oas"]


def load(name):
    return json.loads((DATA / f"{name}.json").read_text())


def public_data():
    out = {name: load(name) for name in DATASETS}
    for row in out["sectors"]:
        row.pop("price", None)
    for row in out["macro"]:
        row.pop("last", None)
    rates = out["rates"]
    now = out["rates_now"][0]
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
  僅供參考，不構成投資建議。數據來自 Yahoo Finance、CFTC 及 FRED，逢星期二至星期六更新；此公開版本不顯示原始價格水平及 ICE BofA 信貸息差數值。<br>
  For information only, not investment advice. Data from Yahoo Finance, the CFTC and FRED, refreshed Tuesday to Saturday; this public version omits raw price levels and ICE BofA spread levels.
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
    data = {name: load(name) for name in DATASETS} if private else public_data()
    for name, rows in data.items():
        (SITE / "data" / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
    body = (ROOT / "index.html").read_text()
    page = (PAGE.replace("__TOKENS__", TOKENS)
                .replace("__SHIM__", SHIM.replace("__DATASETS__", json.dumps(DATASETS)).replace("__PUBLIC__", "false" if private else "true"))
                .replace("__BODY__", body))
    (SITE / "index.html").write_text(page)
    (SITE / ".nojekyll").write_text("")
    print(f"{SITE.name}/ built:", len(DATASETS), "data files")


if __name__ == "__main__":
    import sys
    main(private="--private" in sys.argv)
