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
        status=next(s for s in state['collectors'] if s['name']=='Market.CSGO')
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

    def test_all_markets_have_explicit_coverage(self):
        state=app.snapshot()
        self.assertEqual({m['name'] for m in state['markets']},{s['name'] for s in state['collectors']})
        self.assertEqual({n for n,s in collect.REGISTRY.items() if s['supported']},set(collect.SOURCES))
        self.assertTrue(next(s for s in state['collectors'] if s['name']=='BUFF163')['supported'])
        self.assertFalse(next(s for s in state['collectors'] if s['name']=='BitSkins')['supported'])

    def test_new_price_units_and_exact_item(self):
        self.assertEqual(collect.parse_csfloat({'data':[
            {'price':1,'type':'buy_now','item':{'market_hash_name':'Other'}},
            {'price':88,'type':'buy_now','item':{'market_hash_name':collect.ITEM}},
            {'price':10,'type':'auction','item':{'market_hash_name':collect.ITEM}}]}),'0.88')
        self.assertEqual(collect.parse_waxpeer({'success':True,'items':[
            {'name':collect.ITEM,'min':1234},{'name':'Other','min':1}]}),'1.234')
        for parser,data in [(collect.parse_csfloat,{'data':[]}),
                            (collect.parse_waxpeer,{'success':False,'items':[]})]:
            with self.assertRaises(ValueError):parser(data)

    def test_tradeit_uses_store_price_not_trading_balance_or_case_key(self):
        self.assertEqual(collect.parse_tradeit({'items':[
            {'name':collect.ITEM+' Key','storePrice':1},
            {'name':collect.ITEM,'priceForTrade':999,'storePrice':123}]}),'1.23')
        with self.assertRaises(ValueError):
            collect.parse_tradeit({'items':[{'name':collect.ITEM+' Key','storePrice':123}]})

    def test_http401_distinct_from_network_error(self):
        def denied(name):raise urllib.error.HTTPError(collect.SOURCES[name],401,'Unauthorized',{},None)
        collect.collect_source('CSFloat',denied,lambda:1000)
        status=next(s for s in app.snapshot()['collectors'] if s['name']=='CSFloat')
        self.assertEqual(status['state'],'needs_key')

    def test_fallback_sale_estimate_has_source_and_never_becomes_recommendation(self):
        quote=collect.PriceQuote('0.87','USD',collect.COMPARISON_URL+'#tab-Sell','estimate','Public comparison')
        collect.collect_source('white.market',lambda name:quote,lambda:1000)
        market=next(m for m in app.snapshot()['markets'] if m['name']=='white.market')
        app.mutate('/api/payout-settings',dict(market_id=market['id'],sale_fee='5',withdrawal_fee='2',
            fixed_fee_rub='0',method='Крипто',rub_rate='100',verified=1))
        state=app.snapshot()
        self.assertEqual(state['automatic'][0]['kind'],'estimate')
        self.assertEqual(state['auto_history'][0]['source'],quote.source)
        self.assertEqual(state['quotes'][0]['kind'],'estimate')
        self.assertEqual(state['quotes'][0]['price'],8700)
        self.assertFalse(state['quotes'][0]['eligible'])
        self.assertIn('CSGOSKINS.GG',state['quotes'][0]['source_label'])

    def test_eur_conversion_and_raw_history(self):
        collect.collect_source('SkinBaron',lambda name:('0.88','EUR'),lambda:1000)
        app.mutate('/api/payout-settings',dict(market_id=6,sale_fee='0',withdrawal_fee='0',
            fixed_fee_rub='0',method='Крипто',rub_rate='110',verified=1))
        state=app.snapshot()
        self.assertEqual(state['quotes'][0]['price'],9680)
        self.assertEqual((state['auto_history'][0]['price'],state['auto_history'][0]['currency']),(88,'EUR'))
        # A fallback can quote USD while the direct page uses EUR; never reuse EUR FX.
        collect.collect_source('SkinBaron',lambda name:('0.98','USD'),lambda:1900)
        state=app.snapshot()
        self.assertTrue(state['automatic'][0]['rate_mismatch'])
        self.assertEqual(state['quotes'],[])
        app.mutate('/api/payout-settings',dict(market_id=6,sale_fee='0',withdrawal_fee='0',
            fixed_fee_rub='0',method='Крипто',rub_rate='100',rate_currency='USD',verified=1))
        self.assertEqual(app.snapshot()['quotes'][0]['price'],9800)

    def test_existing_database_migration_preserves_prices_inventory_and_cooldown(self):
        app.mutate('/api/batches',{'owner':'Владелец','quantity':100})
        collect.collect_source('Market.CSGO',self.fetch,lambda:1000)
        with app.connect() as c:
            c.execute('ALTER TABLE collected_prices RENAME TO prices_before_upgrade')
            c.execute('''CREATE TABLE collected_prices(id INTEGER PRIMARY KEY, market_id INTEGER,
                price INTEGER NOT NULL, currency TEXT NOT NULL, observed_at TEXT NOT NULL, source TEXT NOT NULL)''')
            c.execute('''INSERT INTO collected_prices SELECT id,market_id,price,currency,observed_at,source
                FROM prices_before_upgrade''')
            c.execute('DROP TABLE prices_before_upgrade')
        app.init_db()
        state=app.snapshot()
        self.assertEqual(state['automatic'][0]['price'],10025)
        self.assertEqual(state['automatic'][0]['kind'],'listing')
        self.assertEqual(state['batches'][0]['remaining'],100)
        self.assertEqual(next(s for s in state['collectors'] if s['name']=='Market.CSGO')['next_attempt'],0)
        collect.collect_source('Market.CSGO',self.fetch,lambda:1001)
        app.init_db()
        self.assertFalse(collect.collect_source('Market.CSGO',self.fail,lambda:1002))


if __name__ == '__main__':
    unittest.main()
