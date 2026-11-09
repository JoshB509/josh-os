import unittest

from studio.edit_review import parse_scenes, parse_shot_list

SHOTS = '''| Script | Shot |
|---|---|
| **01 · Hook** | |
| 0:00 · When an executive asks you a tough question, it's a make or break moment. | Talking head |
| 0:08 · It makes them look unprepared. And they stop acting on their advice. | **Insert Animation 1** (picture in picture, you in the right-hand box)<br>A credibility bar drops.<br><br>Press 1 on "It makes them look unprepared"<br>Press 2 on "they stop acting on their advice" |
| 0:20 · So they guess. Now don't do that. Who is this guy? | Talking head, then cut to the animation from "So they guess".<br><br>**Insert Animation 2** (full screen)<br>A guess.<br><br>Press 1 on "So they guess"<br><br>Talking head for "Now don't do that", then back to Animation 2 where you left off.<br><br>Press 2 on "Who is this guy?" |
'''
HTML = '''<div class="scene mark" data-from="0" data-to="0"></div>
<div class="scene pip" data-from="1" data-to="2"></div>
<div class="scene mark" data-from="3" data-to="3"></div>
<div class="scene" data-from="4" data-to="5"></div>'''


class EditReviewTests(unittest.TestCase):
    def test_scenes_skip_number_cards(self):
        self.assertEqual(parse_scenes(HTML), [dict(start=1, end=2, type='pip', n=1), dict(start=4, end=5, type='full', n=2)])

    def test_rows_split_into_talking_head_and_animation_parts(self):
        parts = parse_shot_list(SHOTS)
        kinds = [(p['kind'], p.get('n')) for p in parts]
        self.assertEqual(kinds, [('section', None), ('talk', None), ('animation', 1), ('animation', 2), ('talk', None), ('animation', 2)])
        one = parts[2]
        self.assertEqual(one['type'], 'pip')
        self.assertEqual([p['press'] for p in one['presses']], [1, 2])
        self.assertTrue(all(p['found'] for p in one['presses']))
        self.assertEqual(parts[3]['script'], 'So they guess.')
        self.assertEqual(parts[4]['script'], "Now don't do that.")
        self.assertEqual(parts[5]['script'], 'Who is this guy?')


if __name__ == '__main__':
    unittest.main()
