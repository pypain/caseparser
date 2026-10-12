"""Prices embedded by marketplaces in their public HTML pages."""
import json
import re
from decimal import Decimal
from html.parser import HTMLParser

ITEM = 'Danger Zone Case'
COMPARISON_URL = 'https://csgoskins.gg/items/danger-zone-case'
COMPARISON_MARKETS = {
    'steam': 'Steam Community Market', 'csfloat': 'CSFloat',
    'skinport': 'Skinport', 'dmarket': 'DMarket', 'skinbaron': 'SkinBaron',
    'buff163': 'BUFF163', 'buff-market': 'BUFF Market', 'lis-skins': 'LIS-SKINS',
    'waxpeer': 'Waxpeer', 'shadowpay': 'ShadowPay', 'whitemarket': 'white.market',
    'tradeitgg': 'Tradeit', 'csmoney': 'CS.MONEY', 'skinswap': 'SkinSwap',
    'skinland': 'SkinLand', 'avanmarket': 'AvanMarket', 'skinplace': 'SkinPlace',
    'skinscom': 'Skins.com', 'manncostore': 'Mannco.Store', 'skinsell': 'SkinSell',
}


class ComparisonData(HTMLParser):
    """Capture a price only inside its active offer and Buy/Sell tab."""
    def __init__(self):
        super().__init__()
        self.divs = []
        self.row = None
        self.capture = None
        self.rows = []
        self.tabs = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'div':
            self.divs.append(attrs)
            if attrs.get('id') in ('tab-Buy', 'tab-Sell'):
                self.tabs.add(attrs['id'])
            classes = attrs.get('class', '').split()
            if 'active-offer' in classes:
                tab = next((d['id'] for d in reversed(self.divs)
                            if d.get('id') in ('tab-Buy', 'tab-Sell', 'tab-Trade')), None)
                if tab in ('tab-Buy', 'tab-Sell'):
                    self.row = {'depth':len(self.divs), 'tab':tab, 'markets':[], 'prices':[]}
            elif self.row and 'font-bold' in classes and 'text-lg' in classes:
                self.capture = {'depth':len(self.divs), 'text':[]}
        if tag == 'a' and self.row:
            href = attrs.get('href', '')
            match = re.fullmatch(r'https://csgoskins\.gg/markets/([a-z0-9-]+)', href)
            if match:
                self.row['markets'].append(match.group(1))

    def handle_data(self, data):
        if self.capture:
            self.capture['text'].append(data)

    def handle_endtag(self, tag):
        if tag != 'div' or not self.divs:
            return
        if self.capture and self.capture['depth'] == len(self.divs):
            self.row['prices'].append(''.join(self.capture['text']).strip())
            self.capture = None
        if self.row and self.row['depth'] == len(self.divs):
            self.rows.append(self.row)
            self.row = None
        self.divs.pop()


def parse_comparison(html):
    from collect import positive_price
    # Verify the exact product and currency before reading individual rows.
    products = []
    for attrs, raw in page_data(html):
        if attrs.get('type') == 'application/ld+json':
            data = json.loads(raw)
            if isinstance(data, dict) and data.get('@type') == 'Product':
                products.append(data)
    if (len(products) != 1 or products[0].get('name') != ITEM or
            products[0].get('url') != COMPARISON_URL or
            products[0].get('offers', {}).get('priceCurrency') != 'USD'):
        raise ValueError('CSGOSKINS.GG: товар или валюта страницы не совпадают')
    parser = ComparisonData()
    parser.feed(html)
    if parser.divs or 'tab-Buy' not in parser.tabs:
        raise ValueError('CSGOSKINS.GG: страница оборвалась или изменился формат предложений')
    rows = {}
    for row in parser.rows:
        if len(row['markets']) != 1 or len(row['prices']) != 1:
            raise ValueError('CSGOSKINS.GG: неоднозначная строка предложения')
        name = COMPARISON_MARKETS.get(row['markets'][0])
        if not name:
            continue
        text = row['prices'][0]
        if not re.fullmatch(r'\$(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}', text):
            raise ValueError('CSGOSKINS.GG: неизвестный формат цены в USD')
        price = Decimal(positive_price(text[1:].replace(',', '')))
        kind = 'listing' if row['tab'] == 'tab-Buy' else 'estimate'
        key = (name, kind)
        rows[key] = min(rows.get(key, price), price) if kind == 'listing' else max(rows.get(key, price), price)
    result = {}
    for name in COMPARISON_MARKETS.values():
        # Listing offers have priority; Sell is an indicative sale price, not a verified executable bid.
        for kind in ('listing', 'estimate'):
            if (name, kind) in rows:
                result[name] = {'price':str(rows[(name, kind)]), 'currency':'USD', 'kind':kind,
                                'source':COMPARISON_URL + ('#tab-Buy' if kind == 'listing' else '#tab-Sell')}
                break
    if not result:
        raise ValueError('CSGOSKINS.GG: цены подключенных площадок не найдены')
    return result


class PageData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.current = None
        self.attrs = None

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            attrs = dict(attrs)
            if attrs.get('type') in ('application/ld+json', 'application/json'):
                self.current = []
                self.attrs = attrs

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            self.scripts.append((self.attrs, ''.join(self.current)))
            self.current = None


def page_data(html):
    parser = PageData()
    parser.feed(html)
    return parser.scripts


def parse_lis(html):
    from collect import positive_price
    prices = []
    for attrs, raw in page_data(html):
        if attrs.get('type') != 'application/ld+json':
            continue
        data = json.loads(raw)
        rows = data if isinstance(data, list) else data.get('@graph', [data])
        for row in rows:
            if row.get('@type') != 'Product' or row.get('name') != ITEM:
                continue
            offers = row.get('offers', {})
            if (offers.get('priceCurrency') != 'USD' or
                    offers.get('availability') != 'https://schema.org/InStock' or
                    not isinstance(offers.get('offerCount'), (int, float)) or
                    offers['offerCount'] <= 0):
                continue
            prices.append(Decimal(positive_price(offers.get('lowPrice'))))
    if not prices:
        raise ValueError('LIS-SKINS: нет подтвержденных предложений Danger Zone Case в USD')
    return positive_price(min(prices))


def parse_skinbaron(html):
    from collect import positive_price
    prices = []
    for attrs, raw in page_data(html):
        if attrs.get('id') != 'skinbaron-frontend-state':
            continue
        data = json.loads(raw)
        rows = data.get('browser-search-result-state', {}).get('aggregatedMetaOffers', [])
        for row in rows:
            if (row.get('appId') != 730 or
                    row.get('extendedProductInformation', {}).get('localizedName') != ITEM or
                    row.get('numberOfOffers', 0) <= 0):
                continue
            formatted = row.get('formattedLowestPrice', '')
            if not formatted.endswith(' €'):
                raise ValueError('SkinBaron: неизвестная валюта цены')
            price = Decimal(positive_price(formatted[:-2].replace(',', '.')))
            if abs(price - Decimal(str(row['lowestPrice']))) > Decimal('0.01'):
                raise ValueError('SkinBaron: цена не совпадает с данными страницы')
            prices.append(price)
    if not prices:
        raise ValueError('SkinBaron: предложений Danger Zone Case не найдено')
    return positive_price(min(prices))
