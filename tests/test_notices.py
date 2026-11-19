"""The team's own Discord messages (studio/notices.py), Anouk's proposed replies (studio/replies.py) and the bridge's
direct messages. No network, no Discord, no Gmail. A fake bridge and a fake read-only runner stand in for them."""
import json
import random
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
from studio import notices as N  # noqa: E402
from studio import replies as R  # noqa: E402
from studio import banter as BL  # noqa: E402
from studio import gmail_access  # noqa: E402
from studio.store import Store  # noqa: E402

UTC = timezone.utc
DUBAI = N.DUBAI


def dxb(s):
    """A Dubai wall-clock time as UTC."""
    return datetime.fromisoformat(s).replace(tzinfo=DUBAI).astimezone(UTC)


class FakeBridge:
    def __init__(self, ready=N.TEAM, results=None):
        self.ready, self.results, self.sent = set(ready), list(results or []), []

    def dm_ready(self, agent_id):
        return (agent_id in self.ready and agent_id not in N.NEVER), 'not connected'

    def send_dm(self, agent_id, text):
        self.sent.append((agent_id, text))
        r = self.results.pop(0) if self.results else dict(state='sent', message_ids=[str(len(self.sent))])
        if isinstance(r, Exception):
            raise r
        return r


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / 'Data'
        self.data.mkdir()
        self.store = Store(self.data)
        self.bridge = FakeBridge()
        self.busy = ''
        self.n = self.make()

    def make(self):
        n = N.Notices(self.store, self.data, self.bridge, lambda: self.busy, rng=random.Random(7))
        n.settings_override = None
        return n

    def tearDown(self):
        self.tmp.cleanup()

    def no_banter(self):
        self.n.set(dict(banter=False))


class Dedupe(Base):
    def test_a_key_is_only_ever_queued_and_sent_once_even_after_a_restart(self):
        self.no_banter()
        at = dxb('2026-11-16T10:00')
        self.assertIsNotNone(self.n.queue('tariq', 'work', 'Cut is ready.', 'post:1', at))
        self.assertIsNone(self.n.queue('tariq', 'work', 'Cut is ready.', 'post:1', at))
        self.n.tick(at)
        self.assertEqual(len(self.bridge.sent), 1)
        again = self.make()
        self.assertIsNone(again.queue('tariq', 'work', 'Cut is ready.', 'post:1', at + timedelta(hours=5)))
        again.tick(at + timedelta(hours=5))
        self.assertEqual(len(self.bridge.sent), 1)

    def test_a_bot_still_connecting_after_a_restart_waits_instead_of_dropping(self):
        self.no_banter()
        at = dxb('2026-11-16T10:00')
        self.bridge.ready = set()
        self.bridge.dm_ready = lambda a: (a in self.bridge.ready, 'The Discord bot is not connected right now.')
        self.n.queue('sam', 'work', 'Want a new date?', 'max:video:1', at)
        self.n.tick(at)
        self.assertEqual(self.n.record('max:video:1')['state'], 'queued')
        self.bridge.ready = {'sam'}
        self.n.tick(at + timedelta(minutes=1))
        self.assertEqual((self.n.record('max:video:1')['state'], len(self.bridge.sent)), ('sent', 1))
        self.bridge.dm_ready = lambda a: (False, 'No Discord bot is set up for this team member.')
        self.n.queue('laia', 'work', 'x', 'max:email:1', at + timedelta(hours=3))
        self.n.tick(at + timedelta(hours=3))
        self.assertEqual(self.n.record('max:email:1')['state'], 'no_bot')        # a setup problem is still final

    def test_a_send_cut_off_by_a_restart_is_never_repeated(self):
        self.no_banter()
        at = dxb('2026-11-16T10:00')
        b = self.n.queue('tariq', 'work', 'x', 'post:2', at)
        b.update(state='sending')
        self.n._put(b)
        again = self.make()
        self.assertEqual(again.record('post:2')['state'], 'uncertain')
        again.tick(at + timedelta(hours=3))
        self.assertEqual(self.bridge.sent, [])

    def test_a_refused_send_is_tried_again_later_but_an_unclear_one_is_not(self):
        self.no_banter()
        self.bridge.results = [dict(state='failed', reason='403'), dict(state='sent', message_ids=['9'])]
        at = dxb('2026-11-16T10:00')
        self.n.queue('tariq', 'work', 'x', 'post:3', at)
        self.n.tick(at)
        self.assertEqual(self.n.record('post:3')['state'], 'failed')
        self.n.tick(at + timedelta(minutes=5))
        self.assertEqual(len(self.bridge.sent), 1)          # too soon
        self.n.tick(at + timedelta(minutes=16))
        self.assertEqual(self.n.record('post:3')['state'], 'sent')
        self.bridge.results = [dict(state='uncertain', reason='timeout')]
        self.n.queue('sam', 'work', 'y', 'post:4', at + timedelta(hours=1))
        self.n.tick(at + timedelta(hours=1))
        self.n.tick(at + timedelta(hours=6))
        self.assertEqual([a for a, _ in self.bridge.sent].count('sam'), 1)


class QuietHoursAndFloods(Base):
    def test_nothing_goes_in_quiet_hours_and_the_morning_gets_one_message_per_person(self):
        self.no_banter()
        night = dxb('2026-11-16T22:30')
        for i in range(3):
            self.n.queue('tariq', 'work', f'Update {i}.', f'post:n{i}', night + timedelta(minutes=i))
        self.assertIn('quiet hours', self.n.tick(night + timedelta(minutes=10)))
        self.assertIn('quiet hours', self.n.tick(dxb('2026-11-17T07:59')))
        self.assertEqual(self.bridge.sent, [])
        self.n.tick(dxb('2026-11-17T08:01'))
        self.assertEqual(len(self.bridge.sent), 1)
        self.assertIn('Update 0.', self.bridge.sent[0][1])
        self.assertIn('Update 2.', self.bridge.sent[0][1])

    def test_a_backlog_is_capped_and_old_items_stay_in_josh_os(self):
        self.no_banter()
        at = dxb('2026-11-16T10:00')
        self.n.queue('tariq', 'work', 'Ancient.', 'post:old', at - timedelta(hours=30))
        for i in range(12):
            self.n.queue('tariq', 'work', f'Item {i}.', f'post:b{i}', at - timedelta(minutes=12 - i))
        self.n.tick(at)
        self.assertEqual(len(self.bridge.sent), 1)
        text = self.bridge.sent[0][1]
        self.assertIn('And 7 more on my desk in Josh OS.', text)
        self.assertNotIn('Ancient', text)
        self.assertEqual(self.n.record('post:old')['state'], 'stale')

    def test_held_while_recording_or_rendering(self):
        self.no_banter()
        at = dxb('2026-11-16T10:00')
        ran = []
        self.n.checks.append(lambda t: ran.append(t))
        self.n.queue('tariq', 'work', 'x', 'post:h', at)
        self.busy = 'OBS is open and might be recording'
        self.assertIn('Held', self.n.tick(at))
        self.assertEqual((self.bridge.sent, ran), ([], []))
        self.busy = ''
        self.n.tick(at + timedelta(minutes=30))
        self.assertEqual(len(self.bridge.sent), 1)
        self.assertEqual(len(ran), 1)


class Cooldowns(Base):
    def test_gaps_between_any_two_messages_and_per_person(self):
        self.no_banter()
        at = dxb('2026-11-16T10:00')
        self.n.queue('tariq', 'work', 'a', 'post:c1', at)
        self.n.queue('sam', 'work', 'b', 'post:c2', at)
        self.n.tick(at)
        self.n.tick(at + timedelta(minutes=10))
        self.assertEqual(len(self.bridge.sent), 1)               # 20 minutes between any two
        self.n.tick(at + timedelta(minutes=21))
        self.assertEqual(len(self.bridge.sent), 2)
        self.n.queue('tariq', 'work', 'c', 'post:c3', at + timedelta(minutes=30))
        self.n.tick(at + timedelta(minutes=60))
        self.assertEqual(len(self.bridge.sent), 2)               # Tariq waits two hours after his last
        self.n.tick(at + timedelta(minutes=121))
        self.assertEqual(len(self.bridge.sent), 3)

    def test_daily_caps(self):
        self.no_banter()
        at = dxb('2026-11-16T08:00')
        for i in range(6):
            self.n.queue('tariq', 'work', f't{i}', f'post:d{i}', at + timedelta(hours=2 * i, minutes=1))
            self.n.tick(at + timedelta(hours=2 * i, minutes=2))
        self.assertEqual(len(self.bridge.sent), 3)                # three a day per person


class PauseAndMute(Base):
    def test_commands(self):
        at = dxb('2026-11-16T10:00')
        self.assertIsNone(self.n.command('tariq', 'can you check the cut', at))
        self.assertIn('Paused', self.n.command('tariq', '!pause 2h', at))
        self.assertTrue(self.n.paused(at + timedelta(minutes=119)))
        self.assertFalse(self.n.paused(at + timedelta(minutes=121)))
        self.assertIn('Try !pause', self.n.command('tariq', '!pause forever', at))
        self.n.command('tariq', '!pause today', at)
        self.assertEqual(N.dubai(self.n.paused(at)).strftime('%d %H:%M'), '17 08:00')
        self.assertIn('Back on', self.n.command('tariq', '!resume', at))
        self.assertIsNone(self.n.paused(at))
        self.n.command('sam', '!mute', at)
        self.assertEqual(self.n.settings()['muted'], ['sam'])
        self.n.command('sam', '!unmute', at)
        self.assertEqual(self.n.settings()['muted'], [])
        self.n.command('laia', '!banter off', at)
        self.assertFalse(self.n.settings()['banter'])
        self.assertIn('Banter is off', self.n.command('laia', '!notices', at))

    def test_things_made_while_paused_or_muted_are_never_sent_later(self):
        self.no_banter()
        at = dxb('2026-11-16T10:00')
        self.n.command('tariq', '!pause 1h', at)
        self.n.queue('tariq', 'work', 'during pause', 'post:p1', at + timedelta(minutes=5))
        self.n.command('sam', '!mute', at)
        self.n.queue('sam', 'work', 'while muted', 'post:p2', at + timedelta(minutes=5))
        self.n.tick(at + timedelta(hours=2))
        self.n.command('sam', '!unmute', at)
        self.n.tick(at + timedelta(hours=3))
        self.assertEqual(self.bridge.sent, [])
        self.assertEqual({self.n.record('post:p1')['state'], self.n.record('post:p2')['state']}, {'skipped'})

    def test_settings_only_take_known_switches(self):
        with self.assertRaises(ValueError):
            self.n.set(dict(quiet_start=3))
        with self.assertRaises(ValueError):
            self.n.set(dict(paused_until='soon'))
        self.assertEqual(self.n.set(dict(muted=['max', 'tariq', 'nobody']))['muted'], ['tariq'])


class MaxStaysOff(Base):
    def test_max_never_sends(self):
        b = self.n.queue('max', 'work', 'brief', 'post:m', dxb('2026-11-16T10:00'))
        self.assertEqual(b['state'], 'skipped')
        self.assertFalse(self.n.can_send('max')[0])

    def test_max_connects_but_proactive_dm_remains_blocked(self):
        from studio.discord_bridge import Bridge
        (self.data / 'discord.json').write_text(json.dumps(dict(owners=['111'], bots=dict(max=dict(token='fake-a'), tariq=dict(token='fake-b')))))
        br = Bridge(SimpleNamespace(store=self.store))
        self.assertEqual(sorted(br.config['bots']), ['max', 'tariq'])
        self.assertEqual(br.status['kept_off'], [])
        self.assertFalse(br.dm_ready('max')[0])
        self.assertEqual(br.send_dm('max', 'hi')['state'], 'blocked')
        (self.data / 'discord.json').write_text(json.dumps(dict(owners=['111'], bots=dict(max=dict(token='fake-a')))))
        self.assertIn('max', Bridge(SimpleNamespace(store=self.store)).config['bots'])


class Banter(Base):
    def simulate(self, weeks=8, step=15):
        at = dxb('2026-11-16T00:00')
        end = at + timedelta(weeks=weeks)
        while at < end:
            self.n.tick(at)
            at += timedelta(minutes=step)

    def test_one_or_two_a_week_across_the_whole_team_in_working_hours(self):
        posted = []
        self.n.on_banter = lambda a, t: posted.append(a)
        self.simulate()
        sent = [b for b in self.n.all(400) if b['kind'] == 'banter' and b['state'] == 'sent']
        weeks = {}
        for b in sent:
            weeks.setdefault(N.week_key(N._ts(b['sent_at'])), []).append(b)
        self.assertTrue(all(1 <= len(v) <= 2 for v in weeks.values()), {k: len(v) for k, v in weeks.items()})
        self.assertGreaterEqual(len(weeks), 6)
        times = sorted(N._ts(b['sent_at']) for b in sent)
        self.assertTrue(all(b - a >= timedelta(days=2) for a, b in zip(times, times[1:])))
        for b in sent:
            local = N.dubai(N._ts(b['sent_at']))
            self.assertTrue(11 <= local.hour < 17 and local.weekday() < 5, local)
            self.assertIn(b['text'], BL.LINES[b['agent_id']])
        order = [b['agent_id'] for b in sorted(sent, key=lambda b: b['sent_at'])]
        self.assertTrue(all(x != y for x, y in zip(order, order[1:])))
        self.assertNotIn('max', order)
        # Sent exactly as written: no label, no disclaimer, nothing that gives it away (Josh, 12 Nov 2026).
        self.assertTrue(all(t in BL.LINES[a] for a, t in self.bridge.sent))
        self.assertFalse(any('made up' in t.lower() or 'banter' in t.lower() for _, t in self.bridge.sent))
        self.assertEqual(posted, [a for a, _ in self.bridge.sent])

    def test_banter_off_or_muted(self):
        self.n.set(dict(muted=['sam', 'tariq']))
        self.simulate(weeks=4)
        self.assertNotIn('sam', [a for a, _ in self.bridge.sent])
        self.assertNotIn('tariq', [a for a, _ in self.bridge.sent])
        before = len(self.bridge.sent)
        self.n.set(dict(banter=False))
        at = dxb('2026-12-14T00:00')
        for i in range(7 * 24 * 4):
            self.n.tick(at + timedelta(minutes=15 * i))
        self.assertEqual(len(self.bridge.sent), before)

    def test_banter_lines_follow_the_writing_rules(self):
        banned = ('quiet', 'land', 'honest', '\u2014')
        for agent, lines in BL.LINES.items():
            self.assertIn(agent, N.TEAM)
            for line in lines:
                low = line.lower()
                self.assertFalse(any(b in low.split() or b in low for b in ('quiet', 'honest', '\u2014')), line)
                self.assertNotRegex(low, r'\bland(ed)?\b')

    def test_banter_on_the_desk_is_marked_made_up(self):
        from studio.agent import Agents
        a = Agents.__new__(Agents)
        line = a.history_line(dict(role='agent', text='Can I take Biscuit to the vet?', banter=True))
        self.assertIn('made up', line)
        self.assertIn('never act on it', line)


class TariqReminders(Base):
    def test_one_reminder_for_a_script_left_a_day(self):
        self.no_banter()
        at = dxb('2026-11-16T10:00')
        made = (at - timedelta(hours=21)).isoformat()
        with self.store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS workflow_artifacts(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL)')
            c.execute('INSERT INTO workflow_artifacts VALUES(?,?,?,?)', ('a1', 'p1', 'edit_proposal', '{}'))
            c.execute('INSERT INTO workflow_artifacts VALUES(?,?,?,?)', ('a2', 'p2', 'edit_proposal', '{}'))
        arts = {'p1': dict(id='a1', created_at=made), 'p2': dict(id='a2', created_at=(at - timedelta(days=4)).isoformat())}
        flow = SimpleNamespace(status=lambda pid: [dict(key='proposal', state='review')],
                               latest=lambda pid, kind: arts[pid] if kind == 'edit_proposal' else None)
        store = SimpleNamespace(connect=self.store.connect, get=lambda pid: dict(name='4 Signs' if pid == 'p1' else 'Old'))
        self.n.checks.append(N.tariq_reminders(self.n, flow, store))
        self.n.tick(at)
        self.n.tick(at + timedelta(hours=3))
        self.assertEqual(len(self.bridge.sent), 1)
        self.assertIn('The script for 4 Signs has been waiting', self.bridge.sent[0][1])


GMAIL_ON = dict(enabled=True, verified_mailbox='josh@example.com', verified_at='2026-11-11T08:00:00+00:00',
                verification_method='owner_confirmation', verified_tools=list(gmail_access.READ_TOOLS))


class Mail(Base):
    def setUp(self):
        super().setUp()
        self.root = Path(self.tmp.name)
        (self.root / 'settings.json').write_text(json.dumps(dict(gmail_read=GMAIL_ON)), encoding='utf-8')
        self.replies = R.Replies(self.store, self.root)
        self.no_banter()

    def msg(self, i, when):
        return dict(thread_id=f'18c{i}', message_id=f'<m{i}@x>', from_name=f'Learner {i}', from_email=f'l{i}@example.com',
                    subject=f'Question {i}', received_at=when.isoformat(), summary='Asked about module 2.', needs='An answer.',
                    proposed_reply=f'Hi Learner {i},\n\nGood question.\n\nJosh', confidence='high')

    def test_first_check_saves_everything_but_only_messages_about_recent_mail(self):
        at = dxb('2026-11-16T10:00')
        prompts = []

        def run(prompt, schema):
            prompts.append(prompt)
            return dict(reply='Checked.', complete=True, messages=[self.msg(1, at - timedelta(hours=20)), self.msg(2, at - timedelta(hours=1))])
        r = self.replies.check_now(self.n, run, at)
        self.assertEqual((r['new'], r['pinged']), (2, 1))
        self.assertEqual(len(self.replies.all(('proposed',))), 2)
        self.n.tick(at)
        self.assertEqual(len(self.bridge.sent), 1)
        agent, text = self.bridge.sent[0]
        self.assertEqual(agent, 'anouk')
        self.assertIn('Learner 2', text)
        self.assertNotIn('Learner 1', text)
        self.assertIn('Nothing was drafted or sent in Gmail', text)
        self.assertIn('Do not create, update or delete Gmail drafts', prompts[0])
        self.assertIn('untrusted data', prompts[0])

    def test_the_same_email_is_never_saved_or_messaged_twice(self):
        at = dxb('2026-11-16T10:00')
        run = lambda p, s: dict(reply='', complete=True, messages=[self.msg(3, at - timedelta(minutes=30))])
        self.replies.check_now(self.n, run, at)
        r = self.replies.check_now(self.n, run, at + timedelta(hours=2))
        self.assertEqual((r['new'], r['pinged']), (0, 0))
        self.assertEqual(len(self.replies.all()), 1)

    def test_a_failed_check_changes_nothing(self):
        def run(p, s):
            raise RuntimeError('Gmail tools are not available')
        r = self.replies.check_now(self.n, run, dxb('2026-11-16T10:00'))
        self.assertEqual(r['state'], 'failed')
        self.assertEqual(self.replies.all(), [])

    def test_checks_run_every_two_hours_in_working_hours_only(self):
        self.assertTrue(self.replies.check_due(self.n, dxb('2026-11-16T09:00')))
        self.n.put_state('mail_last_start', dict(at=dxb('2026-11-16T09:00').isoformat()))
        self.assertFalse(self.replies.check_due(self.n, dxb('2026-11-16T10:30')))
        self.assertTrue(self.replies.check_due(self.n, dxb('2026-11-16T11:01')))
        self.assertFalse(self.replies.check_due(self.n, dxb('2026-11-16T20:30')))
        self.n.set(dict(mail_checks=False))
        self.assertFalse(self.replies.check_due(self.n, dxb('2026-11-16T13:00')))
        (self.root / 'settings.json').write_text('{}', encoding='utf-8')
        self.n.set(dict(mail_checks=True))
        self.assertFalse(self.replies.check_due(self.n, dxb('2026-11-16T13:00')))

    def test_josh_and_anouk_only_change_the_copy_in_josh_os(self):
        at = dxb('2026-11-16T10:00')
        self.replies.check_now(self.n, lambda p, s: dict(reply='', complete=True, messages=[self.msg(4, at)]), at)
        rid = self.replies.all()[0]['id']
        self.assertEqual(self.replies.update(rid, dict(proposed_reply='Shorter.'))['proposed_reply'], 'Shorter.')
        out = self.replies.apply([dict(id=rid, state='handled'), dict(id='nope', state='handled')])
        self.assertEqual([x['action'] for x in out], ['updated', 'failed'])
        self.assertEqual(self.replies.get(rid)['state'], 'handled')
        with self.assertRaises(ValueError):
            self.replies.update(rid, dict(state='sent'))
        self.assertNotIn('send', R.STATES)

    def test_no_gmail_write_tool_is_named_anywhere_in_the_new_code(self):
        for f in ('notices.py', 'replies.py', 'banter.py'):
            src = (ROOT / 'studio' / f).read_text(encoding='utf-8')
            for tool in ('create_draft', 'update_draft', 'send_message', 'reply', 'forward', 'label_message', 'trash'):
                self.assertNotIn('mcp__claude_ai_Gmail__' + tool, src, f)
                self.assertNotIn("'" + tool + "'", src.replace("'reply'", '').replace("'reply_proposals'", ''), (f, tool))

    def test_the_inbox_check_runs_read_only_with_only_the_gmail_read_tools(self):
        calls = []
        agents = SimpleNamespace(root=self.root, team=SimpleNamespace(all=lambda: dict(agents=[dict(id='anouk')], learnings=[])),
                                 desk_system_prompt=lambda a, l: 'system', thread=lambda p, a: [],
                                 run=lambda *a, **k: (calls.append((a, k)), ('', dict(reply='', complete=True, messages=[])))[1])
        server = SimpleNamespace(store=self.store, root=self.root, agents=agents, discord=None, edit_flow=None)
        real_loop = N.Notices.loop
        N.Notices.loop = lambda self, every=60: None
        try:
            n, replies = N.wire(server)
        finally:
            N.Notices.loop = real_loop
        n.mail_run('prompt', R.MAIL_SCHEMA)
        args, kw = calls[0]
        self.assertTrue(kw['read_only'])
        self.assertTrue(kw['extra_tools'])
        self.assertTrue(set(kw['extra_tools']) <= set(gmail_access.READ_TOOLS))
        self.assertIs(server.agents.replies, replies)
        (self.root / 'settings.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            n.mail_run('prompt', R.MAIL_SCHEMA)


class DeskPosts(Base):
    def test_job_results_become_work_messages_unless_josh_is_chatting_or_it_went_already(self):
        from studio.agent import Agents
        threads = {}
        a = Agents.__new__(Agents)
        a._append = lambda p, agent, *m: threads.setdefault(agent, []).extend(m) or threads[agent]
        a.thread = lambda p, agent: threads.get(agent, [])
        server = SimpleNamespace(store=self.store, root=Path(self.tmp.name), agents=a, discord=None, edit_flow=None)
        a.team = SimpleNamespace(all=lambda: dict(agents=[], learnings=[]))
        a.root = Path(self.tmp.name)
        real_loop = N.Notices.loop
        N.Notices.loop = lambda self, every=60: None
        try:
            n, _ = N.wire(server)
        finally:
            N.Notices.loop = real_loop
        a.post('tariq', '4 Signs is ready for you to review.')
        a.post('tariq', 'Already sent on Discord.', notice=False)
        a.post('max', 'Brief.')
        self.assertEqual([b['text'] for b in n.all() if b['kind'] == 'work' and b['state'] == 'queued'], ['4 Signs is ready for you to review.'])
        threads['sam'] = [dict(role='josh', text='make thumbnails', at=datetime.now(UTC).isoformat())]
        a.post('sam', 'Thumbnails done.')
        self.assertFalse([b for b in n.all() if b['agent_id'] == 'sam'])


if __name__ == '__main__':
    unittest.main()
