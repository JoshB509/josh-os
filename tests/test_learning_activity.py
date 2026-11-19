import copy
import json
import subprocess
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from studio import learning_activity as L
from studio.business import render_brief
from test_business import Base, at

RAW=json.loads((Path(__file__).parent/'fixtures/learning_activity_summary.json').read_text(encoding='utf-8'))
NOW=datetime(2026,11,16,10,56,4,tzinfo=timezone.utc)
ARGS=L.request_for(NOW)

def stream(raw=None,args=None,error=False,name=L.TOOL):
    return '\n'.join(json.dumps(x) for x in [
        dict(type='system',subtype='init',mcp_servers=[dict(name='careeros',status='connected')]),
        dict(message=dict(content=[dict(type='tool_use',id='read1',name=name,input=args or ARGS)])),
        dict(message=dict(content=[dict(type='tool_result',tool_use_id='read1',is_error=error,content=[dict(type='text',text=json.dumps(raw or RAW))])]))])

class Validation(unittest.TestCase):
    def test_dynamic_last_complete_dubai_day(self):
        self.assertEqual(ARGS,dict(start='2026-11-15T00:00',end='2026-11-16T00:00',time_zone='Asia/Dubai'))
        self.assertEqual(L.request_for(at('2026-11-16T20:01:00'))['start'],'2026-11-16T00:00')

    def test_complete_window_keeps_partial_null_and_source_definitions(self):
        r=L.from_stream(stream(),ARGS,NOW.isoformat())
        self.assertTrue(r['summary']['window_complete'])
        m=r['summary']['metrics']
        self.assertEqual((m['active_learners']['value'],m['active_learners']['status']),(7,'partial'))
        self.assertEqual(m['active_learners']['breakdown'],dict(from_recorded_events=4,from_latest_progress_only=3))
        self.assertIn('lower bound',m['active_learners']['caveat'])
        self.assertIsNone(m['minutes_learned']['value'])
        self.assertIsNone(m['minutes_learned']['tracked_since'])
        self.assertEqual(m['new_questions']['public'],0)
        self.assertEqual(m['new_questions']['private'],0)
        self.assertEqual(m['new_reviews']['value'],0)

    def test_tracked_active_and_nullable_explanations_preserve_source(self):
        raw=copy.deepcopy(RAW)
        m=raw['summary']['metrics']
        m['active_learners'].update(status='tracked',caveat=None)
        m['minutes_learned'].update(status='available',value=140.4,reason=None,tracking_needed=None)
        got=L.validate(raw,ARGS,NOW.isoformat())
        self.assertEqual(got['summary']['metrics'],m)
        text=render_brief(NOW.date(),{}, {},[],0,0,0,learning=dict(available=True,**got))
        self.assertIn('Active learners: 7',text)
        self.assertNotIn('Active learners: 7 (partial)',text)
        self.assertIn('140.4',text)
        for name,change in [('active_learners',dict(value=None)),('new_reviews',dict(status='tracked')),
                            ('minutes_learned',dict(reason=[])),('active_learners',dict(definition=None))]:
            bad=copy.deepcopy(raw);bad['summary']['metrics'][name].update(change)
            with self.subTest(name=name,change=change),self.assertRaises(ValueError):
                L.validate(bad,ARGS,NOW.isoformat())

    def test_exact_raw_tool_only_not_model_summary(self):
        for output in ('',json.dumps({'message':{'content':[{'type':'text','text':json.dumps(RAW)}]}}),stream(name='mcp__careeros__find_learners'),stream()+ '\n'+stream()):
            with self.subTest(output=output[:20]),self.assertRaises(ValueError):L.from_stream(output,ARGS,NOW.isoformat())
        with self.assertRaises(ValueError):L.from_stream(stream(error=True),ARGS,NOW.isoformat())
        with self.assertRaises(ValueError):L.from_stream(stream(args=dict(ARGS,end='2026-11-17T00:00')),ARGS,NOW.isoformat())

    def test_wrong_period_zone_bounds_or_incomplete_window_rejected(self):
        for field,value in [('start','2026-11-15T00:00:00Z'),('end','2026-11-16T00:00:00Z'),('start_local','2026-11-14T00:00:00'),('time_zone','UTC'),('bounds','inclusive')]:
            raw=copy.deepcopy(RAW);raw['summary']['window'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):L.validate(raw,ARGS,NOW.isoformat())
        for field,value in [('window_complete',False),('data_through','2026-11-15T19:00:00Z')]:
            raw=copy.deepcopy(RAW);raw['summary'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):L.validate(raw,ARGS,NOW.isoformat())

    def test_future_stale_invalid_generation_and_tracking_times_rejected(self):
        for generated in ('2026-11-16T11:56:00Z','2026-11-16T08:00:00Z','2026-11-16T10:56:00','invalid'):
            raw=copy.deepcopy(RAW);raw['summary']['generated_at']=generated
            with self.subTest(generated=generated),self.assertRaises(ValueError):L.validate(raw,ARGS,NOW.isoformat())
        raw=copy.deepcopy(RAW);raw['summary']['metrics']['active_learners']['tracked_since']='2027-01-01T00:00:00Z'
        with self.assertRaises(ValueError):L.validate(raw,ARGS,NOW.isoformat())

    def test_status_and_numeric_validation(self):
        for change in ({'status':'complete'},{'status':'available','value':None},{'status':'unavailable','value':0},{'value':True},{'value':-1},{'value':1.5},{'value':float('nan')}):
            raw=copy.deepcopy(RAW);raw['summary']['metrics']['active_learners'].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):L.validate(raw,ARGS,NOW.isoformat())
        raw=copy.deepcopy(RAW);del raw['summary']['metrics']['new_reviews']
        with self.assertRaises(ValueError):L.validate(raw,ARGS,NOW.isoformat())

    def test_read_only_fetch_uses_only_exact_tool_and_current_period(self):
        calls=[]
        def run(cmd,**kwargs):
            calls.append((cmd,kwargs));return NS(returncode=0,stdout=stream())
        with patch('studio.agent.find_claude',return_value='claude'):
            result=L.fetch(Path('.'),NOW,run=run,clock=lambda:NOW)
        cmd,kw=calls[0]
        self.assertEqual(cmd[cmd.index('--allowedTools')+1],L.TOOL)
        self.assertEqual(cmd[cmd.index('--tools')+1],'')
        self.assertIn(json.dumps(ARGS),cmd[2])
        self.assertEqual(kw['env']['CLAUDE_CODE_MCP_STARTUP_WAIT_MS'],'30000')
        self.assertEqual(result['requested'],ARGS)

    def test_missing_tool_and_process_errors_do_not_become_data(self):
        with patch('studio.agent.find_claude',return_value='claude'):
            for r in (NS(returncode=1,stdout=stream()),NS(returncode=0,stdout=stream(name='missing'))):
                with self.assertRaises((ValueError,RuntimeError)):L.fetch(Path('.'),NOW,run=lambda *a,**k:r,clock=lambda:NOW,sleep=lambda x:None)
        with patch('studio.agent.find_claude',return_value=None),self.assertRaises(RuntimeError):L.fetch(Path('.'),NOW)

class Cache(unittest.TestCase):
    def test_last_good_survives_failed_refresh_and_stale_period_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            got=L.validate(RAW,ARGS,NOW.isoformat())
            c=L.LearningActivity('.',temp,fetcher=lambda at:got)
            self.assertEqual(c.collect(NOW)['state'],'ok')
            before=(c.path/'learning-activity.json').read_bytes()
            c.fetcher=lambda at: (_ for _ in ()).throw(RuntimeError('tool unavailable'))
            stale=c.collect(NOW+timedelta(days=1))
            self.assertEqual((stale['state'],stale['latest_attempt']['state']),('stale','failed'))
            self.assertEqual((c.path/'learning-activity.json').read_bytes(),before)
            text=render_brief((NOW+timedelta(days=1)).date(),{}, {},[],0,0,0,learning=stale)
            self.assertIn('Older data: learning Sun 15 Nov',text)
            self.assertIn('Active learners: 7 (partial)',text)
            self.assertIn('Minutes learned: unavailable',text)
            self.assertIn('Questions: 0',text)

    def test_invalid_refresh_never_replaces_saved_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            got=L.validate(RAW,ARGS,NOW.isoformat());c=L.LearningActivity('.',temp,fetcher=lambda at:got)
            c.collect(NOW);before=(c.path/'learning-activity.json').read_bytes()
            c.collect(NOW+timedelta(days=1))  # fetcher returned yesterday's period
            self.assertEqual((c.path/'learning-activity.json').read_bytes(),before)
            self.assertEqual(c.facts(NOW+timedelta(days=1))['latest_attempt']['state'],'failed')

    def test_future_or_invalid_cached_metadata_is_not_rendered(self):
        with tempfile.TemporaryDirectory() as temp:
            got=L.validate(RAW,ARGS,NOW.isoformat());c=L.LearningActivity('.',temp,fetcher=lambda at:got)
            c.collect(NOW)
            self.assertFalse(c.facts(NOW-timedelta(hours=2))['available'])
            got['summary']['window']=[];c._write('learning-activity.json',got)
            self.assertEqual(c.facts(NOW)['state'],'invalid')

    def test_same_day_partial_success_is_not_refetched_or_upgraded(self):
        with tempfile.TemporaryDirectory() as temp:
            calls=[]
            def read(at):calls.append(at);return L.validate(RAW,ARGS,NOW.isoformat())
            c=L.LearningActivity('.',temp,fetcher=read)
            c.collect(NOW);again=c.collect(NOW+timedelta(minutes=5))
            self.assertEqual(len(calls),1)
            self.assertEqual(again['summary']['metrics']['active_learners']['status'],'partial')

class Integration(Base):
    def test_catchup_collects_missing_aggregate_once_before_build(self):
        moment=at('2026-11-16T03:00:00');raw=copy.deepcopy(RAW)
        raw['summary']['generated_at']=moment.isoformat();calls=[]
        def read(at):calls.append(at);return L.validate(raw,L.request_for(at),at.isoformat())
        self.biz.learning_activity=L.LearningActivity(self.root,self.root,fetcher=read)
        self.sc.snap['fetched_at']='2026-11-16T02:44:00Z'
        first=self.biz.tick(moment)
        again=self.biz.tick(moment+timedelta(minutes=1))
        self.assertEqual(first,again)
        self.assertEqual(len(calls),1)
        self.assertEqual(len(self.bridge.calls),1)
        self.assertIn('Active learners: 7 (partial)',first['text'])

    def test_missing_aggregate_does_not_relabel_legacy_review_window(self):
        text=render_brief(NOW.date(),{}, {},[],0,0,0,reviews=dict(available=True,count=8),learning=dict(available=False))
        self.assertIn('Reviews: unavailable',text)
        self.assertNotIn('Reviews: 8',text)

    def test_prefetch_collects_learning_even_when_linklr_is_fresh_and_sends_nothing(self):
        raw=copy.deepcopy(RAW);raw['summary']['generated_at']='2026-11-16T02:45:00Z'
        moment=at('2026-11-16T02:45:00');calls=[]
        def read(at):calls.append(at);return L.validate(raw,L.request_for(at),at.isoformat())
        self.biz.learning_activity=L.LearningActivity(self.root,self.root,fetcher=read)
        self.sc.snap['fetched_at']='2026-11-16T02:44:00Z'
        self.biz.prefetch(moment);self.biz.wait_kit(5)
        self.assertEqual(len(calls),1);self.assertEqual(self.sc.refreshes,0);self.assertEqual(self.bridge.calls,[])
        brief=self.biz.build(at('2026-11-16T03:00:00'))
        self.assertIn('Active learners: 7 (partial)',brief['text'])
        self.assertIn('Minutes learned: unavailable',brief['text'])
        self.assertIn('Reviews: 0',brief['text']);self.assertIn('Questions: 0',brief['text'])
        self.assertEqual(brief['sources']['learning']['summary']['metrics']['active_learners']['breakdown']['from_latest_progress_only'],3)
        self.assertTrue(brief['sources']['learning']['summary']['window_complete'])

if __name__=='__main__':unittest.main()
