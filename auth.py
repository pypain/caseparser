"""Password authentication. Store a salted hash, never the plaintext password."""
import getpass
import hashlib
import hmac
import secrets
import time
from http.cookies import SimpleCookie

ITERATIONS = 600000
SESSION_AGE = 12 * 3600


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), ITERATIONS).hex()
    return f'pbkdf2_sha256${ITERATIONS}${salt}${digest}'


def verify(password, encoded):
    try:
        algorithm, rounds, salt, digest = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or not 100000 <= int(rounds) <= 2000000:
            return False
        result = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(result, digest)
    except (ValueError, AttributeError):
        return False


def token_from_cookie(header):
    try:
        cookie = SimpleCookie(header or '')
        return cookie['caseparser_session'].value if 'caseparser_session' in cookie else ''
    except Exception:
        return ''


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def authorized(c, header):
    token = token_from_cookie(header)
    if not token:
        return False
    return c.execute('SELECT 1 FROM sessions WHERE digest=? AND expires>?', (token_digest(token), time.time())).fetchone() is not None


def login(c, password, encoded):
    stamp = time.time()
    c.execute('BEGIN IMMEDIATE')
    c.execute('DELETE FROM login_failures WHERE created_at<?', (stamp-900,))
    c.execute('DELETE FROM sessions WHERE expires<?', (stamp,))
    if c.execute('SELECT COUNT(*) FROM login_failures').fetchone()[0] >= 5:
        return None, 429
    if not isinstance(password, str) or len(password) > 1024 or not verify(password, encoded):
        c.execute('INSERT INTO login_failures(created_at) VALUES(?)', (stamp,))
        return None, 401
    token = secrets.token_urlsafe(32)
    c.execute('INSERT INTO sessions(digest,expires) VALUES(?,?)', (token_digest(token), stamp+SESSION_AGE))
    c.execute('DELETE FROM login_failures')
    return token, 200


if __name__ == '__main__':
    password = getpass.getpass('Пароль приложения: ')
    if not password:
        raise SystemExit('Пароль не может быть пустым')
    print(password_hash(password))
