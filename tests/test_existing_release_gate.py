"""Exercise the unchanged browser-review methods without starting app workers."""
import ast
import secrets
import threading
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace


class ExistingReviewGateTests(unittest.TestCase):
    def setUp(self):
        source=Path(__file__).parents[1]/'studio/server.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='Handler')
        methods=[x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name in
                 ('publishing_browser','publishing_review','publishing_consume')]
        ns={'secrets':secrets,'SimpleCookie':SimpleCookie,
            'PhoneAccess':SimpleNamespace(off=lambda:SimpleNamespace(origins=lambda:()))}
        partial=ast.ClassDef(name='Handler',bases=[],keywords=[],body=methods,decorator_list=[])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[partial],type_ignores=[])),str(source),'exec'),ns)
        self.h=ns['Handler']()
        self.h.server=SimpleNamespace(token='synthetic-local-token',server_port=12345,phone=None,
            pairing=None,publishing_nonces={},publishing_nonce_lock=threading.Lock())
        self.h.headers={'Cookie':'dj_session=synthetic-local-token','Sec-Fetch-Site':'same-origin',
                        'Origin':'http://127.0.0.1:12345'}
        self.job={'id':'synthetic-job','packet':{'packet_digest':'synthetic-digest'},'state':'needs_review'}

    def approval(self):
        reviewed=self.h.publishing_review(self.job)
        return dict(job_id=self.job['id'],packet_digest=self.job['packet']['packet_digest'],review_nonce=reviewed['review_nonce'])

    def test_existing_owner_ui_still_accepts_once(self):
        args=self.approval();self.h.publishing_consume(args)
        with self.assertRaises(ValueError):self.h.publishing_consume(args)

    def test_agent_token_no_nonce_or_approval(self):
        self.h.headers={'X-DJ-Token':'synthetic-local-token'}
        self.assertNotIn('review_nonce',self.h.publishing_review(self.job))
        with self.assertRaises(ValueError):self.h.publishing_consume(dict(job_id='synthetic-job'))

    def test_cross_origin_and_wrong_binding_fail(self):
        args=self.approval();self.h.headers['Origin']='https://elsewhere.example'
        with self.assertRaises(ValueError):self.h.publishing_consume(args)
        self.h.headers['Origin']='http://127.0.0.1:12345'
        args['packet_digest']='changed'
        with self.assertRaises(ValueError):self.h.publishing_consume(args)


if __name__=='__main__':unittest.main()
