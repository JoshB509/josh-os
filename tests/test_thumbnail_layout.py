"""Layout regressions; synthetic render checks stay in memory, with no real thumbnail jobs."""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

from studio import thumbnail_build as builder
from studio.agent import THUMB_JOB_SCHEMA, THUMB_BRIEF
from studio.thumbnail_layout import geometry, text_box
from studio.video_thumbnails import check, VideoThumbnails


class FlexibleThumbnailTests(unittest.TestCase):
    def option(self, **changes):
        return dict(format='middle', pose='seated-13-palm-present-neutral',
                    lines=['SPEAK LIKE', 'A PRO'], baseline=760, head_top=180, body_bottom=1300,
                    style=dict(font='anton', color='#FFE500', size=260, bar=False), **changes)

    def test_reported_j41_design_passes_and_preserves_direction(self):
        fmt, spec, words = check(self.option())
        self.assertEqual(words, 'SPEAK LIKE A PRO')
        self.assertEqual(spec['pose']['head_top'], 180)
        self.assertEqual(spec['pose']['body_bottom'], 1300)
        self.assertEqual(spec['style']['color'], '#FFE500')
        _, size, baselines = builder.layout(spec['lines'], spec['style'], fmt, spec['text_box'])
        self.assertEqual(size, 260)
        self.assertEqual(baselines, [760 - 260 * .92, 760])
        self.assertEqual(spec['text_box']['cx'], 960)

    def test_failed_option_now_reaches_build_and_save_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = VideoThumbnails(None, types.SimpleNamespace(path=lambda pid: tmp), None)
            # Build/output are mocked: this test never creates Josh's thumbnail.
            with mock.patch.object(service, 'build', return_value={}) as build, \
                    mock.patch.object(service, 'under_limit', side_effect=lambda p: p):
                made, errors = service.build_all('J41', {'name': 'J41'}, [self.option()])
            self.assertEqual(errors, [])
            self.assertEqual(len(made), 1)
            self.assertEqual(build.call_args.args[1]['text_box']['baseline'], 760)
            self.assertEqual(Path(made[0]['path']).parent, Path(tmp) / 'thumbnails')

    def test_legacy_defaults_and_old_styled_specs_remain_compatible(self):
        _, middle, _ = check(dict(format='middle', pose='seated-13-palm-present-neutral', words='Hello'))
        self.assertNotIn('style', middle)
        self.assertEqual(middle['line'], dict(text='Hello', cx=960, baseline=975, bar_left=130, bar_right=1790))
        self.assertEqual({k: v for k, v in middle['pose'].items() if k != 'path'},
                         dict(face_cx=960, head_top=22, body_bottom=1100, desk_top=1008))
        _, close, _ = check(dict(format='close', pose='seated-13-palm-present-neutral', lines=['Hi']))
        self.assertEqual(close['lines'], [dict(text='Hi', cx=1297, baseline=470, size=142)])
        _, spec, _ = check(self.option())
        del spec['text_box']  # old serialized style specs had only these top-level fields
        geometry('middle', spec)
        self.assertEqual(text_box(spec, 'middle')['baseline'], 760)

    def test_custom_close_position_scene_and_spacing(self):
        _, spec, _ = check(dict(format='close', pose='seated-13-palm-present-neutral', lines=['Hello', 'World'],
                                baseline=600, text_cx=500, bar_left=0, bar_right=1000, line_gap=1.2,
                                scene='standing', face_cx=1600, face_cy=700, face_w=800, style=dict(font='impact')))
        self.assertEqual(spec['scene'], 'standing')
        self.assertEqual(spec['text_box'], dict(left=0, right=1000, baseline=600, cx=500, line_gap=1.2))
        self.assertEqual(builder.layout(spec['lines'], spec['style'], 'close', spec['text_box'])[2], [600, 840])

    def test_explicit_style_overrides_are_not_design_errors(self):
        style = builder.check_style(dict(font='impact', size=900, outline=30, bolden=10,
                                         color='yellow', bar=True, bar_color='yellow', fit='none'))
        _, size, baselines = builder.layout(['Hello'], style, 'middle', dict(left=0, right=50, baseline=-20, cx=0))
        self.assertEqual((size, baselines), (900, [-20]))
        style = builder.check_style(dict(font='impact', size=20, bar=False, margin=0))
        self.assertEqual(builder.layout(['Hi'], style, 'middle', dict(left=0, right=100, baseline=25, cx=50))[1], 20)

    def test_technical_bounds_still_reject_unsafe_options(self):
        for field, value in [('baseline', float('nan')), ('face_cx', float('inf')), ('head_top', True),
                             ('body_bottom', -100), ('bar_right', 100), ('face_cx', 10**500),
                             ('text_behind', 'yes'), ('shade', 1), ('scene', '../../secret')]:
            option = self.option()
            option[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                check(option)
        for dims in [(0, 1080), (1920, -1), (8192, 8192), (float('inf'), 1080), (True, 1080)]:
            with self.assertRaises(ValueError):
                builder.check_dimensions(*dims)
        builder.check_dimensions(1920, 1080)

    def test_builder_rechecks_geometry_before_any_render(self):
        _, spec, _ = check(self.option())
        spec['text_box']['baseline'] = float('nan')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / 'video' / 'thumbnails' / 'new.png'
            job = root / 'job.json'
            job.write_text(json.dumps(dict(format='middle', spec=spec, out=str(out))))
            with mock.patch.object(builder, 'YOUTUBE', root), self.assertRaisesRegex(SystemExit, 'finite number'):
                builder.main(job)
            self.assertFalse(out.exists())

    def test_pose_scaling_and_supported_image_resources(self):
        subject = Image.new('RGBA', (100, 100), 'red')
        builder.check_pose_size(subject, 20, head_top=22, body_bottom=1100)
        with self.assertRaises(ValueError):
            builder.check_pose_size(subject, 1, face_w=2160)
        with self.assertRaises(ValueError):
            builder.check_pose_size(Image.new('RGBA', (100, 100)), 20, head_top=0, body_bottom=1000)
        with mock.patch.object(Image, 'open') as opened:
            opened.return_value.__enter__.return_value = types.SimpleNamespace(format='JPEG', size=(1920, 1080))
            with self.assertRaisesRegex(ValueError, 'PNG'):
                builder.check_image('synthetic.jpg')

    def test_output_and_asset_path_boundaries_remain_enforced(self):
        _, spec, _ = check(self.option())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / 'job.json'
            for out, pose, expected in [(root / 'bad.png', spec['pose']['path'], 'new .png'),
                                        (root / 'video' / 'thumbnails' / 'new.png', str(root / 'bad.png'), 'Pose Catalogue')]:
                spec['pose']['path'] = pose
                job.write_text(json.dumps(dict(format='middle', spec=spec, out=str(out))))
                with mock.patch.object(builder, 'YOUTUBE', root), self.assertRaisesRegex(SystemExit, expected):
                    builder.main(job)

    def test_front_subject_layer_overlaps_words_and_default_stays_behind(self):
        # Synthetic red subject over a black background, using real text drawing.
        # No actual pose/background is rendered and save captures in-memory pixels only.
        thumb = types.ModuleType('thumb')
        thumb.BG = {'seated': 'synthetic'}
        def place(canvas, *args, **kwargs):
            ImageDraw.Draw(canvas).rectangle((500, 350, 1400, 800), fill='red')
            return {}
        thumb.place_pose = place
        thumb.bottom_shade = lambda start: Image.new('RGBA', (1920, 1080))
        oneline = types.ModuleType('oneline')
        oneline.W, oneline.H, oneline.BAR_UP, oneline.BAR_DOWN = 1920, 1080, .207, .144
        def solid(mask, rgb):
            im = Image.new('RGBA', mask.size, rgb + (255,))
            im.putalpha(mask)
            return im
        oneline.solid = solid
        oneline.drop_shadow = lambda *args: None
        renders = []
        def capture(im, *args, **kwargs):
            renders.append(im.copy())
        with mock.patch.dict(sys.modules, thumb=thumb, oneline=oneline), \
                mock.patch.object(Image, 'open', return_value=Image.new('RGB', (1920, 1080))), \
                mock.patch.object(Image.Image, 'save', capture):
            for behind in (False, True):
                option = self.option(text_behind=behind, shade=False)
                option['style']['shadow'] = False
                fmt, spec, _ = check(option)
                builder.styled(fmt, spec, 'unused')
        yellow = [(x, y) for y in range(400, 760, 2) for x in range(600, 1300, 2)
                  if renders[0].getpixel((x, y)) == (255, 229, 0)]
        self.assertTrue(yellow)
        self.assertTrue(all(renders[1].getpixel(p) == (255, 0, 0) for p in yellow))

    def test_schema_and_brief_expose_supported_overrides(self):
        props = THUMB_JOB_SCHEMA['properties']['options']['items']['properties']
        for name in ('baseline', 'text_cx', 'line_gap', 'text_behind', 'shade', 'scene'):
            self.assertIn(name, props)
        self.assertIn('none', props['style']['properties']['fit']['enum'])
        self.assertIn('not mandatory geometry', THUMB_BRIEF)


if __name__ == '__main__':
    unittest.main()
