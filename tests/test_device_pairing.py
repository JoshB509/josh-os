"""Offline pairing tests. Only a minimal HTTP handler runs; no app services/agents."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from http.client import HTTPConnection
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from studio.pairing import Pairing, PairingError, RateLimited, TTL, SESSION_TTL, ISSUE_LIMIT, POLL_LIMIT, main
from studio.phone import PhoneAccess
from studio.server import Handler

ORIGIN = 'https://pairing.test'


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000.0
        self.master = secrets.token_urlsafe(32)
        self.pairing = Pairing(self.temp.name, self.master, clock=lambda:self.now)

    def test_code_is_not_bearer_wrong_secret_guessed_code_and_origin(self):
        request, secret = self.pairing.issue(ORIGIN)
        self.pairing.approve(request['code'])
        for code, supplied, origin in ((request['code'], '', ORIGIN),
                (request['code'], secrets.token_urlsafe(32), ORIGIN),
                ('FFFF-FFFF-FFFF', secret, ORIGIN), (request['code'], secret, 'https://other.test')):
            self.assertEqual(self.pairing.poll(code, supplied, origin), ('unavailable', None))
        self.assertEqual(self.pairing.poll(request['code'], secret, ORIGIN)[0], 'paired')

    def test_expired_and_replay(self):
        request, secret = self.pairing.issue(ORIGIN)
        self.now += TTL
        with self.assertRaises(PairingError):self.pairing.approve(request['code'])
        self.assertEqual(self.pairing.poll(request['code'],secret,ORIGIN),('unavailable',None))
        request, secret = self.pairing.issue(ORIGIN)
        self.pairing.approve(request['code'])
        self.now += TTL
        self.assertEqual(self.pairing.poll(request['code'],secret,ORIGIN),('unavailable',None))
        request, secret = self.pairing.issue(ORIGIN)
        self.pairing.approve(request['code'])
        state, session = self.pairing.poll(request['code'],secret,ORIGIN)
        self.assertEqual(state,'paired')
        self.assertTrue(self.pairing.valid_session(session,ORIGIN))
        self.assertEqual(self.pairing.poll(request['code'],secret,ORIGIN),('unavailable',None))
        self.assertFalse(self.pairing.valid_session(session,'https://other.test'))
        self.now += SESSION_TTL
        self.assertFalse(self.pairing.valid_session(session,ORIGIN))

    def test_concurrent_consume_across_independent_connections(self):
        request, secret = self.pairing.issue(ORIGIN)
        self.pairing.approve(request['code'])
        barrier = threading.Barrier(8)
        def consume(_):
            pair = Pairing(self.temp.name, clock=lambda:self.now)
            pair.epoch = self.pairing.epoch
            barrier.wait()
            return pair.poll(request['code'],secret,ORIGIN)
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(consume,range(8)))
        self.assertEqual(sum(state=='paired' for state, _ in results),1)
        self.assertEqual(sum(session is not None for _, session in results),1)

    def test_issuance_and_poll_limits_persist_and_storage_is_bounded(self):
        for _ in range(ISSUE_LIMIT):self.pairing.issue(ORIGIN)
        with self.assertRaises(RateLimited):self.pairing.issue(ORIGIN)
        other = Pairing(self.temp.name, clock=lambda:self.now)
        other.epoch = self.pairing.epoch
        with self.assertRaises(RateLimited):other.issue(ORIGIN)
        for _ in range(POLL_LIMIT):self.pairing.poll('FFFF-FFFF-FFFF','',ORIGIN)
        with self.assertRaises(RateLimited):self.pairing.poll('FFFF-FFFF-FFFF','',ORIGIN)
        self.now += 61
        request, secret = self.pairing.issue(ORIGIN)
        self.assertEqual(self.pairing.poll(request['code'],secret,ORIGIN)[0],'pending')
        with self.assertRaises(RateLimited):self.pairing.poll(request['code'],secret,ORIGIN)
        self.now += 2
        self.assertEqual(self.pairing.poll(request['code'],secret,ORIGIN)[0],'pending')
        for _ in range(5):
            self.now += 61
            for _ in range(10):
                try:self.pairing.issue(ORIGIN)
                except RateLimited:pass
        self.assertLessEqual(len(self.pairing.pending()),32)

    def test_cli_exact_approval_safe_output_and_no_secret_storage(self):
        # Real wall clock for this CLI test; it is a separate process entry point.
        pair = Pairing(self.temp.name, self.master)
        request, secret = pair.issue(ORIGIN)
        for args in (['list'], ['approve',request['code']]):
            stream = io.StringIO()
            with redirect_stdout(stream):self.assertEqual(main(['--data',self.temp.name]+args),0)
            value = stream.getvalue()
            self.assertNotIn(secret,value)
            self.assertNotIn(self.master,value)
            self.assertNotIn('secret_hash',value)
        with self.assertRaises(PairingError):pair.approve(request['code'])
        state, session = pair.poll(request['code'],secret,ORIGIN)
        self.assertEqual(state,'paired')
        raw = pair.path.read_bytes()
        for value in (secret,session,self.master):self.assertNotIn(value.encode(),raw)
        pair.revoke_all()
        self.assertFalse(pair.valid_session(session,ORIGIN))

    def test_master_rotation_invalidates_devices_and_pending_requests(self):
        request, secret = self.pairing.issue(ORIGIN)
        self.pairing.approve(request['code'])
        _, session = self.pairing.poll(request['code'],secret,ORIGIN)
        self.pairing.issue(ORIGIN)
        new = Pairing(self.temp.name,secrets.token_urlsafe(32),clock=lambda:self.now)
        self.assertFalse(new.valid_session(session,ORIGIN))
        new.issue(ORIGIN)
        self.assertEqual(len(new.pending()),1)

    def test_session_capacity_does_not_consume_approved_request(self):
        request,secret=self.pairing.issue(ORIGIN)
        self.pairing.approve(request['code'])
        with patch('studio.pairing.MAX_SESSIONS',0):
            with self.assertRaises(RateLimited):self.pairing.poll(request['code'],secret,ORIGIN)
        self.now+=2
        self.assertEqual(self.pairing.poll(request['code'],secret,ORIGIN)[0],'paired')


class PairingHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        cls.server.token = secrets.token_urlsafe(32)
        cls.server.phone = PhoneAccess.off()
        cls.server.phone.host = 'pairing.test'
        cls.server.pairing = Pairing(cls.temp.name,cls.server.token)
        cls.server.publishing_nonces = {}
        cls.server.publishing_nonce_lock = threading.Lock()
        cls.server.publishing = Mock()
        cls.server.publishing.get.return_value = dict(id='fixture',packet={'packet_digest':'digest'})
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()

    def setUp(self):
        self.server.pairing.revoke_all()
        with closing(sqlite3.connect(self.server.pairing.path)) as db:
            db.execute('DELETE FROM limits');db.commit()

    def request(self,path='/',method='GET',body=None,headers=None):
        merged={'Host':'pairing.test'}
        if body is not None:merged.update({'Content-Type':'application/json','Origin':ORIGIN,'Sec-Fetch-Site':'same-origin'})
        merged.update(headers or {})
        merged={k:v for k,v in merged.items() if v is not None}
        conn=HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        try:
            conn.request(method,path,body=json.dumps(body) if body is not None else None,headers=merged)
            r=conn.getresponse()
            return r.status, r.getheaders(), r.read()
        finally:conn.close()

    def issue(self):
        status, headers, body=self.request('/api/pairing/request','POST',{})
        self.assertEqual(status,200)
        cookie=next(v.split(';')[0] for k,v in headers if k.lower()=='set-cookie')
        return json.loads(body)['code'], cookie

    def paired(self):
        code,cookie=self.issue()
        self.server.pairing.approve(code)
        status,headers,body=self.request('/api/pairing/poll','POST',{'code':code},{'Cookie':cookie})
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body),{'state':'paired'})
        session=next(v for k,v in headers if k.lower()=='set-cookie' and v.startswith('__Host-dj_device='))
        for flag in ('Secure','HttpOnly','SameSite=Strict','Path=/'):self.assertIn(flag,session)
        self.assertNotIn('Domain=',session)
        return session.split(';')[0]

    def test_bootstrap_is_deliberate_and_every_other_resource_denied(self):
        status,headers,body=self.request()
        self.assertEqual(status,200);self.assertIn(b'Pair this browser',body)
        self.assertFalse(any(k.lower()=='set-cookie' for k,v in headers))
        self.assertEqual(self.server.pairing.pending(),[])
        self.assertEqual(self.request('/pairing.js')[0],200)
        for path in ('/api/state','/api/phone','/app.js','/admin','/publishing'):
            self.assertEqual(self.request(path)[0],403)
        self.assertEqual(self.request(headers={'Host':'evil.test'})[0],403)

    def test_csrf_host_origin_and_methods(self):
        bad_headers=({'Origin':'https://evil.test'}, {'Origin':'null'}, {'Origin':None},
                     {'Host':'evil.test'}, {'Host':'127.0.0.1:'+str(self.server.server_port)},
                     {'Sec-Fetch-Site':'cross-site'}, {'Sec-Fetch-Site':'same-site'},
                     {'Sec-Fetch-Site':None}, {'Content-Type':'text/plain'})
        for headers in bad_headers:
            self.assertEqual(self.request('/api/pairing/request','POST',{},headers)[0],403)
        self.assertEqual(self.request('/api/pairing/request')[0],404)
        self.assertEqual(self.request('/api/pairing/request','POST',{'padding':'a'*600})[0],400)
        self.assertEqual(self.request(headers={'Origin':'https://evil.test'})[0],403)
        self.assertEqual(self.server.pairing.pending(),[])

    def test_cross_site_landing_allows_only_top_level_navigation(self):
        navigation={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
        for site in ('cross-site','same-site'):
            status,headers,body=self.request(headers=dict(navigation,**{'Sec-Fetch-Site':site}))
            self.assertEqual(status,200)
            self.assertIn(b'Pair this browser',body)
            self.assertIn("frame-ancestors 'none'",dict(headers)['Content-Security-Policy'])
            self.assertFalse(any(k.lower()=='set-cookie' for k,v in headers))
        for override in ({'Sec-Fetch-Mode':'cors'},{'Sec-Fetch-Mode':None},
                         {'Sec-Fetch-Dest':'iframe'},{'Sec-Fetch-Dest':'empty'},
                         {'Sec-Fetch-Dest':None},{'Origin':'https://evil.test'}, {'Host':'evil.test'}):
            self.assertEqual(self.request(headers=dict(navigation,**override))[0],403)
        self.assertEqual(self.request('/pairing.js',headers=navigation)[0],403)
        for path in ('/api/pairing/request','/api/pairing/poll'):
            self.assertEqual(self.request(path,'POST',{},navigation)[0],403)
            self.assertEqual(self.request(path,'POST',{},dict(navigation,Origin='https://evil.test'))[0],403)
        self.assertEqual(self.server.pairing.pending(),[])

    def test_no_remote_approval_or_listing_even_with_master_or_device(self):
        device=self.paired()
        for auth in ({},{'X-DJ-Token':self.server.token},{'Cookie':device}):
            for path in ('/api/pairing/approve','/api/pairing/list','/api/pairing/revoke-all'):
                self.assertEqual(self.request(path,'POST',{},auth)[0],404)
                self.assertEqual(self.request(path,headers=auth)[0],404)

    def test_code_only_poller_cannot_steal_session_and_replay_fails(self):
        code,cookie=self.issue()
        self.server.pairing.approve(code)
        status,headers,body=self.request('/api/pairing/poll','POST',{'code':code})
        self.assertEqual(json.loads(body),{'state':'unavailable'})
        self.assertFalse(any(k.lower()=='set-cookie' for k,v in headers))
        status,headers,body=self.request('/api/pairing/poll','POST',{'code':code},{'Cookie':cookie})
        self.assertEqual(json.loads(body),{'state':'paired'})
        status,headers,body=self.request('/api/pairing/poll','POST',{'code':code},{'Cookie':cookie})
        self.assertEqual(json.loads(body),{'state':'unavailable'})
        self.assertFalse(any(k.lower()=='set-cookie' for k,v in headers))

    def test_paired_pages_and_phone_never_expose_or_set_master(self):
        device=self.paired()
        for path in ('/','/admin','/tasks','/lessons','/publishing','/api/phone','/app.js'):
            status,headers,body=self.request(path,headers={'Cookie':device})
            self.assertEqual(status,200)
            self.assertNotIn(self.server.token.encode(),body)
            self.assertNotIn(device.split('=',1)[1].encode(),body)
            self.assertFalse(any(k.lower()=='set-cookie' for k,v in headers))
        for headers in ({'Origin':'https://evil.test'},{'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(self.request('/api/phone',headers=dict(headers,Cookie=device))[0],403)
        self.assertEqual(self.request('/api/phone','POST',{}, {'Cookie':device,'Origin':None})[0],403)

    def test_publishing_review_gate_and_legacy_auth(self):
        local='127.0.0.1:'+str(self.server.server_port)
        for headers in ({'X-DJ-Token':self.server.token}, {'Cookie':'dj_session='+self.server.token}):
            self.assertEqual(self.request('/tasks',headers=dict(headers,Host=local))[0],200)
            self.assertEqual(self.request('/tasks',headers=dict(headers,Host='evil.test'))[0],403)
            self.assertEqual(self.request('/tasks',headers=dict(headers,Origin='https://evil.test'))[0],403)
        self.assertEqual(self.request('/tasks?token='+self.server.token,headers={'Host':local})[0],200)
        device=self.paired()
        path='/api/publishing/jobs/fixture'
        self.assertNotIn('review_nonce',json.loads(self.request(path,headers={'Cookie':device})[2]))
        self.assertIn('review_nonce',json.loads(self.request(path,headers={'Cookie':device,'Sec-Fetch-Site':'same-origin'})[2]))
        self.assertNotIn('review_nonce',json.loads(self.request(path,headers={'X-DJ-Token':self.server.token,'Sec-Fetch-Site':'same-origin'})[2]))
        review=json.loads(self.request(path,headers={'Cookie':device,'Sec-Fetch-Site':'same-origin'})[2])
        data=dict(job_id='fixture',packet_digest='digest',review_nonce=review['review_nonce'])
        self.server.publishing.approve_and_start.reset_mock()
        self.server.publishing.approve_and_start.return_value={'ok':True}
        self.assertEqual(self.request('/api/publishing/approve','POST',data,{'Cookie':device,'Origin':'https://evil.test'})[0],403)
        self.server.publishing.approve_and_start.assert_not_called()
        self.assertEqual(self.request('/api/publishing/approve','POST',data,{'Cookie':device})[0],200)
        self.server.publishing.approve_and_start.assert_called_once_with('fixture','digest')
        self.assertEqual(self.request('/api/publishing/approve','POST',data,{'Cookie':device})[0],400)

    def test_http_rate_limit_and_storage_failure_fail_closed(self):
        for _ in range(ISSUE_LIMIT):self.issue()
        self.assertEqual(self.request('/api/pairing/request','POST',{})[0],429)
        real=self.server.pairing
        self.server.pairing=Mock()
        self.server.pairing.issue.side_effect=sqlite3.OperationalError('private detail')
        try:
            status,headers,body=self.request('/api/pairing/request','POST',{})
            self.assertEqual(status,503);self.assertNotIn(b'private detail',body)
        finally:self.server.pairing=real


if __name__=='__main__':unittest.main()
