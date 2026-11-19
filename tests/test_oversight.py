"""Max's daily oversight (studio/oversight.py), routed through the real team message rules (studio/notices.py)."""
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
from studio import oversight as O  # noqa: E402
from studio.business import nudge_line  # noqa: E402
from studio.notices import Notices  # noqa: E402
from studio.store import Store  # noqa: E402

UTC = timezone.utc
TID = '6f1c1d2e-0000-4000-8000-000000000001'
DAY = date(2026, 11, 12)
AT = lambda h, d=DAY: datetime(d.year, d.month, d.day, h - 4, 0, tzinfo=UTC)   # h is the Dubai hour


def linklr(days=62, today=DAY, gap=None):
    rows = []
    for i in range(days):
        d = today - timedelta(days=i)
        if gap and d == gap:
            continue
        recent = i <= 7
        rows.append(dict(date=d.isoformat(), course_usd=100 if recent else 50, upsell_usd=10, new_subscribers=5 if recent else 3))
    return dict(daily=rows, today_utc=today.isoformat(), fetched_at=f'{today.isoformat()}T02:45:00+00:00')


def youtube(through=DAY - timedelta(days=3)):
    rows = [dict(date=(through - timedelta(days=i)).isoformat(), views=1000 if i < 7 else 1200, net_subscribers=10) for i in range(30)]
    return dict(daily=rows, data_through=through.isoformat(), fetched_at=f'{DAY.isoformat()}T02:45:00+00:00')


def kit():
    taken = datetime(2026, 11, 12, 2, 45, tzinfo=UTC)
    items = {'a': dict(send_at=(taken - timedelta(days=2)).isoformat(), status='completed', recipients=4000),
             'b': dict(send_at=(taken - timedelta(days=9)).isoformat(), status='completed', recipients=3900),
             'c': dict(send_at=(taken - timedelta(days=10)).isoformat(), status='completed', recipients=3800),
             'd': dict(send_at=(taken + timedelta(days=1)).isoformat(), status='scheduled', recipients=0)}
    return dict(complete=True, taken_at=taken.isoformat(), items=items)


class Goals(unittest.TestCase):
    def test_five_plain_trends_from_the_real_sources(self):
        g = {x['name']: x for x in O.goals(linklr(), youtube(), kit())}
        self.assertEqual((g['Sales (courses and upsells)']['current'], g['Sales (courses and upsells)']['previous']), (770, 420 + 50 * 0 + 0 * 0) if False else (770, 420))
        self.assertEqual(g['Emails collected']['direction'], 'up')
        self.assertEqual((g['Emails sent']['current'], g['Emails sent']['previous'], g['Emails sent']['deliveries']), (1, 2, 4000))
        self.assertEqual((g['YouTube views']['current'], g['YouTube views']['previous'], g['YouTube views']['direction']), (7000, 8400, 'down'))
        self.assertEqual(g['YouTube views']['end'], (DAY - timedelta(days=3)).isoformat())
        self.assertIn('up from $420.00', O.goal_line(g['Sales (courses and upsells)']))

    def test_missing_data_is_never_filled_in(self):
        g = {x['name']: x for x in O.goals(linklr(gap=DAY - timedelta(days=4)), None, None)}
        self.assertFalse(g['Sales (courses and upsells)']['available'])
        self.assertIn('missing', g['Sales (courses and upsells)']['reason'])
        self.assertFalse(g['YouTube views']['available'])
        self.assertFalse(g['Emails sent']['available'])
        self.assertIn('not available', O.goal_line(g['Emails sent']))
        rows = linklr()
        rows['daily'][3]['new_subscribers'] = None
        self.assertFalse({x['name']: x for x in O.goals(rows, None, None)}['Emails collected']['available'])


class Daily(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.store = Store(t / 'Data')
        late = self.store.create('Talk Like an Executive')
        self.store.save(late['id'], dict(publish_date='2026-11-10', stage='Editing',
                                         titles=[dict(id=TID, text='How to Talk Like an Executive')], chosen_title=TID), late['revision'])
        self.late = late['id']
        self.notices = Notices(self.store, self.store.root, bridge=None, busy=lambda: '')
        self.posts = []
        agents = SimpleNamespace(post=lambda who, text, notice=True: self.posts.append((who, text, notice)),
                                 thread=lambda key, who: [dict(role='agent', text=t) for w, t, _ in self.posts if w == who])
        self.tasks = [dict(id='k1', kind='task', status='Next', title='Prepare the November launch', due_date='2026-11-01', owner='Josh')]
        business = SimpleNamespace(_tasks=lambda: self.tasks)
        emails = SimpleNamespace(all=lambda: [dict(id='e1', subject='Sunday tips', planned_date='2026-11-11', status='Draft')])
        replies = SimpleNamespace(all=lambda states: [dict(id='r1aaaaaaaa', state='proposed'), dict(id='r2bbbbbbbb', state='proposed')])
        courses = SimpleNamespace(_all=lambda: [dict(id='l1', title='Lesson 3', stage='Script review')])
        self.now = AT(10)
        self.o = O.Oversight(self.store, self.store.root, business, self.notices, agents, None, emails, courses, replies, clock=lambda: self.now)
        self.o.sources = lambda: (linklr(), youtube(), kit())

    def tearDown(self):
        self.tmp.cleanup()

    def queued(self):
        return [n for n in self.notices.all(100) if n['key'].startswith('max:')]

    def test_runs_once_a_day_from_nine_and_posts_on_maxs_desk(self):
        self.now = AT(8)
        self.assertIsNone(self.o.tick())
        self.now = AT(10)
        rec = self.o.tick()
        self.assertTrue(rec['posted'])
        self.assertEqual([(w, n) for w, _, n in self.posts], [('max', False)])   # his desk only, never Discord
        text = self.posts[0][1]
        self.assertIn('How to Talk Like an Executive', text)                        # the title, not its id
        self.assertIn('Emails sent: 1 broadcast (4,000 deliveries)', text)
        self.now = AT(15)
        self.o.tick()
        self.assertEqual(len(self.posts), 1)                                       # idempotent for the day
        self.assertTrue((self.o.dir / 'oversight-2026-11-12.json').is_file())

    def test_nudges_go_through_the_owner_with_caps_and_gaps(self):
        self.o.tick()
        q = self.queued()
        self.assertEqual(len(q), O.NUDGES_PER_DAY)
        self.assertTrue({n['agent_id'] for n in q} <= {'sam', 'laia', 'saskia', 'anouk', 'tariq'})
        self.assertTrue(all("From Max's daily check" in n['footer'] for n in q))
        self.assertNotIn('max', {n['agent_id'] for n in q})                        # Josh's own task stays on Max's desk
        # The next day: different items, never the same one again within the gap.
        self.now = AT(10, DAY + timedelta(days=1))
        self.o.tick()
        q2 = self.queued()
        first = {n['key'].rsplit(':', 1)[0] for n in q}
        second = {n['key'].rsplit(':', 1)[0] for n in q2} - first
        self.assertEqual(len(q2), 3)                                    # the Kit-sent email is a check, not a nudge
        self.assertNotIn('laia', {n['agent_id'] for n in q2})
        self.assertFalse(first & second)
        # Every item reaches its lifetime cap and then stops.
        for i in range(2, 40):
            self.now = AT(10, DAY + timedelta(days=i))
            self.o.tick()
        per_item = {}
        for n in self.queued():
            per_item[n['key'].rsplit(':', 1)[0]] = per_item.get(n['key'].rsplit(':', 1)[0], 0) + 1
        self.assertTrue(per_item and max(per_item.values()) <= O.MAX_TIMES)

    def test_anouk_nudge_never_acts_and_muted_people_are_recorded_honestly(self):
        self.notices.set(dict(muted=['sam', 'laia', 'saskia']))
        rec = self.o.tick()
        anouk = [n for n in rec['nudges'] if n['agent'] == 'anouk']
        self.assertTrue(anouk)
        n = self.notices.record(anouk[0]['notice_key'])
        self.assertIn('Nothing goes out until you approve each one', n['text'])
        self.assertEqual(n['state'], 'queued')
        self.assertFalse({'sam', 'laia', 'saskia'} & {x['agent'] for x in rec['nudges']})   # muted: no nudge queued
        self.assertIn('Talk Like an Executive', rec['text'])                              # still on Max's desk
        self.notices.set(dict(muted=[], paused_until=(datetime.now(UTC) + timedelta(hours=2)).isoformat()))
        self.now = AT(10, DAY + timedelta(days=1))
        self.assertEqual(self.o.tick()['nudges'], [])                                     # paused: nothing queued

    def test_late_in_the_day_nothing_is_queued(self):
        self.now = AT(20)
        rec = self.o.tick()
        self.assertEqual(rec['nudges'], [])
        self.assertEqual(self.queued(), [])

    def test_outcome_is_read_back_not_assumed(self):
        rec = self.o.tick()
        k = rec['nudges'][0]['notice_key']
        b = self.notices.record(k)
        b.update(state='sent', message_ids=['123'])
        self.notices._put(b)
        self.now = AT(12)
        rec = self.o.tick()
        got = next(x for x in rec['nudges'] if x['notice_key'] == k)
        self.assertEqual((got['state'], got['message_ids']), ('sent', ['123']))
        self.assertTrue(all(x['state'] != 'sent' for x in rec['nudges'] if x['notice_key'] != k))

    def test_unknown_recipient_counts_stay_unknown(self):
        k = kit()
        k['items']['a']['recipients'] = None
        g = O.emails_sent(k)
        self.assertIsNone(g['deliveries'])
        self.assertIn('an unknown number of deliveries', O.goal_line(g))
        self.assertIn('(7,700 deliveries)', O.goal_line(g))

    def test_a_crash_at_any_step_resumes_without_duplicates(self):
        # 1. Crash after the plan is saved, before anything is queued.
        q = self.o.queue_planned
        self.o.queue_planned = lambda rec, now: (_ for _ in ()).throw(RuntimeError('power cut'))
        with self.assertRaises(RuntimeError):
            self.o.tick()
        saved = json.loads((self.o.dir / 'oversight-2026-11-12.json').read_text())
        planned = [n['notice_key'] for n in saved['nudges']]
        self.assertTrue(planned and self.queued() == [] and self.posts == [])
        self.o.queue_planned = q
        # 2. Crash after queueing, while posting.
        post = self.o.agents.post
        def boom(*a, **k):
            post(*a, **k)
            raise RuntimeError('crash right after posting')
        self.o.agents.post = boom
        with self.assertRaises(RuntimeError):
            self.o.tick()
        self.o.agents.post = post
        self.assertEqual(sorted(n['key'] for n in self.queued()), sorted(planned))      # the same keys, once each
        # 3. Restart: nothing is queued or posted again.
        self.o.tick()
        self.o.tick()
        self.assertEqual(sorted(n['key'] for n in self.queued()), sorted(planned))
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(json.loads((self.o.dir / 'nudges.json').read_text()).keys() >= {n['key'] for n in saved['nudges']}, True)

    def test_brief_names_the_video_not_its_title_id(self):
        p = self.store.get(self.late)
        self.assertIn('How to Talk Like an Executive', nudge_line('video_late', p, DAY))


class EmailTruth(unittest.TestCase):
    """Kit's own record beats the local plan, and Max never suggests sending an email (12 Nov 2026: the 10 Nov email
    went out from Kit while Josh OS still had it as Draft, and Max's first nudge said "Send it")."""
    TAKEN = datetime(2026, 11, 12, 2, 45, tzinfo=UTC)

    def snap(self, **items):
        return dict(complete=True, taken_at=self.TAKEN.isoformat(), items=items)

    def finding(self, kit_snap, kit_id=None, status='Draft', planned='2026-11-10'):
        o = O.Oversight(SimpleNamespace(projects=lambda: []), tempfile.mkdtemp(), None, None, None, None,
                        SimpleNamespace(all=lambda: [dict(id='e1', subject='Stop adding slides', planned_date=planned, status=status,
                                                          kit=dict(id=kit_id) if kit_id else None)]), None, None)
        return [f for f in o.findings(DAY, kit_snap) if f['key'].startswith('email:')]

    def test_stale_local_draft_but_kit_sent_it_unlinked(self):
        f = self.finding(self.snap(**{'26927691': dict(send_at='2026-11-10T14:21:04Z', status='completed', recipients=4163)}))
        self.assertEqual([(x['kind'], x['agent']) for x in f], [('reconcile', 'max')])
        self.assertNotIn('Send', f[0]['text'])
        self.assertIn("can't tell if it already went", f[0]['text'])

    def test_unlinked_with_nothing_nearby_is_still_only_a_check(self):
        f = self.finding(self.snap(old=dict(send_at='2026-11-01T10:00:00Z', status='completed')), planned='2026-11-09')
        self.assertEqual([(x['kind'], x['agent']) for x in f], [('reconcile', 'max')])   # may have gone another day
        self.assertNotIn('Send', f[0]['text'])

    def test_an_old_snapshot_cannot_prove_still_a_draft(self):
        old = dict(complete=True, taken_at='2026-11-08T02:45:00+00:00', items={'7': dict(send_at='2026-11-09T10:00:00Z', status='draft')})
        f = self.finding(old, kit_id=7, planned='2026-11-09')
        self.assertEqual(f[0]['kind'], 'reconcile')
        self.assertIn('too old', f[0]['text'])

    def test_linked_and_sent_in_kit_is_never_a_slip(self):
        f = self.finding(self.snap(**{'26927691': dict(send_at='2026-11-10T14:21:04Z', status='completed')}), kit_id=26927691)
        self.assertEqual(f[0]['kind'], 'reconcile')
        self.assertIn('Kit shows the email "Stop adding slides" sent', f[0]['text'])
        f = self.finding(self.snap(**{'5': dict(send_at='2026-11-13T14:00:00Z', status='scheduled')}), kit_id=5)
        self.assertIn('scheduled', f[0]['text'])

    def test_no_kit_evidence_is_a_check_not_a_nudge(self):
        self.assertEqual(self.finding(None, kit_id=7)[0]['kind'], 'reconcile')
        self.assertEqual(self.finding(dict(complete=False, items={}), kit_id=7)[0]['kind'], 'reconcile')

    def test_a_real_slip_needs_its_own_broadcast_still_a_draft_now_and_never_says_send(self):
        f = self.finding(self.snap(**{'7': dict(send_at='2026-11-09T10:00:00Z', status='draft')}), kit_id=7, planned='2026-11-09')
        self.assertEqual([(x['kind'], x['agent']) for x in f], [('slip', 'laia')])
        self.assertNotIn('Send', f[0]['text'])

    def test_reconcile_items_and_suppressed_items_are_never_nudged(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        o = O.Oversight(SimpleNamespace(projects=lambda: []), tmp.name, None, None, None, None, None, None, None)
        o._write('suppressed.json', {'email:e1': dict(reason='Kit sent it')})
        picks = o.pick([dict(key='email:e1:slip:2026-11-09', agent='laia', kind='slip'),
                        dict(key='email:e2:reconcile', agent='max', kind='reconcile'),
                        dict(key='email:e3:slip:2026-11-09', agent='laia', kind='slip')], DAY)
        self.assertEqual([x['key'] for x in picks], ['email:e3:slip:2026-11-09'])


if __name__ == '__main__':
    unittest.main()
