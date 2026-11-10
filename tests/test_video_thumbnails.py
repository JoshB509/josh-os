import unittest

from studio.agent import edit_rule, scoped
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


class EditScopeTests(unittest.TestCase):
    def test_file_edits_are_held_to_one_folder(self):
        self.assertEqual(edit_rule(r'E:\CareerOS\12_YouTube\some-video'), 'Edit(//e/CareerOS/12_YouTube/some-video/**)')
        self.assertEqual(scoped('Read,Grep,Glob,Edit,Write', r'E:\CareerOS\x'), 'Read,Grep,Glob,Edit(//e/CareerOS/x/**)')
        self.assertEqual(scoped('Read,Grep,Glob', r'E:\CareerOS\x'), 'Read,Grep,Glob')


if __name__ == '__main__':
    unittest.main()
