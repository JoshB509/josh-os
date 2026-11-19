"""Prepared integration seam tests; never instantiate the live Server."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from studio.local_asset_chat import LocalAssetChat, ROLES, requested, route
from studio.local_asset_spec import RENDERER_VERSION


DENIED_MESSAGES = (
    'I do not want a visual.', "I don't need images.",
    'Explain how you create images.', 'Tell me how you render diagrams.',
    'How do you make posters?', 'Can you create images?', 'Could you render diagrams?',
    'Are you able to make a banner?', 'Do you know how to create a chart?',
    'Explain how you create images and render a poster.',
    '"Create a poster."', "'Make me a banner.'", '“Create a visual.”',
    "'Create a poster.\nMake me a banner.'", '‘Create a poster.\nMake me a banner.’',
    '`Create a diagram`', '```text\nCreate a poster.\n```', '> Create a poster.',
    'The customer said "create a poster". Explain their request.',
    'If I asked, would you create a visual?', 'Suppose I said create a poster.',
    'Hypothetically: create a banner.', 'For example. Create a poster.',
    'Imagine creating a visual.', 'Create a visual if I approve.',
    'Create a poster only after I approve.', 'When I ask, create a chart.',
    'I might want a poster.', 'I would create a banner.', 'I want to know how to create images.',
    'Create an email and explain how images work.', 'Create no visual.',
    'Create a poster. Actually, do not create it.', 'I am not asking you to create a poster.',
    'Create a poster? I am only asking about your capabilities.',
)

POSITIVE_MESSAGES = (
    'Create a poster.', 'Make me a banner.', 'Please create a custom visual.',
    'Could you make me a banner?', 'Can you create a diagram for this campaign?',
    'Please can you design a poster?', 'I want a visual.', 'I need a campaign banner.',
    "I'd like you to create a diagram.", 'I would like a picture.',
    'Create a visual and explain the three campaign recommendations.',
    'Explain the three recommendations and create a diagram.',
    'Create a chart; do not send it.', 'Make a poster with no branding.',
    "I don't want branding; create a poster.",
    'Create a poster titled "Imagine a brighter future".', 'Resize this banner.',
    'Give me a new thumbnail.', 'Draw a storyboard.', 'Design two campaign posters.',
)


class LocalAssetChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.chat = LocalAssetChat(Path(self.temp.name) / 'assets')
        self.request = {'visuals': [dict(alt='A gold rectangle.', width=128, height=128,
            layers=[dict(kind='rect', x=10, y=10, width=100, height=100, fill='#F5C21B')])]}
        self.request_message = 'Please create a custom visual, without uploading it.'

    def render(self, folder):
        import io
        from PIL import Image
        job = json.loads((folder / 'job.json').read_bytes())
        for i, s in enumerate(job['visuals']):
            data = io.BytesIO()
            Image.new('RGBA', (s['width'], s['height']), '#F5C21B').save(data, format='PNG')
            (folder / f'render_{i:02d}.png').write_bytes(data.getvalue())
        (folder / 'render_metadata.json').write_text(json.dumps({'renderer': RENDERER_VERSION, 'fonts': {}}))

    def make(self, owner='sam', request=None):
        with patch.object(self.chat.assets, '_render', side_effect=self.render):
            saved, note = self.chat.dispatch(owner, request or self.request, message=self.request_message)
        self.assertEqual(saved[0]['action'], 'added', note)
        return saved[0]

    def test_schema_and_dispatch_require_known_actor_current_request_and_nonproactive(self):
        for owner in ROLES:
            self.assertIsNotNone(self.chat.schema(owner, self.request_message))
            row = self.make(owner)
            self.assertEqual(row['owner'], owner)
            for message, proactive in [('', False), ('What is your role?', False), ('Do not create a visual', False), (self.request_message, True)]:
                self.assertIsNone(self.chat.schema(owner, message, proactive))
                with patch.object(self.chat.assets, 'create') as create:
                    saved, note = self.chat.dispatch(owner, self.request, message=message, proactive=proactive)
                    create.assert_not_called()
                    self.assertEqual(saved[0]['action'], 'failed')
        self.assertIsNone(self.chat.schema('unknown', self.request_message))
        self.assertTrue(requested('Create a chart; do not send it.'))

    def test_positive_creation_grammar_and_nonrequest_examples(self):
        for message in POSITIVE_MESSAGES:
            with self.subTest(message=message):
                self.assertTrue(requested(message))
        for message in DENIED_MESSAGES:
            with self.subTest(message=message):
                self.assertFalse(requested(message))

    def test_only_same_owner_generated_inputs_can_be_reused(self):
        first = self.make('sam')
        request = {'visuals': [dict(alt='Reused card', width=128, height=128,
            layers=[dict(kind='image', asset_id=first['id'], x=0, y=0, width=128, height=128)])]}
        second = self.make('sam', request)
        self.assertNotEqual(second['id'], first['id'])
        with patch.object(self.chat.assets, '_render') as render:
            saved, note = self.chat.dispatch('max', request, message=self.request_message)
        render.assert_not_called()
        self.assertEqual(saved[0]['action'], 'failed')
        self.assertIn('another agent', note)
        with self.assertRaises(PermissionError):
            self.chat.read_png('max', first['id'])

    def test_context_and_saved_result_never_supply_paths_or_send_files(self):
        own, other = self.make('laia'), self.make('sam')
        context = '\n'.join(self.chat.context('laia', [dict(saved=[own, other])]))
        self.assertIn(own['id'], context)
        self.assertNotIn(other['id'], context)
        self.assertNotIn(str(self.chat.assets.root), context)
        self.assertNotIn('files', own)
        self.assertNotIn('send_files', own)

    def test_preview_requires_browser_session_rejects_writes_and_verifies_bytes(self):
        row = self.make()
        replies = []
        h = SimpleNamespace(server=SimpleNamespace(local_asset_chat=self.chat), publishing_browser=lambda: False,
            send=lambda data, status=200, **kw: replies.append((data, status, kw)))
        path = '/api/local-assets/sam/' + row['id'] + '.png'
        self.assertTrue(route(h, 'GET', path))
        self.assertEqual(replies[-1][1], 403)
        h.publishing_browser = lambda: True
        route(h, 'POST', path)
        self.assertEqual(replies[-1][1], 405)
        route(h, 'GET', path)
        self.assertEqual(replies[-1][0], self.chat.read_png('sam', row['id']))
        self.assertEqual(replies[-1][2], {'mime': 'image/png', 'cache': 'private, no-store'})
        route(h, 'GET', path.replace('/sam/', '/max/'))
        self.assertEqual(replies[-1][1], 403)
        route(h, 'GET', '/api/local-assets/sam/..%2Foutside.png')
        self.assertEqual(replies[-1][1], 400)
        folder = self.chat.assets.root / 'batches' / row['reference']['batch_id']
        next(folder.glob('*.png')).write_bytes(b'changed')
        route(h, 'GET', path)
        self.assertEqual(replies[-1][1], 400)
        self.assertFalse(route(h, 'GET', '/api/emails'))

    def test_forged_owner_or_destination_in_creation_fails(self):
        for extra in ({'owner': 'max'}, {'out': 'C:/outside'}, {'send_files': ['x']}):
            saved, note = self.chat.dispatch('sam', dict(self.request, **extra), message=self.request_message)
            self.assertEqual(saved[0]['action'], 'failed')
        self.assertFalse(self.chat.assets.root.exists())


if __name__ == '__main__':
    unittest.main()
