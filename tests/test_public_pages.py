import json
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from public_pages import parse_lis, parse_skinbaron, parse_comparison, COMPARISON_URL
import collect


def script(data, script_id=None):
    attrs = 'type="application/json" id="' + script_id + '"' if script_id else 'type="application/ld+json"'
    return '<script ' + attrs + '>' + json.dumps(data) + '</script>'


def offer(slug, price, active=True):
    return ('<div class="' + ('active-offer' if active else 'inactive-offer') + '">' +
            '<div><a href="https://csgoskins.gg/markets/' + slug + '">Market</a></div>' +
            '<div><div>from</div><div class="font-bold text-lg">' + price + '</div></div></div>')


def comparison():
    product={'@type':'Product','name':'Danger Zone Case','url':COMPARISON_URL,
             'offers':{'priceCurrency':'USD'}}
    return (script(product) + '<div id="tab-Buy">' + offer('csfloat','$0.90') +
            offer('csfloat','$1.01') + offer('dmarket','$0.01',False) + '</div>' +
            '<div id="tab-Sell">' + offer('csfloat','$0.70') + offer('whitemarket','$0.87') + '</div>' +
            '<div id="tab-Trade">' + offer('csfloat','$0.02') + '</div>')


class PublicPagesTests(unittest.TestCase):
    def test_lis_uses_exact_in_stock_product_and_usd(self):
        data={'@type':'Product','name':'Danger Zone Case','offers':{
            'priceCurrency':'USD','offerCount':5625,'availability':'https://schema.org/InStock', 'lowPrice':0.99}}
        self.assertEqual(parse_lis(script(data)),'0.99')
        for change in [{'name':'Danger Zone Case Key'}, {'offers':{}},
                       {'offers':dict(data['offers'],offerCount=0)},
                       {'offers':dict(data['offers'],priceCurrency='EUR')}]:
            with self.assertRaises(ValueError):parse_lis(script(dict(data,**change)))

    def test_skinbaron_eur_not_steam_price(self):
        row={'appId':730,'extendedProductInformation':{'localizedName':'Danger Zone Case'},
             'lowestPrice':0.8799999952316284,'formattedLowestPrice':'0.88 €',
             'steamMarketPrice':1.50,'numberOfOffers':20}
        self.assertEqual(parse_skinbaron(script({'browser-search-result-state':{
            'aggregatedMetaOffers':[row]}},'skinbaron-frontend-state')),'0.88')
        row['numberOfOffers']=0
        with self.assertRaises(ValueError):parse_skinbaron(script({'browser-search-result-state':{
            'aggregatedMetaOffers':[row]}},'skinbaron-frontend-state'))

    def test_comparison_separates_listings_buy_orders_and_trade_balance(self):
        rows=parse_comparison(comparison())
        self.assertEqual(rows['CSFloat']['price'],'0.90')
        self.assertEqual(rows['CSFloat']['kind'],'listing')
        self.assertEqual(rows['white.market']['price'],'0.87')
        self.assertEqual(rows['white.market']['kind'],'estimate')
        self.assertNotIn('DMarket',rows)  # An inactive row is not a quote.
        self.assertEqual(rows['CSFloat']['source'],COMPARISON_URL+'#tab-Buy')

    def test_mismatch_truncated_ambiguous_or_wrong_currency_fails_closed(self):
        for html in [comparison().replace('Danger Zone Case','Danger Zone Case Key'),
                     comparison().replace('USD','EUR'), comparison()[:-6],
                     comparison().replace('$0.90','€0.90'),
                     comparison().replace('$0.90','0.90')]:
            with self.assertRaises(ValueError):parse_comparison(html)

    def test_one_public_page_request_for_concurrent_fallbacks(self):
        with patch('collect.fetch_direct_price',side_effect=ValueError('Direct unavailable')), \
             patch('collect.fetch_text',return_value=comparison()) as fetch:
            cycle=collect.CycleFetcher()
            with ThreadPoolExecutor(max_workers=4) as pool:
                results=list(pool.map(cycle,['CSFloat','white.market','CSFloat','white.market']))
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(results[0].source,COMPARISON_URL+'#tab-Buy')
            self.assertIn('Direct unavailable',results[0].note)
            self.assertEqual(results[1].kind,'estimate')

    def test_direct_price_has_priority_and_does_not_fetch_comparison(self):
        with patch('collect.fetch_direct_price',return_value=('0.88','EUR')), \
             patch('collect.fetch_text') as fetch:
            result=collect.CycleFetcher()('SkinBaron')
            self.assertEqual((result.price,result.currency),('0.88','EUR'))
            self.assertEqual(result.source,collect.DIRECT_SOURCES['SkinBaron'])
            fetch.assert_not_called()
