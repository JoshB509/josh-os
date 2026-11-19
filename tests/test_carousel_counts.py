"""Daily Studio post impressions (studio/carousel_counts.py) on made-up Studio reads. No Chrome, Claude or YouTube call."""
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from studio import business as B
from studio import carousel_counts as C
from studio.carousel_read_guard import allowed
from studio.scorecard import Scorecard
from studio.store import Store

ROOT = Path(__file__).resolve().parents[1]


def car(pid, caption, post_date='2026-11-04'):
    return dict(id=pid, stage='Posted', posted_at='2026-11-07T15:22:33Z', archived=False, post_date=post_date, caption=caption,
                link=f'https://www.youtube.com/post/Ugkx{pid}abcdefghijklmnopqr')


CARS = [car('A', 'Six phrases that make you sound more senior in meetings ⬇️\n\nWhich one?'),
        car('B', 'Five mistakes that get your work sent back ⬇️', '2026-11-02')]


def read(total=144385, through='Nov 11, 2026', rows=None):
    rows = rows if rows is not None else [['Six phrases that mak', 4119, 82], ['Five mistakes that g', 2099, 17], ['➡️ Walk into every m', 2000, 5]]
    return dict(range=f'Aug 30, 2011 – {through}', impressions_first=True, top_only=True, total=[total, 917], rows=rows)


def pages(r=None, n=None, over=None):
    over = over or {}
    rows = (r if r is not None else read())['rows']
    out = {}
    for i in range(C.PAGES):
        g = dict(p=i, n=len(rows) if n is None else n, range='Aug 30, 2011 – Nov 11, 2026', f=True, top=True,
                 total=[144385, 917], r=rows[i * C.PAGE_ROWS:(i + 1) * C.PAGE_ROWS])
        g.update(over.get(i, {}))
        out[i] = [g]
    return out


class Reading(unittest.TestCase):
    def test_pages_join_only_when_whole_and_consistent(self):
        j = C.join_pages(pages())
        self.assertEqual((j['total'][0], len(j['rows'])), (144385, 3))
        bad = pages()
        del bad[2]
        self.assertIn('not read in full', C.join_pages(bad)['error'])
        self.assertIn('changed between pages', C.join_pages(pages(over={1: dict(total=[144400, 917])}))['error'])
        self.assertIn('were read', C.join_pages(pages(n=70))['error'])
        twice = pages()
        twice[0].append(twice[0][0])
        self.assertIn('not read in full', C.join_pages(twice)['error'])

    def test_stream_keeps_only_exact_scripts_and_reads_json_followed_by_tab_text(self):
        res = json.dumps(pages()[0][0]).replace('Six phrases', 'Six\nphrases')
        lines = [dict(type='assistant', message=dict(content=[dict(type='tool_use', id='a', name=C.JS, input=dict(text=C.SCRIPTS[0])),
                                                              dict(type='tool_use', id='b', name=C.JS, input=dict(text=C.SCRIPTS[0] + ' '))])),
                 dict(type='user', message=dict(content=[dict(type='tool_result', tool_use_id='a', content=[dict(type='text', text=res), dict(type='text', text='\n\nTab Context: {x}')]),
                                                         dict(type='tool_result', tool_use_id='b', content=[dict(type='text', text=res)])]))]
        got = C.parse_stream('\n'.join(json.dumps(x) for x in lines))
        self.assertEqual(list(got), [0])
        self.assertEqual(got[0][0]['total'], [144385, 917])

    def test_guard_allows_only_the_page_url_scripts_and_browser(self):
        pol = C.policy()
        ok = lambda n, a: allowed(dict(tool_name='mcp__claude-in-chrome__' + n, tool_input=a), pol)  # noqa: E731
        self.assertTrue(ok('navigate', dict(url=C.STUDIO_URL)))
        self.assertFalse(ok('navigate', dict(url='https://studio.youtube.com/post/x/edit')))
        self.assertTrue(ok('javascript_tool', dict(action='javascript_exec', text=C.SCRIPTS[3])))
        self.assertFalse(ok('javascript_tool', dict(action='javascript_exec', text='document.cookie')))
        self.assertFalse(ok('select_browser', dict(deviceId='other')))
        self.assertFalse(ok('computer', dict(action='left_click')))
        self.assertFalse(ok('browser_batch', dict(actions=[])))
        self.assertFalse(allowed(dict(tool_name='Bash', tool_input={}), pol))


class Snapshot(unittest.TestCase):
    def test_channel_total_kept_apart_from_tracked_posts_by_stable_id(self):
        s = C.snapshot(read(), CARS, 't')
        self.assertTrue(s['complete'])
        self.assertEqual((s['metric'], s['channel_impressions'], s['through']), ('post_impressions', 144385, '2026-11-11'))
        self.assertEqual(s['items']['UgkxAabcdefghijklmnopqr']['impressions'], 4119)
        self.assertNotIn('views', json.dumps(s))

    def test_one_studio_row_is_never_given_to_two_tracked_posts(self):
        twins = CARS + [car('C', 'Six phrases that make you sound more senior in meetings ⬇️ again')]
        s = C.snapshot(read(), twins, 't')
        self.assertNotIn('UgkxAabcdefghijklmnopqr', s['items'])
        self.assertNotIn('UgkxCabcdefghijklmnopqr', s['items'])
        self.assertEqual(s['missing']['UgkxCabcdefghijklmnopqr'], 'one Studio row matches more than one tracked post')
        self.assertIn('UgkxBabcdefghijklmnopqr', s['items'])

    def test_two_possible_rows_or_no_row_is_missing_not_zero(self):
        rows = [['Six phrases that mak', 4119, 82], ['Six phrases that', 10, 1]]
        s = C.snapshot(read(rows=rows), CARS, 't')
        self.assertEqual(s['missing']['UgkxAabcdefghijklmnopqr'], 'more than one Studio row matches')
        self.assertEqual(s['missing']['UgkxBabcdefghijklmnopqr'], "not in Studio's top 50")
        self.assertEqual(s['items'], {})

    def test_wrong_page_is_refused(self):
        self.assertFalse(C.snapshot(dict(read(), impressions_first=False), CARS, 't')['complete'])
        self.assertFalse(C.snapshot({'error': 'no table'}, CARS, 't')['complete'])


class Growth(unittest.TestCase):
    def snap(self, through, total, a=4119, b=None, day='2026-11-13'):
        items = {'A': dict(impressions=a, post_date='2026-11-04')}
        if b is not None:
            items['B'] = dict(impressions=b, post_date='2026-11-12')
        return dict(complete=True, taken_at=day + 'T02:50:00+00:00', through=through, channel_impressions=total, items=items, day=day)

    def test_baseline_same_day_one_day_and_gap(self):
        base = self.snap('2026-11-11', 144385)
        self.assertEqual(C.growth(None, base)['state'], 'baseline')
        self.assertEqual(C.growth(base, self.snap('2026-11-11', 144500))['state'], 'no_new_day')
        g = C.growth(base, self.snap('2026-11-12', 146000, 4300, b=800))
        self.assertEqual((g['channel_impressions'], g['tracked'], g['daily']), (1615, {'A': 181, 'B': 800}, True))
        gap = C.growth(base, self.snap('2026-11-14', 150000))
        self.assertEqual((gap['days'], gap['daily']), (3, False))

    def test_decrease_and_old_post_without_baseline_are_left_out(self):
        base = self.snap('2026-11-11', 144385)
        g = C.growth(base, self.snap('2026-11-12', 144000, 4000))
        self.assertEqual((g['channel_impressions'], g['tracked'], sorted(g['decreased'])), (None, {}, ['A', 'channel']))
        old = self.snap('2026-11-12', 145000, 4200, b=50)
        old['items']['B']['post_date'] = '2026-11-01'
        self.assertEqual(C.growth(base, old)['no_baseline'], ['B'])


class Stored(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)
        self.reads = []
        self.cc = C.CarouselCounts(self.store, ROOT, fetch=lambda: self.reads.pop(0), carousels=lambda: CARS)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_baseline_is_never_a_day_and_the_next_comparable_read_is(self):
        self.reads = [read(), {'error': 'Chrome is closed'}, read(144500), read(146000, 'Nov 12, 2026')]
        self.cc.collect(date(2026, 11, 13))
        self.assertEqual(self.cc.daily(), {})
        self.assertEqual(self.cc.collect(date(2026, 11, 13))['day'], '2026-11-13')            # complete, not read again
        self.assertEqual(len(self.reads), 3)
        self.assertFalse(self.cc.collect(date(2026, 11, 14))['complete'])                      # failed read kept, not compared
        self.assertTrue(self.cc.collect(date(2026, 11, 14))['complete'])                        # a later try replaces it
        self.assertEqual(self.cc.growth(date(2026, 11, 14))['state'], 'no_new_day')            # same Studio day as baseline
        self.cc.collect(date(2026, 11, 15))
        # Against the newest read of the previous Studio day (144,500), not the baseline
        self.assertEqual(self.cc.daily(), {'2026-11-12': dict(channel=1500, tracked=0, posts=2)})

    def test_brief_lines_say_impressions_and_keep_all_posts_apart(self):
        self.assertEqual(B.carousel_lines(dict(state='baseline', through='2026-11-11', taken_at='x')),
                         ["Post impressions · first Studio count taken, so daily figures start with Studio's next day"])
        line = B.carousel_lines(dict(state='ok', since='2026-11-11', through='2026-11-12', days=1, daily=True,
                                     channel_impressions=1615, tracked={'A': 181, 'B': 800}))[0]
        self.assertEqual(line, 'Post impressions 1,615 on all posts · 981 on 2 tracked carousels (Thu 12 Nov, Studio)')
        self.assertNotIn('view', line.lower())

    def test_scorecard_draws_no_baseline_day(self):
        self.reads = [read()]
        self.cc.collect(date(2026, 11, 13))
        sc = Scorecard(self.store, ROOT, self.tmp)
        sc._carousels = lambda: CARS
        h = next(c for c in sc.view()['cards'] if c['key'] == 'carousels')['headline']
        self.assertEqual(h['label'], 'Post impressions, all posts')
        self.assertFalse(h['available'])
        self.assertIn('Baseline taken', h['reason'])
        self.assertTrue(all(v is None for v in h['daily']['current']))


if __name__ == '__main__':
    unittest.main()
