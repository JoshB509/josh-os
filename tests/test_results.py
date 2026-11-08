import io, json, tempfile, unittest
from pathlib import Path
from PIL import Image
from studio.store import Store, Conflict
from studio.results import VideoResults
from studio.team import Team
from studio.agent import Agents


def shot(store, colour):
    b = io.BytesIO(); Image.new('RGB', (320, 180), colour).save(b, format='PNG')
    return store.import_image('test.png', b.getvalue())['asset']


class ResultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'Data')
        self.results = VideoResults(self.store)
        self.p = self.store.create('Make them explain')

    def tearDown(self):
        self.temp.cleanup()

    def round_(self, **extra):
        return dict(dict(changed='text', hypothesis='The phrase does the work',
                         variants=[dict(label='A', text='Make them explain', share=48.2),
                                   dict(label='B', text='Stop over-explaining', share=27.1),
                                   dict(label='C', text='The one question', share=24.7)]), **extra)

    def test_empty_then_saved_with_numbers_tests_and_retention(self):
        self.assertEqual(self.results.get(self.p['id'])['revision'], 0)
        r = self.results.save(self.p['id'], dict(
            funnel='middle', metrics=dict(views=12000, subscribers=180, emails=96, customers=4, ctr=6.2, as_of='2026-11-07'),
            tests=[self.round_(winner='A', screenshot=shot(self.store, 'red'))],
            retention=dict(screenshot=shot(self.store, 'blue'), dips=[dict(at='3:40', kind='drop', script='Say this instead', why='Tip paid off')])), 0)
        self.assertEqual((r['revision'], r['tests'][0]['round'], r['tests'][0]['status']), (1, 1, 'done'))
        self.assertEqual(VideoResults(self.store).get(self.p['id']), r)
        with self.assertRaises(Conflict):
            self.results.save(self.p['id'], {'funnel': 'top'}, 0)

    def test_validation(self):
        for bad in [dict(funnel='side'), dict(metrics=dict(views='lots')), dict(metrics=dict(likes=3)),
                    dict(tests=[self.round_(winner='D')]), dict(tests=[self.round_(changed='vibes')]),
                    dict(tests=[self.round_(variants=[dict(label=x) for x in 'ABCD'])]),
                    dict(tests=[self.round_(screenshot='x' * 64)]), dict(review=5), dict(nonsense=1)]:
            with self.assertRaises((ValueError, KeyError), msg=str(bad)):
                self.results.save(self.p['id'], bad, 0)

    def test_sam_saves_merge_numbers_and_lessons(self):
        self.results.save(self.p['id'], dict(metrics=dict(views=1000), lessons=['Short tips need a second hook']), 0)
        done = self.results.apply([dict(project_id=self.p['id'], metrics=dict(emails=40), lessons=['Short tips need a second hook', 'Text with a verb wins']),
                                   dict(project_id='missing', funnel='top')])
        self.assertEqual([d['action'] for d in done], ['results', 'failed'])
        r = self.results.get(self.p['id'])
        self.assertEqual(r['metrics'], dict(views=1000, emails=40))
        self.assertEqual(r['lessons'], ['Short tips need a second hook', 'Text with a verb wins'])

    def test_sam_save_keeps_everything_when_a_screenshot_is_unusable(self):
        # What went wrong on 8 Nov: a screenshot that wasn't a saved image threw the whole save away.
        kept = shot(self.store, 'red')
        self.results.save(self.p['id'], dict(tests=[self.round_(screenshot=kept)]), 0)
        first = self.results.get(self.p['id'])['tests'][0]
        file = self.root / 'graph.png'
        Image.new('RGB', (320, 180), 'blue').save(file)
        done = self.results.apply([dict(
            project_id=self.p['id'], funnel='top', metrics=dict(views=830),
            tests=[dict(self.round_(), id=first['id'], screenshot='screenshot of the test panel')],
            retention=dict(screenshot=str(file), notes='x' * 5000, summary='Holds well after the hook'),
            transcript='Hook line.\nSection one.' * 2000)])
        self.assertEqual(done[0]['action'], 'results')
        self.assertEqual(done[0]['title'], 'Make them explain')
        self.assertIn('Drop them on the Results tab', done[0]['note'])
        r = self.results.get(self.p['id'])
        self.assertEqual((r['funnel'], r['metrics']['views']), ('top', 830))
        self.assertEqual(r['tests'][0]['screenshot'], kept)
        self.assertEqual(self.store.asset(r['retention']['screenshot'])[0].suffix, '.png')
        self.assertTrue(r['transcript'].startswith('Hook line.'))

    def test_failed_video_save_names_the_video(self):
        done = self.results.apply([dict(project_id=self.p['id'], funnel='sideways')])
        self.assertEqual((done[0]['action'], done[0]['title']), ('failed', 'Make them explain'))

    def test_sam_reads_his_goals_and_sees_results_with_screenshots(self):
        (self.root / 'team/goals').mkdir(parents=True)
        (self.root / 'team/goals/sam.md').write_text('Customers matter more than views.', encoding='utf-8')
        team = Team(self.store)
        agents = Agents(self.store, self.root, team)
        agents.results = self.results
        aid = shot(self.store, 'green')
        self.results.save(self.p['id'], dict(funnel='bottom', tests=[self.round_(winner='A', screenshot=aid)]), 0)
        sam = next(a for a in team.all()['agents'] if a['id'] == 'sam')
        self.assertIn('Customers matter more than views.', agents.desk_system_prompt(sam, []))
        self.assertIn('Customers matter more than views.', agents.system_prompt(sam, []))
        snap = agents.desk_snapshot('sam', [])
        self.assertIn(self.p['id'], snap)
        self.assertIn('winner A', snap)
        self.assertIn(str(self.store.asset(aid)[0]), snap)
        self.assertNotIn('winner A', agents.desk_snapshot('laia', []))
        focus = agents.focus({'kind': 'project', 'id': self.p['id']}, [])
        self.assertIn('"videos" list', focus)
        self.assertIn('"funnel": "bottom"', focus)


if __name__ == '__main__':
    unittest.main()
