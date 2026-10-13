"""Flow models for the 資金流向 tab: SPX dealer gamma, CTA trend models (S&P 500 and four 10-year government bonds),
vol-control exposure and the VIX term structure.

Called from build_data.py (build_flows). Writes flowmodel.json (daily history), flowmodel_now.json (one row: latest readings,
scenarios and levels) and gamma.json (dealer gamma by strike). Everything here is a model estimate (rulebook §7): it
assumes how these funds and dealers trade; it is not their orders. Levels are ours, not a vendor's: compare like with
like and read direction, turns and trigger levels rather than the dollar amounts.

Sources: SPX option chain from Cboe's delayed quotes (open interest is the previous session's); S&P 500, VIX family
and SKEW from Yahoo; 10-year yields from FRED and the US Treasury, the ECB (euro-area AAA curve, mostly Germany, as
the Bund stand-in), the Bank of England (IUDMNPY) and Japan's Ministry of Finance.
First set 2026-10-13, trial (rulebook §13).
"""
import io
import json
import re
import urllib.request

import numpy as np
import pandas as pd
import yfinance as yf

from build_data import OUT, fetch_json, fred, r, treasury_latest, write

LOOKBACKS = (20, 60, 125, 250)  # trend windows, about 1, 3, 6 and 12 months of sessions
CTA_VOL_CAP = 0.15  # trend funds size down when 60-day realised vol is above 15% a year
VC_TARGET, VC_MAX = 0.10, 1.5  # vol control: 10% target vol, leverage capped at 1.5x
GEX_DAYS, GEX_BAND, GEX_BUCKET = 30, 0.05, 25  # expiries within 30 days, strikes within 5% of spot, 25-point buckets
CBOE = "https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json"
ECB = ("https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y"
       "?startPeriod=2021-01-01&format=csvdata")
BOE = ("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom=01/Jan/2021"
       "&Dateto=now&SeriesCodes=IUDMNPY&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N")
MOF = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/{}"
HIST = OUT / "_gamma_hist.json"  # one row per session, kept across builds (open interest can't be rebuilt later); not a dataset


def get_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")


# ---- trend models ----
def trend_z(level):
    """Trend strength per lookback in standard deviations: the move over k sessions divided by 60-day daily vol
    times sqrt(k). `level` is log price, or minus the yield for bonds (a falling yield is a rising bond)."""
    sd = level.diff().rolling(60).std()
    return pd.concat({k: (level - level.shift(k)) / (sd * np.sqrt(k)) for k in LOOKBACKS}, axis=1)


def cta_position(px):
    """Equity CTA model position, −100 (full short) to +100 (full long): the average of tanh(z) over the lookbacks,
    scaled down when realised vol is above the cap."""
    lr = np.log(px)
    rv = lr.diff().rolling(60).std() * np.sqrt(252)
    return np.tanh(trend_z(lr)).mean(axis=1) * np.minimum(1, CTA_VOL_CAP / rv) * 100


def vc_exposure(px):
    """Vol-control exposure as % of its 1.5x cap: target vol over the higher of 1- and 3-month realised vol."""
    ret = np.log(px).diff()
    rv = np.maximum(ret.rolling(21).std(), ret.rolling(63).std()) * np.sqrt(252)
    return np.minimum(VC_TARGET / rv, VC_MAX) / VC_MAX * 100


def tomorrow(px, move):
    """The close series with one more session at today's close × (1 + move)."""
    nxt = px.index[-1] + pd.offsets.BDay(1)
    return pd.concat([px, pd.Series([px.iloc[-1] * (1 + move)], index=[nxt])])


def cta_levels(px):
    """Where the equity model turns: each lookback's trend flips sign if tomorrow closes below the close k−1 sessions
    before today (the base tomorrow's window starts from); and the close at which the whole model is flat."""
    flips = [{"lookback": k, "level": r(px.iloc[-k], 0), "dist": r((px.iloc[-k] / px.iloc[-1] - 1) * 100, 1)}
             for k in LOOKBACKS]
    flat = None
    for m in np.arange(0, -0.20, -0.001):
        if cta_position(tomorrow(px, m)).iloc[-1] <= 0:
            flat = {"level": r(px.iloc[-1] * (1 + m), 0), "dist": r(m * 100, 1)}
            break
    return flips, flat


def scenarios(px):
    """Tomorrow's model readings for a range of S&P moves."""
    out = []
    for m in (-0.05, -0.03, -0.02, -0.01, 0, 0.01, 0.02):
        s = tomorrow(px, m)
        out.append({"move": m * 100, "level": r(s.iloc[-1], 0), "cta": r(cta_position(s).iloc[-1], 0),
                    "vc": r(vc_exposure(s).iloc[-1], 0)})
    return out


# ---- 10-year yields ----
def us10():
    s = fred("DGS10")
    try:
        t = treasury_latest()["nominal10"].dropna()  # same-day Treasury yields; FRED copies them a day or more later
        s = pd.concat([s, t[t.index > s.index[-1]]])
    except Exception as e:
        print(f"  WARNING Treasury 10y failed, FRED only: {e}")
    return s


def de10():
    df = pd.read_csv(io.StringIO(get_text(ECB)))
    return pd.Series(df["OBS_VALUE"].values, index=pd.to_datetime(df["TIME_PERIOD"])).astype(float)


def uk10():
    df = pd.read_csv(io.StringIO(get_text(BOE)), header=None, names=["date", "v"])
    df = df[pd.to_numeric(df["v"], errors="coerce").notna()]
    return pd.Series(df["v"].astype(float).values, index=pd.to_datetime(df["date"], format="%d %b %Y"))


def jp10():
    frames = []
    for f in ("historical/jgbcme_all.csv", "jgbcme.csv"):  # the history file stops at last month's end
        df = pd.read_csv(io.StringIO(get_text(MOF.format(f))), skiprows=1)
        df = df[df["Date"].astype(str).str.match(r"^\d{4}/\d{1,2}/\d{1,2}$")]
        frames.append(pd.Series(pd.to_numeric(df["10Y"], errors="coerce").values, index=pd.to_datetime(df["Date"])))
    s = pd.concat(frames).dropna()
    return s[~s.index.duplicated(keep="last")].sort_index()


BONDS = {"us": ("美國", "US", us10), "de": ("德國（歐元區AAA）", "Germany (euro AAA)", de10), "uk": ("英國", "UK", uk10),
         "jp": ("日本", "Japan", jp10)}


def bond_signals():
    """Trend signal per market in standard deviations (average z over the lookbacks, each capped at ±3; negative =
    trend funds short bonds, betting on higher yields) plus the latest yield and its date."""
    out = {}
    for key, (_, _, fn) in BONDS.items():
        try:
            y = fn().sort_index()
            y = y[y.index >= "2021-01-01"]
            sig = trend_z(-y).clip(-3, 3).mean(axis=1)
            out[key] = (sig, y)
        except Exception as e:
            print(f"  WARNING {key} 10y yield failed: {e}")
    return out


# ---- dealer gamma ----
def bs_gamma(spot, k, t, iv):
    d1 = (np.log(spot / k) + 0.5 * iv * iv * t) / (iv * np.sqrt(t))
    return np.exp(-d1 * d1 / 2) / np.sqrt(2 * np.pi) / (spot * iv * np.sqrt(t))


def gex(spot, ch):
    """Dealer gamma in $bn per 1% move, assuming dealers are long the calls and short the puts customers trade."""
    sign = np.where(ch["cp"] == "C", 1, -1)
    return float((sign * bs_gamma(spot, ch["k"], ch["t"], ch["iv"]) * ch["oi"] * 100 * spot * spot * 0.01).sum() / 1e9)


def next_opex(ts):
    """Third Friday of this month, or of next month once it has passed."""
    for add in (0, 1):
        m = (ts.normalize().replace(day=1) + pd.DateOffset(months=add))
        fri = m + pd.offsets.Week(weekday=4) if m.weekday() != 4 else m
        third = fri + pd.Timedelta(days=14)
        if third.date() >= ts.date():
            return third
    return None


def build_gamma(d=None):
    d = d or fetch_json(CBOE)["data"]
    spot = float(d["current_price"])
    snap = pd.Timestamp(d.get("last_trade_time") or pd.Timestamp.now(tz="America/New_York").tz_localize(None))
    rows = []
    for o in d["options"]:
        m = re.match(r"SPXW?(\d{6})([CP])(\d{8})$", o["option"])
        if m and o.get("open_interest") and o.get("iv"):
            rows.append((pd.Timestamp("20" + m[1]) + pd.Timedelta(hours=16), m[2], int(m[3]) / 1000,
                         float(o["open_interest"]), float(o["iv"])))
    ch = pd.DataFrame(rows, columns=["exp", "cp", "k", "oi", "iv"])
    ch["exp"] = pd.to_datetime(ch["exp"])
    if len(ch) < 1000:  # Cboe blanks quotes and greeks for a while after the US open
        raise ValueError(f"only {len(ch)} options with open interest and implied vol")
    ch["days"] = (ch["exp"] - snap).dt.total_seconds() / 86400
    ch = ch[(ch["days"] > 0) & (ch["days"] <= GEX_DAYS) & (ch["iv"] > 0)].copy()
    ch["t"] = np.maximum(ch["days"], 0.25) / 365
    opex = next_opex(snap)
    week_end = snap.normalize() + pd.Timedelta(days=(4 - snap.weekday()) % 7, hours=16)  # this Friday's close
    front = ch[ch["exp"] <= week_end]
    by_opex = ch[ch["exp"] <= opex + pd.Timedelta(hours=16)] if opex is not None else ch.iloc[0:0]
    net = gex(spot, ch)
    # the flip: where net gamma changes sign, searched within ±5% and taken nearest to spot
    grid = spot * (1 + np.arange(-GEX_BAND, GEX_BAND + 1e-9, 0.001))
    vals = np.array([gex(x, ch) for x in grid])
    cross = [grid[i - 1] + (grid[i] - grid[i - 1]) * (-vals[i - 1]) / (vals[i] - vals[i - 1])
             for i in range(1, len(grid)) if np.sign(vals[i]) != np.sign(vals[i - 1])]
    flip = min(cross, key=lambda x: abs(x - spot)) if cross else None
    # gamma by strike at spot, in buckets
    near = ch[(ch["k"] >= spot * (1 - GEX_BAND)) & (ch["k"] <= spot * (1 + GEX_BAND))].copy()
    sign = np.where(near["cp"] == "C", 1, -1)
    near["g"] = sign * bs_gamma(spot, near["k"], near["t"], near["iv"]) * near["oi"] * 100 * spot * spot * 0.01 / 1e9
    near["front"] = np.where(near["exp"] <= week_end, near["g"], 0.0)
    near["bucket"] = (near["k"] // GEX_BUCKET * GEX_BUCKET).astype(int)
    b = near.groupby("bucket")[["g", "front"]].sum()
    strikes = near.groupby("k")["g"].sum()
    top = lambda s: [int(k) for k in s.abs().sort_values(ascending=False).index[:3]]
    now = {"gex_date": snap.strftime("%Y-%m-%d"), "gex_time": snap.strftime("%H:%M"), "spot": r(spot, 0),
           "gex": r(net, 1), "gex_front": r(gex(spot, front), 1), "gex_opex": r(gex(spot, by_opex), 1),
           "opex": opex.strftime("%Y-%m-%d") if opex is not None else None,
           "flip": r(flip, 0), "flip_dist": r((flip / spot - 1) * 100, 2) if flip else None,
           "strikes_above": top(strikes[strikes.index > spot]), "strikes_below": top(strikes[strikes.index < spot])}
    rows = [{"strike": int(k), "gex": r(v.g, 2), "front": r(v.front, 2)} for k, v in b.iterrows()]
    return now, rows


def gamma_history(now):
    try:
        hist = json.loads(HIST.read_text())
    except (OSError, ValueError):
        hist = {}
    hist[now["gex_date"]] = {k: now[k] for k in ("spot", "gex", "gex_front", "flip")}
    HIST.write_text(json.dumps(dict(sorted(hist.items())), separators=(",", ":")))
    return hist


# ---- build ----
def build_flows(asof):
    px = yf.download("^GSPC", start="2015-01-01", auto_adjust=False, progress=False)["Close"].squeeze().dropna()
    px.index = px.index.tz_localize(None)
    px = px.loc[:asof]
    vol = yf.download(["^VIX", "^VIX9D", "^VIX3M", "^SKEW"], start="2022-01-01", auto_adjust=False,
                      progress=False)["Close"]
    vol.index = vol.index.tz_localize(None)
    vol = vol.loc[:asof]
    cta, vc = cta_position(px), vc_exposure(px)
    ret = np.log(px).diff()
    rv1, rv3 = ret.rolling(21).std() * np.sqrt(252) * 100, ret.rolling(63).std() * np.sqrt(252) * 100
    bonds = bond_signals()
    try:
        g_now, g_rows = build_gamma()
        hist = gamma_history(g_now)
    except Exception as e:  # keep the last good reading; its gex_date shows how old it is
        print(f"  WARNING dealer gamma failed, keeping the last reading: {e}")
        try:
            old = json.loads((OUT / "flowmodel_now.json").read_text())[0]
            g_now = {k: v for k, v in old.items() if k.startswith(("gex", "flip", "strikes")) or k in ("opex", "spot")}
            g_rows = json.loads((OUT / "gamma.json").read_text())
            hist = json.loads(HIST.read_text())
        except (OSError, ValueError, IndexError):
            g_now, g_rows, hist = {}, [], {}

    idx = px.index[px.index > px.index[-1] - pd.DateOffset(years=3)]
    v = vol.reindex(idx).ffill(limit=3)
    bsig = {k: s.reindex(idx, method="ffill", tolerance=pd.Timedelta(days=5)) for k, (s, _) in bonds.items()}
    rows = []
    for d in idx:
        ds, gh = d.strftime("%Y-%m-%d"), hist.get(d.strftime("%Y-%m-%d"), {})
        vix, v3 = v.at[d, "^VIX"], v.at[d, "^VIX3M"]
        rows.append({"date": ds, "spx": r(px[d], 0), "cta": r(cta[d], 1), "vc": r(vc[d], 1), "rv1": r(rv1[d], 1),
                     "rv3": r(rv3[d], 1), "vix": r(vix, 2), "vix3m": r(v3, 2), "vix_ratio": r(vix / v3, 3),
                     "skew": r(v.at[d, "^SKEW"], 1), **{f"b_{k}": r(s[d], 2) for k, s in bsig.items()},
                     "gex": gh.get("gex"), "flip": gh.get("flip")})
    write("flowmodel", rows)

    chg = lambda s, n: r(s.iloc[-1] - s.iloc[-1 - n], 1)
    pctile = lambda s: r((s.loc[idx[0]:] < s.iloc[-1]).mean() * 100, 0)
    flips, flat = cta_levels(px)
    last_v = vol.dropna(subset=["^VIX"]).iloc[-1]
    now = {"date": px.index[-1].strftime("%Y-%m-%d"), "spx": r(px.iloc[-1], 0),
           "cta": r(cta.iloc[-1], 0), "cta_1d": chg(cta, 1), "cta_5d": chg(cta, 5), "cta_pctile": pctile(cta),
           "cta_flips": flips, "cta_flat": flat,
           "vc": r(vc.iloc[-1], 0), "vc_1d": chg(vc, 1), "vc_5d": chg(vc, 5), "vc_21d": chg(vc, 21),
           "vc_pctile": pctile(vc), "rv1": r(rv1.iloc[-1], 1), "rv3": r(rv3.iloc[-1], 1),
           "scenarios": scenarios(px),
           "vix": r(last_v["^VIX"], 2), "vix9d": r(last_v["^VIX9D"], 2), "vix3m": r(last_v["^VIX3M"], 2),
           "vix_ratio": r(last_v["^VIX"] / last_v["^VIX3M"], 3), "skew": r(last_v["^SKEW"], 1),
           "bonds": [{"key": k, "name": BONDS[k][0], "name_en": BONDS[k][1], "signal": r(s.iloc[-1], 2), "chg_5d": r(s.iloc[-1] - s.iloc[-6], 2),
                      "yield": r(y.iloc[-1], 3), "date": y.index[-1].strftime("%Y-%m-%d")}
                     for k, (s, y) in bonds.items()],
           **g_now}
    write("flowmodel_now", [now])
    write("gamma", g_rows)
    return now
