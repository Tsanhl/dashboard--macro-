import unittest
import numpy as np
import pandas as pd

from market_observations import index_member_drawdowns


class DrawdownTests(unittest.TestCase):
    def test_each_member_uses_own_peak_before_median(self):
        members = pd.DataFrame({'A': [100., 80., 90.], 'B': [40., 100., 50.]})
        benchmark = pd.Series([100., 110., 99.])
        result = index_member_drawdowns(benchmark, members, window=3)
        self.assertAlmostEqual(result.iloc[-1].index_drawdown, -10.)
        self.assertAlmostEqual(result.iloc[-1].member_median_drawdown, -30.)
        self.assertEqual(result.iloc[-1].drawdown_members, 2)
        self.assertTrue(pd.isna(result.iloc[1].member_median_drawdown))

    def test_missing_prices_are_not_carried_and_coverage_changes(self):
        members = pd.DataFrame({'A': [100., 90., 80., 85.], 'B': [100., np.nan, 80., np.nan]})
        result = index_member_drawdowns(pd.Series([100., 100., np.nan, 100.]), members, window=3)
        self.assertEqual(result.iloc[-1].drawdown_members, 1)
        self.assertEqual(result.iloc[-1].drawdown_universe, 2)
        self.assertTrue(pd.isna(result.iloc[-1].index_drawdown))

    def test_expired_peak_can_reduce_drawdown_without_price_increase(self):
        members = pd.DataFrame({'A': [100., 90., 80., 80.]})
        result = index_member_drawdowns(members.A, members, window=3)
        self.assertAlmostEqual(result.iloc[2].member_median_drawdown, -20.)
        self.assertAlmostEqual(result.iloc[3].member_median_drawdown, (80/90-1)*100)
