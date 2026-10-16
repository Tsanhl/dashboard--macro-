"""Daily, price-only evidence for the local summary. Methods: analysis_lens.md §11a."""
import pandas as pd


def check_prices(close, members, asof):
    """Same daily equal-weight basket as the board, limited to a complete contiguous tail.

    Never compound a missing member as zero or bridge a missing basket day. Both
    relative lines start at 100 on the same date; underlying prices stay local.
    """
    cols = ["SPY", "SMH", *members]
    frame = close.reindex(columns=cols).loc[:asof].tail(260)
    returns = frame.pct_change(fill_method=None)
    valid = frame.notna().all(axis=1) & returns.notna().all(axis=1)
    if frame.empty or not valid.iloc[-1]:
        return []
    invalid = [i for i, ok in enumerate(valid) if not ok]
    start = invalid[-1] + 1 if invalid else 0
    frame, returns = frame.iloc[start:], returns.iloc[start:]
    if len(frame) < 2:
        return []
    neo = (1 + returns[members].mean(axis=1)).cumprod()
    rel = pd.DataFrame({"smh_spy": frame.SMH / frame.SPY, "neo_spy": neo / frame.SPY})
    rel = rel / rel.iloc[0] * 100
    return [{"date": str(d.date()), "smh_spy": round(float(rel.loc[d, "smh_spy"]), 6),
             "neo_spy": round(float(rel.loc[d, "neo_spy"]), 6),
             "spy_1d": round(float(returns.loc[d, "SPY"] * 100), 6),
             "neo_members": len(members), "neo_expected": len(members)} for d in frame.index]
