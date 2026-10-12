"""Local case inventory and marketplace comparison. Python 3.9, no dependencies."""
import csv
import io
import json
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import auth
from sources import REGISTRY

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('CASEPARSER_DB', ROOT / '.data' / 'cases.sqlite3'))
COLLECTOR = None
PASSWORD_HASH = os.environ.get('CASEPARSER_PASSWORD_HASH', '')
COOKIE_SECURE = os.environ.get('CASEPARSER_COOKIE_SECURE', '0') == '1'
PAGES = ('/', '/prices', '/collection', '/inventory', '/history', '/markets')
MARKETS = [
    ('Steam Community Market', 'https://steamcommunity.com/market/', 'Баланс Steam; прямого вывода нет', 0),
    ('CSFloat', 'https://csfloat.com/', 'Условия и доступность в РФ требуют проверки', 1),
    ('Skinport', 'https://skinport.com/', 'Условия и доступность в РФ требуют проверки', 1),
    ('DMarket', 'https://dmarket.com/', 'Условия и доступность в РФ требуют проверки', 1),
    ('BitSkins', 'https://bitskins.com/', 'Условия и доступность в РФ требуют проверки', 1),
    ('SkinBaron', 'https://skinbaron.de/', 'Условия и доступность в РФ требуют проверки', 1),
    ('GamerPay', 'https://gamerpay.gg/', 'Условия и доступность в РФ требуют проверки', 1),
    ('BUFF163', 'https://buff.163.com/', 'Условия и доступность в РФ требуют проверки', 1),
    ('BUFF Market', 'https://buff.market/', 'Актуальность сервиса и вывода требует проверки', 1),
    ('YouPin', 'https://www.youpin898.com/', 'Условия и доступность в РФ требуют проверки', 1),
    ('C5Game', 'https://www.c5game.com/', 'Условия и доступность в РФ требуют проверки', 1),
    ('Market.CSGO', 'https://market.csgo.com/', 'Условия и доступность в РФ требуют проверки', 1),
    ('LIS-SKINS', 'https://lis-skins.com/', 'Условия и доступность в РФ требуют проверки', 1),
    ('Waxpeer', 'https://waxpeer.com/', 'Условия и доступность в РФ требуют проверки', 1),
    ('ShadowPay', 'https://shadowpay.com/', 'Условия и доступность в РФ требуют проверки', 1),
    ('white.market', 'https://white.market/', 'Условия и доступность в РФ требуют проверки', 1),
    ('Tradeit', 'https://tradeit.gg/', 'Условия и доступность в РФ требуют проверки', 1),
    ('CS.MONEY', 'https://csgoskins.gg/markets/csmoney', 'Условия и доступность в РФ требуют проверки', 1),
    ('SkinSwap', 'https://csgoskins.gg/markets/skinswap', 'Условия и доступность в РФ требуют проверки', 1),
    ('SkinLand', 'https://csgoskins.gg/markets/skinland', 'Условия и доступность в РФ требуют проверки', 1),
    ('AvanMarket', 'https://csgoskins.gg/markets/avanmarket', 'Условия и доступность в РФ требуют проверки', 1),
    ('SkinPlace', 'https://csgoskins.gg/markets/skinplace', 'Условия и доступность в РФ требуют проверки', 1),
    ('Skins.com', 'https://csgoskins.gg/markets/skinscom', 'Условия и доступность в РФ требуют проверки', 1),
    ('Mannco.Store', 'https://csgoskins.gg/markets/manncostore', 'Условия и доступность в РФ требуют проверки', 1),
    ('SkinSell', 'https://csgoskins.gg/markets/skinsell', 'Условия и доступность в РФ требуют проверки', 1),
]


def connect():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS markets(
          id INTEGER PRIMARY KEY, name TEXT UNIQUE, url TEXT, note TEXT,
          cash INTEGER, excluded INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS quotes(
          id INTEGER PRIMARY KEY, market_id INTEGER REFERENCES markets(id),
          price INTEGER NOT NULL, sale_fee TEXT NOT NULL, withdrawal_fee TEXT NOT NULL,
          fixed_fee INTEGER NOT NULL, method TEXT NOT NULL, verified INTEGER NOT NULL,
          kind TEXT NOT NULL, observed_at TEXT NOT NULL, source TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS batches(
          id INTEGER PRIMARY KEY, owner TEXT NOT NULL, quantity INTEGER NOT NULL,
          received_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sales(
          id INTEGER PRIMARY KEY, batch_id INTEGER REFERENCES batches(id),
          market_id INTEGER REFERENCES markets(id), quantity INTEGER NOT NULL,
          received INTEGER NOT NULL, own_share INTEGER NOT NULL, owner_share INTEGER NOT NULL,
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payments(
          id INTEGER PRIMARY KEY, batch_id INTEGER REFERENCES batches(id),
          amount INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS collected_prices(
          id INTEGER PRIMARY KEY, market_id INTEGER REFERENCES markets(id),
          price INTEGER NOT NULL, currency TEXT NOT NULL,
          observed_at TEXT NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'listing');
        CREATE INDEX IF NOT EXISTS collected_market ON collected_prices(market_id,id);
        CREATE TABLE IF NOT EXISTS collector_status(
          name TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
          last_attempt TEXT, last_success TEXT, next_attempt REAL NOT NULL DEFAULT 0,
          failures INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS payout_settings(
          market_id INTEGER PRIMARY KEY REFERENCES markets(id), sale_fee TEXT NOT NULL,
          withdrawal_fee TEXT NOT NULL, fixed_fee INTEGER NOT NULL, method TEXT NOT NULL,
          verified INTEGER NOT NULL, rub_rate TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payout_rate_currency(
          market_id INTEGER PRIMARY KEY REFERENCES markets(id), currency TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(digest TEXT PRIMARY KEY,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS login_failures(created_at REAL NOT NULL);
        ''')
        columns = {r['name'] for r in c.execute('PRAGMA table_info(collected_prices)')}
        if 'kind' not in columns:
            c.execute("ALTER TABLE collected_prices ADD COLUMN kind TEXT NOT NULL DEFAULT 'listing'")
            # New fallback paths deserve an immediate attempt after this upgrade.
            c.execute('UPDATE collector_status SET next_attempt=0')
        c.executemany('INSERT OR IGNORE INTO markets(name,url,note,cash) VALUES(?,?,?,?)', MARKETS)
        # Earlier versions supported RUB and USD only. Keep those existing rates bound
        # to their currency without changing the old payout_settings table layout.
        c.execute('''INSERT OR IGNORE INTO payout_rate_currency(market_id,currency)
            SELECT s.market_id, CASE WHEN m.name IN ('Market.CSGO','Steam Community Market')
              THEN 'RUB' ELSE 'USD' END FROM payout_settings s JOIN markets m ON m.id=s.market_id''')
        for name,source in REGISTRY.items():
            c.execute('INSERT OR IGNORE INTO collector_status(name,state,error) VALUES(?,?,?)',
                      (name,'pending' if source['supported'] else 'not_connected',
                       '' if source['supported'] else source.get('reason','Автосбор пока не реализован: требуется проверка документации и доступа к источнику.')))
            if not source['supported']:
                c.execute("UPDATE collector_status SET state='not_connected',error=? WHERE name=?",
                          (source.get('reason','Автосбор пока не реализован: требуется проверка документации и доступа к источнику.'),name))
            if source['supported']:
                c.execute("UPDATE collector_status SET state='pending',error='',next_attempt=0 WHERE name=? AND state='not_connected'",(name,))


def now():
    return datetime.now(timezone.utc).isoformat()


def money(value):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or n > 100000000:
            raise ValueError('Сумма должна быть от 0 до 100 000 000 ₽')
        return int((n * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise ValueError('Некорректная сумма') from exc


def percentage(value):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or not 0 <= n <= 100:
            raise ValueError('Комиссия должна быть от 0 до 100%')
        return str(n)
    except InvalidOperation as exc:
        raise ValueError('Некорректная комиссия') from exc


def quantity(value):
    try:
        n = int(str(value))
    except (ValueError, TypeError) as exc:
        raise ValueError('Количество должно быть целым числом') from exc
    if not 1 <= n <= 1000000:
        raise ValueError('Количество должно быть от 1 до 1 000 000')
    return n


def flag(value):
    if value in (True, 1, '1'):
        return 1
    if value in (False, 0, '0'):
        return 0
    raise ValueError('Флаг должен быть 0 или 1')


def estimate(q, count):
    gross = Decimal(q['price'] * count)
    after_sale = gross * (1 - Decimal(q['sale_fee']) / 100)
    net = max(Decimal(0), after_sale * (1 - Decimal(q['withdrawal_fee']) / 100) - q['fixed_fee'])
    received = int(net.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    own = int((Decimal(received) * Decimal('0.15')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return {'received': received, 'own_share': own, 'owner_share': received - own}


def batch_state(c, batch_id):
    b = c.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
    if not b:
        raise ValueError('Партия не найдена')
    sold, owed = c.execute('SELECT COALESCE(SUM(quantity),0),COALESCE(SUM(owner_share),0) FROM sales WHERE batch_id=?', (batch_id,)).fetchone()
    paid = c.execute('SELECT COALESCE(SUM(amount),0) FROM payments WHERE batch_id=?', (batch_id,)).fetchone()[0]
    return dict(b) | {'remaining': b['quantity'] - sold, 'sold': sold, 'owed': owed, 'paid': paid, 'balance': owed - paid}


def add_quote(c, d):
    market = c.execute('SELECT id FROM markets WHERE name=?', (d['market'],)).fetchone()
    if not market:
        raise ValueError('Неизвестная площадка: ' + str(d['market']))
    stamp = datetime.fromisoformat(d.get('observed_at') or now())
    if stamp.tzinfo is None:
        raise ValueError('Время цены должно содержать часовой пояс')
    if stamp > datetime.now(timezone.utc):
        raise ValueError('Цена не может быть из будущего')
    kind = d['kind']
    if kind not in ('instant', 'listing'):
        raise ValueError('Тип цены: instant или listing')
    method, source = str(d['method']).strip(), str(d['source']).strip()
    if not method or not source:
        raise ValueError('Укажите способ вывода и источник')
    c.execute('''INSERT INTO quotes(market_id,price,sale_fee,withdrawal_fee,fixed_fee,method,verified,kind,observed_at,source)
                 VALUES(?,?,?,?,?,?,?,?,?,?)''',
              (market['id'], money(d['price_rub']), percentage(d['sale_fee']), percentage(d['withdrawal_fee']),
               money(d['fixed_fee_rub']), method, flag(d['verified']), kind, stamp.astimezone(timezone.utc).isoformat(), source))


def snapshot(count=1):
    with connect() as c:
        markets = [dict(r) for r in c.execute('SELECT * FROM markets ORDER BY id')]
        quotes = [dict(r) for r in c.execute('''SELECT q.*,m.name,m.cash,m.excluded FROM quotes q JOIN markets m ON m.id=q.market_id
          WHERE q.id=(SELECT q2.id FROM quotes q2 WHERE q2.market_id=q.market_id AND q2.kind=q.kind AND q2.method=q.method
          ORDER BY q2.observed_at DESC,q2.id DESC LIMIT 1)''')]
        automatic = [dict(r) for r in c.execute('''SELECT p.*,m.name,m.cash,m.excluded FROM collected_prices p
          JOIN markets m ON m.id=p.market_id WHERE p.id=(SELECT MAX(p2.id) FROM collected_prices p2 WHERE p2.market_id=p.market_id)''')]
        settings = {r['market_id']: dict(r) for r in c.execute('''SELECT s.*,r.currency AS rate_currency
            FROM payout_settings s JOIN payout_rate_currency r ON r.market_id=s.market_id''')}
        for p in automatic:
            p['source_label'] = 'CSGOSKINS.GG · публичное сравнение' if urlparse(p['source']).hostname == 'csgoskins.gg' else 'Прямой источник площадки'
            p['stale'] = (datetime.now(timezone.utc) - datetime.fromisoformat(p['observed_at'])).total_seconds() > 86400
            s = settings.get(p['market_id'])
            p['rate_mismatch'] = bool(s and s['rate_currency'] != p['currency'])
            if p['rate_mismatch']:
                continue
            if s:
                converted = int((Decimal(p['price']) * (Decimal(s['rub_rate']) if p['currency'] != 'RUB' else 1)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                quotes.append(p | s | {'price': converted, 'automatic': True})
        for q in quotes:
            q.update(estimate(q, count))
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(q['observed_at'])).total_seconds()
            q['stale'] = age > 86400
            q['eligible'] = bool(q['cash'] and not q['excluded'] and q['verified'] and not q['stale'] and q['kind'] != 'estimate')
        quotes.sort(key=lambda q: (not q['eligible'], -q['received']))
        batches = [batch_state(c, r[0]) for r in c.execute('SELECT id FROM batches ORDER BY id DESC')]
        sales = [dict(r) for r in c.execute('SELECT s.*,b.owner,m.name FROM sales s JOIN batches b ON b.id=s.batch_id JOIN markets m ON m.id=s.market_id ORDER BY s.id DESC')]
        history = [dict(r) for r in c.execute('SELECT q.*,m.name FROM quotes q JOIN markets m ON m.id=q.market_id ORDER BY observed_at,id')]
        auto_history = [dict(r) for r in c.execute('SELECT p.*,m.name FROM collected_prices p JOIN markets m ON m.id=p.market_id ORDER BY observed_at,id')]
        statuses = [dict(r) for r in c.execute('SELECT * FROM collector_status ORDER BY name')]
        for status in statuses:
            status['supported'] = REGISTRY.get(status['name'],{}).get('supported',False)
            status['docs'] = REGISTRY.get(status['name'],{}).get('docs','')
        payments = [dict(r) for r in c.execute('SELECT p.*,b.owner FROM payments p JOIN batches b ON b.id=p.batch_id ORDER BY p.id DESC')]
    return dict(markets=markets, quotes=quotes, batches=batches, sales=sales, history=history, payments=payments,
                automatic=automatic, auto_history=auto_history, collectors=statuses, payout_settings=list(settings.values()))


def mutate(path, d):
    with connect() as c:
        # Serialize stock/balance checks and writes, including concurrent HTTP requests.
        c.execute('BEGIN IMMEDIATE')
        if path == '/api/payout-settings':
            market_id = int(d['market_id'])
            if not c.execute('SELECT id FROM markets WHERE id=?', (market_id,)).fetchone():
                raise ValueError('Площадка не найдена')
            latest = c.execute('SELECT currency FROM collected_prices WHERE market_id=? ORDER BY id DESC LIMIT 1',
                               (market_id,)).fetchone()
            currency = d.get('rate_currency') or (latest['currency'] if latest else None)
            if currency not in ('RUB','USD','EUR'):
                raise ValueError('Укажите валюту цены для этого курса: RUB, USD или EUR')
            try:
                rate = Decimal(str(d['rub_rate']))
            except InvalidOperation as exc:
                raise ValueError('Некорректный курс') from exc
            if not rate.is_finite() or not 0 < rate <= 1000000:
                raise ValueError('Укажите положительный курс к рублю')
            method = str(d['method']).strip()
            if not method:
                raise ValueError('Укажите способ вывода')
            c.execute('''INSERT INTO payout_settings VALUES(?,?,?,?,?,?,?) ON CONFLICT(market_id) DO UPDATE SET
              sale_fee=excluded.sale_fee,withdrawal_fee=excluded.withdrawal_fee,fixed_fee=excluded.fixed_fee,
              method=excluded.method,verified=excluded.verified,rub_rate=excluded.rub_rate''',
              (market_id, percentage(d['sale_fee']), percentage(d['withdrawal_fee']), money(d['fixed_fee_rub']),
               method, flag(d['verified']), str(rate)))
            c.execute('''INSERT INTO payout_rate_currency(market_id,currency) VALUES(?,?)
                ON CONFLICT(market_id) DO UPDATE SET currency=excluded.currency''', (market_id,currency))
        elif path == '/api/quotes':
            add_quote(c, d)
        elif path == '/api/import':
            rows = list(csv.DictReader(io.StringIO(d['csv'])))
            if not rows or len(rows) > 10000:
                raise ValueError('CSV должен содержать от 1 до 10 000 строк')
            for row in rows:
                add_quote(c, row)
        elif path == '/api/batches':
            owner = str(d['owner']).strip()
            if not owner or len(owner) > 200:
                raise ValueError('Укажите владельца (до 200 символов)')
            c.execute('INSERT INTO batches(owner,quantity,received_at) VALUES(?,?,?)', (owner, quantity(d['quantity']), now()))
        elif path == '/api/sales':
            b = batch_state(c, int(d['batch_id']))
            n = quantity(d['quantity'])
            if n > b['remaining']:
                raise ValueError('В партии недостаточно кейсов')
            m = c.execute('SELECT * FROM markets WHERE id=?', (int(d['market_id']),)).fetchone()
            if not m or not m['cash'] or m['excluded']:
                raise ValueError('Площадка не допускает денежную продажу или исключена')
            received = money(d['received_rub'])
            if received <= 0:
                raise ValueError('Введите фактически полученную сумму после вывода')
            own = int((Decimal(received) * Decimal('0.15')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            c.execute('INSERT INTO sales(batch_id,market_id,quantity,received,own_share,owner_share,created_at) VALUES(?,?,?,?,?,?,?)',
                      (b['id'], m['id'], n, received, own, received-own, now()))
        elif path == '/api/payments':
            b = batch_state(c, int(d['batch_id']))
            amount = money(d['amount_rub'])
            if amount <= 0 or amount > b['balance']:
                raise ValueError('Выплата должна быть положительной и не превышать долг владельцу')
            c.execute('INSERT INTO payments(batch_id,amount,created_at) VALUES(?,?,?)', (b['id'], amount, now()))
        elif path == '/api/markets':
            if not c.execute('SELECT id FROM markets WHERE id=?', (int(d['id']),)).fetchone():
                raise ValueError('Площадка не найдена')
            c.execute('UPDATE markets SET excluded=? WHERE id=?', (flag(d['excluded']), int(d['id'])))
        else:
            raise ValueError('Неизвестное действие')
    return {'ok': True}


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type='application/json; charset=utf-8', headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'same-origin')
        for key,value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def authenticated(self):
        if not PASSWORD_HASH:
            return True
        with connect() as c:
            return auth.authorized(c, self.headers.get('Cookie'))

    def session_cookie(self, token, age=auth.SESSION_AGE):
        return f'caseparser_session={token}; Path=/; Max-Age={age}; HttpOnly; SameSite=Strict' + ('; Secure' if COOKIE_SECURE else '')

    def do_GET(self):
        from urllib.parse import parse_qs
        u = urlparse(self.path)
        try:
            if u.path in ('/login', '/login.js', '/style.css'):
                name = {'/login':'login.html','/login.js':'login.js','/style.css':'style.css'}[u.path]
                mime = {'/login':'text/html','/login.js':'text/javascript','/style.css':'text/css'}[u.path]
                self.send(200,(ROOT/'static'/name).read_bytes(),mime+'; charset=utf-8')
                return
            if not self.authenticated():
                if u.path.startswith('/api/'):
                    self.send(401,b'{"error":"Authentication required"}')
                else:
                    self.send(303,b'',headers={'Location':'/login'})
                return
            if u.path == '/api/state':
                count = quantity(parse_qs(u.query).get('quantity', ['1'])[0])
                self.send(200, json.dumps(snapshot(count), ensure_ascii=False).encode())
            elif u.path in PAGES or u.path == '/app.js':
                file = ROOT / 'static' / ('app.js' if u.path == '/app.js' else 'index.html')
                mime = 'text/javascript' if u.path == '/app.js' else 'text/html'
                self.send(200, file.read_bytes(), mime + '; charset=utf-8')
            else:
                self.send(404, b'{"error":"Not found"}')
        except ValueError as exc:
            self.send(400, json.dumps({'error': str(exc)}, ensure_ascii=False).encode())

    def do_POST(self):
        # Require JSON and same-origin browser requests, including login.
        origin = self.headers.get('Origin')
        if origin and urlparse(origin).netloc != self.headers.get('Host'):
            self.send(403, b'{"error":"Origin rejected"}')
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 2000000 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('Ожидается JSON размером до 2 МБ')
            d = json.loads(self.rfile.read(length))
            if not isinstance(d, dict):
                raise ValueError('Ожидается JSON-объект')
            path = urlparse(self.path).path
            if path == '/api/login':
                if not PASSWORD_HASH:
                    self.send(200,b'{"ok":true}')
                    return
                with connect() as c:
                    token,status = auth.login(c,d.get('password'),PASSWORD_HASH)
                if token:
                    self.send(200,b'{"ok":true}',headers={'Set-Cookie':self.session_cookie(token)})
                else:
                    message = 'Слишком много попыток. Повтори через 15 минут.' if status==429 else 'Неверный пароль'
                    self.send(status,json.dumps({'error':message},ensure_ascii=False).encode())
                return
            if not self.authenticated():
                self.send(401,b'{"error":"Authentication required"}')
                return
            if path == '/api/logout':
                with connect() as c:
                    token = auth.token_from_cookie(self.headers.get('Cookie'))
                    c.execute('DELETE FROM sessions WHERE digest=?',(auth.token_digest(token),))
                self.send(200,b'{"ok":true}',headers={'Set-Cookie':self.session_cookie('',0)})
                return
            if urlparse(self.path).path == '/api/collect':
                if COLLECTOR is None:
                    raise ValueError('Сборщик не запущен')
                COLLECTOR.trigger()
                result = {'ok': True}
            else:
                result = mutate(urlparse(self.path).path, d)
            self.send(200, json.dumps(result).encode())
        except (ValueError, KeyError, TypeError, sqlite3.IntegrityError) as exc:
            self.send(400, json.dumps({'error': str(exc)}, ensure_ascii=False).encode())


if __name__ == '__main__':
    import sys
    sys.modules['app'] = sys.modules[__name__]
    from collect import Collector
    init_db()
    port = int(os.environ.get('PORT', '8000'))
    host = os.environ.get('CASEPARSER_HOST','127.0.0.1')
    cert,key = os.environ.get('CASEPARSER_TLS_CERT'),os.environ.get('CASEPARSER_TLS_KEY')
    if bool(cert) != bool(key):
        raise SystemExit('Both TLS certificate and key are required')
    if host not in ('127.0.0.1','localhost','::1') and (not PASSWORD_HASH or not cert or not COOKIE_SECURE):
        raise SystemExit('Public binding requires a password hash, TLS and secure cookies')
    server = ThreadingHTTPServer((host, port), Handler)
    if cert:
        import ssl
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert,key)
        server.socket = context.wrap_socket(server.socket,server_side=True)
    print(f"Caseparser: {'https' if cert else 'http'}://{host}:{port}", flush=True)
    COLLECTOR = Collector()
    COLLECTOR.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        COLLECTOR.stop()
        server.server_close()
