"""The scorecard's windows, comparisons, gaps and revenue groups, on made-up snapshots. No Linklr call is made."""
import json
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from studio.scorecard import Scorecard
from studio.store import Store

ROOT = Path(__file__).resolve().parents[1]


def day(d, **kw):
    r = dict(date=d.isoformat(), course_usd=0, upsell_usd=0, clients_usd=0, other_usd=0, refunds_usd=0, purchases=0,
             new_subscribers=0, unsubscribes=0)
    r.update(kw)
    return r


class ScorecardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)
        self.sc = Scorecard(self.store, ROOT, self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, today, daily, **extra):
        snap = dict(fetched_at='2026-12-15T06:00:00+00:00', response_seconds=20, today_utc=today.isoformat(),
                    status=dict(youtube_data_through='2026-10-16', last_payment_at='2026-12-14T10:00:00+00:00', warnings=[]),
                    daily=daily, acq1=dict(by_type={'youtube_video': 2, 'community_post': 1}),
                    acq31=dict(by_type={'youtube_video': 32, 'community_post': 11, 'qr_code': 2}),
                    acq61=dict(by_type={'youtube_video': 52, 'community_post': 30, 'qr_code': 3}),
                    mtd_summary=dict(udemy_income=50000, youtube_income=0), course_payments=[], missing=[])
        snap.update(extra)
        (self.tmp / 'scorecard' / 'snapshot.json').write_text(json.dumps(snap), encoding='utf-8')

    def full(self, today, **per_day):
        return [day(today - timedelta(days=i), **per_day) for i in range(61, -1, -1)]

    def card(self, v, key):
        return next(c for c in v['cards'] if c['key'] == key)

    def test_windows_are_completed_utc_days(self):
        today = date(2026, 12, 15)
        self.write(today, self.full(today))
        w = self.sc.view()['windows']
        self.assertEqual(w['current'], dict(start='2026-11-15', end='2026-12-14'))
        self.assertEqual(w['previous'], dict(start='2026-10-16', end='2026-11-14'))

    def test_full_comparison_gives_absolute_change(self):
        today = date(2026, 12, 15)
        daily = self.full(today, new_subscribers=3, unsubscribes=1)
        for r in daily:
            if r['date'] >= '2026-11-15':
                r['new_subscribers'] = 5
        self.write(today, daily)
        h = self.card(self.sc.view(), 'email')['headline']
        self.assertEqual((h['value'], h['previous'], h['delta']), (120, 60, 60))

    def test_days_before_tracking_are_not_zero_and_block_the_change(self):
        today = date(2026, 11, 11)
        self.write(today, self.full(today, new_subscribers=2))
        h = self.card(self.sc.view(), 'email')['headline']
        self.assertTrue(h['previous_partial'])
        self.assertIsNone(h['delta'])
        self.assertIsNone(h['daily']['previous'][0])
        self.assertIn('1 October', h['previous_note'])

    def test_missing_day_is_a_gap_not_a_zero(self):
        today = date(2026, 12, 15)
        daily = [r for r in self.full(today, new_subscribers=4) if r['date'] != '2026-12-01']
        self.write(today, daily)
        h = self.card(self.sc.view(), 'email')['headline']
        self.assertIsNone(h['daily']['current'][16])
        self.assertTrue(h['current_partial'])
        self.assertIsNone(h['delta'])
        self.assertEqual(h['value'], 29 * 4)

    def test_real_zero_days_count_as_zero(self):
        today = date(2026, 12, 15)
        self.write(today, self.full(today))
        h = self.card(self.sc.view(), 'email')['headline']
        self.assertEqual((h['value'], h['delta']), (0, 0))

    def test_revenue_groups(self):
        today = date(2026, 12, 15)
        self.write(today, self.full(today, course_usd=100, upsell_usd=10, clients_usd=50, other_usd=5, refunds_usd=7, purchases=2))
        v = self.sc.view()
        rev = self.card(v, 'revenue')
        self.assertEqual(rev['headline']['value'], 30 * 165)  # before refunds, Udemy not added in
        self.assertEqual(rev['supporting'][0]['value'], 30 * 110)  # course and upsells only
        self.assertEqual(rev['supporting'][1]['value'], 30 * 50)  # services
        self.assertIn('Udemy $500.00', rev['headline']['note'])
        self.assertIn('unassigned', rev['headline']['note'])
        course = self.card(v, 'course')
        self.assertEqual(course['supporting'][2]['value'], 30 * 110)  # clients and other excluded

    def test_course_purchases_count_by_purchase_date(self):
        today = date(2026, 12, 15)
        pays = [dict(at='2026-11-15T00:10:00+00:00', cents=34500, refunded_cents=0), dict(at='2026-12-14T23:59:00+00:00', cents=34500, refunded_cents=0),
                dict(at='2026-12-15T01:00:00+00:00', cents=34500, refunded_cents=0), dict(at='2026-11-14T23:50:00+00:00', cents=34500, refunded_cents=0)]
        self.write(today, self.full(today), course_payments=pays)
        h = self.card(self.sc.view(), 'course')['headline']
        self.assertEqual((h['value'], h['previous']), (2, 1))  # today and the day before the window are left out

    def test_course_purchases_unavailable_when_report_missing(self):
        today = date(2026, 12, 15)
        self.write(today, self.full(today), course_payments=None)
        h = self.card(self.sc.view(), 'course')['headline']
        self.assertFalse(h['available'])

    def test_attribution_leaves_today_out(self):
        today = date(2026, 12, 15)
        self.write(today, self.full(today))
        v = self.sc.view()
        yt = self.card(v, 'youtube')['supporting'][2]
        self.assertEqual((yt['value'], yt['previous']), (30, 20))
        car = self.card(v, 'carousels')['supporting'][2]
        self.assertEqual((car['value'], car['previous']), (12, 20))

    def test_unavailable_cards_say_why(self):
        today = date(2026, 12, 15)
        self.write(today, self.full(today))
        v = self.sc.view()
        self.assertFalse(self.card(v, 'youtube')['headline']['available'])
        self.assertIn('2026-10-16', self.card(v, 'youtube')['headline']['reason'])
        self.assertTrue(all(not m['available'] for c in v['cards'] if c['key'] == 'learning' for m in [c['headline']] + c['supporting']))

    def test_month_to_date_against_same_days_and_course_goal(self):
        today = date(2026, 12, 11)
        self.write(today, self.full(today, course_usd=100, upsell_usd=20, clients_usd=1000))
        t = self.sc.view()['mtd']
        self.assertEqual((t['completed_days'], t['course_upsell'], t['course_upsell_prev']), (10, 1200, 1200))
        self.assertEqual((t['compare']['start'], t['compare']['end']), ('2026-11-01', '2026-11-10'))
        self.assertEqual(t['basis'], 'course_upsell')
        self.assertEqual(t['gross'], 11200)
        self.assertEqual(t['needed_per_day'], round((15000 - 1200) / 21, 2))

    def test_first_of_month_has_no_completed_days(self):
        today = date(2026, 12, 1)
        self.write(today, self.full(today, course_usd=100))
        t = self.sc.view()['mtd']
        self.assertEqual(t['completed_days'], 0)
        self.assertEqual(t['course_upsell'], 0)

    def test_failed_refresh_keeps_last_good(self):
        today = date(2026, 12, 15)
        self.write(today, self.full(today, new_subscribers=1))
        before = self.sc.snapshot()
        with mock.patch.object(Scorecard, '_raw', side_effect=RuntimeError('Linklr is down')):
            self.sc._refresh()
        self.assertEqual(self.sc.snapshot(), before)
        self.assertEqual(self.sc.attempt()['state'], 'failed')
        self.assertIn('Linklr is down', self.sc.attempt()['error'])

    def test_refresh_keeps_no_buyer_details(self):
        today = date.today()
        buyers = dict(rows=[dict(purchase_at='2026-11-08T10:29:28+00:00', purchase_email='someone@example.com', usd_amount=54700, refunds=[],
                                 linked_contacts=[dict(email='someone@example.com')])])
        calls = self.sc._calls(today)
        raw = {(n, json.dumps(a, sort_keys=True)): (buyers if n == 'get_course_buyer_journeys' else
                                                    dict(daily=[day(today)]) if n == 'get_daily_metrics' else {}) for n, a in calls}
        want = [(n, json.dumps(a, sort_keys=True)) for n, a in calls]
        with mock.patch.object(Scorecard, '_raw', return_value=(raw, want, [])):
            self.sc._refresh()
        text = (self.tmp / 'scorecard' / 'snapshot.json').read_text(encoding='utf-8')
        self.assertNotIn('@', text)
        self.assertEqual(set(self.sc.snapshot()['course_payments'][0]), {'at', 'cents', 'refunded_cents'})

    def test_persisted_tool_result_is_read_and_removed(self):
        f = self.tmp / 'big.txt'
        f.write_text(json.dumps([dict(type='text', text='{"rows": []}')]), encoding='utf-8')
        body = f'<persisted-output>\nOutput too large (59.7KB). Full output saved to: {f}\n\nPreview (first 2KB):\n[...]\n</persisted-output>'
        self.assertEqual(json.loads(Scorecard._persisted(body)), {'rows': []})
        self.assertFalse(f.exists())


if __name__ == '__main__':
    unittest.main()
