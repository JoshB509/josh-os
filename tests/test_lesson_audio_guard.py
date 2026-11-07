import copy,json,os,shutil,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(r'E:\Videos\DaJoshiResolve');STAGE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));import studio;studio.__path__.insert(0,str(STAGE/'studio'))
from studio.lesson_audio import inspect_audio,identity,sha256,stable_reader
from studio.lessons import Lessons
from studio.store import Store,Conflict
FF=str(ROOT/'tools/ffmpeg.exe')

class FakeOBS:
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def status(self):return dict(connected=True,recording=False,profile='Lessons',collection='Lessons - Facecam and Slides',scene='Lesson - Slides LEFT + Facecam RIGHT')

class AudioTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();cls.fixtures=Path(cls.temp.name)
  def make(name,args):
   path=cls.fixtures/name
   subprocess.run([FF,'-hide_banner','-loglevel','error','-nostdin',*args,str(path)],check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
   os.utime(path,(time.time()-20,)*2);return path
  cls.none=make('no-audio.mkv',['-f','lavfi','-i','color=s=160x90:r=10','-t','2','-c:v','libx264'])
  cls.zero=make('zero.mkv',['-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','2','-c:a','pcm_f32le'])
  cls.speech=make('speech.wav',['-ss','20','-i',r'E:\Videos\Input\2026-10-17 13-41-30.mp4','-t','8','-vn','-c:a','pcm_s16le'])
  cls.leading=make('leading.wav',['-i',str(cls.speech),'-af','adelay=30000|30000','-c:a','pcm_s16le'])
  cls.multi=make('multi.mkv',['-i',str(cls.zero),'-i',str(cls.speech),'-map','0:a','-map','1:a','-c:a','pcm_f32le'])
  cls.low=make('low.wav',['-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-af','volume=0.0001','-t','2','-c:a','pcm_f32le'])
  cls.noise=make('noise.wav',['-f','lavfi','-i','anoisesrc=amplitude=0.1:sample_rate=48000','-t','2','-c:a','pcm_f32le'])
  cls.corrupt=cls.fixtures/'corrupt.mkv';cls.corrupt.write_bytes(b'not a media file')
  cls.partial=cls.fixtures/'partial.mkv';b=cls.multi.read_bytes();cls.partial.write_bytes(b[:len(b)//2])
 @classmethod
 def tearDownClass(cls):cls.temp.cleanup()
 def setUp(self):
  self.tempdir=tempfile.TemporaryDirectory();self.folder=Path(self.tempdir.name)
  cfg=copy.deepcopy(json.loads((ROOT/'Data/lesson-catalog.json').read_text()));cfg['lessons']=cfg['lessons'][:2];cfg.update(recording_input=str(self.folder/'input'),recording_archive=str(self.folder/'archive'))
  self.l=Lessons(Store(self.folder/'Data'),ROOT,cfg,FakeOBS);self.lid=cfg['lessons'][0]['id']
 def tearDown(self):self.l.audio.close();self.tempdir.cleanup()
 def take(self,source,tid='one'):
  e=self.l.entry(self.lid);dest=self.l.archive/e['track_key']/('part-'+e['code'].split('.')[0])/(e['code']+'-'+e['slug'])/source.name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest);os.utime(dest,(time.time()-20,)*2)
  t=dict(id=tid,lesson_id=self.lid,binding_id='session',path=str(dest),name=dest.name,source_path=str(self.l.input/source.name),size=dest.stat().st_size,sha256=sha256(dest),duration=8,recorded_at='2026-10-18T15:00:00Z',approved_at=None,uploaded=None,student_access_verified=None)
  with self.l.store.connect() as c:c.execute('INSERT INTO lesson_takes VALUES(?,?,?)',(tid,self.lid,json.dumps(t)))
  return t
 def checked(self,t):self.l.audio.ensure(t);self.l.audio.jobs.join();return self.l.audio.ensure(t)
 def test_no_stream(self):self.assertEqual(inspect_audio(self.none,FF)['reason'],'missing_audio')
 def test_all_zero(self):self.assertEqual(inspect_audio(self.zero,FF)['reason'],'digital_silence')
 def test_speech_technical_only(self):
  r=self.checked(self.take(self.speech));self.assertEqual(r['status'],'passed');self.assertFalse(r['speech_verified']);self.assertTrue(r['can_continue'])
 def test_long_leading_silence(self):self.assertEqual(inspect_audio(self.leading,FF)['status'],'passed')
 def test_valid_nondefault_track(self):
  r=inspect_audio(self.multi,FF);self.assertEqual(r['status'],'passed');self.assertEqual(len(r['streams']),2);self.assertTrue(r['streams'][0]['digital_zero']);self.assertEqual(r['signal_streams'],[1])
 def test_corrupt(self):self.assertEqual(inspect_audio(self.corrupt,FF)['reason'],'decode_error')
 def test_partial(self):self.assertEqual(inspect_audio(self.partial,FF)['reason'],'decode_error')
 def test_low_level_requires_real_listening_confirmation(self):
  t=self.take(self.low);r=self.checked(t);self.assertEqual(r['status'],'warning');self.assertFalse(r['can_continue'])
  with self.assertRaises(Conflict):self.l.audio.require(t)
  with self.assertRaises(ValueError):self.l.audio.audition(t,dict(confirmed=True))
  r=self.l.audio.audition(t,dict(confirmed=True,heard_speech=True));self.assertTrue(r['can_continue']);self.assertEqual(r['verification_kind'],'user_reported_listening')
 def test_noise_is_never_verified_speech(self):
  r=self.checked(self.take(self.noise));self.assertFalse(r['speech_verified']);self.assertEqual(r['verification_kind'],'technical_signal_only')
 def test_pending_blocks_direct_actions(self):
  t=self.take(self.speech)
  with patch('studio.lesson_audio.inspect_audio',side_effect=lambda *a:(time.sleep(.5) or inspect_audio(*a))):
   self.l.audio.ensure(t)
   for action in ['prefer','approve','uploaded','student_access_verified']:
    with self.assertRaises(Conflict):self.l.take_action(self.lid,dict(action=action,take_id=t['id'],confirmed=True))
   self.l.audio.jobs.join()
 def test_silence_cannot_be_overridden_by_audition(self):
  t=self.take(self.zero);self.checked(t)
  with self.assertRaises(Conflict):self.l.audio.audition(t,dict(confirmed=True,heard_speech=True))
 def test_failed_keeps_previous_good_selection_and_history(self):
  good=self.take(self.speech);self.checked(good);self.l.take_action(self.lid,dict(action='prefer',take_id=good['id']))
  history=dict(take_id=good['id'],status='recorded',confirmed_at='original')
  with self.l.store.connect() as c:
   s=json.loads(c.execute('SELECT body FROM lesson_state WHERE id=?',(self.lid,)).fetchone()['body']);s['recorded_completion']=history;c.execute('UPDATE lesson_state SET body=? WHERE id=?',(json.dumps(s),self.lid))
  bad=self.take(self.zero,'bad');self.checked(bad)
  with self.assertRaises(Conflict):self.l.take_action(self.lid,dict(action='prefer',take_id='bad'))
  state=self.l.state(self.lid);self.assertEqual(state['preferred_take'],'one');self.assertEqual(state['recorded_completion'],history)
 def test_stale_cache_invalidated_and_no_upload_file(self):
  t=self.take(self.speech);self.checked(t);self.l.take_action(self.lid,dict(action='prefer',take_id=t['id']))
  Path(t['path']).write_bytes(self.zero.read_bytes());os.utime(t['path'],(time.time()-20,)*2)
  r=self.l.audio.ensure(t);self.assertFalse(r['can_continue']);self.l.audio.jobs.join()
  self.assertFalse(self.l.audio.ensure(t)['can_continue']);self.assertEqual(self.l.delivery_plan(self.lid)['files'],[])
 def test_content_hash_guard_even_if_stat_cache_spoofed(self):
  t=self.take(self.speech);self.checked(t);p=Path(t['path']);p.write_bytes(self.zero.read_bytes())
  old=self.l.audio._read(t['id']);old['identity']=identity(p);self.l.audio._save(t['id'],old)
  with self.assertRaisesRegex(Conflict,'changed'):self.l.audio.require(t)
 def test_open_writer_is_not_validated(self):
  if os.name!='nt':self.skipTest('Windows sharing lock')
  t=self.take(self.speech)
  with Path(t['path']).open('ab'):
   self.l.audio.ensure(t);self.l.audio.jobs.join();self.assertEqual(self.l.audio._read(t['id'])['status'],'waiting')
  self.l.audio.ensure(t);self.l.audio.jobs.join();self.assertTrue(self.l.audio.ensure(t)['can_continue'])
 def test_cache_survives_restart(self):
  t=self.take(self.speech);first=self.checked(t);self.l.audio.close()
  self.l=Lessons(self.l.store,ROOT,self.l.config,FakeOBS);second=self.l.audio.ensure(t)
  self.assertEqual(first['checked_at'],second['checked_at']);self.assertTrue(second['can_continue'])
 def test_saved_take_review_with_obs_closed_preserves_binding(self):
  t=self.take(self.zero);self.checked(t);other=self.l.config['lessons'][1]['id'];bound=dict(id='other-session',lesson_id=other)
  with self.l.store.connect() as c:c.execute('INSERT INTO lesson_binding VALUES(1,?)',(json.dumps(bound),))
  with patch.object(self.l,'obs',return_value={'connected':False}):
   detail=self.l.select(self.lid);self.assertEqual(detail['recording']['takes'][0]['audio_validation']['reason'],'digital_silence')
   self.assertEqual(self.l.binding(),bound)
 def test_http_pending_failed_and_repeated_done_guards(self):
  import threading,urllib.request,urllib.error
  from http.server import ThreadingHTTPServer
  from studio.server import Handler
  t=self.take(self.speech);bound=dict(id='session',lesson_id=self.lid)
  with self.l.store.connect() as c:c.execute('INSERT INTO lesson_binding VALUES(1,?)',(json.dumps(bound),))
  scan=dict(valid=True,ambiguous=False,incomplete=[],completed={self.l.recording_key(t['source_path']):dict(start_order=1)})
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.lessons=self.l;server.store=self.l.store;server.token='test-only'
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  def post(action,data):
   req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/lessons/{self.lid}/'+action,data=json.dumps(data).encode(),headers={'X-DJ-Token':'test-only','Content-Type':'application/json'})
   try:
    with urllib.request.urlopen(req) as r:return r.status,json.load(r)
   except urllib.error.HTTPError as e:return e.code,json.load(e)
  try:
   with patch.object(self.l,'session_evidence',return_value=scan),patch.object(self.l,'handover',return_value={}),patch.object(self.l,'pending_recordings',return_value=[]):
    with patch('studio.lesson_audio.inspect_audio',side_effect=lambda *a:(time.sleep(.5) or inspect_audio(*a))):
     self.l.audio.ensure(t)
     self.assertEqual(post('done',dict(confirmed=True,binding_id='session'))[0],409)
     self.assertEqual(post('take',dict(action='complete',confirmed=True,take_id=t['id'],binding_id='session'))[0],409)
     self.l.audio.jobs.join()
    status,first=post('done',dict(confirmed=True,binding_id='session'));self.assertEqual(status,200)
    self.assertEqual(post('done',dict(confirmed=True,binding_id='session')),(200,first))
    history=first['recorded_completion'];old_selection=first['preferred_take']
    bad=self.take(self.zero,'bad');self.checked(bad);scan['completed'][self.l.recording_key(bad['source_path'])]=dict(start_order=2)
    self.assertEqual(post('done',dict(confirmed=True,binding_id='session'))[0],409)
    self.assertEqual(post('take',dict(action='uploaded',take_id='bad',confirmed=True,url='https://example.com/video',note='test'))[0],409)
    state=self.l.state(self.lid);self.assertEqual(state['recorded_completion'],history);self.assertEqual(state['preferred_take'],old_selection)
  finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main(verbosity=2)
