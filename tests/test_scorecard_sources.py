"""Carousel counts, CareerOS reviews and the untracked daily stats on the Scorecard and in Max's brief.
No Claude, CareerOS, YouTube or Linklr call is made. Carousel rows and CareerOS replies are shaped like the real ones
read on 13 Nov 2026, with made-up people."""
import json
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from studio import business as B
from studio import scorecard as S
from studio.scorecard import Scorecard
from studio.store import Store

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def post(pid, post_date, posted_at, results, link=True, **kw):
    r = dict(id=pid, stage='Posted', post_date=post_date, posted_at=posted_at, archived=False, results=results,
             link=f'https://www.youtube.com/post/Ugkx{pid}abcdefghijklmnop' if link else '')
    r.update(kw)
    return r


# The four posts imported together on 7 Nov, as they are in Josh OS: real publish dates, d1 views, d7 likes only.
IMPORTED = [
    post('A', '2026-11-04', '2026-11-07T15:22:33Z', {'d1': dict(views=3261, likes=79, comments=3, checked_at='2026-11-07'),
                                                     'd7': dict(likes=86, comments=3, checked_at='2026-11-13')}),
    post('B', '2026-11-02', '2026-11-07T15:22:33Z', {'d1': dict(views=1981, likes=16, checked_at='2026-11-07'),
                                                     'd7': dict(likes=17, checked_at='2026-11-13')}),
    post('C', '2026-11-06', '2026-11-07T15:22:33Z', {'d1': dict(views=987, likes=34, checked_at='2026-11-07'),
                                                     'd7': dict(likes=47, checked_at='2026-11-13')}),
    post('D', '2026-11-05', '2026-11-07T16:24:51Z', {'d1': dict(views=1414, likes=12, checked_at='2026-11-07'),
                                                     'd7': dict(likes=14, checked_at='2026-11-13')}),
    post('E', '2026-11-07', '2026-11-07T15:22:33Z', {'d1': dict(likes=31, comments=2, checked_at='2026-11-13')}),
]


def review(i, at, **kw):
    r = dict(review_id=f'r{i}', learner_id=f'u{i}', learner_email=f'person{i}@example.com', name=f'Person {i}', job_title='Manager',
             rating=5, before='before text', after_first_use='after text', body='review text', approved_for_display=False,
             learner_allows_public_use=False, incentivised=True, submitted_at=at, updated_at=at)
    r.update(kw)
    return r


def stream(reviews, args=None, status='connected', calls=1):
    lines = [json.dumps(dict(type='system', subtype='init', mcp_servers=[dict(name='careeros', status=status)]))]
    for n in range(calls):
        lines.append(json.dumps(dict(type='assistant', message=dict(content=[dict(type='tool_use', id=f't{n}', name=S.REVIEWS_TOOL,
                                                                                  input=args if args is not None else {'limit': 200})]))))
        lines.append(json.dumps(dict(type='user', message=dict(content=[dict(type='tool_result', tool_use_id=f't{n}', content=[dict(
            type='text', text=json.dumps(dict(reviews=reviews, note='Approval and publication are unchanged by these tools.')))])]))))
    return '\n'.join(lines)


class Carousels(unittest.TestCase):
    def test_publish_date_stable_id_and_newest_reading_of_each_count(self):
        ps = {p['id']: p for p in S.carousel_posts(IMPORTED)}
        self.assertEqual(ps['A']['day'], '2026-11-04')                          # not the 7 Nov import day
        self.assertEqual(ps['A']['post_id'], 'UgkxAabcdefghijklmnop')
        self.assertEqual((ps['A']['views'], ps['A']['views_checked']), (3261, '2026-11-07'))   # d7 has no views, d1 is kept
        self.assertEqual((ps['A']['likes'], ps['A']['likes_checked']), (86, '2026-11-13'))
        self.assertIsNone(ps['E']['views'])

    def test_lower_later_reading_is_used_and_flagged_and_missing_publish_date_is_said(self):
        rows = [post('F', '', '2026-11-09T10:00:00Z', {'d1': dict(views=500, checked_at='2026-11-10'),
                                                      'd7': dict(views=450, checked_at='2026-11-16')})]
        p = S.carousel_posts(rows)[0]
        self.assertEqual((p['views'], p['lowered'], p['day'], p['day_basis']), (450, ['views'], '2026-11-09', 'marked_posted'))

    def test_archived_and_unposted_are_left_out(self):
        rows = [post('G', '2026-11-03', None, {}), post('H', '2026-11-03', '2026-11-03T09:00:00Z', {}, archived=True)]
        self.assertEqual(S.carousel_posts(rows), [])


class Reviews(unittest.TestCase):
    def test_complete_read_keeps_only_ids_times_and_flags(self):
        snap = S.reviews_from_stream(stream([review(1, '2026-10-01T18:23:31.137351+00:00')]), '2026-11-13T02:46:00+00:00')
        self.assertTrue(snap['complete'], snap['reason'])
        self.assertEqual(snap['items'], [dict(id='r1', submitted_at='2026-10-01T18:23:31.137351+00:00', rating=5, approved=False,
                                              public_ok=False, incentivised=True)])
        text = json.dumps(snap)
        for private in ('person1@example.com', 'Person 1', 'review text', 'before text', 'Manager'):
            self.assertNotIn(private, text)

    def test_a_full_page_is_never_called_complete(self):
        many = [review(i, '2026-11-01T10:00:00+00:00') for i in range(200)]
        snap = S.reviews_from_stream(stream(many), 'x')
        self.assertFalse(snap['complete'])
        self.assertIn('no paging', snap['reason'])

    def test_bad_reads_are_incomplete(self):
        for out, why in ((stream([review(1, '2026-10-01T18:23:31')]), 'time zone'),
                         (stream([review(1, '2026-10-01T18:23:31+00:00'), review(1, '2026-10-02T18:23:31+00:00')]), 'twice'),
                         (stream([], args={'limit': 50}), 'every review'),
                         (stream([], calls=2), 'more than once'),
                         (stream([], calls=0), 'not read')):
            snap = S.reviews_from_stream(out, 'x')
            self.assertFalse(snap['complete'], why)
            self.assertIn(why, snap['reason'])
            self.assertEqual(snap['items'], [])


class Sources(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)
        self.sc = Scorecard(self.store, ROOT, self.tmp)
        today = date(2026, 11, 13)
        daily = [dict(date=(today - timedelta(days=i)).isoformat(), course_usd=0, upsell_usd=0, clients_usd=0, other_usd=0,
                      refunds_usd=0, purchases=0, new_subscribers=0, unsubscribes=0) for i in range(61, 0, -1)]
        (self.tmp / 'scorecard' / 'snapshot.json').write_text(json.dumps(dict(
            fetched_at='2026-11-13T02:45:28+00:00', today_utc=today.isoformat(), status={}, daily=daily,
            acq1=dict(by_type={}), acq31=dict(by_type={}), acq61=dict(by_type={}), course_payments=[], missing=[])), encoding='utf-8')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def card(self, key):
        return next(c for c in self.sc.view()['cards'] if c['key'] == key)

    def fake_run(self, outputs):
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw))
            return SimpleNamespace(stdout=outputs[min(len(calls), len(outputs)) - 1], stderr='', returncode=0)
        return run, calls

    def read(self, outputs):
        from unittest import mock
        run, calls = self.fake_run(outputs)
        with mock.patch('studio.agent.find_claude', return_value='claude.exe'):
            snap = self.sc.read_reviews(run=run, sleep=lambda s: None)
        return snap, calls

    def test_carousel_card_counts_posts_by_publish_date_and_never_plots_saved_checks(self):
        self.sc._carousels = lambda: IMPORTED
        c = self.card('carousels')
        h = c['headline']
        self.assertEqual(h['label'], 'Post impressions, all posts')
        self.assertFalse(h['available'])                                         # no Studio reads in this test
        self.assertNotIn('7643', json.dumps(c))                                  # the old import-day spike is gone
        self.assertNotIn('Views', json.dumps(c))
        self.assertEqual(c['supporting'][0]['value'], 5)                         # five posts, by their publish dates
        self.assertEqual(c['supporting'][1]['label'], 'Post impressions, tracked carousels')

    def test_reviews_read_tries_again_when_careeros_is_still_connecting_and_never_saves_people(self):
        pending = stream([], status='pending', calls=0)
        ok = stream([review(1, '2026-10-01T18:23:31+00:00'), review(2, '2026-11-12T08:00:00+00:00', approved_for_display=True)])
        snap, calls = self.read([pending, ok])
        self.assertTrue(snap['complete'])
        self.assertEqual(len(calls), 2)
        cmd, kw = calls[0]
        self.assertEqual(cmd[cmd.index('--allowedTools') + 1], S.REVIEWS_TOOL)  # the only tool this run may use
        self.assertEqual(kw['env']['CLAUDE_CODE_MCP_STARTUP_WAIT_MS'], '30000')
        saved = (self.tmp / 'scorecard' / 'reviews.json').read_text(encoding='utf-8')
        self.assertNotIn('example.com', saved)
        r = self.card('learning')['supporting'][3]
        self.assertEqual((r['label'], r['available'], r['value']), ('Reviews submitted', True, 1))   # 12 Nov is in the window
        self.assertIn('2 reviews in all, 1 approved for display', r['note'])

    def test_a_failed_read_keeps_the_last_complete_one_and_says_so(self):
        self.read([stream([review(1, '2026-11-12T08:00:00+00:00')])])
        snap, _ = self.read([stream([review(i, '2026-11-12T08:00:00+00:00') for i in range(200)])])
        self.assertFalse(snap['complete'])
        self.assertEqual(len(self.sc.reviews()['items']), 1)
        r = self.card('learning')['supporting'][3]
        self.assertIn('newest read failed', r['note'])

    def test_unread_reviews_and_untracked_stats_say_why(self):
        sup = {m['label']: m for m in self.card('learning')['supporting']}
        self.assertFalse(sup['Reviews submitted']['available'])
        self.assertFalse(sup['Resource requests']['available'])
        self.assertIn('not that it finished', sup['Resource requests']['reason'])
        self.assertFalse(sup['Learning time']['available'])
        self.assertIn('No validated playback aggregate', sup['Learning time']['reason'])


class Brief(unittest.TestCase):
    RV = dict(taken_at='2026-11-13T02:46:00+00:00', complete=True,
              items=[dict(id='r1', submitted_at='2026-10-01T18:23:31+00:00'), dict(id='r2', submitted_at='2026-11-12T08:00:00+00:00')])

    def test_reviews_on_the_money_day(self):
        r = B.review_facts(self.RV, {'state': 'ok'}, date(2026, 11, 12))
        self.assertEqual((r['count'], r['total'], r['notes']), (1, 2, []))
        self.assertEqual(B.review_lines(r), ['Reviews 1 · 2 in all'])

    def test_a_read_before_the_day_ended_or_a_failed_read_is_noted(self):
        early = dict(self.RV, taken_at='2026-11-12T20:00:00+00:00')
        self.assertIn('before Thu 12 Nov ended', B.review_facts(early, None, date(2026, 11, 12))['notes'][0])
        self.assertIn('newest reviews read failed', B.review_facts(self.RV, {'state': 'failed'}, date(2026, 11, 12))['notes'][0])

    def test_whole_brief_shows_reviews_once_and_stays_short(self):
        from tests.test_business import FakeScorecard, snapshot
        from studio.business import Business
        tmp = Path(tempfile.mkdtemp())
        try:
            sc = FakeScorecard(snapshot())
            sc.reviews, sc.reviews_attempt = (lambda: self.RV), (lambda: {'state': 'ok'})
            text = Business(Store(tmp), tmp, scorecard=sc).build(datetime(2026, 11, 12, 3, 0, tzinfo=UTC))['text']
            self.assertIn('Reviews: 0', text)                          # 11 Nov UTC, the brief's money day
            self.assertNotIn('Not tracked', text)
            self.assertFalse(any(line.startswith('-#') for line in text.splitlines()))
            self.assertLess(len(text), 1200)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_read_is_not_a_zero(self):
        r = B.review_facts(None, {'state': 'failed', 'reason': 'connector pending'}, date(2026, 11, 12))
        self.assertFalse(r['available'])
        self.assertEqual(B.review_lines(r), ['Reviews · not available today'])


if __name__ == '__main__':
    unittest.main()
