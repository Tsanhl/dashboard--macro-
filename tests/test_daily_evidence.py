"""Protect dated comparisons and the local/public boundary; no network required."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_site
from daily_summary import credit_change, reference_reading, borrower_reading


def quote(day, price, yield_pct, **extra):
    return dict(price_date=day, clean_price_per_100=price, yield_pct=yield_pct,
                isin='TEST123', currency='USD', yield_type='YTW semiannual',
                provider='Example', price_basis='vendor_close_per_100',
                source='https://example.com/quote', **extra)


class EvidenceTests(unittest.TestCase):
    def read(self, rows, asof='2026-10-15'):
        return reference_reading({'reference_quotes': rows}, asof)

    def test_price_percent_and_yield_basis_points_are_distinct(self):
        r = self.read([quote('2026-10-14', 80, 15), quote('2026-10-15', 84, 14.5)])
        self.assertEqual((r['price_change'], r['price_return_pct'], r['yield_change_bp']), (4, 5, -50))
        self.assertIn('未確認', r['status'])

    def test_revision_is_not_another_day(self):
        r = self.read([quote('2026-10-14',80,15), quote('2026-10-15',83,14.6,superseded=True), quote('2026-10-15',84,14.5)])
        self.assertEqual(r['price_change'],4)
        self.assertEqual(r['prior']['price_date'],'2026-10-14')

    def test_unresolved_duplicate_is_not_cherry_picked(self):
        r = self.read([quote('2026-10-14',80,15),quote('2026-10-15',83,14.6),quote('2026-10-15',84,14.5)])
        self.assertIsNone(r['latest'])
        self.assertIsNone(r['price_change'])

    def test_future_quote_and_invalid_values_are_excluded(self):
        r = self.read([quote('2026-10-14',80,15),quote('2026-10-16',84,14.5),quote('2026-10-15',float('nan'),14)])
        self.assertEqual(r['latest']['price_date'],'2026-10-14')
        self.assertIsNone(r['price_change'])

    def test_different_bond_or_basis_is_not_compared(self):
        for key,value in [('isin','OTHER123'),('yield_type','YTM'),('price_basis','dirty')]:
            last=quote('2026-10-15',84,14.5);last[key]=value
            self.assertIsNone(self.read([quote('2026-10-14',80,15),last])['price_change'])

    def test_missing_prior_is_not_zero(self):
        self.assertIsNone(self.read([quote('2026-10-15',84,14.5)])['price_change'])

    def test_credit_needs_two_shared_real_observation_dates(self):
        rt={'ccc_date':'2026-10-14','bb_date':'2026-10-14','ccc_prev_date':'2026-10-13','bb_prev_date':'2026-10-13','ccc_1d':15,'bb_1d':8}
        self.assertEqual(credit_change(rt,['ccc','bb']),(7,'2026-10-14','2026-10-13'))
        for key,value in [('bb_date','2026-10-13'),('ccc_1d',float('nan')),('ccc_prev_date','2026-10-14')]:
            other=rt|{key:value};self.assertIsNone(credit_change(other,['ccc','bb'])[0])

    def test_bond_reference_does_not_confirm_borrower_spread(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'dashboard/data').mkdir(parents=True);(root/'board_evidence').mkdir()
            (root/'board_evidence/borrower_credit.json').write_text(json.dumps({'issuers':[{'id':i,'name':i,'reference_quotes':[quote('2026-10-14',80,15),quote('2026-10-15',84,14.5)]} for i in ['CRWV','ORCL']]}))
            with patch('daily_summary.DATA',root/'dashboard/data'):
                self.assertEqual(borrower_reading('2026-10-15')[0],'信用改善未確認')
                self.assertEqual(borrower_reading('2026-10-19')[0],'信用改善未確認')

    def test_public_summary_omits_private_overview_and_raw_trigger_levels(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);hold=root/'holdings.json';hold.write_text(json.dumps({'held':['XYZ'],'picks':[]}))
            text='總結（10月15日收市）：\n私人取態\n\n---\n\n每日模型詳情：\n\n* CTA 今日 −3.6、近5日 −5.2；一個月趨勢轉負參考 7650.0，整體轉平 7341.0。\n* VIX／VIX3M 0.89、SKEW 160.4（10月15日）。\n* 業績：XYZ（收市後）、ABC。\n'
            with patch.object(build_site,'latest_summary',return_value=[{'session':'2026-10-15','text':text}]),patch.object(build_site,'HOLDINGS',hold),patch.object(build_site,'PUBLIC_SUMMARY',root/'summary.md'):
                build_site.write_public_summary();out=(root/'summary.md').read_text()
            for private in ['私人取態','7650','7341','160.4','XYZ']:
                self.assertNotIn(private,out)
            self.assertIn('0.89',out);self.assertIn('ABC',out)

    def test_public_datasets_exclude_private_evidence_and_raw_levels(self):
        data={'check_prices':[{'spy_1d':1}],'steps':[{}],'ai_layers':[{}],
              'holdings':[{'ticker':'PRIVATE_SENTINEL','kind':'held'}],
              'borrower_credit':[{'private_note':'PRIVATE_SENTINEL'}],
              'rates':[{'date':'2026-10-14','ccc_oas':12.82,'bb_oas':1.99}],
              'rates_now':[{'ccc_oas':12.82,'bb_oas':1.99}],
              'sectors':[{'price':123,'r1d':1}], 'flowmodel_now':[{'spx':7650,'cta_flips':[{'level':7300,'dist':-4}]}]}
        with patch.object(build_site,'load_all',return_value=copy.deepcopy(data)):
            public=build_site.public_data()
        for key in build_site.LOCAL_ONLY:self.assertNotIn(key,public)
        self.assertNotIn('price',public['sectors'][0]);self.assertNotIn('ccc_oas',public['rates'][0])
        self.assertNotIn('level',public['flowmodel_now'][0]['cta_flips'][0])

    def test_public_summary_omits_updated_model_reference_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            text=('總結（10月16日收市）：\n私人取態\n\n---\n\n每日模型詳情：\n\n'
                  '* CTA 今日 −11.0、近5日 −19.9；下一交易日一個月趨勢判定參考 7765.0，整體轉平 7361.0。\n'
                  '* VIX／VIX3M 0.975（10月16日）；SKEW 160.4（10月15日，16日尚未提供）。\n')
            with patch.object(build_site,'latest_summary',return_value=[{'session':'2026-10-16','text':text}]),patch.object(build_site,'HOLDINGS',root/'missing.json'),patch.object(build_site,'PUBLIC_SUMMARY',root/'summary.md'):
                build_site.write_public_summary();out=(root/'summary.md').read_text()
            for private in ['私人取態','7765','7361','趨勢判定參考','160.4','SKEW']:
                self.assertNotIn(private,out)
            self.assertIn('CTA 今日 −11.0、近5日 −19.9。',out)
            self.assertIn('VIX／VIX3M 0.975（10月16日）。',out)

if __name__=='__main__':unittest.main()
