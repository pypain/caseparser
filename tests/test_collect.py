import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import urllib.error
import app
import collect


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = app.DB
        app.DB = Path(self.tmp.name) / 'collector.sqlite3'
        app.init_db()

    def tearDown(self):
        app.DB = self.old_db
        self.tmp.cleanup()

    def fetch(self, name):
        return '100.25', 'RUB'

    def fail(self, name):
        raise urllib.error.URLError('Tunnel connection failed: 403 Forbidden')

    def test_real_format_parsers(self):
        self.assertEqual(collect.parse_steam({'success':True,'lowest_price':'1 234,56 руб.'}), '1234.56')
        self.assertEqual(collect.parse_steam({'success':True,'lowest_price':'123,45 ₽'}), '123.45')
        self.assertEqual(collect.parse_skinport([{'market_hash_name':'Danger Zone Case','min_price':1.23,'currency':'USD'}]), '1.23')
        for data in ({'success':True,'lowest_price':'$1.23'}, {'success':False}, {'success':True,'lowest_price':'1-2 ₽'}):
            with self.assertRaises(ValueError):
                collect.parse_steam(data)

    def test_currency_mismatch(self):
        with self.assertRaises(ValueError):
            collect.parse_price({'success':True,'currency':'USD','items':[]})
        with self.assertRaises(ValueError):
            collect.parse_skinport([{'market_hash_name':'Danger Zone Case','min_price':1,'currency':'EUR'}])

    def test_collection_history_and_cooldown(self):
        self.assertTrue(collect.collect_source('Market.CSGO',self.fetch,lambda:1000))
        self.assertFalse(collect.collect_source('Market.CSGO',self.fail,lambda:1001))
        self.assertEqual(len(app.snapshot()['auto_history']),1)
        self.assertTrue(collect.collect_source('Market.CSGO',self.fetch,lambda:1900))
        self.assertEqual(len(app.snapshot()['auto_history']),2)
        self.assertEqual(app.snapshot()['automatic'][0]['price'],10025)
        self.assertEqual(app.snapshot()['quotes'],[])  # Missing fees must never be treated as zero.

    def test_failure_preserves_price_and_backoff(self):
        collect.collect_source('Market.CSGO',self.fetch,lambda:1000)
        collect.collect_source('Market.CSGO',self.fail,lambda:1900)
        collect.collect_source('Market.CSGO',self.fail,lambda:2800)
        state=app.snapshot()
        self.assertEqual(len(state['auto_history']),1)
        status=state['collectors'][0]
        self.assertEqual(status['failures'],2)
        self.assertEqual(status['next_attempt'],4600)
        self.assertIn('Прокси',status['error'])
        self.assertIsNotNone(status['last_success'])

    def test_concurrent_collectors_share_claim(self):
        def run(_):
            return collect.collect_source('Market.CSGO',self.fetch,lambda:1000)
        with ThreadPoolExecutor(max_workers=2) as pool:
            result=list(pool.map(run,range(2)))
        self.assertEqual(sum(result),1)
        self.assertEqual(len(app.snapshot()['auto_history']),1)

    def test_fees_apply_without_changing_raw_history(self):
        collect.collect_source('Skinport',lambda n:('10','USD'),lambda:1000)
        app.mutate('/api/payout-settings',dict(market_id=3,sale_fee='5',withdrawal_fee='2',
            fixed_fee_rub='10',method='Крипто',rub_rate='100',verified=1))
        state=app.snapshot()
        q=state['quotes'][0]
        self.assertEqual((q['price'],q['received'],q['own_share']), (100000,92100,13815))
        self.assertTrue(q['eligible'])
        self.assertEqual(state['auto_history'][0]['price'],1000)
        self.assertEqual(state['auto_history'][0]['currency'],'USD')

    def test_rub_rate_is_not_applied_to_rubles(self):
        collect.collect_source('Market.CSGO',self.fetch,lambda:1000)
        app.mutate('/api/payout-settings',dict(market_id=12,sale_fee='0',withdrawal_fee='0',
            fixed_fee_rub='0',method='СБП',rub_rate='100',verified=0))
        q=app.snapshot()['quotes'][0]
        self.assertEqual(q['price'],10025)
        self.assertFalse(q['eligible'])

    def test_restart_does_not_refetch_early(self):
        collect.collect_source('Market.CSGO',self.fetch,lambda:1000)
        app.init_db()
        self.assertFalse(collect.collect_source('Market.CSGO',self.fail,lambda:1002))

    def test_reject_redirect_to_other_host(self):
        import urllib.request
        req=urllib.request.Request(collect.URL)
        with self.assertRaises(ValueError):
            collect.SameHostRedirect().redirect_request(req,None,302,'',{},'https://example.com/')


if __name__ == '__main__':
    unittest.main()
