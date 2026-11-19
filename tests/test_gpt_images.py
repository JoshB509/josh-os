"""Offline provider and host tests. Transport is mocked; no OpenAI request is made."""
import base64
import hashlib
import io
import json
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from PIL import Image

from studio.agent import Agents, GPT_THUMB_JOB_SCHEMA, GPT_THUMB_BRIEF
from studio.gpt_images import GPTImages, ImageProviderError, image_input, NoRedirect
from studio.store import Store
from studio.video_thumbnails import VideoThumbnails


def png(size=(1536, 864), color='blue'):
    out = io.BytesIO()
    Image.new('RGB', size, color).save(out, 'PNG')
    return out.getvalue()


def response(raw=None):
    return {'data': [{'b64_json': base64.b64encode(raw if raw is not None else png()).decode()}]}, 'req_synthetic'


class ProviderTests(unittest.TestCase):
    def provider(self, transport=None):
        return GPTImages(environ={'OPENAI_API_KEY': 'synthetic-test-only'}, transport=transport or mock.Mock(return_value=response()))

    def test_missing_key_is_explicit_and_never_calls_network(self):
        transport = mock.Mock()
        provider = GPTImages(environ={}, transport=transport)
        self.assertFalse(provider.status()['ready'])
        with self.assertRaisesRegex(ImageProviderError, 'OPENAI_API_KEY'):
            provider.render('A blue circle')
        transport.assert_not_called()
        self.assertNotIn('synthetic-test-only', json.dumps(self.provider().status()))

    def test_unknown_model_refused(self):
        provider = GPTImages(environ={'OPENAI_API_KEY': 'synthetic-test-only', 'CAREEROS_GPT_IMAGE_MODEL': 'arbitrary-model'})
        with self.assertRaisesRegex(ImageProviderError, 'Unsupported'):
            provider.require_ready()

    def test_generation_uses_official_payload_and_validated_output(self):
        transport = mock.Mock(return_value=response())
        raw, metadata = self.provider(transport).render('A blue circle')
        endpoint, payload = transport.call_args.args
        self.assertEqual(endpoint, 'generations')
        self.assertEqual(payload['size'], '1536x864')
        self.assertEqual(payload['model'], 'gpt-image-2')
        self.assertEqual(payload['n'], 1)
        self.assertNotIn('images', payload)
        self.assertNotIn('synthetic-test-only', json.dumps(payload))
        with Image.open(io.BytesIO(raw)) as im:
            self.assertEqual(im.size, (1920, 1080))
        self.assertEqual(metadata['provider'], 'gpt-image')
        self.assertEqual(metadata['operation'], 'generations')

    def test_edit_transmits_exact_references_in_order_as_data_urls(self):
        transport = mock.Mock(return_value=response())
        images = [image_input(png((32, 32), c), role) for c, role in [('red', 'subject'), ('blue', 'background')]]
        _, metadata = self.provider(transport).render('Keep subject; change lettering', images)
        endpoint, payload = transport.call_args.args
        self.assertEqual(endpoint, 'edits')
        self.assertEqual(metadata['input_count'], 2)
        for index, ref in enumerate(payload['images']):
            self.assertTrue(ref['image_url'].startswith('data:image/png;base64,'))
            self.assertEqual(base64.b64decode(ref['image_url'].split(',')[1]), images[index]['data'])

    def test_invalid_inputs_rejected_before_request(self):
        transport = mock.Mock()
        provider = self.provider(transport)
        for prompt, refs in [('', []), ('x' * 32001, []), ('Hi', [dict(data=b'bad', role='ref')]),
                             ('Hi', [image_input(png((16, 16)), 'ref')] * 7)]:
            with self.subTest(prompt=prompt[:10]), self.assertRaises(ImageProviderError):
                provider.render(prompt, refs)
        transport.assert_not_called()

    def test_invalid_provider_outputs_are_not_saved_or_called_success(self):
        invalid = [({}, 'req'), ({'data': [{'url': 'https://example.com/image.png'}]}, 'req'),
                   ({'data': [{'b64_json': 'not-base64'}]}, 'req'), response(b'not an image'), response(png((100, 100)))]
        for result in invalid:
            with self.subTest(result=str(result)[:60]), self.assertRaisesRegex(ImageProviderError, 'No thumbnail was saved'):
                self.provider(mock.Mock(return_value=result)).render('Hi')

    def test_http_failure_is_sanitized_and_not_retried(self):
        for code in (400, 401, 403, 429, 500):
            opener = mock.Mock()
            opener.open.side_effect = urllib.error.HTTPError('https://api.openai.com', code,
                                                             'synthetic-test-only private provider details', {}, io.BytesIO(b'private'))
            with mock.patch('studio.gpt_images.urllib.request.build_opener', return_value=opener), \
                    self.assertRaises(ImageProviderError) as error:
                self.provider()._request('generations', {'prompt': 'Hi'})
            self.assertNotIn('synthetic-test-only', str(error.exception))
            self.assertNotIn('private', str(error.exception))
            opener.open.assert_called_once()

    def test_transport_uses_fixed_https_host_and_authorization_header(self):
        opened = mock.Mock()
        opened.__enter__ = mock.Mock(return_value=opened)
        opened.__exit__ = mock.Mock(return_value=False)
        opened.read.return_value = json.dumps(response()[0]).encode()
        opened.headers = {'x-request-id': 'req_test'}
        opener = mock.Mock()
        opener.open.return_value = opened
        with mock.patch('studio.gpt_images.urllib.request.build_opener', return_value=opener):
            self.provider()._request('edits', {'prompt': 'Hi'})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/images/edits')
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-test-only')
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 240)
        with self.assertRaises(ImageProviderError):
            NoRedirect().redirect_request(request, None, 302, '', {}, 'https://example.com')

    def test_timeout_is_uncertain_and_never_retried(self):
        opener = mock.Mock()
        opener.open.side_effect = TimeoutError('private')
        with mock.patch('studio.gpt_images.urllib.request.build_opener', return_value=opener), \
                self.assertRaisesRegex(ImageProviderError, 'uncertain'):
            self.provider()._request('generations', {'prompt': 'Hi'})
        opener.open.assert_called_once()


class ThumbnailGPTIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'Data')
        self.project = self.store.create('Synthetic test video')
        self.folder = self.root / 'video'
        self.folder.mkdir()
        self.transport = mock.Mock(return_value=response())
        self.provider = GPTImages(environ={'OPENAI_API_KEY': 'synthetic-test-only'}, transport=self.transport)
        self.agents = types.SimpleNamespace(thumbnail_job=mock.Mock(return_value=('Already made it!', {
            'options': [dict(label='Synthetic', words='HELLO', prompt='A blue background with HELLO')]})), post=mock.Mock())
        self.service = VideoThumbnails(self.store, types.SimpleNamespace(path=lambda pid: self.folder), self.agents, self.provider)

    def test_synthetic_end_to_end_save_import_and_provenance(self):
        aid = self.store.import_image('reference.png', png((32, 32), 'red'))['asset']
        source, _ = self.store.asset(aid)
        before = source.read_bytes()
        self.service._work(self.project['id'], 'Sam suggests a bar', '', 'gpt-image', [aid],
                           'No bar. Yellow Speak Like a Pro. Hands over the text.')
        endpoint, payload = self.transport.call_args.args
        self.assertEqual(endpoint, 'edits')
        self.assertIn('User composition instructions (highest priority):\nNo bar.', payload['prompt'])
        output = next((self.folder / 'thumbnails').glob('*.png'))
        receipt = json.loads(output.with_suffix('.json').read_text())
        self.assertEqual(receipt['provider'], 'gpt-image')
        self.assertEqual(receipt['operation'], 'edits')
        self.assertEqual(receipt['references'][0]['sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(self.store.get(self.project['id']), self.project)
        posted = self.agents.post.call_args
        self.assertIn('with GPT Image', posted.args[1])
        self.assertNotIn('Already made it!', posted.args[1])
        self.assertEqual(len(posted.kwargs['images']), 1)

    def test_missing_key_fails_before_thread_and_does_not_fallback(self):
        self.service.image_provider = GPTImages(environ={})
        with mock.patch('studio.video_thumbnails.threading.Thread') as thread, \
                mock.patch.object(self.service, 'build') as local, self.assertRaisesRegex(ValueError, 'OPENAI_API_KEY'):
            self.service.start(self.project['id'], 'Create with GPT')
        thread.assert_not_called()
        local.assert_not_called()

    def test_provider_failure_cannot_reuse_planner_success_claim(self):
        self.transport.side_effect = ImageProviderError('OpenAI quota prevented image creation. No local fallback was run.')
        with mock.patch.object(self.service, 'build') as local:
            self.service._work(self.project['id'], 'GPT please', '', 'gpt-image')
        local.assert_not_called()
        text = self.agents.post.call_args.args[1]
        self.assertIn('No GPT image was created or saved', text)
        self.assertNotIn('Already made it!', text)
        self.assertEqual(list((self.folder / 'thumbnails').glob('*.png')), [])

    def test_existing_outputs_are_preserved_and_versions_advance(self):
        options = [dict(prompt='A circle', label='Test', words='Hello')]
        first, errors = self.service.build_all(self.project['id'], self.project, options, provider='gpt-image')
        self.assertFalse(errors)
        original = Path(first[0]['path']).read_bytes()
        second, errors = self.service.build_all(self.project['id'], self.project, options, provider='gpt-image')
        self.assertFalse(errors)
        self.assertNotEqual(first[0]['path'], second[0]['path'])
        self.assertEqual(Path(first[0]['path']).read_bytes(), original)

    def test_legacy_compositor_is_explicit_and_remains_available(self):
        with mock.patch.object(self.service, 'build', return_value={}) as local, \
                mock.patch.object(self.service, 'under_limit', side_effect=lambda p: p):
            made, errors = self.service.build_all(self.project['id'], self.project,
                [dict(format='middle', pose='seated-13-palm-present-neutral', words='Hello')], provider='local')
        self.assertFalse(errors)
        self.assertEqual(made[0]['provider'], 'local')
        local.assert_called_once()
        self.transport.assert_not_called()

    def test_reference_paths_and_urls_rejected(self):
        for refs in (['../../secrets'], ['https://example.com/pic.png'], 'a' * 64):
            with self.assertRaises(ValueError):
                self.service.reference_ids(refs)

    def test_changed_reference_rejected_before_upload(self):
        aid = self.store.import_image('reference.png', png((16, 16), 'red'))['asset']
        path, _ = self.store.asset(aid)
        path.write_bytes(png((16, 16), 'green'))
        made, errors = self.service.build_all(self.project['id'], self.project,
            [dict(prompt='Edit the reference')], provider='gpt-image', reference_assets=[aid])
        self.assertFalse(made)
        self.assertIn('reference image changed', errors[0])
        self.transport.assert_not_called()

    def test_catalogue_subject_and_background_are_edit_references(self):
        subject = self.root / 'subject.png'
        background = self.root / 'background.png'
        subject.write_bytes(png((32, 32), 'red'))
        background.write_bytes(png((32, 32), 'blue'))
        with mock.patch('studio.video_thumbnails.POSES', self.root), \
                mock.patch('studio.video_thumbnails.pose_path', return_value=subject), \
                mock.patch.dict('studio.thumbnail_build.BACKGROUNDS', seated=background):
            made, errors = self.service.build_all(self.project['id'], self.project,
                [dict(prompt='Use the subject and room', pose='catalogue-pose', scene='seated')], provider='gpt-image')
        self.assertFalse(errors)
        self.assertEqual(len(made), 1)
        endpoint, payload = self.transport.call_args.args
        self.assertEqual(endpoint, 'edits')
        self.assertEqual(len(payload['images']), 2)
        self.assertIn('Josh subject/identity reference', payload['prompt'])

    def test_chat_dispatch_preserves_user_note_and_defaults_to_gpt(self):
        agent = object.__new__(Agents)
        agent.store = self.store
        agent.thread = mock.Mock(return_value=[])
        aid = self.store.import_image('attached.png', png((16, 16)))['asset']
        with mock.patch.object(self.service, 'start', return_value={'action': 'started'}) as start:
            agent.start_thumbnail_request(self.service, dict(project_id=self.project['id'], note='Sam proposal'),
                                           'Exact user instructions', [aid], '')
        self.assertEqual(start.call_args.kwargs['provider'], 'gpt-image')
        self.assertEqual(start.call_args.kwargs['user_note'], 'Exact user instructions')
        self.assertEqual(start.call_args.kwargs['reference_assets'], [aid])
        with self.assertRaisesRegex(ValueError, 'already attached'):
            agent.start_thumbnail_request(self.service, dict(project_id=self.project['id'], reference_assets=['f' * 64]), 'Hi', [], '')

    def test_gpt_planning_schema_has_no_template_geometry(self):
        props = GPT_THUMB_JOB_SCHEMA['properties']['options']['items']['properties']
        self.assertIn('prompt', props)
        self.assertNotIn('baseline', props)
        self.assertIn('user', GPT_THUMB_BRIEF.lower())
        self.assertIn('No automatic local fallback', GPT_THUMB_BRIEF)

    def test_thumbnail_job_uses_gpt_schema_and_original_instructions(self):
        agent = object.__new__(Agents)
        agent.team = types.SimpleNamespace(all=lambda: dict(agents=[dict(id='sam')], learnings=[]))
        agent.training = mock.Mock(return_value=('', []))
        agent.training_dir = mock.Mock(return_value=self.root)
        agent.desk_system_prompt = mock.Mock(return_value='You are Sam')
        agent.run = mock.Mock(return_value=('', {'options': []}))
        agent.thumbnail_job(self.project, 'Sam proposal', self.folder, provider='gpt-image',
                            reference_assets=['a' * 64], user_note='Place text high, no yellow bar')
        self.assertEqual(agent.run.call_args.args[2], GPT_THUMB_JOB_SCHEMA)
        self.assertIn('Place text high, no yellow bar', agent.run.call_args.args[1])
        self.assertEqual(agent.run.call_args.kwargs['tools'], 'Read,Grep,Glob')


if __name__ == '__main__':
    unittest.main()
