import json
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
import app
import auth


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hash=auth.password_hash('test-only-password')

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old=(app.DB,app.PASSWORD_HASH,app.COOKIE_SECURE)
        app.DB=Path(self.tmp.name)/'auth.sqlite3';app.PASSWORD_HASH=self.hash;app.COOKIE_SECURE=True
        app.init_db()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        app.DB,app.PASSWORD_HASH,app.COOKIE_SECURE=self.old
        self.tmp.cleanup()

    def post(self,path,data,cookie=None,origin=None):
        headers={'Content-Type':'application/json'}
        if cookie:headers['Cookie']=cookie
        if origin:headers['Origin']=origin
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode(),headers=headers)
        try:return urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:return e

    def test_password_hash(self):
        self.assertNotIn('test-only-password',self.hash)
        self.assertTrue(auth.verify('test-only-password',self.hash))
        self.assertFalse(auth.verify('wrong',self.hash))

    def test_unauthenticated_api_and_pages(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.base+'/api/state')
        self.assertEqual(caught.exception.code,401)
        caught.exception.close()
        with urllib.request.urlopen(self.base+'/inventory') as r:
            self.assertTrue(r.url.endswith('/login'))
            self.assertIn('Войти',r.read().decode())
        r=self.post('/api/batches',{'owner':'x','quantity':1});self.assertEqual(r.code,401);r.close()

    def test_login_session_logout(self):
        with self.post('/api/login',{'password':'test-only-password'}) as r:
            self.assertEqual(r.status,200);cookie=r.headers['Set-Cookie']
            for flag in ('HttpOnly','Secure','SameSite=Strict'):
                self.assertIn(flag,cookie)
        session=cookie.split(';')[0]
        req=urllib.request.Request(self.base+'/api/state',headers={'Cookie':session})
        with urllib.request.urlopen(req) as r:self.assertIn('markets',json.load(r))
        with app.connect() as c:
            digest=c.execute('SELECT digest FROM sessions').fetchone()[0]
            self.assertNotIn(session.split('=')[1],digest)
        with self.post('/api/logout',{},session) as r:self.assertIn('Max-Age=0',r.headers['Set-Cookie'])
        with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(req)
        caught.exception.close()

    def test_rate_limit(self):
        for _ in range(5):
            r=self.post('/api/login',{'password':'wrong'});self.assertEqual(r.code,401);r.close()
        r=self.post('/api/login',{'password':'test-only-password'});self.assertEqual(r.code,429);r.close()

    def test_expired_session(self):
        with app.connect() as c:
            c.execute('INSERT INTO sessions VALUES(?,?)',(auth.token_digest('expired'),time.time()-1))
            self.assertFalse(auth.authorized(c,'caseparser_session=expired'))

    def test_cross_origin_login_rejected(self):
        r=self.post('/api/login',{'password':'test-only-password'},origin='https://evil.example')
        self.assertEqual(r.code,403);r.close()


if __name__=='__main__':unittest.main()
