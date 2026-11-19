"""Isolated local assets only: no app, real data, connectors or credentials."""
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from studio import local_asset_spec as spec
from studio.local_assets import AuthorizedImage, LocalAssets, check_png, digest


def png(size=(64, 64), color='#3366AA'):
    data = io.BytesIO()
    Image.new('RGBA', size, color).save(data, format='PNG')
    return data.getvalue()


def scene(**changes):
    value = dict(width=320, height=180, alt='A yellow card on a blue canvas.',
        background='#123456', layers=[dict(kind='rect', x=10, y=20, width=100, height=80, fill='#F5C21B')])
    value.update(changes)
    return value


def request(**changes):
    return {'visuals': [scene(**changes)]}


class SpecTests(unittest.TestCase):
    def test_defaults_are_overridable_and_normalization_is_idempotent(self):
        normalized = spec.validate_request(request(template='dark', background='transparent', branding=None))
        self.assertEqual(normalized, spec.validate_request({'visuals': normalized}))
        self.assertEqual(normalized[0]['background'], '#00000000')
        self.assertIsNone(normalized[0]['branding'])
        self.assertEqual(spec.visual({'alt': 'Diagram', 'layers': scene()['layers']})['width'], 1200)

    def test_unknown_ownership_paths_urls_code_and_svg_are_rejected(self):
        invalid = [dict(request(), owner='sam'), dict(request(), out='C:/outside'),
                   request(svg='<svg/>'), request(html='<script/>'), request(renderer='anything')]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                spec.validate_request(value)
        for field, value in [('path', 'C:/secret.png'), ('url', 'https://example.test/x.png'), ('code', 'print(1)')]:
            item = request()
            item['visuals'][0]['layers'][0][field] = value
            with self.assertRaises(ValueError):
                spec.validate_request(item)
        for aid in ['../input', 'https://x', 'file.png', 'C:\\x', '']:
            with self.assertRaises(ValueError):
                spec.validate_request(request(layers=[dict(kind='image', x=0, y=0, width=64, height=64, asset_id=aid)]))

    def test_dimensions_numbers_counts_and_text_are_bounded(self):
        invalid = [request(width=True), request(height=0), request(width=4097), request(width=4096, height=4096),
                   request(width=10**1000), request(width=float('inf')), request(width=float('nan')),
                   request(layers=[]), request(layers=scene()['layers'] * 129), request(alt=''),
                   {'visuals': [scene()] * 17}, {'visuals': [scene(width=4000, height=2000)] * 5}]
        for value in invalid:
            with self.subTest(value=str(value)[:100]), self.assertRaises(ValueError):
                spec.validate_request(value)
        for value in [float('nan'), '1', -1, 2, True]:
            item = request()
            item['visuals'][0]['layers'][0]['opacity'] = value
            with self.assertRaises(ValueError):
                spec.validate_request(item)
        with self.assertRaises(ValueError):
            spec.validate_request(request(background='url(https://example.test)'))

    def test_complete_geometry_and_text_dialect(self):
        layers = [dict(kind='ellipse', x=0, y=0, width=80, height=80),
                  dict(kind='line', points=[[0, 0], [90, 80]], stroke='#FFFFFF'),
                  dict(kind='polygon', points=[[0, 0], [40, 20], [20, 90]], fill='#3366AA80'),
                  dict(kind='text', x=10, y=10, width=250, height=100, text='Why this matters',
                       size=30, min_size=20, font='serif', bold=True, align='center')]
        result = spec.validate_request(request(layers=layers))
        self.assertEqual([x['kind'] for x in result[0]['layers']], ['ellipse', 'line', 'polygon', 'text'])


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'owned-assets'
        self.service = LocalAssets(self.root)

    def fake_render(self, folder):
        job = json.loads((folder / 'job.json').read_bytes())
        for index, s in enumerate(job['visuals']):
            (folder / f'render_{index:02d}.png').write_bytes(png((s['width'], s['height'])))
        (folder / 'render_metadata.json').write_text(json.dumps({'renderer': spec.RENDERER_VERSION, 'fonts': {}}))

    def create_fake(self, owner='sam', data=None):
        with patch.object(self.service, '_render', side_effect=self.fake_render):
            return self.service.create(data or request(), owner=owner)

    def folder(self, result):
        return self.root / 'batches' / result['batch_id']

    def test_construct_and_invalid_request_do_not_write_storage(self):
        self.assertFalse(self.root.exists())
        with self.assertRaises(ValueError):
            self.service.create(dict(request(), owner='sam'), owner='sam')
        self.assertFalse(self.root.exists())

    def test_all_six_owners_can_create_but_cannot_read_another_owners_asset(self):
        roles = ['sam', 'tariq', 'laia', 'saskia', 'anouk', 'max']
        for owner in roles:
            made = self.create_fake(owner)
            ref = made['assets'][0]['reference']
            self.assertEqual(check_png(self.service.read_png(ref, owner=owner)), (320, 180))
            for other in set(roles) - {owner}:
                with self.assertRaises(PermissionError):
                    self.service.read_png(ref, owner=other)

    def test_outputs_are_unique_immutable_and_have_no_delivery_paths(self):
        a, b = self.create_fake(), self.create_fake()
        ar, br = a['assets'][0]['reference'], b['assets'][0]['reference']
        self.assertNotEqual(ar['asset_id'], br['asset_id'])
        self.assertNotEqual(ar['version'], br['version'])
        self.assertEqual(ar['source_hash'], br['source_hash'])
        self.assertEqual(self.service.read_png(ar, owner='sam'), self.service.read_png(br, owner='sam'))
        self.assertEqual(set(p.name for p in self.folder(a).iterdir()), {'manifest.json', ar['asset_id'] + '-' + ar['version'] + '.png'})
        self.assertEqual(list((self.root / '.staging').iterdir()), [])
        self.assertNotIn('path', json.dumps(a))
        self.assertNotIn('send_files', json.dumps(a))
        manifest = json.loads((self.folder(a) / 'manifest.json').read_bytes())
        self.assertEqual(manifest['owner'], 'sam')
        self.assertEqual(manifest['assets'][0]['source']['renderer']['renderer'], spec.RENDERER_VERSION)

    def test_forged_references_and_modified_png_or_source_are_rejected(self):
        result = self.create_fake()
        ref = result['assets'][0]['reference']
        for key, value in [('owner', 'sam'), ('batch_id', '../x'), ('png_hash', '0' * 64), ('version', '0' * 32)]:
            forged = dict(ref, **{key: value})
            with self.assertRaises((ValueError, KeyError)):
                self.service.read_png(forged, owner='sam')
        image = next(self.folder(result).glob('*.png'))
        original = image.read_bytes()
        image.write_bytes(png((320, 180), '#FF0000'))
        with self.assertRaisesRegex(ValueError, 'PNG changed'):
            self.service.read_png(ref, owner='sam')
        image.write_bytes(original)
        manifest_file = self.folder(result) / 'manifest.json'
        manifest = json.loads(manifest_file.read_bytes())
        manifest['assets'][0]['source']['spec']['background'] = '#999999'
        manifest_file.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'source or reference'):
            self.service.read_png(ref, owner='sam')

    def test_only_host_authorized_png_inputs_are_resolved_once_and_hashed(self):
        raw = png()
        calls = []
        def resolve(owner, aid):
            calls.append((owner, aid))
            if owner != 'max' or aid != 'source_1':
                raise PermissionError('Input is not owned.')
            return AuthorizedImage(raw, digest(raw))
        self.service.resolve_image = resolve
        layer = dict(kind='image', x=0, y=0, width=64, height=64, asset_id='source_1')
        data = request(layers=[layer, layer])
        made = self.create_fake('max', data)
        self.assertEqual(calls, [('max', 'source_1')])
        m = json.loads((self.folder(made) / 'manifest.json').read_bytes())
        self.assertEqual(m['assets'][0]['source']['inputs'], {'source_1': digest(raw)})
        with self.assertRaises(PermissionError):
            self.create_fake('sam', data)
        self.service.resolve_image = None
        with self.assertRaises(PermissionError):
            self.create_fake('max', data)
        self.service.resolve_image = lambda *args: AuthorizedImage(raw, '0' * 64)
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.create_fake('max', data)

    def test_failed_partial_or_wrong_size_render_commits_nothing_and_preserves_previous(self):
        first = self.create_fake()
        ref = first['assets'][0]['reference']
        old = self.service.read_png(ref, owner='sam')
        def partial(folder):
            self.fake_render(folder)
            (folder / 'render_00.png').unlink()
        def wrong(folder):
            self.fake_render(folder)
            (folder / 'render_00.png').write_bytes(png((64, 64)))
        def corrupt(folder):
            self.fake_render(folder)
            (folder / 'render_00.png').write_bytes(b'not a png')
        for effect in [ValueError('render failed'), partial, wrong, corrupt]:
            with patch.object(self.service, '_render', side_effect=effect):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.service.create(request(), owner='sam')
            self.assertEqual(self.service.read_png(ref, owner='sam'), old)
            self.assertEqual(len(list((self.root / 'batches').iterdir())), 1)
            self.assertEqual(list((self.root / '.staging').iterdir()), [])

    def test_output_metadata_tampering_and_root_redirection_fail_closed(self):
        made = self.create_fake()
        ref = made['assets'][0]['reference']
        path = self.folder(made) / 'manifest.json'
        manifest = json.loads(path.read_bytes())
        manifest['assets'][0]['alt'] = 'Changed claim'
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'metadata changed'):
            self.service.read_png(ref, owner='sam')
        stage_root = self.root / '.staging'
        stage_root.rmdir()
        elsewhere = Path(self.tmp.name) / 'elsewhere'
        elsewhere.mkdir()
        try:
            stage_root.symlink_to(elsewhere, target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks requires permission on this host.')
        with self.assertRaises(ValueError):
            self.create_fake()
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_missing_or_non_png_input_is_rejected_before_rendering(self):
        layer = dict(kind='image', x=0, y=0, width=64, height=64, asset_id='source_1')
        for source in [None, AuthorizedImage(b'<svg/>', digest(b'<svg/>'))]:
            self.service.resolve_image = lambda *args: source
            with patch.object(self.service, '_render') as render:
                with self.assertRaises(ValueError):
                    self.service.create(request(layers=[layer]), owner='sam')
                render.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_uncommitted_staging_and_symlinked_outputs_are_not_served(self):
        made = self.create_fake()
        ref = made['assets'][0]['reference']
        final = self.folder(made)
        staged = self.root / '.staging' / made['batch_id']
        final.rename(staged)
        with self.assertRaises(KeyError):
            self.service.read_png(ref, owner='sam')
        staged.rename(final)
        target = Path(self.tmp.name) / 'outside.png'
        target.write_bytes(png((320, 180)))
        image = next(final.glob('*.png'))
        image.unlink()
        try:
            image.symlink_to(target)
        except OSError:
            self.skipTest('Creating symlinks requires permission on this host.')
        with self.assertRaises(ValueError):
            self.service.read_png(ref, owner='sam')

    def test_busy_renderer_does_not_create_storage(self):
        with patch('studio.local_assets._SLOTS') as slots:
            slots.acquire.return_value = False
            with self.assertRaisesRegex(ValueError, 'busy'):
                self.service.create(request(), owner='sam')
        self.assertFalse(self.root.exists())

    def test_subprocess_is_isolated_bounded_and_does_not_inherit_secrets(self):
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired('worker', 90), 0]
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-secret', 'PYTHONPATH': 'evil'}):
            with patch('studio.local_assets.subprocess.Popen', return_value=process) as launch:
                with self.assertRaisesRegex(ValueError, 'timed out'):
                    self.service._render(Path(self.tmp.name))
        args, kw = launch.call_args
        self.assertIn('-I', args[0])
        self.assertIn('-B', args[0])
        self.assertNotIn('shell', kw)
        self.assertNotIn('OPENAI_API_KEY', kw['env'])
        self.assertNotIn('PYTHONPATH', kw['env'])
        self.assertEqual(kw['stdout'], subprocess.DEVNULL)
        self.assertEqual(kw['stderr'], subprocess.DEVNULL)
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_args_list[0].kwargs['timeout'], 90)

    def test_actual_offline_worker_custom_canvas_composition_and_transparency(self):
        a = scene(background='transparent', template='plain')
        b = scene(width=180, height=320, template='dark', layers=[
            dict(kind='ellipse', x=20, y=40, width=100, height=100, fill='#00CC88'),
            dict(kind='line', points=[[0, 250], [170, 250]], stroke='#FFFFFF', stroke_width=5),
            dict(kind='polygon', points=[[5, 5], [50, 5], [30, 30]], fill='#FF0000')])
        result = self.service.create({'visuals': [a, b]}, owner='anouk')
        raw = self.service.read_png(result['assets'][0]['reference'], owner='anouk')
        with Image.open(io.BytesIO(raw)) as image:
            self.assertEqual(image.size, (320, 180))
            self.assertEqual(image.getpixel((0, 0))[3], 0)
            self.assertEqual(image.getpixel((20, 30)), (245, 194, 27, 255))
        self.assertEqual(check_png(self.service.read_png(result['assets'][1]['reference'], owner='anouk')), (180, 320))

    def test_actual_text_branding_and_authorized_image_render(self):
        raw = png(color='#3366AA')
        self.service.resolve_image = lambda owner, aid: AuthorizedImage(raw, digest(raw))
        data = request(branding={'text': 'CareerOS', 'color': '#FFFFFF'}, layers=[
            dict(kind='image', asset_id='approved', x=10, y=10, width=64, height=64),
            dict(kind='text', text='Before and after', x=90, y=15, width=210, height=90,
                 size=28, min_size=12, color='#FFFFFF', bold=True)])
        result = self.service.create(data, owner='laia')
        raw = self.service.read_png(result['assets'][0]['reference'], owner='laia')
        with Image.open(io.BytesIO(raw)) as image:
            self.assertEqual(image.getpixel((20, 20)), (51, 102, 170, 255))
        metadata = json.loads((self.folder(result) / 'manifest.json').read_bytes())
        self.assertTrue(metadata['assets'][0]['source']['renderer']['fonts'])

    def test_text_that_cannot_fit_fails_without_committing(self):
        data = request(layers=[dict(kind='text', text='Too much text for this box',
            x=0, y=0, width=1, height=1, size=24, min_size=24)])
        with self.assertRaisesRegex(ValueError, 'rendering failed'):
            self.service.create(data, owner='saskia')
        self.assertEqual(list((self.root / 'batches').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
