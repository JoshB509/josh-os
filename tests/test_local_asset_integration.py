"""Actual chat/HTTP/UI wiring against isolated data; never start Server or Claude."""
import ast
import io
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from http.cookies import SimpleCookie
import urllib.parse

from PIL import Image
from studio.agent import Agents
from studio.chat_capabilities import matrix
from studio.emails import Emails
from studio.local_asset_chat import LocalAssetChat, ROLES
from studio.local_asset_spec import RENDERER_VERSION
from studio.phone import PhoneAccess
from studio.store import Store, Conflict
from studio.team import Team

ROOT = Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'Data')
        self.agents = Agents(self.store, self.root, Team(self.store))
        self.chat = LocalAssetChat(self.store.root / 'local-assets')
        self.agents.local_asset_chat = self.chat
        self.agents.training = lambda owner: ('', [])
        self.request = {'visuals': [dict(alt='A custom gold card.', width=128, height=128,
            layers=[dict(kind='rect', x=8, y=8, width=100, height=100, fill='#F5C21B')])]}
        self.answer = dict(reply='Model claimed completion.', create_local_assets=self.request)
        self.calls = []
        def run(system, prompt, schema, *args, **kwargs):
            self.calls.append(dict(schema=schema, kwargs=kwargs, system=system, prompt=prompt))
            return self.answer['reply'], self.answer
        self.agents.run = run
        self.render_patch = patch.object(self.chat.assets, '_render', side_effect=self.render)
        self.render_patch.start()
        self.addCleanup(self.render_patch.stop)

    def render(self, folder):
        job = json.loads((folder / 'job.json').read_bytes())
        for i, scene in enumerate(job['visuals']):
            data = io.BytesIO()
            Image.new('RGBA', (scene['width'], scene['height']), '#F5C21B').save(data, format='PNG')
            (folder / f'render_{i:02d}.png').write_bytes(data.getvalue())
        (folder / 'render_metadata.json').write_text(json.dumps({'renderer': RENDERER_VERSION, 'fonts': {}}))

    def ask(self, owner='sam', message='Create a standalone custom visual.', **extra):
        return self.agents.chat(dict(agent_id=owner, message=message, **extra), [])

    def test_all_six_roles_have_schema_and_host_owned_local_results_without_new_tools(self):
        for owner in ROLES:
            with self.subTest(owner=owner):
                result = self.ask(owner)
                row = next(x for x in result['reply']['saved'] if x['kind'] == 'local_asset')
                self.assertEqual((row['owner'], row['action']), (owner, 'added'))
                self.assertIn('create_local_assets', self.calls[-1]['schema']['properties'])
                self.assertIsNone(self.calls[-1]['kwargs']['tools'])
                self.assertEqual(self.calls[-1]['kwargs']['read_only'], owner == 'anouk')
                self.assertNotIn('files', result['reply'])
                self.assertNotIn('send_files', result['reply'])
                self.assertEqual(self.store.projects(), [])
                self.assertIn('visual.create_local', matrix()['agents'][owner]['local_actions'])

    def test_nonrequest_and_proactive_model_payloads_do_not_render(self):
        self.agents.feedback = SimpleNamespace(pending=lambda owner: [{'id': 'edit-1'}],
            describe=lambda edits: 'A local text edit.', mark=lambda ids: None)
        for owner in ROLES:
            for proactive in (False, True):
                with patch.object(self.chat.assets, 'create') as create:
                    result = self.agents.chat(dict(agent_id=owner, message='Hello'), [], proactive=proactive)
                create.assert_not_called()
                self.assertNotIn('create_local_assets', self.calls[-1]['schema']['properties'])
                self.assertFalse(any(x.get('action') == 'added' for x in result['reply'].get('saved', [])))

    def test_adversarial_payload_cannot_override_nonrequest_for_any_role(self):
        from tests.test_local_asset_chat import DENIED_MESSAGES
        for owner in ROLES:
            for message in DENIED_MESSAGES:
                with self.subTest(owner=owner, message=message):
                    with patch.object(self.chat.assets, 'create') as create:
                        result = self.ask(owner, message)
                    create.assert_not_called()
                    self.assertNotIn('create_local_assets', self.calls[-1]['schema']['properties'])
                    self.assertFalse(any(x.get('action') == 'added' for x in result['reply'].get('saved', [])))
                    self.assertIn('No local visual was created.', result['reply']['text'])
                    self.assertFalse(self.chat.assets.root.exists())

    def test_common_visual_requests_render_for_all_six_roles(self):
        for owner in ROLES:
            for message in ('Create a poster.', 'Make me a banner.'):
                with self.subTest(owner=owner, message=message):
                    result = self.ask(owner, message)
                    self.assertIn('create_local_assets', self.calls[-1]['schema']['properties'])
                    self.assertEqual(result['reply']['saved'][0]['action'], 'added')

    def test_receipt_preserves_recommendations_and_earlier_verified_notice(self):
        explanation = '1. Target returning learners.\n2. Demonstrate the outcome.\n3. Offer a clear next step.'
        # Sam's packet is built from a named video (19 Nov 2026), so the fixture names one and stubs the builder.
        from studio.chat_release_actions import ChatReleaseActions
        video = self.store.create('Fixture video')
        self.agents.publishing = SimpleNamespace(prepare=lambda request: dict(id='fixture-review', state='needs_review'))
        self.agents.chat_release_actions = ChatReleaseActions(SimpleNamespace(store=self.store))
        self.answer.update(reply=explanation, prepare_release=dict(project_id=video['id'], mode='unlisted', made_for_kids=False,
                                                                   contains_paid_promotion=False, contains_synthetic_media=False))
        built = patch('studio.project_publishing.prepare_for_sam', return_value=dict(id='fixture-review', state='needs_review'))
        built.start()
        self.addCleanup(built.stop)
        for invalid in (False, True):
            with self.subTest(invalid=invalid):
                self.answer['create_local_assets'] = dict(self.request, owner='max') if invalid else self.request
                text = self.ask(message=f'Create a visual for {video["id"]} and explain the three campaign recommendations.')['reply']['text']
                self.assertIn(explanation, text)
                self.assertIn('Release prepared for your review.', text)
                self.assertIn('Nothing has been uploaded yet.', text)
                self.assertIn('Local visual creation failed:' if invalid else 'Created 1 local visual.', text)

    def test_receipt_survives_existing_email_failure_sanitization(self):
        self.agents.emails = Emails(self.store, self.root)
        failure = dict(kind='email', action='failed', title='Fixture draft', error='Stale revision')
        with patch.object(self.agents, 'email_answer', return_value=([failure], None)):
            text = self.ask('laia')['reply']['text']
        self.assertIn('The email changes were not all saved.', text)
        self.assertIn('Stale revision', text)
        self.assertIn('Created 1 local visual.', text)
        self.assertNotIn('Model claimed completion.', text)

    def test_empty_reply_and_existing_receipt_do_not_duplicate_receipt(self):
        self.answer['reply'] = ''
        text = self.ask()['reply']['text']
        self.assertTrue(text.startswith('Created 1 local visual.'))
        self.answer['reply'] = text
        again = self.ask()['reply']['text']
        self.assertEqual(again.count('Created 1 local visual.'), 1)

    def test_model_cannot_spoof_owner_and_discord_does_not_auto_attach(self):
        self.answer = dict(reply='Done', owner='max', create_local_assets=dict(self.request, owner='max'))
        result = self.ask('sam')
        self.assertEqual(result['reply']['saved'][0]['action'], 'failed')
        self.assertFalse(self.chat.assets.root.exists())
        self.answer = dict(reply='Done', create_local_assets=self.request)
        with patch.object(self.agents, 'files_to_send', wraps=self.agents.files_to_send) as files:
            result = self.ask('sam', via='discord', discord={})
        files.assert_called_once_with(None)
        self.assertNotIn('files', result['reply'])
        self.assertEqual(result['reply']['saved'][0]['owner'], 'sam')

    def test_show_email_keeps_creation_read_only_and_laia_guidance_remains(self):
        self.agents.emails = Emails(self.store, self.root)
        self.answer = dict(reply='Show it', show_email='missing', create_local_assets=self.request)
        with patch.object(self.chat.assets, 'create') as create:
            result = self.ask('laia', 'Show that email and create a visual.')
        create.assert_not_called()
        self.assertNotIn('local_asset', [x.get('kind') for x in result['reply'].get('saved', [])])
        self.assertIn('Up to 16 visuals', self.calls[-1]['system'])
        self.assertIn('standalone SVG', self.calls[-1]['system'])
        self.assertIn('email_visuals', self.calls[-1]['schema']['properties'])

    def handler(self, path, headers):
        source = ROOT / 'studio/server.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Handler')
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in ('route', 'authorized', 'publishing_browser')]
        ns = dict(urllib=urllib, json=json, secrets=secrets, SimpleCookie=SimpleCookie, PhoneAccess=PhoneAccess,
                  Path=Path, Conflict=Conflict, NotAllowed=PermissionError, __name__='studio.server', __package__='studio')
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), ns)
        responses = []
        h = SimpleNamespace(path=path, headers=headers, command='GET',
            server=SimpleNamespace(server_port=12345, token='test-only-token', phone=PhoneAccess.off(),
                                   local_asset_chat=self.chat),
            pairing_origin=lambda: None, device_cookie_valid=lambda cookie: False,
            send=lambda data, status=200, **kw: responses.append((data, status, kw)))
        h.authorized = lambda: ns['authorized'](h)
        h.publishing_browser = lambda write=False: ns['publishing_browser'](h, write)
        return h, responses, ns['route']

    def test_actual_http_auth_runs_first_and_token_only_cannot_fetch_preview(self):
        row = self.ask()['reply']['saved'][0]
        path = '/api/local-assets/sam/' + row['id'] + '.png'
        base = {'Host': '127.0.0.1:12345', 'Sec-Fetch-Site': 'same-origin'}
        for extra, expected in [({}, 403), ({'X-DJ-Token': 'test-only-token'}, 403),
                ({'Cookie': 'dj_session=test-only-token'}, 200),
                ({'Cookie': 'dj_session=test-only-token', 'Origin': 'https://outside.test'}, 403)]:
            h, response, route = self.handler(path, dict(base, **extra))
            route(h, 'GET')
            self.assertEqual(response[-1][1], expected)
            if expected == 200:
                self.assertEqual(response[-1][0], self.chat.read_png('sam', row['id']))
                self.assertEqual(response[-1][2]['cache'], 'private, no-store')
        h, response, route = self.handler(path.replace('/sam/', '/max/'), dict(base, Cookie='dj_session=test-only-token'))
        route(h, 'GET')
        self.assertEqual(response[-1][1], 403)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for the isolated JS helper check')
    def test_web_preview_escapes_metadata_and_uses_only_local_verified_routes(self):
        source = (ROOT / 'studio/web/team.js').read_text(encoding='utf-8')
        helper = source[source.index('function roomLocalAsset('):source.index('function roomSavedItem(')]
        script = "const esc=x=>String(x).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n" + helper
        script += "\nconst x={owner:'sam',id:'a'.repeat(32)+'_'+'b'.repeat(32),action:'added',title:'<unsafe>',alt:'\" onerror=alert(1)'};console.log(JSON.stringify({html:roomLocalAsset(x),bad:roomLocalAsset({...x,id:'../secret'})}));"
        result = subprocess.run([shutil.which('node'), '-'], input=script, text=True, capture_output=True, timeout=10, check=True)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered['bad'], '')
        self.assertIn('/api/local-assets/sam/', rendered['html'])
        self.assertIn('download=', rendered['html'])
        self.assertIn('&lt;unsafe&gt;', rendered['html'])
        self.assertNotIn('alt="" onerror=', rendered['html'])
        self.assertNotIn('token=', rendered['html'])
        self.assertIn("if(x.kind==='local_asset')return roomLocalAsset(x);", source)


if __name__ == '__main__':
    unittest.main()
