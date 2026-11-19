"""Flexible local visuals: resource limits, immutable readback and actual offline rendering."""
import asyncio
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from studio.email_visuals import VISUAL_PROPS
from studio.email_visuals import sha
from studio.visual_spec import normalize_visual, normalize_visuals, safe_svg
from studio.render_email_visuals import visual_html, render_email_visuals
from tests import test_email_visuals as visual_fixtures
VISUAL, SVG = visual_fixtures.VISUAL, visual_fixtures.SVG
from tests.test_kit_drafts import Base


class FlexibleVisualTests(visual_fixtures.VisualTests):
    def render(self, slides, folder):
        for i, slide in enumerate(slides, 1):
            Image.new('RGB', (slide['width'], slide['height']), '#F6F5F1').save(folder / f'slide_{i:02d}.png')

    def test_sixteen_wide_assets_history_and_index_bounds(self):
        request = self.request()
        request['visuals'] = [dict(VISUAL, width=1280, height=720, badge='BEFORE') for _ in range(16)]
        first = self.emails.visuals.generate(request)
        self.assertEqual(len(first['images']), 16)
        version = first['images'][0]['version']
        self.generate()
        png = self.emails.visuals.read_png(first['id'], version, '16')
        self.assertEqual(Image.open(io.BytesIO(png)).size, (1280, 720))
        for value in ('0', '17', '016', '-1', '1.0', True):
            with self.assertRaises(ValueError): self.emails.visuals.read_png(first['id'], version, value)
        self.assertEqual(VISUAL_PROPS['properties']['visuals']['maxItems'], 16)

    def test_all_design_choices_change_the_source_hash(self):
        original = self.generate()['images'][0]['source_hash']
        for change in [dict(width=1400), dict(height=720), dict(footer=True), dict(badge='AFTER'),
                       dict(note='A clearer decision.'), dict(background='#FFEEDD'), dict(template='plain', layout='text'),
                       dict(aspect_ratio='1:1'), dict(layout='card')]:
            request = self.request(); request['visuals'][0].update(change)
            saved = self.emails.visuals.generate(request)
            self.assertNotEqual(original, saved['images'][0]['source_hash'], change)
            self.assertEqual(saved['approval_status'], 'Pending final approval')

    def test_wrong_dimensions_and_layout_failure_retain_saved_version(self):
        saved = self.generate()
        request = self.request(); request['visuals'][0].update(width=1280, height=720)
        with patch.object(self.emails.visuals, 'render', side_effect=lambda slides, folder: (folder/'slide_01.png').write_bytes(self.png)):
            with self.assertRaisesRegex(ValueError, 'dimensions'): self.emails.visuals.generate(request)
        with patch.object(self.emails.visuals, 'render', side_effect=ValueError('Note does not fit')):
            with self.assertRaises(ValueError): self.emails.visuals.generate(request)
        self.assertEqual(self.fresh(), saved)

    def test_old_square_manifest_without_design_fields_still_reads(self):
        version = 'a' * 32
        folder = self.emails.visuals.folder(self.email['id'], version); folder.mkdir(parents=True)
        slide = dict(lines=['Original square artwork'], svg=SVG)
        image = dict(version=version,index=1,source_hash=sha(json.dumps(slide,sort_keys=True).encode()),
                     png_hash=sha(self.png),alt='Original square image',after_line=1)
        (folder/'slide_01.png').write_bytes(self.png)
        (folder/'manifest.json').write_text(json.dumps(dict(email_id=self.email['id'],slides=[slide],images=[image])))
        saved = self.emails.save(self.email['id'], {'images':[image]}, self.fresh()['revision'], sync_remote=False)
        self.assertEqual(self.emails.visuals.read_png(saved['id'],version,'1'),self.png)
        self.generate()
        self.assertEqual(self.emails.visuals.read_png(saved['id'],version,'1'),self.png)


class SpecTests(unittest.TestCase):
    def test_aspect_ratio_and_template_overrides(self):
        self.assertEqual(normalize_visual(dict(lines=['Test'], width=1600, aspect_ratio='16:9'))['height'], 900)
        self.assertEqual(normalize_visual(dict(lines=['Test'], height=900, aspect_ratio='16:9'))['width'], 1600)
        slide = normalize_visual(dict(template='slide-card', svg=SVG, width=1800, height=1000))
        self.assertEqual((slide['width'], slide['height']), (1800,1000))
        self.assertFalse(slide['footer'])
        self.assertEqual(slide,normalize_visual(slide))
        text=normalize_visual(dict(lines=['Text-only layout']))
        self.assertEqual(text,normalize_visual(text))
        html = visual_html(slide)
        self.assertNotIn('@josh', html); self.assertNotIn('class="foot', html)
        self.assertNotIn('class="dots', html)
        custom = normalize_visual(dict(svg=SVG, layout='standalone', width=1600, height=900))
        self.assertNotIn('<h1>', visual_html(custom))

    def test_resource_and_custom_spec_limits(self):
        for change in [dict(width=True), dict(height=319), dict(width=4097), dict(width=4096,height=4096),
                       dict(width=4096,height=320), dict(aspect_ratio='NaN'), dict(aspect_ratio='0:9'),
                       dict(width=1600,height=900,aspect_ratio='1:1'), dict(footer='false'), dict(badge='OTHER'),
                       dict(note='x'*301), dict(note='two\nlines'), dict(layout='other'), dict(background='url(secret)'),
                       dict(template='file:///secret'), dict(image='C:/secret'), dict(svg='<svg>'),
                       dict(layout='standalone'), dict(lines=[]), dict(svg=None)]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                normalize_visual(dict(lines=['Hello'], **change)) if 'lines' not in change else normalize_visual(change)
        with self.assertRaises(ValueError): normalize_visuals([dict(lines=['Hello'],width=2800,height=2800)]*6)
        with self.assertRaises(ValueError): normalize_visuals([dict(lines=['Hello'])]*17)
        for svg in ['<svg viewBox="0 0 NaN 20"/>', '<svg viewBox="0 0 -2 20"/>',
                    '<svg><path fill="url(#missing)"/></svg>', '<svg>'+'<g>'*34+'</g>'*34+'</svg>',
                    '<svg>'+('<rect/>'*2000)+'</svg>', '<svg><defs><marker id="a"><path marker-end="url(#a)"/></marker></defs></svg>']:
            with self.subTest(svg=svg[:100]), self.assertRaises(ValueError):safe_svg(svg)


class ActualRenderTests(unittest.TestCase):
    def test_eight_cards_custom_canvas_and_overflow(self):
        # Dedicated headless browser, no live profile, app, network, or real email.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            slides = [dict(template='slide-card',svg=SVG,width=1280,height=800,
                           badge='BEFORE' if i%2==0 else 'AFTER', note='Each line is something the business gets.') for i in range(8)]
            asyncio.run(render_email_visuals(slides, root/'cards'))
            self.assertEqual(len(list((root/'cards').glob('slide_*.png'))),8)
            self.assertEqual(Image.open(root/'cards/slide_08.png').size,(1280,800))
            asyncio.run(render_email_visuals(slides, root/'repeat'))
            self.assertEqual((root/'cards/slide_01.png').read_bytes(),(root/'repeat/slide_01.png').read_bytes())
            with self.assertRaisesRegex(ValueError,'fresh'):asyncio.run(render_email_visuals(slides,root/'cards'))
            asyncio.run(render_email_visuals([dict(svg=SVG,layout='standalone',width=1600,height=900),
                                             normalize_visual(dict(lines=['A clear decision','Start with the ask.'],width=900,height=1200))],root/'custom'))
            with self.assertRaisesRegex(Exception,'note is too long'):
                asyncio.run(render_email_visuals([dict(template='slide-card',svg=SVG,width=640,height=480,note='Wide notes '*25)],root/'overflow'))

    def test_real_subprocess_generate_and_read_eighth_asset(self):
        fixture=Base();fixture.setUp()
        try:
            with patch('urllib.request.urlopen',side_effect=AssertionError('No network allowed')):
                request=dict(email_id=fixture.email['id'],revision=fixture.email['revision'],
                             visuals=[dict(VISUAL,width=1280,height=720,layout='card',lines=[],badge='AFTER',note='A readable note.') for _ in range(8)])
                saved=fixture.emails.visuals.generate(request)
                self.assertEqual(len(saved['images']),8)
                png=fixture.emails.visuals.read_png(saved['id'],saved['images'][7]['version'],'8')
                self.assertEqual(Image.open(io.BytesIO(png)).size,(1280,720))
                self.assertEqual(saved['approval_status'],'Pending final approval')
        finally:
            fixture.doCleanups()


if __name__ == '__main__':unittest.main()
