import copy,importlib.util,json,unittest,sqlite3,tempfile
from unittest.mock import patch
from datetime import datetime,timezone
from pathlib import Path
spec=importlib.util.spec_from_file_location('maxbrief',Path(__file__).resolve().parents[1]/'tools'/'prepare_max_brief.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class TestBrief(unittest.TestCase):
 def setUp(self):
  self.at=datetime(2026,11,14,5,tzinfo=timezone.utc)
  self.r={'meta':{'generated_at':'2026-11-14T04:57:00Z','currency':'USD','timezone':'UTC'},'daily':[dict(date='2026-11-13',course_usd=644,upsell_usd=0,tracked_link_clicks=89,sales_page_views=111,sales_page_visitors=83,checkout_clicks=6,new_subscribers=29,unsubscribes=7,purchases=2,clients_usd=900,other_usd=500)]}
 def test_correct_day_and_excludes_other(self):
  b=m.build(self.r,self.at);self.assertEqual(b['day_utc'],'2026-11-13');self.assertIn('USD 644.00',m.render(b))
 def test_raw_tool_wrapper(self):
  self.assertEqual(m.build({'content':[{'type':'text','text':json.dumps(self.r)}]},self.at)['delivery'],'not_sent')
 def test_stale(self):
  self.r['meta']['generated_at']='2026-11-13T23:00:00Z'
  with self.assertRaises(ValueError):m.build(self.r,self.at)
 def test_no_timezone(self):
  self.r['meta']['generated_at']='2026-11-14T04:00:00'
  with self.assertRaises(ValueError):m.build(self.r,self.at)
 def test_duplicate_day(self):
  self.r['daily']*=2
  with self.assertRaises(ValueError):m.build(self.r,self.at)
 def test_missing_is_not_zero(self):
  del self.r['daily'][0]['checkout_clicks']
  with self.assertRaises(ValueError):m.build(self.r,self.at)
 def test_partial_day_not_used(self):
  self.r['daily'][0]['date']='2026-11-14'
  with self.assertRaises(ValueError):m.build(self.r,self.at)
 def test_nan_rejected(self):
  self.r['daily'][0]['course_usd']=float('nan')
  with self.assertRaises(ValueError):m.build(self.r,self.at)
 def test_boolean_rejected(self):
  self.r['daily'][0]['purchases']=True
  with self.assertRaises(ValueError):m.build(self.r,self.at)

class CachedSources(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.data=Path(self.tmp.name);self.at=datetime(2026,11,15,5,tzinfo=timezone.utc)
  self.c=sqlite3.connect(self.data/'studio.sqlite');self.addCleanup(self.c.close)
  self.c.execute('PRAGMA journal_mode=WAL')
  self.c.execute('CREATE TABLE business_counters(key TEXT PRIMARY KEY,body TEXT)');self.c.commit()
 def put(self,key,obj):
  self.c.execute('INSERT OR REPLACE INTO business_counters VALUES (?,?)',(key,json.dumps(obj)));self.c.commit()
 def snap(self,name,day,total):
  s=dict(complete=True,taken_at=f'2026-11-{day}T02:45:00Z',items={})
  if name=='carousel':s.update(through=f'2026-11-{day-2}',channel_impressions=total,items={'post':dict(impressions=total,post_date='2026-11-01')})
  else:s['items']={'1':dict(status='completed',send_at='2026-11-01T13:00:00Z',recipients=5000,opened=total,clicks=total//10,tracking_off=False)}
  return s
 def pair(self,name):
  self.put(name+':2026-11-13',self.snap(name,13,1000))
  self.put(name+':2026-11-15',self.snap(name,15,1100))
 def result(self):return m.counter_sources(self.data,self.at)
 def test_committed_wal_read_and_gap_period(self):
  self.pair('kit');self.pair('carousel')
  self.assertTrue((self.data/'studio.sqlite-wal').is_file())
  r=self.result()
  self.assertEqual(r['kit']['result']['opens'],100)
  self.assertEqual(r['kit']['result']['hours'],48)
  self.assertFalse(r['carousel']['result']['daily'])
  self.assertEqual(r['carousel']['result']['channel_impressions'],100)
  self.assertEqual(r['carousel']['period']['start_exclusive'],'2026-11-11')
  self.assertEqual(r['carousel']['source_taken_at'],'2026-11-15T02:45:00Z')
 def test_read_error_not_missing_snapshot(self):
  with patch.object(m.sqlite3,'connect',side_effect=sqlite3.OperationalError('unable to open database file')):
   r=self.result()
  self.assertEqual(r['kit']['state'],'read_error')
  self.assertIn('Snapshot presence could not be checked',r['kit']['reason'])
 def test_missing_table_is_read_error(self):
  self.c.execute('DROP TABLE business_counters');self.c.commit()
  self.assertEqual(self.result()['kit']['state'],'read_error')
 def test_missing_database_is_not_created(self):
  other=self.data/'missing';r=m.counter_sources(other,self.at)
  self.assertEqual(r['kit']['state'],'missing_file');self.assertFalse(other.exists())
 def test_missing_snapshot_and_incomplete_are_distinct(self):
  self.assertEqual(self.result()['kit']['state'],'missing_snapshot')
  self.put('kit:2026-11-15',dict(complete=False,reason='connector pending',taken_at='2026-11-15T02:45:00Z'))
  r=self.result()['kit'];self.assertEqual(r['state'],'incomplete');self.assertEqual(r['reason'],'connector pending')
 def test_baseline_never_daily_traffic(self):
  self.put('carousel:2026-11-15',self.snap('carousel',15,154225))
  r=self.result()['carousel'];self.assertFalse(r['available']);self.assertEqual(r['state'],'baseline')
  self.assertNotIn('channel_impressions',r['result'])
 def test_studio_same_day_is_not_zero_growth(self):
  self.pair('carousel');s=self.snap('carousel',15,1100);s['through']='2026-11-11';self.put('carousel:2026-11-15',s)
  r=self.result()['carousel'];self.assertEqual(r['state'],'no_new_day');self.assertFalse(r['available'])
 def test_reduced_channel_total_is_not_available(self):
  self.pair('carousel');self.put('carousel:2026-11-15',self.snap('carousel',15,900))
  self.assertEqual(self.result()['carousel']['state'],'invalid_delta')
 def test_no_opens_without_recipients_or_proven_status(self):
  self.pair('kit')
  for field,value in [('recipients',None),('recipients',0),('recipients',True),('status','scheduled'),('opened',True),('tracking_off',True)]:
   with self.subTest(field=field,value=value):
    s=self.snap('kit',15,1100);s['items']['1'][field]=value;self.put('kit:2026-11-15',s)
    self.assertEqual(self.result()['kit']['state'],'invalid_snapshot')
 def test_planned_future_send_cannot_be_proof(self):
  self.pair('kit');s=self.snap('kit',15,1100);s['items']['1']['send_at']='2026-11-16T00:00:00Z';self.put('kit:2026-11-15',s)
  self.assertFalse(self.result()['kit']['available'])
 def test_completed_new_broadcast_is_explicit_kit_evidence(self):
  self.pair('kit');s=self.snap('kit',15,1100)
  s['items']['2']=dict(status='completed',send_at='2026-11-14T13:00:00Z',recipients=4000,opened=20,clicks=1)
  self.put('kit:2026-11-15',s);r=self.result()['kit']
  self.assertEqual(r['result']['opens'],120)
  self.assertEqual(r['new_completed_broadcasts'][0]['broadcast_id'],'2')
 def test_malformed_kit_does_not_hide_valid_carousel(self):
  self.pair('carousel');self.c.execute("INSERT INTO business_counters VALUES('kit:2026-11-15','bad json')");self.c.commit()
  r=self.result();self.assertEqual(r['kit']['state'],'invalid_snapshot');self.assertTrue(r['carousel']['available'])
 def test_future_and_naive_snapshot_rejected(self):
  self.pair('kit')
  for taken in ('2026-11-16T02:45:00Z','2026-11-15T02:45:00'):
   s=self.snap('kit',15,1100);s['taken_at']=taken;self.put('kit:2026-11-15',s)
   self.assertEqual(self.result()['kit']['state'],'invalid_snapshot')
 def reviews(self,taken='2026-11-15T02:45:00Z',complete=True):
  p=self.data/'scorecard';p.mkdir(exist_ok=True)
  (p/'reviews.json').write_text(json.dumps(dict(complete=complete,taken_at=taken,items=[dict(id='one',submitted_at='2026-11-13T16:40:00Z')])) ,encoding='utf-8')
  r={'day_utc':'2026-11-14'};m.learning_source(r,self.data,self.at);return r['learning']
 def test_reviews_zero_is_supported_but_learning_minutes_not_invented(self):
  r=self.reviews();self.assertEqual(r['reviews']['count'],0);self.assertTrue(r['reviews']['available'])
  self.assertEqual(r['reviews']['total'],1);self.assertEqual(r['metrics']['learning_minutes']['state'],'unsupported')
  self.assertNotIn('value',r['metrics']['learning_minutes'])
 def test_stale_or_partial_reviews_not_reported_as_zero(self):
  self.assertEqual(self.reviews('2026-11-14T02:45:00Z')['reviews']['state'],'stale')
  self.assertEqual(self.reviews(complete=False)['reviews']['state'],'incomplete')
 def test_render_exact_gap_and_baseline_exclusion(self):
  self.pair('kit');self.pair('carousel');r=dict(day_utc='2026-11-14',**self.result());text=m.render(r)
  self.assertIn('2026-11-12 through 2026-11-13; 2 Studio reporting days',text)
  self.assertIn('48.0 hours',text);self.assertIn('baseline is excluded',text)
  self.assertNotIn('Post impressions: 1100',text)
 def test_youtube_uses_direct_cache_day(self):
  p=self.data/'youtube';p.mkdir()
  row=dict(date='2026-11-12',views=11436,net_subscribers=34)
  (p/'snapshot.json').write_text(json.dumps(dict(fetched_at='2026-11-15T02:45:00Z',data_through='2026-11-12',timezone='America/Los_Angeles',daily=[row])),encoding='utf-8')
  r={'day_utc':'2026-11-14'};m.cached_sources(r,self.data,self.at)
  self.assertEqual(r['youtube']['day'],'2026-11-12');self.assertTrue(r['youtube']['available'])


if __name__=='__main__':unittest.main()

