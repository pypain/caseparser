import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import app
from collect import parse_price


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = app.DB
        app.DB = Path(self.tmp.name) / 'test.sqlite3'
        app.init_db()
        self.quote = dict(market='Market.CSGO', price_rub='100', sale_fee='5', withdrawal_fee='2',
                          fixed_fee_rub='10', method='СБП', verified='1', kind='instant', source='Проверено вручную')

    def tearDown(self):
        app.DB = self.old
        self.tmp.cleanup()

    def batch(self):
        app.mutate('/api/batches', {'owner':'Иван','quantity':'10'})

    def sale(self, n='3', received='1000.01'):
        return app.mutate('/api/sales', {'batch_id':1,'market_id':12,'quantity':n,'received_rub':received})

    def test_commissions_and_shares(self):
        app.mutate('/api/quotes', self.quote)
        q = app.snapshot(10)['quotes'][0]
        self.assertEqual(q['received'], 92100)
        self.assertEqual(q['own_share'], 13815)
        self.assertEqual(q['owner_share'], 78285)
        self.assertTrue(q['eligible'])

    def test_actual_payout_and_debt(self):
        self.batch()
        self.sale()
        s = app.snapshot()['sales'][0]
        self.assertEqual((s['received'],s['own_share'],s['owner_share']), (100001,15000,85001))
        app.mutate('/api/payments', {'batch_id':1,'amount_rub':'800'})
        b = app.snapshot()['batches'][0]
        self.assertEqual((b['remaining'],b['balance']), (7,5001))
        with self.assertRaises(ValueError):
            app.mutate('/api/payments', {'batch_id':1,'amount_rub':'50.02'})
        with self.assertRaises(ValueError):
            self.sale('8')

    def test_excluded_and_steam_are_not_sales(self):
        self.batch()
        app.mutate('/api/markets', {'id':12,'excluded':1})
        with self.assertRaises(ValueError):
            self.sale()
        with self.assertRaises(ValueError):
            app.mutate('/api/sales', {'batch_id':1,'market_id':1,'quantity':1,'received_rub':100})

    def test_import_is_atomic(self):
        text='market,price_rub,sale_fee,withdrawal_fee,fixed_fee_rub,method,verified,kind,source\nMarket.CSGO,100,5,2,0,СБП,1,instant,test\nUnknown,100,5,2,0,СБП,1,instant,test\n'
        with self.assertRaises(ValueError):
            app.mutate('/api/import', {'csv':text})
        self.assertEqual(app.snapshot()['history'], [])

    def test_unverified_and_stale(self):
        app.mutate('/api/quotes', self.quote | {'verified':'0'})
        self.assertFalse(app.snapshot()['quotes'][0]['eligible'])
        app.mutate('/api/quotes', self.quote | {'method':'Карта','observed_at':'2020-01-01T00:00:00+00:00'})
        self.assertTrue(any(q['stale'] for q in app.snapshot()['quotes']))

    def test_concurrent_sales_cannot_oversell(self):
        self.batch()
        def sell(_):
            try:
                self.sale('7')
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(sell, range(2)))
        self.assertEqual(sum(results),1)
        self.assertEqual(app.snapshot()['batches'][0]['remaining'],3)

    def test_invalid_inputs_and_future_prices(self):
        for value in ('NaN','Infinity','-1'):
            with self.assertRaises(ValueError):
                app.money(value)
        with self.assertRaises(ValueError):
            app.mutate('/api/quotes', self.quote | {'observed_at':'2099-01-01T00:00:00+00:00'})
        with self.assertRaises(ValueError):
            app.quantity('1.5')

    def test_api_adapter_validates_response(self):
        self.assertEqual(parse_price({'success':True,'items':[
            {'market_hash_name':'Other Case','price':'1'},
            {'market_hash_name':'Danger Zone Case','price':'99.25'}]}), '99.25')
        for data in ({'success':False}, {'success':True,'items':[]},
                     {'success':True,'items':[{'market_hash_name':'Danger Zone Case','price':'0'}]}):
            with self.assertRaises(ValueError):
                parse_price(data)


if __name__ == '__main__':
    unittest.main()
