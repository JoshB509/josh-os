"""Offline review gate tests. No server, credentials, Google or model calls."""
import io
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from studio.server import Handler


class PublishingReviewTests(unittest.TestCase):
    def handler(self, browser=True):
        h=Handler.__new__(Handler)
        h.server=SimpleNamespace(token='test-session',server_port=12345,phone=None,
            publishing_nonces={},publishing_nonce_lock=threading.Lock(),publishing=Mock())
        h.server.publishing.get.return_value=self.job()
        h.headers={'Host':'127.0.0.1:12345'}
        if browser:h.headers.update({'Cookie':'dj_session=test-session','Sec-Fetch-Site':'same-origin','Origin':'http://127.0.0.1:12345'})
        h.path='/api/publishing/jobs/fixture'
        return h
    def job(self):return {'id':'fixture','packet':{'packet_digest':'digest'},'state':'needs_review'}
    def approval(self,h):
        result=h.publishing_review(self.job())
        return {'job_id':'fixture','packet_digest':'digest','review_nonce':result['review_nonce']}
    def test_browser_gate(self):
        self.assertTrue(self.handler().publishing_browser(True))
    def test_api_token_is_not_approval(self):
        h=self.handler(False);h.headers['X-DJ-Token']='test-session'
        self.assertNotIn('review_nonce',h.publishing_review(self.job()))
        with self.assertRaises(ValueError):h.publishing_consume({'job_id':'fixture'})
    def test_cross_origin_rejected(self):
        h=self.handler();data=self.approval(h);h.headers['Origin']='https://evil.invalid'
        with self.assertRaises(ValueError):h.publishing_consume(data)
    def test_missing_fetch_site_rejected(self):
        h=self.handler();del h.headers['Sec-Fetch-Site']
        self.assertFalse(h.publishing_browser())
    def test_wrong_cookie_rejected(self):
        h=self.handler();h.headers['Cookie']='dj_session=wrong'
        self.assertFalse(h.publishing_browser())
    def test_nonce_once(self):
        h=self.handler();data=self.approval(h);h.publishing_consume(data)
        with self.assertRaises(ValueError):h.publishing_consume(data)
    def test_nonce_job_binding(self):
        h=self.handler();data=self.approval(h);data['job_id']='other'
        with self.assertRaises(ValueError):h.publishing_consume(data)
    def test_nonce_digest_binding(self):
        h=self.handler();data=self.approval(h);data['packet_digest']='changed'
        with self.assertRaises(ValueError):h.publishing_consume(data)
    def test_expired(self):
        h=self.handler();data=self.approval(h)
        h.server.publishing_nonces[data['review_nonce']]=('fixture','digest',time.monotonic()-1)
        with self.assertRaises(ValueError):h.publishing_consume(data)
    def route(self,h,path,data):
        h.path=path;raw=json.dumps(data).encode();h.headers['Content-Length']=str(len(raw));h.rfile=io.BytesIO(raw);h.send=Mock();h.route('POST')
    def test_approve_calls_only_matching_job(self):
        h=self.handler();data=self.approval(h);self.route(h,'/api/publishing/approve',data)
        h.server.publishing.approve_and_start.assert_called_once_with('fixture','digest')
    def test_no_nonce_no_upload(self):
        h=self.handler();self.route(h,'/api/publishing/approve',{'job_id':'fixture','packet_digest':'digest'})
        h.server.publishing.approve_and_start.assert_not_called()
        self.assertEqual(h.send.call_args.args[1],400)
    def test_prepare_does_not_approve(self):
        h=self.handler();self.route(h,'/api/publishing/prepare',{'title':'Draft'})
        h.server.publishing.prepare.assert_called_once_with({'title':'Draft'})
        h.server.publishing.approve_and_start.assert_not_called()

if __name__=='__main__':unittest.main()
