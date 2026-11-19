"""Kit broadcast snapshots and growth (studio/counters.py), and how Max's brief shows them. No Kit, no Claude Code."""
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
from studio import counters as C  # noqa: E402
from studio import business as B  # noqa: E402
from studio.store import Store  # noqa: E402
from studio.tasks import Tasks  # noqa: E402
from tests.test_business import FakeScorecard, FakeBridge, snapshot, at  # noqa: E402


def bc(bid, opened, clicks=0, sent='2026-11-01T13:00:00Z', status='completed', recipients=4000):
    return dict(id=bid, subject='s', send_at=sent, stats=dict(recipients=recipients, emails_opened=opened, total_clicks=clicks,
                                                             unsubscribes=1, status=status, open_tracking_disabled=False))


def page(items, end, more, after=None):
    return dict(input={'per_page': 25, **({'after': after} if after else {})},
                result=dict(broadcasts=items, pagination=dict(has_next_page=more, end_cursor=end)))


def snap(taken, items, complete=True):
    return dict(taken_at=taken, complete=complete, items={str(k): dict(send_at=s, opened=o, clicks=c) for k, (o, c, s) in items.items()})


class Pages(unittest.TestCase):
    def test_complete_chain_keeps_only_sent_broadcasts(self):
        s = C.snapshot_from_pages([page([bc(1, 10), bc(2, 0, status='draft')], 'A', True),
                                   page([bc(3, 5, status='sending')], 'B', False, after='A')], '2026-11-12T02:45:00+00:00')
        self.assertTrue(s['complete'])
        self.assertEqual(sorted(s['items']), ['1', '3'])
        self.assertEqual(s['items']['1']['opened'], 10)

    def test_broken_or_partial_chains_are_incomplete(self):
        cases = {
            'skipped page': [page([bc(1, 1)], 'A', True), page([bc(2, 1)], 'C', False, after='B')],
            'stopped early': [page([bc(1, 1)], 'A', True)],
            'started mid-list': [page([bc(1, 1)], 'B', False, after='A')],
            'same id twice': [page([bc(1, 1)], 'A', True), page([bc(1, 1)], 'B', False, after='A')],
            'not a report': [dict(input={}, result={'error': 'x'})],
            'nothing': [],
        }
        for name, pages in cases.items():
            with self.subTest(name):
                s = C.snapshot_from_pages(pages, '2026-11-12T02:45:00+00:00')
                self.assertFalse(s['complete'])
                self.assertTrue(s['reason'])

    def test_stream_parsing_takes_raw_tool_results_only(self):
        lines = [json.dumps(dict(type='system', subtype='init', mcp_servers=[dict(name='claude.ai Kit', status='connected')])),
                 json.dumps(dict(type='assistant', message=dict(content=[dict(type='tool_use', id='t1', name=C.KIT_TOOL, input={'per_page': 25}),
                                                                         dict(type='text', text='Opens were 99999')]))),
                 json.dumps(dict(type='user', message=dict(content=[dict(type='tool_result', tool_use_id='t1', content=[dict(
                     type='text', text=json.dumps(dict(broadcasts=[bc(7, 42)], pagination=dict(has_next_page=False, end_cursor='Z'))))])])))]
        stdout = '\n'.join(lines)
        self.assertEqual(C.kit_server_status(stdout), 'connected')
        s = C.snapshot_from_pages(C.parse_stream(stdout), 'now')
        self.assertEqual((s['complete'], s['items']['7']['opened']), (True, 42))

    def test_kit_child_waits_for_connectors_and_is_read_only(self):
        seen = {}

        def run(cmd, **kw):
            seen.update(cmd=cmd, env=kw['env'])
            return SimpleNamespace(stdout='', stderr='')
        import studio.agent as agent
        old = agent.find_claude
        agent.find_claude = lambda root: 'claude.exe'
        try:
            C.fetch_kit(ROOT, run=run, tries=1)
        finally:
            agent.find_claude = old
        self.assertEqual(seen['env']['CLAUDE_CODE_MCP_STARTUP_WAIT_MS'], '30000')
        self.assertEqual(seen['cmd'][seen['cmd'].index('--allowedTools') + 1], C.KIT_TOOL)   # the one read-only stats tool
        self.assertEqual(seen['cmd'][seen['cmd'].index('--tools') + 1], '')                 # no built-in tools at all

    def test_pending_connector_is_retried_once_and_recorded(self):
        pending = json.dumps(dict(type='system', subtype='init', mcp_servers=[dict(name='claude.ai Kit', status='pending')]))
        calls, slept = [], []
        run = lambda *a, **k: calls.append(1) or SimpleNamespace(stdout=pending, stderr='')  # noqa: E731
        import studio.agent as agent
        old = agent.find_claude
        agent.find_claude = lambda root: 'claude.exe'
        try:
            s = C.fetch_kit(ROOT, run=run, sleep=slept.append)
        finally:
            agent.find_claude = old
        self.assertEqual((len(calls), slept), (2, [15]))
        self.assertFalse(s['complete'])
        self.assertIn('pending when Claude Code started', s['reason'])
        self.assertIn('Tried twice', s['reason'])


class Growth(unittest.TestCase):
    T0, T1 = '2026-11-11T02:45:00+00:00', '2026-11-12T02:45:00+00:00'

    def test_first_complete_snapshot_is_only_a_baseline(self):
        self.assertEqual(C.kit_growth(None, snap(self.T1, {1: (10, 1, '2026-11-01T13:00:00Z')}))['state'], 'baseline')

    def test_incomplete_today_gives_no_figure(self):
        g = C.kit_growth(snap(self.T0, {}), dict(snap(self.T1, {}, complete=False), reason='partial'))
        self.assertEqual((g['state'], g['reason']), ('incomplete', 'partial'))

    def test_growth_rules(self):
        prev = snap(self.T0, {1: (100, 10, '2026-11-01T13:00:00Z'), 2: (50, 5, '2026-11-02T13:00:00Z'), 3: (40, 4, '2026-11-03T13:00:00Z'),
                              9: (5, 0, '2026-10-01T13:00:00Z')})
        cur = snap(self.T1, {1: (130, 12, '2026-11-01T13:00:00Z'),      # grew 30 and 2
                             2: (45, 5, '2026-11-02T13:00:00Z'),        # went down, left out
                             3: (40, 4, '2026-11-03T13:00:00Z'),        # no change
                             4: (200, 9, '2026-11-11T13:00:00Z'),       # sent after the last snapshot, counts from zero
                             5: (70, 1, '2026-11-05T13:00:00Z')})       # existed before, no earlier count, left out
        g = C.kit_growth(prev, cur)
        self.assertEqual((g['opens'], g['clicks'], g['broadcasts'], g['new']), (230, 11, 3, 1))
        self.assertEqual((g['decreased'], g['no_baseline'], g['missing']), (1, 1, 1))
        self.assertTrue(g['daily'])
        self.assertEqual(g['hours'], 24.0)

    def test_a_missed_day_is_shown_as_the_whole_gap(self):
        g = C.kit_growth(snap('2026-11-09T02:45:00+00:00', {1: (10, 0, '2026-11-01T13:00:00Z')}),
                         snap(self.T1, {1: (25, 0, '2026-11-01T13:00:00Z')}))
        self.assertEqual((g['opens'], g['hours'], g['daily']), (15, 72.0, False))


class Store_(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.results = []

    def tearDown(self):
        self.temp.cleanup()

    def counters(self):
        def fetch():
            self.results.append(1)
            return self.queue.pop(0)
        return C.Counters(self.store, ROOT, fetch=fetch)

    def test_one_complete_snapshot_per_day_and_partials_are_replaced(self):
        self.queue = [snap(Growth.T1, {}, complete=False), snap(Growth.T1, {1: (5, 0, 'x')})]
        c = self.counters()
        d = date(2026, 11, 12)
        self.assertFalse(c.collect(d)['complete'])
        self.assertTrue(c.collect(d)['complete'])
        c.collect(d)
        self.assertEqual(len(self.results), 2)   # third call did nothing, today's is complete
        self.assertTrue(C.Counters(self.store, ROOT, fetch=lambda: 1 / 0).collect(d)['complete'])   # survives a restart

    def test_growth_compares_with_the_newest_complete_earlier_day(self):
        self.queue = [snap('2026-11-10T02:45:00+00:00', {1: (10, 0, '2026-11-01T13:00:00Z')}),
                      snap('2026-11-11T02:45:00+00:00', {1: (99, 0, '2026-11-01T13:00:00Z')}, complete=False),
                      snap(Growth.T1, {1: (40, 0, '2026-11-01T13:00:00Z')})]
        c = self.counters()
        for d in (date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)):
            c.collect(d)
        g = c.growth(date(2026, 11, 12))
        self.assertEqual((g['opens'], g['hours'], g['daily']), (30, 48.0, False))


class InTheBrief(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = Store(root)
        self.queue = []
        self.counters = C.Counters(self.store, ROOT, fetch=lambda: self.queue.pop(0))
        self.biz = B.Business(self.store, root, FakeScorecard(snapshot()), Tasks(self.store), FakeBridge(), counters=self.counters)

    def tearDown(self):
        self.temp.cleanup()

    def text(self, when='2026-11-12T03:00:00'):
        return self.biz.build(at(when))['text']

    def test_no_snapshot_yet_and_baseline_and_growth(self):
        self.assertIn('Opens: unavailable', B.render_brief(date(2026, 11, 12), {}, {}, [], 0, 0, 0, None))
        self.queue = [dict(snap('2026-11-11T02:45:00+00:00', {}, complete=False), reason='pending')]
        self.counters.collect(date(2026, 11, 11))
        self.assertIn('Opens: unavailable', self.text('2026-11-11T03:00:00'))
        self.queue = [snap('2026-11-12T02:45:00+00:00', {1: (100, 3, '2026-11-01T13:00:00Z')})]
        self.counters.collect(date(2026, 11, 12))
        self.assertIn('Opens: unavailable', self.text())
        self.queue = [snap('2026-11-13T02:45:00+00:00', {1: (130, 5, '2026-11-01T13:00:00Z')})]
        self.counters.collect(date(2026, 11, 13))
        t = self.text('2026-11-13T03:00:00')
        self.assertIn('Opens: 30', t)
        self.assertIn('Clicks: 2', t)
        self.assertNotIn('left out', t)

    def test_gap_and_left_out_are_said_briefly(self):
        self.queue = [snap('2026-11-09T02:45:00+00:00', {1: (100, 3, '2026-11-01T13:00:00Z')}),
                      snap('2026-11-12T02:45:00+00:00', {1: (90, 3, '2026-11-01T13:00:00Z'), 2: (5, 0, '2026-11-11T13:00:00Z')})]
        self.counters.collect(date(2026, 11, 9))
        self.counters.collect(date(2026, 11, 12))
        t = self.text()
        self.assertIn('Opens: unavailable', t)
        self.assertEqual(self.biz.build(at('2026-11-12T03:00:00'))['sources']['kit']['period']['hours'], 72.0)
        self.assertNotIn('-#', t)
        self.assertEqual(self.biz.build(at('2026-11-12T03:00:00'))['sources']['kit']['coverage']['decreased'], 1)

    def test_prefetch_and_tick_take_one_snapshot(self):
        self.queue = [snap('2026-11-12T02:45:00+00:00', {1: (100, 3, '2026-11-01T13:00:00Z')})]
        self.biz.prefetch(at('2026-11-12T02:45:00'))
        self.biz.wait_kit(5)
        self.assertEqual(self.queue, [])
        self.biz.tick(at('2026-11-12T03:00:00'))   # today's is complete, so no second fetch (the queue is empty)
        self.assertTrue(self.counters.get('kit:2026-11-12')['complete'])


if __name__ == '__main__':
    unittest.main()
