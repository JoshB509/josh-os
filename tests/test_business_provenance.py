"""Saved source provenance follows observations, not preview/build time."""
import json
from test_business import Base, at
from studio.counters import Counters
from studio.carousel_counts import CarouselCounts


class Provenance(Base):
    def setUp(self):
        super().setUp()
        self.biz.counters = Counters(self.store, self.root)
        self.biz.carousel_counts = CarouselCounts(self.store, self.root)

    def seed(self, kind, day, taken, complete=True, **kw):
        snapshot = dict(day=day, taken_at=taken, complete=complete, items={}, **kw)
        with self.store.connect() as c:
            c.execute('INSERT OR REPLACE INTO business_counters VALUES(?,?)', (kind+':'+day, json.dumps(snapshot)))

    def sources(self, moment='2026-11-12T03:00:00'):
        return self.biz.build(at(moment))['sources']

    def pair(self):
        for kind in ('kit', 'carousel'):
            self.seed(kind, '2026-11-10', '2026-11-10T02:46:00+00:00', through='2026-11-08', channel_impressions=20)
            self.seed(kind, '2026-11-12', '2026-11-12T02:47:00+00:00', through='2026-11-10', channel_impressions=30)

    def test_observation_times_and_reporting_days_are_distinct(self):
        self.pair()
        s=self.sources()
        for name in ('kit', 'carousel'):
            self.assertEqual(s[name]['state'], 'ok')
            self.assertEqual(s[name]['observation'], {'start':'2026-11-10T02:46:00+00:00','end':'2026-11-12T02:47:00+00:00'})
        self.assertEqual(s['kit']['period'], dict(basis='observed counter growth', start='2026-11-10T02:46:00+00:00',end='2026-11-12T02:47:00+00:00',hours=48.0))
        self.assertEqual(s['carousel']['period'], dict(basis='YouTube Studio reporting days',start_exclusive='2026-11-08',end_inclusive='2026-11-10',days=2))
        later=self.sources('2026-11-12T06:00:00')
        self.assertEqual(later['kit'],s['kit'])
        self.assertEqual(later['carousel'],s['carousel'])

    def test_baseline_records_collection_without_inventing_delta(self):
        for kind in ('kit','carousel'):
            self.seed(kind,'2026-11-12','2026-11-12T02:45:00Z',through='2026-11-10',channel_impressions=30)
        s=self.sources()
        for name in ('kit','carousel'):
            self.assertEqual(s[name]['state'],'baseline')
            self.assertIsNone(s[name]['observation']['start'])
            self.assertEqual(s[name]['observation']['end'],'2026-11-12T02:45:00Z')
        self.assertIsNone(s['kit']['period']['hours'])
        self.assertIsNone(s['carousel']['period']['days'])
        self.assertEqual(s['carousel']['period']['end_inclusive'],'2026-11-10')

    def test_incomplete_and_missing_reads_stay_explicit(self):
        self.seed('kit','2026-11-12','2026-11-12T02:45:00Z',complete=False,reason='partial pages')
        s=self.sources()
        self.assertEqual(s['kit']['state'],'incomplete')
        self.assertEqual(s['kit']['reason'],'partial pages')
        self.assertEqual(s['kit']['observation']['end'],'2026-11-12T02:45:00Z')
        self.assertEqual(s['carousel']['state'],'incomplete')
        self.assertIsNone(s['carousel']['observation']['end'])
        self.assertIsNone(s['carousel']['period']['end_inclusive'])

    def test_no_new_reporting_day_keeps_actual_observations(self):
        self.pair()
        self.seed('carousel','2026-11-12','2026-11-12T02:47:00+00:00',through='2026-11-08',channel_impressions=20)
        s=self.sources()['carousel']
        self.assertEqual(s['state'],'no_new_day')
        self.assertEqual(s['period']['start_exclusive'],s['period']['end_inclusive'])
        self.assertEqual(s['observation']['end'],'2026-11-12T02:47:00+00:00')

    def test_future_build_never_rewrites_sent_receipt(self):
        self.pair()
        sent=self.biz.tick(at('2026-11-12T03:00:00'),refresh=False)
        self.assertEqual(sent['state'],'sent')
        self.assertIn('kit',sent['sources'])
        with self.store.connect() as c:
            before=c.execute('SELECT body FROM business_outbox WHERE key=?',(sent['key'],)).fetchone()[0]
        self.sources('2026-11-12T04:00:00')
        self.biz.tick(at('2026-11-12T04:01:00'),refresh=False)
        with self.store.connect() as c:
            after=c.execute('SELECT body FROM business_outbox WHERE key=?',(sent['key'],)).fetchone()[0]
        self.assertEqual(after,before)
        self.assertEqual(len(self.bridge.calls),1)
