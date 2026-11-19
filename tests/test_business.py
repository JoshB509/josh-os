"""Max, the Business Manager (studio/business.py). No network, no Discord, no Linklr: fake sources and a fake bridge."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
from studio import business as B  # noqa: E402
from studio.business import Business  # noqa: E402
from studio.discord_bridge import business_audience, split, Bridge  # noqa: E402
from studio.store import Store  # noqa: E402
from studio.tasks import Tasks  # noqa: E402
from studio.team import Team  # noqa: E402

UTC = timezone.utc


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def row(day, **kw):
    r = dict(date=day, course_usd=49.0, upsell_usd=0, clients_usd=669.99, other_usd=0, refunds_usd=0, net_usd=718.99, purchases=2,
             tracked_link_clicks=108, sales_page_views=104, sales_page_visitors=79, checkout_clicks=2, new_subscribers=21, unsubscribes=7,
             clicks_by_source=dict(community_post=32, youtube_video=71, lead_magnet_doc=1, broadcast_email=2, behaviour_email=2))
    r.update(kw)
    return r


def snapshot(fetched='2026-11-12T02:50:00+00:00', days=('2026-11-01', '2026-11-11'), **status):
    d0, d1 = (datetime.fromisoformat(d).date() for d in days)
    rows = [row((d0 + timedelta(days=i)).isoformat()) for i in range((d1 - d0).days + 1)]
    st = dict(last_email_list_sync_at='2026-11-12T02:15:00Z', last_activity_at='2026-11-12T02:30:00Z')
    st.update(status)
    return dict(fetched_at=fetched, today_utc=fetched[:10], status=st, daily=rows,
                course_payments=[dict(at='2026-11-11T09:00:00+00:00', cents=4900, refunded_cents=0)])


YT = dict(source='YouTube Analytics', timezone='America/Los_Angeles', data_through='2026-11-09', missing_days=0,
          fetched_at='2026-11-12T02:51:00+00:00', daily=[dict(date='2026-11-09', views=8149, net_subscribers=36)])


class FakeScorecard:
    def __init__(self, snap, yt=YT, fail_refresh=False):
        self.snap, self.fail, self.refreshes = snap, fail_refresh, 0
        self.youtube = SimpleNamespace(snapshot=lambda: yt, attempt=lambda: {'state': 'ok'})

    def snapshot(self):
        return self.snap

    def attempt(self):
        return {'state': 'ok'}

    running = False

    def refreshing(self):
        return self.running

    def refresh(self, wait=False):
        self.refreshes += 1
        if self.fail:
            raise RuntimeError('Linklr is down')


class FakeBridge:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def send_fixed(self, agent_id, settings, parts):
        self.calls.append((agent_id, settings['channel_id'], list(parts)))
        r = self.results.pop(0) if self.results else dict(state='sent')
        if isinstance(r, Exception):
            raise r
        if r.get('state') == 'sent' and 'message_ids' not in r:
            r = dict(r, message_ids=[str(1000 + i) for i in range(len(parts))], guild_id=settings['guild_id'], channel_id=settings['channel_id'])
        return r


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.tasks = Tasks(self.store)
        self.sc = FakeScorecard(snapshot())
        self.bridge = FakeBridge()
        self.biz = self.make()
        self.enable()

    def make(self):
        return Business(self.store, self.root, self.sc, self.tasks, self.bridge)

    def enable(self, **kw):
        s = dict(B.SETTINGS_TEMPLATE, enabled=True, channel_id='1570000000000000001', bot_user_id='1570000000000000099')
        s.update(kw)
        (self.root / 'business').mkdir(exist_ok=True)
        (self.root / 'business' / 'settings.json').write_text(json.dumps(s), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()


class DatesAndSources(Base):
    def test_not_due_before_7_dubai_and_due_at_7(self):
        self.assertIsNone(self.biz.tick(at('2026-11-12T02:59:00')))
        self.assertEqual(self.bridge.calls, [])
        r = self.biz.tick(at('2026-11-12T03:00:00'))
        self.assertEqual((r['key'], r['state']), ('brief:2026-11-12', 'sent'))

    def test_dubai_date_names_the_brief_not_utc(self):
        # 21:30 UTC on the 11th is 01:30 on the 12th in Dubai, which is before 07:00, so nothing is due.
        self.assertIsNone(self.biz.due(at('2026-11-11T21:30:00')))
        self.assertEqual(self.biz.due(at('2026-11-12T03:30:00')).isoformat(), '2026-11-12')

    def test_linklr_day_is_the_completed_utc_day_with_exact_label(self):
        b = self.biz.build(at('2026-11-12T03:00:00'))
        self.assertEqual(b['sources']['linklr']['day'], '2026-11-11')
        self.assertEqual(b['sources']['linklr']['timezone'], 'UTC')
        self.assertFalse(b['sources']['linklr']['stale'])
        self.assertIn('**💰 Sales**', b['text'])
        for row in ('Revenue: $718.99', 'Course revenue: $49.00', 'Client revenue: $669.99', 'Upsell revenue: $0.00', 'Course sales: 1', 'Total sales: 2'):
            self.assertIn(row, b['text'])
        self.assertNotIn('04:00', b['text'])

    def test_snapshot_read_before_day_ended_falls_back_and_says_so(self):
        f = B.linklr_facts(snapshot(fetched='2026-11-11T04:44:45+00:00'), at('2026-11-12T03:00:00'))
        self.assertEqual(f['day'], '2026-11-10')
        self.assertTrue(f['stale'])
        self.sc.snap = snapshot(fetched='2026-11-11T04:44:45+00:00')
        text = self.biz.build(at('2026-11-12T03:00:00'))['text']
        self.assertIn('Older data: sales, email list and link clicks Tue 10 Nov', text)
        self.assertNotIn('-#', text)

    def test_sync_before_day_end_is_flagged_as_maybe_incomplete(self):
        f = B.linklr_facts(snapshot(last_email_list_sync_at='2026-11-11T22:00:00Z'), at('2026-11-12T03:00:00'))
        self.assertIn('Email joins and unsubscribes may be incomplete, since Linklr synced them before the day ended.', f['notes'])
        self.assertEqual(len(f['notes']), 1)   # nothing else is flagged when the other sync is on time

    def test_missing_day_is_unavailable_not_zero(self):
        s = snapshot(days=('2026-11-01', '2026-11-10'))
        f = B.linklr_facts(s, at('2026-11-12T03:00:00'))
        self.assertFalse(f['available'])
        self.sc.snap = s
        text = self.biz.build(at('2026-11-12T03:00:00'))['text']
        self.assertIn('Revenue: unavailable', text)
        self.assertNotIn('Gross $', text)

    def test_no_snapshot_and_no_youtube(self):
        self.sc.snap = None
        self.sc.youtube.snapshot = lambda: None
        text = self.biz.build(at('2026-11-12T03:00:00'))['text']
        self.assertIn('Revenue: unavailable', text)
        self.assertIn('Views: unavailable', text)

    def test_youtube_uses_its_own_pacific_day(self):
        text = self.biz.build(at('2026-11-12T03:00:00'))['text']
        self.assertEqual(self.biz.build(at('2026-11-12T03:00:00'))['sources']['youtube']['day'], '2026-11-09')
        self.assertIn('Views: 8,149', text)
        self.assertIn('Net subscribers: +36', text)

    def test_brief_is_just_the_numbers(self):
        text = self.biz.build(at('2026-11-12T03:00:00'))['text']
        for gone in ('Not in the daily numbers', 'Not tracked yet', 'Linklr read', 'YouTube read', 'Ask me about any number',
                     'as Linklr reports it', 'weren\'t reported by YouTube', 'The goal counts', 'running totals', ' UTC, which is'):
            self.assertNotIn(gone, text)
        self.assertFalse(any(line.startswith('-#') for line in text.splitlines()))   # no notes when nothing would mislead
        self.assertLess(len(text), 1200)

    def test_no_upsell_count_is_invented(self):
        text = self.biz.build(at('2026-11-12T03:00:00'))['text']
        self.assertNotIn('Upsell sales: 0', text)
        self.assertNotIn('upsell count', text.lower())
        self.assertIn('Upsell revenue: $0.00', text)
        self.assertIn('Upsell sales: unavailable', text)

    def test_utc_days_carry_their_dubai_window(self):
        text = self.biz.build(at('2026-11-12T03:00:00'))['text']
        self.assertEqual(self.biz.build(at('2026-11-12T03:00:00'))['sources']['linklr']['timezone'], 'UTC')
        self.assertEqual(B.utc_window(datetime(2026, 11, 30).date()), 'Mon 30 Nov UTC, which is 04:00 Mon 30 Nov to 04:00 Tue 1 Dec in Dubai')
        self.assertNotIn('yesterday', text.lower())
        self.assertNotIn(' am', text)
        self.assertNotIn(' pm', text)

    def test_click_sources_as_saved(self):
        text = self.biz.build(at('2026-11-12T03:00:00'))['text']
        self.assertIn('Video link clicks: 71', text)
        self.assertIn('Community-post link clicks: 32', text)
        self.assertEqual(B.click_sources(None, 108)[0], 'Clicks by source were not reported for this day.')
        self.assertEqual(B.click_sources({}, 0)[0], 'No clicks by source were reported for this day.')
        line, note = B.click_sources({'qr_code': 0, 'youtube_video': 5, 'new_thing': 1}, 9)
        self.assertEqual(line, 'Clicks by source · YouTube videos 5 · new thing 1 · QR codes 0')   # zero kept, not dropped
        self.assertEqual(note, 'The sources add up to 6 of the 9 tracked clicks.')
        s = snapshot()
        del s['daily'][-1]['clicks_by_source']
        self.sc.snap = s
        self.assertIn('Video link clicks: unavailable', self.biz.build(at('2026-11-12T03:00:00'))['text'])

    def test_josh_confirmation_is_saved_on_the_goal_and_measured(self):
        g = next(t for t in self.tasks.all()['items'] if t['id'] == B.SEEDED_GOAL_ID)
        self.assertEqual(g['evidence'], B.GOAL_CONFIRMATION)
        self.assertIn('USD15,000 per month in course plus upsell revenue, excluding client, Udemy and unassigned/other income', g['evidence'])
        self.assertEqual((g['owner'], g['revision'], g['title']), ('Josh', 2, 'Reach USD 15,000/month from courses + upsells'))
        self.assertTrue(B.goal_info(self.tasks.all()['items'])['confirmed'])
        text = self.biz.build(at('2026-11-12T03:00:00'))['text']
        self.assertNotIn('this month', text)
        self.assertEqual(self.biz.build(at('2026-11-12T03:00:00'))['sources']['linklr']['mtd']['course_upsell'], 539.0)
        self.assertNotIn("hasn't been confirmed", text)
        self.assertNotIn('starting default', text)
        # Running Business again changes nothing.
        self.make()
        self.assertEqual(next(t for t in self.tasks.all()['items'] if t['id'] == B.SEEDED_GOAL_ID)['revision'], 2)

    def test_untouched_seed_without_confirmation_is_not_measured(self):
        seed = dict(id=B.SEEDED_GOAL_ID, revision=1, kind='goal', status='Next', title='Reach USD 15,000/month from courses + upsells')
        self.assertFalse(B.goal_info([seed])['confirmed'])
        self.assertTrue(B.goal_info([dict(seed, evidence=B.GOAL_CONFIRMATION)])['confirmed'])

    def test_confirmation_never_overrides_josh_or_restores_a_deleted_goal(self):
        for case in ('edited', 'done', 'deleted'):
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as d:
                    store = Store(Path(d))
                    tasks = Tasks(store)
                    g = next(t for t in tasks.all()['items'] if t['id'] == B.SEEDED_GOAL_ID)
                    if case == 'edited':
                        tasks.save(g['id'], {'title': 'Reach USD 12,000/month from courses + upsells'}, 1)
                    elif case == 'done':
                        tasks.save(g['id'], {'status': 'Done'}, 1)
                    else:
                        with store.connect() as c:
                            c.execute('DELETE FROM task_items WHERE id=?', (g['id'],))
                    Business(store, Path(d), self.sc, tasks, self.bridge)
                    after = next((t for t in tasks.all()['items'] if t['id'] == B.SEEDED_GOAL_ID), None)
                    if case == 'deleted':
                        self.assertIsNone(after)
                    else:
                        self.assertEqual((after['revision'], after.get('evidence', '')), (2, ''))
                        if case == 'edited':
                            self.assertEqual(after['title'], 'Reach USD 12,000/month from courses + upsells')

    def test_finished_goal_means_no_goal_figure(self):
        g = next(t for t in self.tasks.all()['items'] if t['id'] == B.SEEDED_GOAL_ID)
        self.tasks.save(g['id'], {'status': 'Done'}, g['revision'])
        text = self.biz.build(at('2026-11-12T03:00:00'))['text']
        self.assertNotIn('this month', text)
        self.assertNotIn('15,000', text)
        self.assertNotIn('goal', text.lower())

    def test_net_mismatch_is_shown_not_hidden(self):
        s = snapshot()
        s['daily'][-1]['net_usd'] = 600.0
        f = B.linklr_facts(s, at('2026-11-12T03:00:00'))
        self.assertTrue(any("isn't gross minus refunds" in n for n in f['notes']))

    def test_linklr_outage_still_sends_last_good_snapshot_with_dates(self):
        self.sc.fail = True
        self.sc.snap = snapshot(fetched='2026-11-11T04:44:45+00:00')
        r = self.biz.tick(at('2026-11-12T03:00:00'))
        self.assertEqual(self.sc.refreshes, 1)
        self.assertEqual(r['state'], 'sent')
        self.assertEqual(r['sources']['linklr']['day'], '2026-11-10')
        self.assertTrue(r['sources']['linklr']['stale'])


class Timing(Base):
    def test_loop_wakes_on_the_prefetch_and_brief_minutes(self):
        self.assertEqual(B.next_wake(at('2026-11-12T02:59:30')), 30)
        self.assertEqual(B.next_wake(at('2026-11-12T02:44:50')), 10)
        self.assertEqual(B.next_wake(at('2026-11-12T02:59:59.800000')), 0.5)
        self.assertEqual(B.next_wake(at('2026-11-12T03:00:01')), 60)
        self.assertEqual(B.next_wake(at('2026-11-12T12:00:00')), 60)
        # Late evening Dubai: the next marks are tomorrow, still never more than a minute away.
        self.assertEqual(B.next_wake(at('2026-11-12T20:00:00')), 60)

    def test_brief_goes_on_the_first_tick_at_07_00_and_not_a_second_before(self):
        self.assertIsNone(self.biz.tick(at('2026-11-12T02:59:59')))
        self.assertEqual(self.biz.tick(at('2026-11-12T03:00:00'))['state'], 'sent')

    def test_prefetch_starts_once_at_06_45_and_sends_nothing(self):
        self.sc.snap = snapshot(fetched='2026-11-11T04:44:45+00:00')
        self.assertIsNone(self.biz.prefetch(at('2026-11-12T02:44:59')))
        self.assertEqual(self.biz.prefetch(at('2026-11-12T02:45:00')), 'done')
        self.assertIsNone(self.biz.prefetch(at('2026-11-12T02:50:00')))
        self.assertEqual(self.sc.refreshes, 1)
        self.assertEqual(self.bridge.calls, [])

    def test_fresh_snapshot_is_not_refreshed_again(self):
        self.biz.tick(at('2026-11-12T03:00:00'))
        self.assertEqual(self.sc.refreshes, 0)

    def test_slow_linklr_delays_the_brief_two_minutes_at_most(self):
        self.sc.snap = snapshot(fetched='2026-11-11T04:44:45+00:00')
        self.sc.running = True       # a refresh that never finishes
        slept = []
        self.biz._sleep = slept.append
        r = self.biz.tick(at('2026-11-12T03:00:00'))
        self.assertEqual(sum(slept), B.REFRESH_WAIT_S)
        self.assertEqual(self.sc.refreshes, 0)   # one already running, so no second one
        self.assertEqual(r['state'], 'sent')
        self.assertTrue(r['sources']['linklr']['stale'])
        self.assertIn('Older data:', r['text'])
        self.assertIn("Linklr doesn't have Wed 11 Nov yet.", r['sources']['linklr']['notes'])


class Delivery(Base):
    def test_second_trigger_and_restart_do_not_send_again(self):
        self.biz.tick(at('2026-11-12T03:00:00'))
        self.biz.tick(at('2026-11-12T03:01:00'))
        self.make().tick(at('2026-11-12T03:30:00'))
        self.assertEqual(len(self.bridge.calls), 1)
        r = self.biz.record('brief:2026-11-12')
        self.assertEqual(r['state'], 'sent')
        self.assertTrue(r['message_ids'])
        self.assertEqual(r['channel_id'], '1570000000000000001')

    def test_crash_while_sending_becomes_uncertain_and_is_never_resent(self):
        body = self.biz.claim('brief:2026-11-12', lambda: self.biz.build(at('2026-11-12T03:00:00')), at('2026-11-12T03:00:00'))
        self.assertEqual(body['state'], 'sending')
        again = self.make()   # Josh OS restarts mid-send
        self.assertEqual(again.record('brief:2026-11-12')['state'], 'uncertain')
        self.assertEqual(again.tick(at('2026-11-12T04:00:00'))['state'], 'uncertain')
        self.assertEqual(self.bridge.calls, [])

    def test_uncertain_and_partial_outcomes_are_not_retried(self):
        for result in (dict(state='uncertain', reason='timeout'), dict(state='partial', message_ids=['1'], reason='stopped')):
            with self.subTest(result=result['state']):
                self.tearDown()
                self.setUp()
                self.bridge.results = [result]
                self.biz.tick(at('2026-11-12T03:00:00'))
                self.biz.tick(at('2026-11-12T04:00:00'))
                self.assertEqual(len(self.bridge.calls), 1)
                self.assertEqual(self.biz.record('brief:2026-11-12')['state'], result['state'])

    def test_exception_in_send_is_uncertain(self):
        self.bridge.results = [RuntimeError('socket closed')]
        r = self.biz.tick(at('2026-11-12T03:00:00'))
        self.assertEqual(r['state'], 'uncertain')

    def test_blocked_is_retried_after_the_gap_with_the_same_payload(self):
        self.bridge.results = [dict(state='blocked', reason='bot not connected'), dict(state='sent')]
        first = self.biz.tick(at('2026-11-12T03:00:00'))
        self.assertEqual(first['state'], 'blocked')
        self.biz.tick(at('2026-11-12T03:05:00'))
        self.assertEqual(len(self.bridge.calls), 1)
        second = self.biz.tick(at('2026-11-12T03:11:00'))
        self.assertEqual(second['state'], 'sent')
        self.assertEqual(self.bridge.calls[0][2], self.bridge.calls[1][2])
        self.assertEqual(second['attempts'], 2)

    def test_attempts_are_capped(self):
        self.bridge.results = [dict(state='failed', reason='403')] * 10
        t = at('2026-11-12T03:00:00')
        for i in range(10):
            self.biz.tick(t + timedelta(minutes=11 * i))
        self.assertEqual(len(self.bridge.calls), B.MAX_ATTEMPTS)

    def test_switched_off_records_blocked_and_sends_nothing(self):
        self.enable(enabled=False)
        r = self.biz.tick(at('2026-11-12T03:00:00'))
        self.assertEqual(r['state'], 'blocked')
        self.assertIn('not switched on', r['reason'])
        self.assertEqual(self.bridge.calls, [])
        self.enable()
        self.assertEqual(self.biz.tick(at('2026-11-12T03:20:00'))['state'], 'sent')

    def test_missed_window_is_recorded_not_sent_late(self):
        r = self.biz.tick(at('2026-11-12T08:01:00'))
        self.assertIsNone(r)
        self.assertEqual(self.biz.record('brief:2026-11-12')['state'], 'missed')
        self.assertEqual(self.bridge.calls, [])

    def test_long_brief_is_split_under_discords_limit(self):
        for i in range(3):
            self.tasks.create(dict(title=f'Task {i} ' + 'z' * 230, due_date='2026-11-05'))
        b = self.biz.build(at('2026-11-12T03:00:00'))
        self.assertEqual(len(b['parts']), 1)
        self.assertNotIn('Task 0', b['text'])
        long = b['text'] + '\n' + 'Long report ' * 600
        parts = split(long)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 1900 for p in parts))
        self.assertEqual(''.join(parts).replace(' ', '').replace('\n', ''), long.replace(' ', '').replace('\n', ''))


class Privacy(unittest.TestCase):
    OWNERS = ['111']
    BASE = dict(found=True, guild_id='9', expected_guild_id='9', bot_user_id='77', expected_bot_user_id='77', can_send=True,
                owner_is_josh=True, our_bot_ids=['77', '78'], unconnected_bots=[], member_count=3)

    def check(self, **kw):
        return business_audience(dict(self.BASE, **kw), self.OWNERS)

    def test_count_matches_josh_plus_his_bots(self):
        self.assertEqual(self.check(), (True, ''))

    def test_extra_members_block_without_member_list(self):
        ok, why = self.check(member_count=6)
        self.assertFalse(ok)
        self.assertIn('6 members', why)

    def test_member_list_with_a_stranger_blocks(self):
        ok, why = self.check(member_count=6, viewers=['111', '77', '555'])
        self.assertFalse(ok)
        self.assertIn('1 account(s)', why)

    def test_member_list_of_only_josh_and_bots_passes_even_in_bigger_server(self):
        self.assertTrue(self.check(member_count=6, viewers=['111', '77', '78'])[0])

    def test_wrong_guild_wrong_bot_cannot_post(self):
        self.assertFalse(self.check(guild_id='8')[0])
        self.assertFalse(self.check(bot_user_id='70')[0])
        self.assertFalse(self.check(can_send=False)[0])
        self.assertFalse(self.check(found=False)[0])

    def test_server_owner_does_not_matter_when_channel_readers_are_known(self):
        self.assertTrue(self.check(owner_is_josh=False, member_count=40, viewers=['111', '77'])[0])
        self.assertFalse(self.check(owner_is_josh=False, member_count=40, viewers=['111', '77', '999'])[0])

    def test_count_method_needs_josh_as_the_one_person(self):
        ok, why = self.check(owner_is_josh=False)
        self.assertFalse(ok)
        self.assertIn('Server Members Intent is off', why)

    def test_unconnected_bot_blocks_count_method(self):
        self.assertFalse(self.check(unconnected_bots=['tariq'])[0])

    def test_fixed_channel_uses_pinned_ids_only(self):
        g = SimpleNamespace(id=9, name='CareerOS Office', owner_id=111, member_count=6, chunked=False, default_role='everyone',
                            me='me')
        perms = SimpleNamespace(send_messages=True, view_channel=True)
        ch = SimpleNamespace(id=42, name='business', guild=g, permissions_for=lambda who: perms, members=[])
        client = SimpleNamespace(user=SimpleNamespace(id=77), get_channel=lambda cid: ch if cid == 42 else None,
                                 intents=SimpleNamespace(members=False), get_guild=lambda gid: g)
        br = Bridge.__new__(Bridge)
        br.config, br.clients, br.status = dict(owners=['111'], bots={'max': {}}), {'max': client}, dict(bots={'max': {'connected': True}})
        found, why, ev = br.fixed_channel(client, 'max', dict(guild_id='9', channel_id='42', bot_user_id='77'))
        self.assertIsNone(found)
        self.assertIn('6 members', why)
        self.assertEqual(ev['channel_id'], '42')
        g.member_count = 2
        found, why, ev = br.fixed_channel(client, 'max', dict(guild_id='9', channel_id='42', bot_user_id='77'))
        self.assertIs(found, ch)
        self.assertIsNone(br.fixed_channel(client, 'max', dict(guild_id='9', channel_id='43'))[0])
        self.assertIn('not pinned', br.fixed_channel(client, 'max', dict(guild_id='9', channel_id=''))[1])


class TodosAndNudges(Base):
    def test_create_complete_snooze_reopen_show_changed_record(self):
        saved, lines = self.biz.apply_todos([dict(action='create', title='Ask Priya for a review', next_step='Send the ask in Slack',
                                                  due_date='2026-11-14', evidence='Josh in Discord, 12 Nov')])
        self.assertEqual(saved[0]['action'], 'created')
        tid = saved[0]['id']
        self.assertIn('Ask Priya for a review · Next · owner Josh · due 2026-11-14', lines[0])
        _, lines = self.biz.apply_todos([dict(action='snooze', id=tid, snoozed_until='2026-11-16')])
        self.assertIn('snoozed until 2026-11-16', lines[0])
        _, lines = self.biz.apply_todos([dict(action='complete', id=tid)])
        self.assertIn('· Done ·', lines[0])
        _, lines = self.biz.apply_todos([dict(action='reopen', id=tid)])
        self.assertIn('· Next ·', lines[0])
        item = next(t for t in self.tasks.all()['items'] if t['id'] == tid)
        self.assertEqual(item['evidence'], 'Josh in Discord, 12 Nov')

    def test_bad_changes_fail_cleanly(self):
        saved, lines = self.biz.apply_todos([dict(action='complete', id='nope'), dict(action='snooze', id='nope'),
                                             dict(action='create', title='')])
        self.assertEqual([s['action'] for s in saved], ['failed'] * 3)
        self.assertEqual(lines, [])

    def test_only_verified_unfinished_items_are_nudged(self):
        today = '2026-11-12'
        self.tasks.create(dict(title='Overdue thing', due_date='2026-11-10', next_step='Write the outline'))
        self.tasks.create(dict(title='Done thing', due_date='2026-11-10', status='Done'))
        self.tasks.create(dict(title='Idea thing', due_date='2026-11-10', status='Idea'))
        self.tasks.create(dict(title='Later thing', due_date='2026-11-10', status='Later'))
        self.tasks.create(dict(title='No date thing'))
        self.tasks.create(dict(title='Snoozed thing', due_date='2026-11-10', snoozed_until='2026-11-20'))
        lines = [l for _, l in self.biz.pick_nudges(at(today + 'T03:00:00'))]
        joined = '\n'.join(lines)
        self.assertIn('"Overdue thing" is still open, due Tue 10 Nov. Done? Tell me and I\'ll close it. If not, give me a new date.', joined)
        self.assertNotIn('was due', joined)
        self.assertNotIn("haven't started", joined)
        for name in ('Done thing', 'Idea thing', 'Later thing', 'No date thing', 'Snoozed thing'):
            self.assertNotIn(name, joined)
        # The seeded launch task is open in the list with a past date, so it is a real, listed commitment.
        self.assertIn('Prepare the November 1 launch', joined)

    def test_repeat_gap_quiet_hours_and_cap(self):
        for i in range(5):
            self.tasks.create(dict(title=f'Late {i}', due_date='2026-11-0' + str(i + 2)))
        first = self.biz.pick_nudges(at('2026-11-12T03:00:00'))
        self.assertEqual(len(first), B.MAX_NUDGES)
        self.biz.mark_nudged([k for k, _ in first], datetime(2026, 11, 12).date())
        nxt = self.biz.pick_nudges(at('2026-11-13T03:00:00'))
        self.assertTrue(set(k for k, _ in first).isdisjoint(k for k, _ in nxt))
        self.assertEqual(self.biz.pick_nudges(at('2026-11-12T18:00:00')), [])   # 22:00 Dubai, quiet hours
        again = self.biz.pick_nudges(at('2026-11-14T03:00:00'))
        self.assertTrue(set(k for k, _ in first) & set(k for k, _ in again))

    def test_sent_brief_counts_its_nudges(self):
        self.tasks.create(dict(title='Late one', due_date='2026-11-10'))
        before = self.biz.nudge_state()
        r = self.biz.tick(at('2026-11-12T03:00:00'))
        self.assertEqual(r['nudge_keys'], [])
        self.assertEqual(self.biz.nudge_state(), before)
        self.assertNotIn('Late one', r['text'])

    def test_video_with_close_date_at_early_stage(self):
        p = self.store.create('Promotion video')
        p = self.store.save(p['id'], dict(publish_date='2026-11-14', stage='Scripting'), p['revision'])
        lines = [l for _, l in self.biz.pick_nudges(at('2026-11-12T03:00:00'))]
        self.assertIn('"Promotion video" goes out Sat 14 Nov and the board still has it at Scripting.', '\n'.join(lines))
        self.biz.apply_todos([dict(action='snooze_video', id=p['id'], snoozed_until='2026-11-14')])
        self.assertFalse(any('Promotion video' in l for _, l in self.biz.pick_nudges(at('2026-11-12T03:00:00'))))

    def test_tasks_rank_before_videos_and_old_video_dates_are_left_alone(self):
        old = self.store.create('Silent Test')
        self.store.save(old['id'], dict(publish_date='2026-10-19', stage='Scripting'), old['revision'])
        recent = self.store.create('Fresh video')
        self.store.save(recent['id'], dict(publish_date='2026-11-09', stage='Scripting'), recent['revision'])
        keys = [k for k, _ in self.biz.pick_nudges(at('2026-11-12T03:00:00'))]
        self.assertTrue(keys[0].startswith('task:'))
        self.assertIn(f"video:{recent['id']}:late", keys)
        self.assertNotIn(f"video:{old['id']}:late", keys)

    def test_nudge_words_follow_josh_rules(self):
        self.tasks.create(dict(title='Late one', due_date='2026-11-10'))
        text = self.biz.build(at('2026-11-12T03:00:00'))['text']
        for banned in ('Late one', 'Your list', 'past due', "Tell me and I'll"):
            self.assertNotIn(banned, text)


class MaxProfile(unittest.TestCase):
    def test_untouched_max_gets_the_new_profile_and_edited_max_keeps_edits(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d))
            Team(store)
            with store.connect() as c:
                body = json.loads(c.execute("SELECT body FROM team_agents WHERE id='max'").fetchone()['body'])
                body.update(status='Coming later', check_in='Weekly, once the business metrics are added.',
                            personality='Commercial and numbers-first. Keeps every conversation tied to revenue.')
                c.execute("UPDATE team_agents SET body=? WHERE id='max'", (json.dumps(body),))
            m = next(a for a in Team(store).all()['agents'] if a['id'] == 'max')
            self.assertEqual(m['status'], 'Draft')
            self.assertIn('07:00 Dubai', m['check_in'])
            self.assertEqual(m['name'], 'Max')
            with store.connect() as c:
                body = json.loads(c.execute("SELECT body FROM team_agents WHERE id='max'").fetchone()['body'])
                body['check_in'] = 'My own words'
                c.execute("UPDATE team_agents SET body=? WHERE id='max'", (json.dumps(body),))
            m = next(a for a in Team(store).all()['agents'] if a['id'] == 'max')
            self.assertEqual(m['check_in'], 'My own words')


if __name__ == '__main__':
    unittest.main()
