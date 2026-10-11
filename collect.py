"""Public price adapters and persistent rate-limited automatic collection."""
import argparse
import gzip
import io
import json
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import app

ITEM = 'Danger Zone Case'
URL = 'https://market.csgo.com/api/v2/prices/RUB.json'
SOURCES = {
    'Market.CSGO': URL,
    'Steam Community Market': 'https://steamcommunity.com/market/priceoverview/?appid=730&currency=5&market_hash_name=Danger%20Zone%20Case',
    'Skinport': 'https://api.skinport.com/v1/items?app_id=730&currency=USD&tradable=0',
}
INTERVAL = 900
MAX_BYTES = 32 * 1024 * 1024


def positive_price(value):
    if app.money(value) <= 0:
        raise ValueError('Источник вернул пустую или нулевую цену')
    return str(Decimal(str(value)))


def parse_price(data):
    if data.get('success') is not True or data.get('currency', 'RUB') != 'RUB' or not isinstance(data.get('items'), list):
        raise ValueError('Неожиданный формат API Market.CSGO')
    matches = [x for x in data['items'] if x.get('market_hash_name') == ITEM]
    if len(matches) != 1:
        raise ValueError('API не вернул единственную цену Danger Zone Case')
    return positive_price(matches[0].get('price'))


def parse_steam(data):
    if data.get('success') is not True:
        raise ValueError('Steam не вернул успешный ответ')
    text = str(data.get('lowest_price', ''))
    if not re.search(r'руб|₽|RUB', text, re.I):
        raise ValueError('Steam вернул неизвестную валюту')
    clean = re.sub(r'руб\.?|₽|RUB', '', text, flags=re.I).strip()
    clean = re.sub(r'[\s\u00a0\u202f]', '', clean).replace(',', '.')
    if not re.fullmatch(r'\d+(?:\.\d{1,2})?', clean):
        raise ValueError('Неизвестный формат цены Steam')
    return positive_price(clean)


def parse_skinport(data):
    if not isinstance(data, list):
        raise ValueError('Неожиданный формат API Skinport')
    matches = [x for x in data if x.get('market_hash_name') == ITEM]
    if len(matches) != 1 or matches[0].get('currency') != 'USD':
        raise ValueError('Skinport: товар не найден или валюта не USD')
    return positive_price(matches[0].get('min_price'))


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme != 'https' or urlparse(newurl).hostname != urlparse(req.full_url).hostname:
            raise ValueError('Источник перенаправил запрос на другой домен')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent':'Caseparser/0.2', 'Accept':'application/json', 'Accept-Encoding':'gzip'})
    with urllib.request.build_opener(SameHostRedirect()).open(req, timeout=12) as r:
        raw = r.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Ответ API слишком большой')
        if r.headers.get('Content-Encoding') == 'gzip':
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                raw = gz.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('Распакованный ответ API слишком большой')
        return json.loads(raw)


def fetch_price(name):
    data = fetch_json(SOURCES[name])
    if name == 'Market.CSGO':
        return parse_price(data), 'RUB'
    if name == 'Steam Community Market':
        return parse_steam(data), 'RUB'
    return parse_skinport(data), 'USD'


def error_message(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return f'HTTP {exc.code}: источник отклонил запрос'
    if isinstance(exc, urllib.error.URLError):
        if '403' in str(exc.reason):
            return 'Прокси запретил доступ (403). Проверь разрешенные домены среды.'
        return 'Ошибка соединения с источником: ' + str(exc.reason)[:200]
    return str(exc)[:300]


def collect_source(name, fetcher=fetch_price, clock=time.time):
    stamp = clock()
    with app.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT OR IGNORE INTO collector_status(name) VALUES(?)', (name,))
        old = c.execute('SELECT * FROM collector_status WHERE name=?', (name,)).fetchone()
        if old['next_attempt'] > stamp:
            return False
        c.execute('UPDATE collector_status SET last_attempt=?,next_attempt=?,state=? WHERE name=?',
                  (app.now(), stamp + INTERVAL, 'running', name))
    try:
        price, currency = fetcher(name)
        price = positive_price(price)
        if currency not in ('RUB', 'USD'):
            raise ValueError('Неизвестная валюта')
        with app.connect() as c:
            market_id = c.execute('SELECT id FROM markets WHERE name=?', (name,)).fetchone()[0]
            c.execute('INSERT INTO collected_prices(market_id,price,currency,observed_at,source) VALUES(?,?,?,?,?)',
                      (market_id, app.money(price), currency, app.now(), SOURCES[name]))
            c.execute('UPDATE collector_status SET state=?,error=?,failures=0,last_success=?,next_attempt=? WHERE name=?',
                      ('ok','',app.now(),clock() + INTERVAL,name))
    except Exception as exc:
        failures = old['failures'] + 1
        with app.connect() as c:
            c.execute('UPDATE collector_status SET state=?,error=?,failures=?,next_attempt=? WHERE name=?',
                      ('error', error_message(exc), failures, clock() + min(21600, INTERVAL * 2 ** min(failures-1,5)), name))
    return True


class Collector:
    def __init__(self):
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name='price-collector', daemon=True)
        self.thread.start()

    def run_once(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(collect_source, SOURCES))

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                print('Collector storage error: ' + str(exc), flush=True)
            self.wake_event.wait(30)
            self.wake_event.clear()

    def trigger(self):
        self.wake_event.set()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread:
            self.thread.join(timeout=15)


def main():
    parser = argparse.ArgumentParser(description='Сбор Danger Zone Case: Market.CSGO, Steam, Skinport')
    parser.add_argument('--once', action='store_true', help='Один цикл вместо постоянного обновления')
    args = parser.parse_args()
    app.init_db()
    worker = Collector()
    if args.once:
        worker.run_once()
        with app.connect() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM collector_status')]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 1 if any(r['state'] == 'error' for r in rows) else 0
    worker.start()
    try:
        while True:
            worker.thread.join(timeout=1)
    except KeyboardInterrupt:
        worker.stop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
