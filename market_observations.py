"""Descriptive price observations, without trading thresholds or inferred flows."""
import pandas as pd


def index_member_drawdowns(benchmark, members, window=252):
    """Distance from each series' own rolling adjusted-close high.

    Require every observation in the window; never forward-fill missing prices.
    The median is of individual drawdowns, not a drawdown of a median price.
    Membership is the supplied current universe, not historical membership.
    """
    benchmark = benchmark.reindex(members.index).where(lambda s: s > 0)
    members = members.where(members > 0)
    benchmark_dd = (benchmark / benchmark.rolling(window, min_periods=window).max() - 1) * 100
    member_dd = (members / members.rolling(window, min_periods=window).max() - 1) * 100
    return pd.DataFrame({
        'index_drawdown': benchmark_dd,
        'member_median_drawdown': member_dd.median(axis=1),
        'drawdown_members': member_dd.notna().sum(axis=1),
        'drawdown_universe': len(members.columns),
    })
