"""Isolated carousel override fixtures; no real carousel, browser or publishing."""
import ast
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from studio.agent import Agents
from studio.carousels import Carousels
from studio.carousel_overrides import CarouselOverrides, SCHEMA, read_slide, wants_override
from studio.local_asset_chat import LocalAssetChat, ROLES
from studio.local_assets import LocalAssets
from studio.local_asset_spec import RENDERER_VERSION
from studio.store import Store, Conflict
from studio.team import Team

ROOT = Path(__file__).resolve().parents[1]


def png(color='gold', size=(128, 128), fmt='PNG'):
    output = io.BytesIO()
    Image.new('RGB', size, color).save(output, format=fmt)
    return output.getvalue()


class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.slide_root = self.root / 'slides'
        self.original = self.slide_root / 'Carousel' / 'original'
        self.original.mkdir(parents=True)
        for n in range(1, 10):
            (self.original / f'slide_{n:02d}.png').write_bytes(png((n * 20, 0, 0)))
        self.originals = {p.name: p.read_bytes() for p in self.original.iterdir()}
        self.root_patch = patch('studio.carousels.SLIDE_ROOT', self.slide_root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.store = Store(self.root / 'Data')
        self.tracker = Carousels(self.store)
        slides = '\n---\n'.join(f'Original slide {n}\nKeep these words.' for n in range(1, 10))
        self.item = self.tracker.create(dict(title='Executive updates', slides=slides, caption='Keep caption and tracked link',
            folder=str(self.original.relative_to(self.slide_root)), stage='Approved',
            design=dict(style='Paper', words=self.tracker.words_hash(dict(slides=slides)))))
        self.chat = LocalAssetChat(self.store.root / 'local-assets')
        self.service = CarouselOverrides(self.tracker, self.chat)
        self.message = 'Redesign the last slide of Executive updates.'
        self.context = dict(kind='carousel', id=self.item['id'])
        self.visual = dict(alt='Free slide pack CTA', width=128, height=128, branding=None,
                           layers=[dict(kind='rect', x=0, y=0, width=128, height=128, fill='#F5C21B')])
        self.request = dict(carousel_id=self.item['id'], revision=self.item['revision'], slide_number=9, visual=self.visual)
        self.render_patch = patch.object(LocalAssets, '_render', side_effect=self.render)
        self.render_patch.start()
        self.addCleanup(self.render_patch.stop)

    def render(self, folder):
        job = json.loads((folder / 'job.json').read_bytes())
        for i, scene in enumerate(job['visuals']):
            (folder / f'render_{i:02d}.png').write_bytes(png(size=(scene['width'], scene['height'])))
        (folder / 'render_metadata.json').write_text(json.dumps(dict(renderer=RENDERER_VERSION, fonts={})))

    def scope(self, **extra):
        return self.service.prepare(owner='sam', message=self.message, context=self.context, items=[self.tracker.get(self.item['id'])], **extra)

    def apply(self, request=None, scope=None, **extra):
        return self.service.apply(request or self.request, scope=scope or self.scope(), owner='sam', message=self.message, **extra)

    def assert_originals(self):
        self.assertEqual({p.name: p.read_bytes() for p in self.original.iterdir()}, self.originals)

    def test_replaces_only_ninth_image_immutable_version_and_requires_review(self):
        row = self.apply()
        item = self.tracker.get(self.item['id'])
        self.assertEqual(item['revision'], self.item['revision'] + 1)
        self.assertEqual(item['stage'], 'Ready for review')
        self.assertEqual(item['slides'], self.item['slides'])
        self.assertEqual(item['caption'], self.item['caption'])
        self.assertEqual(item['tags'], self.item['tags'])
        self.assertEqual(item['owner'], 'sam')
        self.assertNotEqual(item['folder'], self.item['folder'])
        images = self.tracker.images(item)
        self.assertEqual(len(images), 9)
        for n in range(8):
            self.assertEqual(images[n].read_bytes(), self.originals[f'slide_{n + 1:02d}.png'])
        self.assertNotEqual(images[8].read_bytes(), self.originals['slide_09.png'])
        self.assertEqual(read_slide(self.tracker, item, 8), (png(), 'image/png'))
        self.assert_originals()
        self.assertNotIn('files', row)
        self.assertNotIn('send_files', row)

    def test_changes_only_requested_slide_text_and_keeps_separators(self):
        self.apply(dict(self.request, slide_text='FREE SLIDE PACK\nTwelve executive examples.'))
        saved = self.tracker.get(self.item['id'])
        before = self.item['slides'].rsplit('\n---\n', 1)[0]
        self.assertEqual(saved['slides'], before + '\n---\nFREE SLIDE PACK\nTwelve executive examples.')
        self.assertEqual(saved['design']['words'], self.tracker.words_hash(saved))

    def test_request_fields_identity_revision_and_slide_number_fail_closed(self):
        for changes in ({'owner': 'max'}, {'folder': str(self.root)}, {'send_files': ['x']}, {'revision': 0},
                        {'revision': True}, {'carousel_id': 'another'}, {'slide_number': 0}, {'slide_number': True},
                        {'slide_number': 10}, {'slide_number': 1}, {'local_asset_id': 'a' * 32 + '_' + 'b' * 32},
                        {'slide_text': 'Other\n---\nExtra'}, {'slide_text': '<svg/>'}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, PermissionError)):
                self.apply(dict(self.request, **changes))
        self.assertEqual(self.tracker.get(self.item['id']), self.item)
        self.assertFalse((self.slide_root / '_LocalOverrides').exists())
        self.assert_originals()

    def test_only_current_sam_request_and_unique_target_can_prepare(self):
        for owner in ROLES:
            for message, proactive in [(self.message, owner != 'sam'), ('Do not replace the final slide.', False),
                    ('Explain how you redesign a slide.', False), ('"Redesign the last slide."', False)]:
                scope = self.service.prepare(owner=owner, message=message, proactive=proactive, context=self.context, items=[self.item])
                if owner == 'sam' and message == self.message and not proactive:
                    self.assertIsNotNone(scope)
                else:
                    self.assertIsNone(scope)
        self.assertIsNone(self.service.prepare(owner='sam', message='Replace the last slide.', context={}, items=[self.item]))
        self.assertIsNotNone(self.service.prepare(owner='sam', message=self.message, context={}, items=[self.item]))
        for msg in ('Replace the final CTA.', 'Make the last slide a photo.', 'Attach this visual to slide 9.'):
            self.assertTrue(wants_override('sam', msg))
        self.assertFalse(wants_override('sam', 'Create a standalone visual.'))

    def test_other_owner_scheduled_posted_and_archived_are_not_mutable(self):
        for changes in ({'owner': 'laia'}, {'stage': 'Scheduled'}, {'stage': 'Posted'}, {'archived': True}):
            with self.subTest(changes=changes):
                old = self.tracker.get(self.item['id'])
                self.tracker.save(old['id'], changes, old['revision'])
                try:
                    result = self.service.prepare(owner='sam', message=self.message, context=self.context, items=[self.tracker.get(old['id'])])
                    self.assertIsNone(result)
                except (ValueError, PermissionError):
                    pass
                current = self.tracker.get(old['id'])
                self.tracker.save(old['id'], {k: self.item[k] for k in changes}, current['revision'])
        self.assert_originals()

    def test_upload_resolver_requires_current_attachment_id_and_matching_bytes(self):
        aid = self.store.import_image('Cover.jpg', png(fmt='JPEG'))['asset']
        scope = self.scope(images=[aid])
        self.assertIn('upload_' + aid, self.service.context(scope))
        source = self.service._source(scope, 'sam', 'upload_' + aid)
        self.assertTrue(source.png.startswith(b'\x89PNG'))
        with self.assertRaises(PermissionError):
            self.service._source(self.scope(), 'sam', 'upload_' + aid)
        path, _ = self.store.asset(aid)
        path.write_bytes(png('blue'))
        with self.assertRaises(ValueError):
            self.service._source(scope, 'sam', 'upload_' + aid)

    def test_other_owners_directory_cannot_be_claimed_through_legacy_folder(self):
        self.tracker.create(dict(title='Laia private images', owner='laia', folder=self.item['folder']))
        with self.assertRaises(PermissionError):
            self.scope()
        self.assert_originals()

    def test_store_filename_and_arbitrary_path_or_url_are_never_sources(self):
        aid = self.store.import_image('Cover.png', png())['asset']
        scope = self.scope(images=[aid])
        for value in (str(self.original / 'slide_09.png'), 'https://example.test/photo.png', '../escape'):
            with self.assertRaises(PermissionError):
                self.service._source(scope, 'sam', value)
        with self.store.connect() as c:
            c.execute('UPDATE assets SET filename=? WHERE id=?', ('../elsewhere.png', aid))
        with self.assertRaises(ValueError):
            self.service._source(scope, 'sam', 'upload_' + aid)

    def test_existing_slide_sources_are_hash_bound_to_this_snapshot(self):
        scope = self.scope()
        token = 'slide_1_' + scope.originals[0][1]
        self.assertTrue(self.service._source(scope, 'sam', token).png)
        (self.original / 'slide_01.png').write_bytes(png('blue'))
        with self.assertRaises(ValueError):
            self.service._source(scope, 'sam', token)
        with self.assertRaises(Conflict):
            self.apply(scope=scope)
        self.assertFalse((self.slide_root / '_LocalOverrides').exists())

    def test_existing_local_asset_attachment_requires_same_owner(self):
        made = self.chat.assets.create({'visuals': [self.visual]}, owner='laia')['assets'][0]['reference']
        key = made['batch_id'] + '_' + made['asset_id']
        request = {k: v for k, v in self.request.items() if k != 'visual'}
        request['local_asset_id'] = key
        with self.assertRaises(PermissionError):
            self.apply(request)
        made = self.chat.assets.create({'visuals': [self.visual]}, owner='sam')['assets'][0]['reference']
        request['local_asset_id'] = made['batch_id'] + '_' + made['asset_id']
        self.apply(request)
        self.assert_originals()

    def test_revision_change_during_render_cannot_attach(self):
        scope = self.scope()
        def render(folder):
            self.render(folder)
            self.tracker.save(self.item['id'], {'caption': 'Latest owner edit'}, self.item['revision'])
        with patch.object(LocalAssets, '_render', side_effect=render), self.assertRaises(Conflict):
            self.apply(scope=scope)
        self.assertEqual(self.tracker.get(self.item['id'])['folder'], self.item['folder'])
        self.assertEqual(self.tracker.get(self.item['id'])['caption'], 'Latest owner edit')
        self.assertFalse((self.slide_root / '_LocalOverrides').exists())
        self.assert_originals()

    def test_render_failure_does_not_change_record_or_original_images(self):
        with patch.object(LocalAssets, '_render', side_effect=ValueError('fixture render failed')), self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.tracker.get(self.item['id']), self.item)
        self.assert_originals()

    def test_commit_conflict_preserves_record_and_originals(self):
        with patch.object(self.tracker, 'save', side_effect=Conflict('newer revision')), self.assertRaises(Conflict):
            self.apply()
        self.assertEqual(self.tracker.get(self.item['id']), self.item)
        self.assert_originals()

    def test_modified_override_images_and_manifest_fail_verified_preview(self):
        self.apply()
        item = self.tracker.get(self.item['id'])
        target = self.tracker.images(item)[8]
        target.write_bytes(png('blue'))
        with self.assertRaises(ValueError):
            read_slide(self.tracker, item, 8)
        target.write_bytes(png())
        manifest = target.parent / 'manifest.json'
        manifest.write_text('{}')
        with self.assertRaises(ValueError):
            self.tracker.images(item)

    def test_second_override_retains_first_custom_image_and_all_versions(self):
        self.apply()
        first = self.tracker.get(self.item['id'])
        paths = self.tracker.images(first)
        before = [x.read_bytes() for x in paths]
        self.message = 'Redesign the first slide of Executive updates.'
        scope = self.scope()
        self.apply(dict(self.request, revision=scope.revision, slide_number=1), scope=scope)
        second = self.tracker.get(self.item['id'])
        self.assertNotEqual(first['folder'], second['folder'])
        self.assertEqual([x.read_bytes() for x in paths], before)
        after = [x.read_bytes() for x in self.tracker.images(second)]
        self.assertEqual(after[1:], before[1:])
        self.assert_originals()

    def test_legacy_rerender_cannot_overwrite_custom_version_or_old_folder(self):
        self.apply()
        with self.assertRaisesRegex(ValueError, 'custom slides'):
            self.tracker.make_design(self.item['id'])
        self.assert_originals()

    def test_actual_offline_composition_accepts_attached_photo_and_text(self):
        aid = self.store.import_image('Photo.jpg', png('navy', fmt='JPEG'))['asset']
        scope = self.scope(images=[aid])
        visual = dict(alt='Photo with free label', width=256, height=256, branding=None,
            layers=[dict(kind='image', asset_id='upload_' + aid, x=0, y=0, width=256, height=256, fit='cover'),
                    dict(kind='text', text='FREE', x=10, y=10, width=236, height=70, size=50, color='#FFFFFF')])
        self.render_patch.stop()
        self.apply(dict(self.request, visual=visual), scope=scope)
        raw, _ = read_slide(self.tracker, self.tracker.get(self.item['id']), 8)
        self.assertEqual(Image.open(io.BytesIO(raw)).size, (256, 256))
        self.assert_originals()


class ChatTests(OverrideTests):
    # Keep inherited fixture helpers without repeating the service tests.
    def setUp(self):
        super().setUp()
        self.agents = Agents(self.store, self.root, Team(self.store))
        self.agents.local_asset_chat = self.chat
        self.agents.training = lambda owner: ('', [])
        self.answer = dict(reply='Here is the visual rationale.', carousel_slide_override=self.request)
        self.calls = []
        def run(system, prompt, schema, *args, **kwargs):
            self.calls.append(dict(system=system, prompt=prompt, schema=schema, kwargs=kwargs))
            return self.answer['reply'], self.answer
        self.agents.run = run

    def ask(self, owner='sam', message=None, **kwargs):
        return self.agents.chat(dict(agent_id=owner, message=message or self.message, context=self.context),
            [self.tracker.get(self.item['id'])], self.tracker, **kwargs)

    def test_chat_exposes_only_sam_action_and_ignores_simultaneous_legacy_changes(self):
        self.answer['carousels'] = [dict(id=self.item['id'], slides='Destroy all nine', make_design=True, owner='max')]
        with patch.object(self.tracker, 'apply', wraps=self.tracker.apply) as apply:
            result = self.ask()
        self.assertEqual(apply.call_args.args[1], [])
        self.assertIn('carousel_slide_override', self.calls[-1]['schema']['properties'])
        self.assertNotIn('carousels', self.calls[-1]['schema']['properties'])
        self.assertIsNone(self.calls[-1]['kwargs']['tools'])
        self.assertEqual(self.tracker.get(self.item['id'])['slides'], self.item['slides'])
        self.assertIn('visual rationale', result['reply']['text'])
        self.assertIn('Replaced slide 9 locally', result['reply']['text'])
        self.assertNotIn('files', result['reply'])

    def test_chat_wrong_role_proactive_and_nonrequest_payloads_cannot_mutate(self):
        self.agents.feedback = SimpleNamespace(pending=lambda owner: [{'id': 'edit'}], describe=lambda rows: 'A text change.', mark=lambda ids: None)
        for owner, message, proactive in [('laia', self.message, False), ('max', self.message, False),
                ('sam', 'Explain how you redesign the last slide.', False), ('sam', self.message, True)]:
            with self.subTest(owner=owner, proactive=proactive):
                self.ask(owner, message, proactive=proactive)
                self.assertNotIn('carousel_slide_override', self.calls[-1]['schema']['properties'])
                self.assertEqual(self.tracker.get(self.item['id']), self.item)
        self.assert_originals()

    def test_guidance_describes_real_override_and_keeps_laia_workflow(self):
        text = '\n'.join(self.agents.carousel_rules())
        self.assertIn('Paper is a default', text)
        self.assertIn('carousel_slide_override', text)
        self.assertNotIn('ask Claude Code', text)

    def test_http_existing_authenticated_route_uses_verified_override_reader(self):
        tree = ast.parse((ROOT / 'studio/server.py').read_text(encoding='utf-8'))
        handler = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == 'Handler')
        route = next(x for x in handler.body if isinstance(x, ast.FunctionDef) and x.name == 'route')
        text = ast.unparse(route)
        self.assertLess(text.index('authorized()'), text.index('read_slide'))
        self.assertIn("cache='private, no-store'", text)


# unittest inherits test methods; remove duplicated service tests from ChatTests.
for _name in tuple(OverrideTests.__dict__):
    if _name.startswith('test_'):
        setattr(ChatTests, _name, None)


if __name__ == '__main__':
    unittest.main()
