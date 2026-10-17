"""Completed NYSE sessions, including holidays and early closes."""
from functools import lru_cache
import pandas as pd
import exchange_calendars as xc


@lru_cache(maxsize=8)
def calendar(year):
    return xc.get_calendar('XNYS', start=f'{year-4}-01-01', end=f'{year+1}-12-31')


def completed_session(now=None):
    now = pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        raise ValueError('A timezone-aware clock is required')
    cal = calendar(now.year)
    days = cal.sessions_in_range((now-pd.Timedelta(days=15)).date(), now.date())
    # Allow the closing bar to settle; never use an unfinished session.
    return next(d.strftime('%Y-%m-%d') for d in reversed(days)
                if cal.session_close(d) + pd.Timedelta(minutes=15) <= now)


def week_complete(asof):
    day = pd.Timestamp(asof)
    monday = day - pd.Timedelta(days=day.weekday())
    days = calendar(day.year).sessions_in_range(monday, monday + pd.Timedelta(days=4))
    return bool(len(days) and days[-1].date() == day.date())
