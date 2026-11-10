import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio import thumbnail_build
from studio.agent import edit_rule, scoped
from studio.thumbnail_build import FONTS, check_style, layout
from studio.video_thumbnails import check


class ThumbnailOptionTests(unittest.TestCase):
    def test_a_middle_shot_becomes_the_approved_spec(self):
        fmt, spec, words = check(dict(format='middle', pose='seated-13-palm-present-neutral', words='Make Them Explain'))
        self.assertEqual(fmt, 'middle')
        self.assertEqual(spec['scene'], 'seated')
        self.assertTrue(spec['pose']['path'].endswith('seated-13-palm-present-neutral.png'))
        self.assertEqual((spec['line']['baseline'], spec['line']['bar_right']), (975, 1790))
        self.assertEqual(words, 'Make Them Explain')

    def test_close_words_shrink_to_fit(self):
        _, spec, _ = check(dict(format='close', pose='seated-06-side-point-neutral', lines=['Stop sounding', 'junior']))
        self.assertLess(spec['lines'][0]['size'], 142)
        self.assertEqual(spec['lines'][0]['cx'], 1297)

    def test_anything_outside_the_limits_is_refused(self):
        bad = [dict(format='middle', pose='../../secrets', words='Hi'),
               dict(format='middle', pose='seated-13-palm-present-neutral', words='Hi', bar_right=1900),
               dict(format='whiteboard', pose='seated-13-palm-present-neutral', words='Hi'),
               dict(format='middle', pose='seated-13-palm-present-neutral', words='a; rm -rf /'),
               dict(format='middle', pose='no-such-pose', words='Hi'),
               dict(format='close', pose='seated-06-side-point-neutral', lines=['one', 'two', 'three'])]
        for option in bad:
            with self.assertRaises(ValueError, msg=str(option)):
                check(option)


class TextStyleTests(unittest.TestCase):
    P = 'seated-13-palm-present-neutral'

    def test_big_bold_yellow_words(self):
        fmt, spec, words = check(dict(format='middle', pose=self.P, lines=['Stop sounding', 'junior'],
                                      style=dict(font='anton', color='bright-yellow', bar=False, size=260, case='upper')))
        self.assertEqual(spec['lines'], ['STOP SOUNDING', 'JUNIOR'])
        self.assertEqual((spec['style']['color'], spec['style']['bar'], spec['style']['weight']), ('#FFE500', False, 'regular'))

    def test_every_listed_font_is_on_this_pc(self):
        missing = [str(f) for v in FONTS.values() for f in v['weights'].values() if not f.is_file()]
        self.assertEqual(missing, [])

    def test_bad_styles_are_refused(self):
        bad = [dict(font='comic-sans'), dict(font=r'C:\Windows\Fonts\arial.ttf'), dict(font=r'..\..\x'), dict(font='anton', weight='thin'),
               dict(color='rgb(1,2,3)'), dict(color='#FFF'), dict(size=900), dict(size='big'), dict(outline=50), dict(bolden=-1),
               dict(color='yellow', bar=True), dict(case='lower'), dict(fit='stretch'), dict(bar='yes')]
        for style in bad:
            with self.assertRaises(ValueError, msg=str(style)):
                check_style(style)
        with self.assertRaises(ValueError):
            check(dict(format='middle', pose=self.P, lines=['a', 'b', 'c'], style={}))

    def test_a_missing_font_file_is_reported_not_swapped(self):
        gone = dict(FONTS, anton=dict(label='Anton', weights={'regular': Path(r'E:\no\such\Anton.woff2')}))
        with mock.patch.dict(thumbnail_build.FONTS, gone, clear=True):
            with self.assertRaisesRegex(ValueError, 'missing from this PC'):
                check_style(dict(font='anton'))

    def test_long_words_shrink_and_impossible_words_are_refused(self):
        style = check_style(dict(font='impact', size=360, bar=False, color='yellow'))
        _, size, _ = layout(['This is far too many words for one line'], style, 'middle', dict(left=130, right=1790, baseline=975, cx=960))
        self.assertLess(size, 360)
        wide = check_style(dict(font='chivo', size=200, bar=False, color='yellow'))
        with self.assertRaisesRegex(ValueError, 'too long to fit'):
            layout(['W' * 40], wide, 'close', dict(left=737, right=1857, baseline=470, cx=1297))

    def test_the_builder_refuses_an_unchecked_style(self):
        _, spec, _ = check(dict(format='middle', pose=self.P, words='Hi', style=dict(font='anton')))
        spec['style']['font'] = r'C:\Windows\Fonts\arial.ttf'
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / 'job.json'
            job.write_text(json.dumps(dict(format='middle', spec=spec, out=r'E:\CareerOS\12_YouTube\_none\thumbnails\x.png')))
            run = subprocess.run([sys.executable, thumbnail_build.__file__, str(job)], capture_output=True, text=True, timeout=120)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('Refused: Font must be one of', run.stderr)


class EditScopeTests(unittest.TestCase):
    def test_file_edits_are_held_to_one_folder(self):
        self.assertEqual(edit_rule(r'E:\CareerOS\12_YouTube\some-video'), 'Edit(//e/CareerOS/12_YouTube/some-video/**)')
        self.assertEqual(scoped('Read,Grep,Glob,Edit,Write', r'E:\CareerOS\x'), 'Read,Grep,Glob,Edit(//e/CareerOS/x/**)')
        self.assertEqual(scoped('Read,Grep,Glob', r'E:\CareerOS\x'), 'Read,Grep,Glob')


if __name__ == '__main__':
    unittest.main()
