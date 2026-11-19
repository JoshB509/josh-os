import unittest
from datetime import date
from test_business import Base, at
from studio.business import render_brief, youtube_facts


class Glance(Base):
    def test_five_sections_one_number_per_line_no_notes_or_task_nags(self):
        b=self.biz.build(at('2026-11-12T03:00:00'));text=b['text']
        self.assertEqual([s for s in text.splitlines() if s.startswith('**')][1:],
                         ['**💰 Sales**','**▶️ YouTube**','**🖼️ Carousels**','**📧 Emails**','**🎓 Learning**'])
        for bad in ('UTC','Pacific','this month','Your list','-#','CTR','resource requests','past due'):
            self.assertNotIn(bad,text)
        self.assertEqual(b['nudge_keys'],[])
        self.assertLess(len(text),1400)
        self.assertIn('currency',b['sources']['definitions'])
        self.assertIn('summed across broadcasts',b['sources']['definitions']['email_opens'])

    def test_subscriber_gross_counts_only_when_source_has_them(self):
        cache=dict(data_through='2026-11-10',fetched_at='2026-11-12T02:45:00Z',daily=[dict(date='2026-11-10',views=100,net_subscribers=7)])
        f=youtube_facts(cache)
        t=render_brief(date(2026,11,12),{},f,[],0,0,0)
        self.assertIn('Subscribers gained: unavailable',t)
        self.assertIn('Subscribers lost: unavailable',t)
        self.assertIn('Net subscribers: +7',t)
        cache['daily'][0].update(subscribers_gained=10,subscribers_lost=3)
        t=render_brief(date(2026,11,12),{},youtube_facts(cache),[],0,0,0)
        self.assertIn('Subscribers gained: 10',t)
        self.assertIn('Subscribers lost: 3',t)

    def test_missing_values_are_unavailable_and_zero_stays_zero(self):
        self.sc.snap=None
        t=self.biz.build(at('2026-11-12T03:00:00'))['text']
        for label in ('Revenue','Total sales','Course sales','New subscribers','Downloads','Minutes learned','Active learners','Comments','Questions'):
            self.assertIn(label+': unavailable',t)
        t=render_brief(date(2026,11,12),dict(available=True,figures={'gross':0,'transactions':0}),{},[],0,0,0)
        self.assertIn('Revenue: $0.00',t)
        self.assertIn('Total sales: 0',t)
        self.assertIn('Client sales: unavailable',t)

    def test_revenue_does_not_establish_client_or_upsell_purchase_counts(self):
        t=render_brief(date(2026,11,12),dict(available=True,figures={'clients':1000,'upsell':0}),{},[],0,0,0)
        self.assertIn('Client revenue: $1,000.00',t)
        self.assertIn('Client sales: unavailable',t)
        self.assertIn('Upsell sales: unavailable',t)

    def test_daily_counter_partial_labels_and_multi_day_withholding(self):
        k=dict(state='ok',daily=True,opens=20,clicks=4,missing=1)
        c=dict(state='ok',daily=True,tracked={'p':10},channel_impressions=30,no_baseline=['q'],through='2026-11-10')
        t=render_brief(date(2026,11,12),{}, {},[],0,0,0,kit=k,carousels=c)
        self.assertIn('Opens: 20 (partial)',t)
        self.assertIn('Tracked carousel impressions: 10 (partial)',t)
        self.assertIn('All-post impressions: 30',t)
        k['daily']=False;c['daily']=False
        t=render_brief(date(2026,11,12),{}, {},[],0,0,0,kit=k,carousels=c)
        self.assertIn('Opens: unavailable',t)
        self.assertIn('All-post impressions: unavailable',t)

    def test_baseline_and_incomplete_are_not_zeros(self):
        for state in ('baseline','incomplete','no_new_day'):
            t=render_brief(date(2026,11,12),{}, {},[],0,0,0,kit={'state':state},carousels={'state':state})
            self.assertIn('Opens: unavailable',t)
            self.assertIn('Tracked carousel impressions: unavailable',t)

    def test_ordinary_lag_has_no_note_but_severely_old_data_does(self):
        yt=dict(available=True,day='2026-11-10',views=100)
        t=render_brief(date(2026,11,12),{},yt,[],0,0,0)
        self.assertNotIn('Older data:',t)
        yt['day']='2026-10-16'
        t=render_brief(date(2026,11,12),{},yt,[],0,0,0)
        self.assertIn('Older data: YouTube Fri 16 Oct',t)

    def test_source_specific_clicks_never_become_downloads_or_views(self):
        t=self.biz.build(at('2026-11-12T03:00:00'))['text']
        self.assertIn('Video link clicks: 71',t)
        self.assertIn('Community-post link clicks: 32',t)
        self.assertEqual(t.count('Downloads: unavailable'),2)
        self.assertNotIn('Downloads: 71',t)
        self.assertNotIn('Views: 71',t)

    def test_partial_email_and_click_reads_are_flagged_compactly(self):
        self.sc.snap['status'].update(last_email_list_sync_at='2026-11-11T22:00:00Z',last_activity_at='2026-11-11T23:00:00Z')
        b=self.biz.build(at('2026-11-12T03:00:00'))
        self.assertIn('New subscribers: 21 (partial)',b['text'])
        self.assertIn('Video link clicks: 71 (partial)',b['text'])
        self.assertEqual(len(b['sources']['linklr']['notes']),2)

if __name__=='__main__': unittest.main()
