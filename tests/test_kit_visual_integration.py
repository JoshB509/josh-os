"""Exercise the actual server dispatcher against isolated owner-review fixtures."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.parse

from studio.kit_visual_host import wire
from tests.test_kit_visual_review import Handler
from tests import test_kit_visual_insertions as insertion_fixtures


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=insertion_fixtures.InsertionTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups);self.addCleanup(self.fixture.tearDown)
        path=Path(os.environ.get('KIT_VISUAL_SERVER_SOURCE',str(Path(__file__).parents[1]/'studio/server.py')))
        tree=ast.parse(path.read_text(encoding='utf-8'))
        handler=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Handler')
        route=next(n for n in handler.body if isinstance(n,ast.FunctionDef) and n.name=='route')
        namespace={'urllib':urllib,'__name__':'studio.server','__package__':'studio'};exec(compile(ast.Module(body=[route],type_ignores=[]),str(path),'exec'),namespace)
        self.route=namespace['route']
        fake=SimpleNamespace(root=self.fixture.drafts.root,store=self.fixture.service.store,emails=self.fixture.emails,kit_drafts=self.fixture.drafts)
        with patch('studio.kit_chrome_worker.KitChromeWorker.__init__',side_effect=AssertionError('No browser worker during wiring')):
            self.review=wire(fake)
    def handler(self,path,authorized=True):
        h=Handler(path);h.server.kit_visual_review=self.review;h.authorized=lambda:authorized
        h.server.people=SimpleNamespace(all=lambda:[{'id':'existing-route'}])
        return h
    def test_authorized_owner_page_is_dispatched_without_worker_or_network(self):
        h=self.handler('/kit-visuals');self.route(h,'GET')
        self.assertEqual(h.response[0],200);self.assertIn(b'Review visuals',h.response[1])
    def test_existing_authorization_runs_before_review_dispatch(self):
        h=self.handler('/kit-visuals',False)
        with patch.object(self.review,'route',side_effect=AssertionError('No review before existing auth')):self.route(h,'GET')
        self.assertEqual(h.response[0],403)
    def test_unrelated_route_and_pairing_dispatch_preserved(self):
        h=self.handler('/api/people');del h.server.kit_visual_review;self.route(h,'GET');self.assertEqual(h.response[1],[{'id':'existing-route'}])
        h=self.handler('/api/pairing/status',False);h.pairing_route=lambda method,path:h.send({'pairing':'existing-route'})
        with patch.object(self.review,'route',side_effect=AssertionError('Pairing is separate')):self.route(h,'GET')
        self.assertEqual(h.response[1],{'pairing':'existing-route'})
    def test_review_status_is_read_only_and_insertion_remains_disabled(self):
        h=self.handler('/api/kit-visual-review/email/'+self.fixture.email['id']);self.route(h,'GET')
        self.assertEqual(h.response[0],200);self.assertFalse(h.response[1]['readiness']['available'])
        self.assertTrue(h.response[1]['readiness']['can_inspect']);self.assertEqual(self.fixture.browser.writes,[])


if __name__=='__main__':unittest.main()
