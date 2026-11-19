"""Owner-review HTTP contract tests using only isolated stores and a mock browser."""
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from studio.kit_visual_review import KitVisualReview
from tests import test_kit_visual_insertions as insertion_fixtures


class Handler:
    def __init__(self,path,body=None,**headers):
        self.path=path;self.server=SimpleNamespace(server_port=8765,token='test-owner-session')
        self.headers={'Host':'127.0.0.1:8765','Cookie':'dj_session=test-owner-session','Sec-Fetch-Site':'same-origin'}
        self.headers.update(headers)
        if body is not None:
            raw=json.dumps(body).encode();self.headers['Content-Length']=str(len(raw));self.headers.setdefault('Origin','http://127.0.0.1:8765')
            self.rfile=io.BytesIO(raw)
        self.response=None
    def pairing_origin(self):return 'https://owner.example' if self.headers.get('Host')=='owner.example' else None
    def device_cookie_valid(self,cookie):return '__Host-dj_device' in cookie and cookie['__Host-dj_device'].value=='paired-owner'
    def send(self,data,status=200,**kwargs):self.response=(status,data,kwargs)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture=insertion_fixtures.InsertionTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups);self.addCleanup(self.fixture.tearDown)
        self.service=self.fixture.service;self.review=KitVisualReview(self.service)
    def get(self,path,**headers):
        h=Handler(path,**headers);self.assertTrue(self.review.route(h,'GET',path));return h.response[:2]
    def post(self,action,data,**headers):
        path='/api/kit-visual-review/'+action;h=Handler(path,data,**headers);self.assertTrue(self.review.route(h,'POST',path));return h.response[:2]
    def packet(self):
        row=self.fixture.prepare();return self.get('/api/kit-visual-review/packet/'+row['id'])[1]
    def data(self,row):return dict(id=row['id'],digest=row['digest'],nonce=row['review_nonce'])
    def test_load_is_read_only_and_previews_are_owned_local_routes(self):
        row=self.packet();self.assertTrue(row['visual_urls'][0].startswith('/api/emails/'+self.fixture.email['id']+'/visuals/'))
        self.assertIsNone(row['approval']);self.assertEqual(self.fixture.browser.writes,[])
    def test_exact_approval_then_separate_execution_and_recovery(self):
        row=self.packet();status,approved=self.post('approve',self.data(row));self.assertEqual(status,200)
        self.assertEqual(approved['approval']['channel'],'authenticated-owner-browser');self.assertEqual(self.fixture.browser.writes,[])
        status,done=self.post('execute',self.data(approved));self.assertEqual(status,200);self.assertEqual(done['state'],'verified_complete')
        self.assertEqual(self.fixture.browser.writes,['upload','insert']);self.assertNotIn('review_nonce',done)
    def test_nonce_is_single_use_and_action_digest_bound(self):
        for change in ('wrong_action','wrong_digest','replay'):
            row=self.packet();data=self.data(row)
            if change=='wrong_action':self.assertEqual(self.post('execute',data)[0],400)
            elif change=='wrong_digest':data['digest']='bad';self.assertEqual(self.post('approve',data)[0],400)
            else:self.assertEqual(self.post('approve',data)[0],200)
            self.assertEqual(self.post('approve',self.data(row))[0],400)
        self.assertEqual(self.fixture.browser.writes,[])
    def test_token_callers_cross_origin_and_missing_cookie_cannot_approve(self):
        cases=[{'X-DJ-Token':'test-owner-session'},{'Cookie':''},{'Origin':'https://evil.example'},
               {'Sec-Fetch-Site':'cross-site'},{'Sec-Fetch-Site':'none'},{'Host':'evil.example'},
               {'Cookie':'dj_session=another-session'}]
        for headers in cases:
            with self.subTest(headers=headers):self.assertEqual(self.post('approve',self.data(self.packet()),**headers)[0],403)
        self.assertEqual(self.fixture.browser.writes,[])
    def test_paired_owner_session_cannot_use_local_session_nonce(self):
        row=self.packet();headers={'Host':'owner.example','Cookie':'__Host-dj_device=paired-owner','Origin':'https://owner.example'}
        self.assertEqual(self.post('approve',self.data(row),**headers)[0],400)
        status,row=self.get('/api/kit-visual-review/packet/'+row['id'],**headers);self.assertEqual(status,200)
        self.assertEqual(self.post('approve',self.data(row),**headers)[0],200)
    def test_expired_nonce_and_unexpected_identity_fields_rejected(self):
        row=self.packet();self.review.nonces[row['review_nonce']]['expires']=0
        self.assertEqual(self.post('approve',self.data(row))[0],400)
        row=self.packet();self.assertEqual(self.post('approve',dict(self.data(row),actor_id='owner'))[0],400)
    def test_revision_conflict_still_blocks_after_owner_click(self):
        row=self.packet();p=self.fixture.fresh();self.fixture.emails.save(p['id'],dict(subject='New subject'),p['revision'])
        self.assertEqual(self.post('approve',self.data(row))[0],409);self.assertEqual(self.fixture.browser.writes,[])
    def test_unavailable_browser_never_uploads_or_silently_succeeds(self):
        row=self.packet();self.fixture.browser.available=False
        status,result=self.post('approve',self.data(row));self.assertEqual(status,400);self.assertIn('disabled',result['error'])
        self.assertEqual(self.fixture.browser.writes,[])
    def test_static_page_and_assets_have_no_implicit_worker_calls(self):
        with patch.object(self.fixture.browser,'inspect',side_effect=AssertionError('No automatic browser read')):
            for path in ('/kit-visuals','/kit-visual-review.js','/kit-visual-review.css'):
                status,data=self.get(path);self.assertEqual(status,200);self.assertIsInstance(data,bytes)
        h=Handler('/api/emails');self.assertFalse(self.review.route(h,'GET','/api/emails'))
    def test_query_tokens_rejected_and_unknown_action_has_no_write(self):
        h=Handler('/kit-visuals?token=test-owner-session');self.review.route(h,'GET','/kit-visuals');self.assertEqual(h.response[0],403)
        self.assertEqual(self.post('send',self.data(self.packet()))[0],400);self.assertEqual(self.fixture.browser.writes,[])


if __name__=='__main__':unittest.main()
