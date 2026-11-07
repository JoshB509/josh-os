"""Local lesson recording support. No record controls, uploads or source edits."""
import base64,contextlib,ctypes,hashlib,json,os,re,sqlite3,subprocess,threading,time,uuid
from pathlib import Path
from datetime import datetime,timezone
from .store import Conflict
from .lesson_evidence import scan_logs
from .lesson_audio import AudioChecks

PROFILE='Lessons'
COLLECTION='Lessons - Facecam and Slides'
SCENE='Lesson - Slides LEFT + Facecam RIGHT'
def now():return datetime.now(timezone.utc).isoformat()
def digest(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()

class OBS:
 """Use existing local OBS websocket settings; never configure credentials."""
 def __enter__(self):
  import websocket
  cfg=json.loads((Path(os.environ['APPDATA'])/'obs-studio/plugin_config/obs-websocket/config.json').read_text())
  if not cfg.get('server_enabled'):raise ValueError('OBS websocket is disabled')
  self.ws=websocket.create_connection('ws://127.0.0.1:'+str(int(cfg.get('server_port',4455))),timeout=3,suppress_origin=True)
  try:
   hello=json.loads(self.ws.recv())['d'];ident={'rpcVersion':1,'eventSubscriptions':0}
   if 'authentication' in hello:
    a=hello['authentication'];secret=base64.b64encode(hashlib.sha256((cfg['server_password']+a['salt']).encode()).digest()).decode()
    ident['authentication']=base64.b64encode(hashlib.sha256((secret+a['challenge']).encode()).digest()).decode()
   self.ws.send(json.dumps({'op':1,'d':ident}))
   if json.loads(self.ws.recv()).get('op')!=2:raise ValueError('OBS authentication failed')
   return self
  except Exception:self.ws.close();raise
 def __exit__(self,*args):self.ws.close()
 def call(self,kind,data=None):
  allowed={'GetRecordStatus','GetStreamStatus','GetVirtualCamStatus','GetReplayBufferStatus','GetProfileList','GetSceneCollectionList','GetSceneList','SetCurrentProfile','SetCurrentSceneCollection','SetCurrentProgramScene'}
  if kind not in allowed:raise ValueError('Unsupported OBS operation')
  rid=str(uuid.uuid4());self.ws.send(json.dumps({'op':6,'d':{'requestType':kind,'requestId':rid,'requestData':data or {}}}))
  while True:
   msg=json.loads(self.ws.recv())
   if msg.get('op')==7 and msg['d']['requestId']==rid:
    d=msg['d']
    if not d['requestStatus']['result']:raise ValueError('OBS could not complete '+kind)
    return d.get('responseData',{})
 def idle(self):
  for kind in ['GetRecordStatus','GetStreamStatus','GetVirtualCamStatus','GetReplayBufferStatus']:
   if self.call(kind).get('outputActive') is not False:raise Conflict('OBS has an active or unknown output. Leave it untouched; finish manually first.')
 def status(self):
  return dict(connected=True,recording=self.call('GetRecordStatus').get('outputActive'),profile=self.call('GetProfileList')['currentProfileName'],collection=self.call('GetSceneCollectionList')['currentSceneCollectionName'],scene=self.call('GetSceneList')['currentProgramSceneName'])
 def prepare(self):
  self.idle();profiles=self.call('GetProfileList');collections=self.call('GetSceneCollectionList')
  if PROFILE not in [p if isinstance(p,str) else p['profileName'] for p in profiles['profiles']] or COLLECTION not in [s if isinstance(s,str) else s['sceneCollectionName'] for s in collections['sceneCollections']]:raise ValueError('Existing Lessons setup was not found. Select it manually; no configuration changed.')
  if profiles['currentProfileName']!=PROFILE:self.idle();self.call('SetCurrentProfile',{'profileName':PROFILE})
  if collections['currentSceneCollectionName']!=COLLECTION:self.idle();self.call('SetCurrentSceneCollection',{'sceneCollectionName':COLLECTION})
  scenes=self.call('GetSceneList')
  if SCENE not in [s['sceneName'] for s in scenes['scenes']]:raise ValueError('Expected lesson scene missing; inspect OBS manually')
  if scenes['currentProgramSceneName']!=SCENE:self.idle();self.call('SetCurrentProgramScene',{'sceneName':SCENE})
  result=self.status();result['prepared']=result['profile']==PROFILE and result['collection']==COLLECTION and result['scene']==SCENE;return result

def ensure_obs_running():
 """Only explicit preparation may launch OBS; status polling never does."""
 if os.name!='nt':raise ValueError('Open OBS manually, then retry lesson preparation.')
 # Refuse uncertain process detection instead of risking a second OBS instance.
 processes=subprocess.run(['tasklist.exe','/FO','CSV','/NH'],capture_output=True,text=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 if processes.returncode:raise ValueError('Could not check whether OBS is running. Open OBS manually and retry.')
 import csv,io
 if any(row and row[0].lower() in {'obs64.exe','obs32.exe','obs.exe'} for row in csv.reader(io.StringIO(processes.stdout))):return False
 executable=Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'obs-studio/bin/64bit/obs64.exe'
 if not executable.is_file():raise ValueError('OBS was not found in its standard installation folder. Open OBS manually and retry.')
 # No --startrecording, --startstreaming, --startvirtualcam or restart flags.
 subprocess.Popen([str(executable),'--profile',PROFILE,'--collection',COLLECTION,'--scene',SCENE],cwd=str(executable.parent))
 return True

@contextlib.contextmanager
def preparation_connection(factory):
 # Catch connection refusal only during connection, never replay a failed setup.
 connection=factory()
 try:obs=connection.__enter__()
 except ConnectionRefusedError:
  ensure_obs_running()
  for attempt in range(20):
   time.sleep(.5);connection=factory()
   try:obs=connection.__enter__();break
   except ConnectionRefusedError:
    if attempt==19:raise ValueError('OBS is not accepting connections yet. Wait for OBS to finish opening, check its WebSocket server, then retry. No lesson was bound.') from None
 try:yield obs
 finally:connection.__exit__(None,None,None)

@contextlib.contextmanager
def completed_file(path):
 """Windows exclusive read denies a writer both before and during copying."""
 if os.name=='nt':
  import msvcrt
  kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CreateFileW.restype=ctypes.c_void_p
  kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
  handle=kernel.CreateFileW(str(path),0x80000000,0,None,3,0x80,None)
  if handle==ctypes.c_void_p(-1).value:raise Conflict('Recording is open or still being written. Stop recording in OBS and retry after it finishes.')
  try:fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
  except Exception:kernel.CloseHandle(ctypes.c_void_p(handle));raise
  with os.fdopen(fd,'rb') as f:yield f
 else:
  with path.open('rb') as f:yield f

class Lessons:
 def __init__(self,store,root,config=None,obs_factory=OBS):
  self.store=store;self.root=Path(root);self.config=config or json.loads((self.root/'Data/lesson-catalog.json').read_text(encoding='utf-8'));self.obs_factory=obs_factory;self.lock=threading.Lock();self.handover_lock=threading.RLock()
  self.entries={x['id']:x for x in self.config['lessons']}
  self.input=Path(self.config['recording_input']).resolve();self.archive=Path(self.config['recording_archive']).resolve()
  with store.connect() as c:c.executescript('''CREATE TABLE IF NOT EXISTS lesson_state(id TEXT PRIMARY KEY,body TEXT NOT NULL);CREATE TABLE IF NOT EXISTS lesson_takes(id TEXT PRIMARY KEY,lesson_id TEXT NOT NULL,body TEXT NOT NULL);CREATE TABLE IF NOT EXISTS lesson_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL);''');c.execute('INSERT OR IGNORE INTO lesson_migrations VALUES(1,?)',(now(),))
  with store.connect() as c:c.execute('CREATE TABLE IF NOT EXISTS lesson_binding(singleton INTEGER PRIMARY KEY CHECK(singleton=1),body TEXT NOT NULL)');c.execute('INSERT OR IGNORE INTO lesson_migrations VALUES(2,?)',(now(),))
  # Additive session history keeps old lesson boundaries after the next lesson.
  with store.connect() as c:
   c.execute('CREATE TABLE IF NOT EXISTS lesson_sessions(id TEXT PRIMARY KEY,lesson_id TEXT NOT NULL,body TEXT NOT NULL)')
   row=c.execute('SELECT body FROM lesson_binding WHERE singleton=1').fetchone()
   if row:
    bound=json.loads(row['body']);c.execute('INSERT OR IGNORE INTO lesson_sessions VALUES(?,?,?)',(bound['id'],bound['lesson_id'],json.dumps(bound)))
   c.execute('INSERT OR IGNORE INTO lesson_migrations VALUES(3,?)',(now(),))
  self.audio=AudioChecks(store,self.config['ffmpeg'])
  # Additive validation of existing takes; never rewrite selections/history.
  with store.connect() as c:old_takes=[json.loads(r['body']) for r in c.execute('SELECT body FROM lesson_takes')]
  for take in old_takes:self.audio.ensure(take)
  self.released_import_error=None
  threading.Thread(target=self.released_handover_loop,daemon=True).start()
 def entry(self,lid):
  if lid not in self.entries:raise ValueError('Unknown lesson')
  return self.entries[lid]
 def state(self,lid):
  self.entry(lid)
  with self.store.connect() as c:
   s=c.execute('SELECT body FROM lesson_state WHERE id=?',(lid,)).fetchone();takes=[json.loads(r['body']) for r in c.execute('SELECT body FROM lesson_takes WHERE lesson_id=? ORDER BY rowid',(lid,))]
  for take in takes:take['audio_validation']=self.audio.ensure(take)
  return dict(**(json.loads(s['body']) if s else dict(id=lid,preferred_take=None,planned_date='')),takes=takes)
 def binding(self):
  with self.store.connect() as c:row=c.execute('SELECT body FROM lesson_binding WHERE singleton=1').fetchone()
  return json.loads(row['body']) if row else None
 def recorder_idle(self):
  """Only actual stopped output (or reliably closed OBS) releases the recorder."""
  status=self.obs()
  if status.get('connected'):
   if status.get('recording') is not False:raise Conflict('OBS is recording or its state is unknown. Stop recording manually first.')
   return status
  if os.name!='nt':raise Conflict('Cannot verify that OBS has stopped. Reconnect OBS before releasing the recorder.')
  import csv,io
  check=subprocess.run(['tasklist.exe','/FO','CSV','/NH'],capture_output=True,text=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  if check.returncode or any(row and row[0].lower() in {'obs64.exe','obs32.exe','obs.exe'} for row in csv.reader(io.StringIO(check.stdout))):raise Conflict('OBS is running but its recording state is unavailable. Reconnect it before switching lessons.')
  return dict(connected=False,recording=False,verified_closed=True)
 def session_candidates(self,bound):
  files=[]
  if self.input.is_dir():
   for p in self.input.iterdir():
    if p.is_file() and p.suffix.lower() in {'.mkv','.mp4','.mov','.webm'}:
     st=p.stat();files.append(dict(name=p.name,path=str(p.resolve()),size=st.st_size,mtime_ns=str(st.st_mtime_ns),ready_age_seconds=max(0,time.time()-st.st_mtime)))
  excluded={self.recording_key(p) for p in bound.get('existing_paths',[])}
  files=[x for x in files if self.recording_key(x['path']) not in excluded and int(x['mtime_ns'])>=int(bound['prepared_ns'])]
  if bound.get('released_at') or bound.get('closed_log_cursor'):
   paths=[x['path'] for x in bound.get('recovery_files',[])] if 'recovery_files' in bound else bound.get('closed_existing_paths',[])
   keys={self.recording_key(p) for p in paths}
   files=[x for x in files if self.recording_key(x['path']) in keys]
  return sorted(files,key=lambda x:int(x['mtime_ns']),reverse=True)
 def release_snapshot(self,bound,reason):
  if bound.get('released_at'):return dict(bound)
  frozen=dict(bound)
  frozen.update(released_at=now(),release_reason=reason,recovery_files=self.session_candidates(bound),release_evidence=self.session_evidence(bound))
  if not frozen.get('closed_log_cursor'):
   frozen.update(closed_log_cursor=self.obs_log_cursor(),closed_existing_paths=[str(p.resolve()) for p in self.input.iterdir() if p.is_file()] if self.input.is_dir() else [])
  return frozen
 def release_session(self,lid=None,binding_id=None,reason='done'):
  if lid:self.entry(lid)
  with self.handover_lock:
   with self.lock:
    self.recorder_idle();bound=self.binding()
    if not bound:
     return dict(released=True,binding=None,message='Recorder is free. Saved takes and recovery history are preserved.')
    if (lid and bound['lesson_id']!=lid) or (binding_id and bound['id']!=binding_id):raise Conflict('A different recording session is selected. Refresh before finishing it.')
    frozen=self.release_snapshot(bound,reason)
    self.recorder_idle()
    with self.store.connect() as c:
     c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT body FROM lesson_binding WHERE singleton=1').fetchone()
     if not row or json.loads(row['body'])['id']!=bound['id']:raise Conflict('The recording session changed. No new session was released.')
     self.recorder_idle()
     c.execute('INSERT OR REPLACE INTO lesson_sessions VALUES(?,?,?)',(bound['id'],bound['lesson_id'],json.dumps(frozen)))
     c.execute('DELETE FROM lesson_binding WHERE singleton=1')
    return dict(released=True,binding=None,session_id=bound['id'],message='Recorder is free. Pending files remain in Recovery; choose any lesson when ready.')
 def recovery(self,lid=None):
  if lid:self.entry(lid)
  with self.store.connect() as c:
   sessions={r['id']:json.loads(r['body']) for r in c.execute('SELECT id,body FROM lesson_sessions')}
   used={self.recording_key(json.loads(r['body'])['source_path']) for r in c.execute('SELECT body FROM lesson_takes')}
  active=self.binding()
  if active:sessions[active['id']]=active
  items=[];history=[]
  for bound in sorted(sessions.values(),key=lambda b:b.get('prepared_at',''),reverse=True):
   if lid and bound['lesson_id']!=lid:continue
   available=bound['lesson_id'] in self.entries
   lesson=self.entries.get(bound['lesson_id'],dict(title='Unavailable lesson ('+bound['lesson_id']+')',code='Unmapped'));files=self.session_candidates(bound);groups={}
   for f in files:
    key=self.recording_key(f['path'])
    if key not in used:groups.setdefault(key,[]).append(f)
   for group in groups.values():
    f=sorted(group,key=lambda f:Path(f['path']).suffix.lower()!='.mp4')[0]
    items.append(dict(f,session_id=bound['id'],lesson_id=bound['lesson_id'],lesson_title=lesson['title'],lesson_code=lesson['code'],lesson_available=available,confirmation_required=True))
   history.append(dict(session_id=bound['id'],lesson_id=bound['lesson_id'],prepared_at=bound['prepared_at'],released_at=bound.get('released_at'),pending=len(groups),evidence=bound.get('release_evidence',{}).get('reason',''),original_files=bound.get('recovery_files',[])))
  return dict(items=items,sessions=history,automatic_import_notice=getattr(self,'released_import_error',None),message='Choose the exact file and its original lesson. Nothing is assigned automatically from this queue.')
 def select(self,lid):
  self.entry(lid);bound=self.binding()
  if bound and bound['lesson_id']!=lid:self.release_session(bound['lesson_id'],bound['id'],'selected_another_lesson')
  elif not bound:self.recorder_idle()
  return self.detail(lid)
 def obs_log_cursor(self,bound=None):
  folder=Path(os.environ.get('APPDATA',''))/'obs-studio/logs'
  logs=sorted(folder.glob('*.txt'),key=lambda p:p.name,reverse=True)
  if not logs:return None
  p=logs[0]
  try:text=p.read_text(encoding='utf-8-sig',errors='replace')
  except OSError:return None
  offset=len(text)
  if bound:
   # Upgrade an already prepared session without guessing from file mtime.
   # OBS log timestamps use this PC's local timezone, like its filename.
   from datetime import timedelta
   try:
    day=datetime.strptime(p.stem,'%Y-%m-%d %H-%M-%S').date();prepared=int(bound['prepared_ns'])/1e9
    if datetime.strptime(p.stem,'%Y-%m-%d %H-%M-%S').timestamp()>prepared:return None
    position=0;previous=None;found=False
    for line in text.splitlines(keepends=True):
     match=re.match(r'(\d{2}:\d{2}:\d{2}\.\d{3}):',line)
     if match:
      clock=datetime.strptime(match[1],'%H:%M:%S.%f').time()
      if previous and previous.hour==23 and clock.hour==0:day+=timedelta(days=1)
      previous=clock
      if datetime.combine(day,clock).timestamp()>=prepared:offset=position;found=True;break
     position+=len(line)
    if not found:return None
   except (ValueError,KeyError):return None
  return dict(path=str(p.resolve()),offset=offset,prefix_sha256=hashlib.sha256(text[:offset].encode()).hexdigest())
 def recording_key(self,path):
  p=Path(path).resolve()
  return str(p.with_suffix('') if p.suffix.lower() in {'.mkv','.mp4'} else p).casefold()
 def pending_recordings(self,lid):
  with self.store.connect() as c:used={self.recording_key(json.loads(r['body'])['source_path']) for r in c.execute('SELECT body FROM lesson_takes')}
  return [r for r in self.candidates(lid) if self.recording_key(r['path']) not in used]
 def session_evidence(self,bound):
  if not bound.get('obs_log_cursor'):bound=dict(bound,obs_log_cursor=self.obs_log_cursor(bound))
  return scan_logs(bound,Path(os.environ.get('APPDATA',''))/'obs-studio/logs',self.input,SCENE)
 def remember_evidence(self,bound,scan):
  """Append validation anchors without changing preparation or existing history."""
  if not scan['valid'] or scan['ambiguous']:return
  with self.store.connect() as c:
   c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT body FROM lesson_binding WHERE singleton=1').fetchone()
   current=json.loads(row['body']) if row else None
   if not current or current['id']!=bound['id']:raise Conflict('Lesson session changed while checking OBS evidence.')
   history=current.setdefault('obs_log_history',[])
   for anchor in scan['log_history']:
    if anchor not in history:history.append(anchor)
   c.execute('UPDATE lesson_binding SET body=? WHERE singleton=1',(json.dumps(current),))
   c.execute('INSERT OR REPLACE INTO lesson_sessions VALUES(?,?,?)',(current['id'],current['lesson_id'],json.dumps(current)))
 def stopped_recording_evidence(self,bound):return self.session_evidence(bound)['completed']
 def finish_session(self,lid,data):
  if data.get('confirmed') is not True:raise ValueError('Confirm that you are finished recording.')
  result=self.release_session(lid,data.get('binding_id'),'done')
  return dict(self.state(lid),recorder_released=True,recovery_pending=len(self.recovery(lid)['items']),message=result['message'])
 def released_handover_loop(self):
  """Released imports continue independently of navigation and the recorder gate."""
  while True:
   time.sleep(5)
   try:self.handover_released();self.released_import_error=None
   except Exception:self.released_import_error='Automatic import is deferred. Preserved recordings remain available in Recovery.'
 def handover_released(self,lid=None):
  if lid:self.entry(lid)
  if not self.handover_lock.acquire(False):return dict(status='waiting',message='Saving a finished recording.',imported=0,proposed=[])
  try:
   # Only v2 released boundaries are eligible. Older/ambiguous evidence stays manual.
   with self.store.connect() as c:
    sessions=[json.loads(r['body']) for r in c.execute('SELECT body FROM lesson_sessions')]
    used={self.recording_key(json.loads(r['body'])['source_path']) for r in c.execute('SELECT body FROM lesson_takes')}
   imported=0;proposed=[];waiting=False
   for bound in sessions:
    if not bound.get('released_at') or bound['lesson_id'] not in self.entries or (lid and bound['lesson_id']!=lid):continue
    if not any(self.recording_key(x['path']) not in used for x in self.session_candidates(bound)):continue
    result=self._handover_bound(bound,archived=True);imported+=result['imported'];proposed.extend(result['proposed'])
    waiting=waiting or result['status'] in {'waiting','recording'}
   return dict(status='proposed' if proposed else 'waiting' if waiting else 'review' if imported else 'ready',message='Finished recordings are still saving; the recorder is free.' if waiting else 'Some recordings need explicit file and lesson confirmation in Recovery.' if proposed else 'Finished takes are saved for review.' if imported else 'Saved takes and recovery history are preserved.',imported=imported,proposed=proposed,**(dict(recording=self.state(lid)) if lid else {}))
  finally:self.handover_lock.release()
 def handover(self,lid,expected_binding_id=None):
  self.entry(lid)
  if not self.handover_lock.acquire(False):return dict(status='waiting',message='Recording handover is already checking this session.',imported=0,proposed=[])
  try:
   bound=self.binding()
   if not bound or bound['lesson_id']!=lid:
    return self.handover_released(lid)
   if expected_binding_id and bound['id']!=expected_binding_id:raise Conflict('Lesson session changed. Refresh before reviewing recordings.')
   return self._handover_bound(bound)
  finally:self.handover_lock.release()
 def _handover_bound(self,bound,archived=False):
  lid=bound['lesson_id']
  status=self.obs()
  if not status.get('connected'):return dict(status='waiting',message='Waiting to verify OBS has stopped. Recordings remain in the input folder.',imported=0,proposed=[])
  if status.get('recording') is not False:return dict(status='recording',message='Recording in OBS. Your finished take will appear here after stopping.',imported=0,proposed=[])
  scan=self.session_evidence(bound)
  if not scan['valid'] or scan['ambiguous']:
   return dict(status='proposed',message='Session evidence needs review. '+scan.get('reason',''),imported=0,proposed=[dict(reason=scan.get('reason','Uncertain session evidence'))],recording=self.state(lid))
  if not archived:self.remember_evidence(bound,scan)
  evidence=scan['completed'];groups={}
  with self.store.connect() as c:used={self.recording_key(json.loads(r['body'])['source_path']) for r in c.execute('SELECT body FROM lesson_takes')}
  for item in self.session_candidates(bound):
   if self.recording_key(item['path']) not in used:groups.setdefault(self.recording_key(item['path']),[]).append(item)
  imported=0;proposed=[];waiting=False
  correct_setup=(status.get('profile'),status.get('collection'),status.get('scene'))==(PROFILE,COLLECTION,SCENE)
  for key,items in sorted(groups.items(),key=lambda pair:evidence.get(pair[0],{}).get('start_order',10**12)):
   # Prefer the MP4 remux, but let OBS finish both files before copying either.
   items.sort(key=lambda x:(Path(x['path']).suffix.lower()!='.mp4',x['name']))
   item=items[0]
   if any(x['ready_age_seconds']<8 for x in items):waiting=True;break
   if any(Path(x['path']).suffix.lower()=='.mkv' for x in items) and not any(Path(x['path']).suffix.lower()=='.mp4' for x in items):waiting=True;break
   if key not in evidence or not correct_setup:
    proposed.append(dict(path=item['path'],name=item['name'],reason='Recording needs explicit confirmation: no complete OBS start/stop evidence for this prepared session.'));continue
   proof=evidence[key]
   # The source named by OBS must be among this session's new files.
   if not any(Path(x['path']).resolve()==Path(proof['recording_path']).resolve() for x in items):
    proposed.append(dict(path=item['path'],name=item['name'],reason='OBS original is missing; confirm this recording explicitly.'));continue
   try:
    # Deny active writers on BOTH the original and any remux throughout import.
    with contextlib.ExitStack() as stack:
     for sibling in items[1:]:stack.enter_context(completed_file(Path(sibling['path'])))
     prior=len(self.state(lid)['takes'])
     result=self.import_take(lid,dict(item,confirmed_stopped=True,expected_binding_id=bound['id'],handover_evidence=proof,**(dict(session_id=bound['id'],confirmed_association=True) if archived else {})))
     imported+=len(result['takes'])-prior
   except (Conflict,OSError):waiting=True;break
   except ValueError as error:proposed.append(dict(path=item['path'],name=item['name'],reason=str(error)))
  message='Take saved. Audio validation runs in the background; you can finish or choose another lesson.' if imported else 'Confirm the proposed recording before adding it to this lesson.' if proposed else 'Waiting for OBS to finish writing the recording.' if waiting else 'Every take stays with this lesson. Done releases the recorder; review or recover files separately.'
  return dict(status='review' if imported else 'proposed' if proposed else 'waiting' if waiting else 'ready',message=message,imported=imported,proposed=proposed,recording=self.state(lid))

 def prepare_lesson(self,lid):
  if not self.handover_lock.acquire(False):raise Conflict('Still saving the active lesson. Retry in a moment.')
  try:return self._prepare_lesson(lid)
  finally:self.handover_lock.release()
 def _prepare_lesson(self,lid):
  e=self.entry(lid)
  if not self.lock.acquire(False):raise Conflict('A take import or lesson preparation is in progress')
  try:
   previous=self.binding()
   if previous and previous['lesson_id']==lid and not previous.get('released_at') and not previous.get('closed_log_cursor'):
    with preparation_connection(self.obs_factory) as obs:obs.idle();status=obs.prepare()
    return dict(status,binding=previous,opened=[],warnings=[],message='Same lesson session. Start the next take with Stream Deck.')
   self.recorder_idle()
   frozen=self.release_snapshot(previous,'prepared_another_lesson') if previous else None
   with preparation_connection(self.obs_factory) as obs:
    obs.idle();status=obs.prepare()
    if not status.get('prepared'):raise Conflict('Select the Lessons setup in OBS before binding this lesson')
    obs.idle()
   bound=dict(id=str(uuid.uuid4()),lesson_id=lid,prepared_at=now(),prepared_ns=str(time.time_ns()),existing_paths=[x['path'] for x in self.candidates()])
   bound['obs_log_cursor']=self.obs_log_cursor()
   # Snapshot every existing video, not just the 20 newest shown by the picker.
   bound['existing_paths']=[str(p.resolve()) for p in self.input.iterdir() if p.is_file()] if self.input.is_dir() else []
   with self.store.connect() as c:
    c.execute('BEGIN IMMEDIATE')
    self.recorder_idle()
    current=self.binding()
    if (current or {}).get('id')!=(previous or {}).get('id'):raise Conflict('Session changed while preparing the lesson. Retry.')
    if frozen:c.execute('INSERT OR REPLACE INTO lesson_sessions VALUES(?,?,?)',(frozen['id'],frozen['lesson_id'],json.dumps(frozen)))
    c.execute('INSERT OR REPLACE INTO lesson_binding VALUES(1,?)',(json.dumps(bound),))
    c.execute('INSERT OR REPLACE INTO lesson_sessions VALUES(?,?,?)',(bound['id'],lid,json.dumps(bound)))
   opened=[];warnings=[]
   # The cued script shows in the app when it is saved locally, so Notion only opens as a fallback.
   for value in ([] if self.cues(lid) else [e.get('source_url')])+[m['path'] for m in e.get('materials',[]) if m['kind']=='slides' and Path(m['path']).is_file()]:
    if value:
     try:os.startfile(value);opened.append(value)
     except OSError:warnings.append('Open this lesson source manually: '+value)
   return dict(status,binding=bound,opened=opened,warnings=warnings,message='Lesson bound. Check slide placement in OBS and Prompter Screen mode. Start and stop with your existing OBS controls; click slides directly.')
  except Conflict:raise
  except Exception as error:raise ValueError('Could not prepare and bind the lesson. Check OBS manually and retry; recording can continue independently. '+str(error))
  finally:self.lock.release()
 def cues(self,lid):
  p=self.root/'Data'/'lesson-cues'/(lid+'.md')
  return p.read_text(encoding='utf-8-sig') if p.is_file() else ''
 def catalog(self):return dict(lifecycle_version=2,lessons=[dict(x,recording=self.state(x['id']),has_cues=bool(self.cues(x['id']))) for x in self.entries.values()],binding=self.binding(),plan=self.config['plan'],mapping_note=self.config.get('mapping_note',''),recording_input=str(self.input),recording_archive=str(self.archive),obs_setup=dict(profile=PROFILE,collection=COLLECTION,scene=SCENE))
 def detail(self,lid):
  e=dict(self.entry(lid));e['materials']=[dict(m,exists=Path(m['path']).is_file()) for m in e.get('materials',[])];script=Path(e['script']) if e.get('script') else None
  cues=self.cues(lid)
  return dict(e,recording=self.state(lid),binding=self.binding(),cues_text=cues,cue_count=len(set(re.findall(r'CHANGE (\d+)',cues))),script_text=script.read_text(encoding='utf-8-sig') if script and script.is_file() else '',script_missing=not(script and script.is_file()))
 def plan(self,lid,value):
  from datetime import date
  if value:date.fromisoformat(value)
  s=self.state(lid);s.pop('takes');s['planned_date']=value
  with self.store.connect() as c:c.execute('INSERT OR REPLACE INTO lesson_state VALUES(?,?)',(lid,json.dumps(s)))
  return self.state(lid)
 def obs(self,prepare=False):
  try:
   with (preparation_connection(self.obs_factory) if prepare else self.obs_factory()) as obs:return obs.prepare() if prepare else obs.status()
  except Conflict:raise
  except Exception:return dict(connected=False,prepared=False,message='OBS is not available. Open OBS manually, choose profile Lessons, scene collection Lessons - Facecam and Slides, then Lesson - Slides LEFT + Facecam RIGHT. Recording stays manual. No credentials or settings were changed.')
 def candidates(self,lid=None):
  files=[]
  if self.input.is_dir():
   for p in self.input.iterdir():
    if p.is_file() and p.suffix.lower() in {'.mkv','.mp4','.mov','.webm'}:
     s=p.stat();files.append(dict(name=p.name,path=str(p),size=s.st_size,mtime_ns=str(s.st_mtime_ns),ready_age_seconds=max(0,time.time()-s.st_mtime)))
  if lid:
   bound=self.binding()
   if not bound or bound['lesson_id']!=lid:raise Conflict('Prepare this lesson before recording; no completed takes are bound to it yet.')
   files=[x for x in files if x['path'] not in bound['existing_paths'] and int(x['mtime_ns'])>=int(bound['prepared_ns'])]
   if bound.get('closed_log_cursor'):
    closed_paths={str(Path(p).resolve()).casefold() for p in bound.get('closed_existing_paths',[])}
    files=[x for x in files if str(Path(x['path']).resolve()).casefold() in closed_paths]
  ordered=sorted(files,key=lambda x:int(x['mtime_ns']),reverse=True)
  return ordered if lid else ordered[:20]
 def prompter(self):
  command=[r'C:\Users\Anigma PC\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe',str(Path(__file__).parent/'prompter.mjs')]
  def read(mode):
   p=subprocess.run(command+[mode],capture_output=True,text=True,timeout=12,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
   if p.returncode:raise ValueError('Camera Hub unavailable')
   return json.loads(p.stdout)
  try:
   status=read('status')
   if status['screen_mode']:return status
   with self.obs_factory() as obs:obs.idle()
   return read('screen')
  except Exception:return dict(connected=False,screen_mode=False,message='Select Screen mode manually in Camera Hub and verify the intended screen on the Prompter. Automatic mode preparation is unavailable or OBS is busy.')
 def import_take(self,lid,data):
  e=self.entry(lid)
  if data.get('confirmed_stopped') is not True:raise ValueError('Confirm the recording has finished in OBS')
  source=Path(data['path']).resolve()
  if source.parent!=self.input or source.suffix.lower() not in {'.mkv','.mp4','.mov','.webm'}:raise ValueError('Choose a recording from the configured OBS input folder')
  if not self.lock.acquire(False):raise Conflict('Another recording import is in progress')
  try:
   bound=self.binding()
   if data.get('session_id'):
    if data.get('confirmed_association') is not True:raise ValueError('Confirm this exact file belongs to the displayed lesson.')
    with self.store.connect() as c:row=c.execute('SELECT body FROM lesson_sessions WHERE id=? AND lesson_id=?',(data['session_id'],lid)).fetchone()
    if not row:raise Conflict('Recovery session is unavailable for this lesson.')
    bound=json.loads(row['body'])
    if str(source) not in {x['path'] for x in self.session_candidates(bound)}:raise Conflict('This file is outside the preserved session. No file was assigned.')
   if not bound or bound['lesson_id']!=lid:raise Conflict('Choose the file from its original lesson in Recovery.')
   if data.get('expected_binding_id') and bound['id']!=data['expected_binding_id']:raise Conflict('Lesson session changed; no recording was assigned.')
   with self.store.connect() as c:existing_takes=[json.loads(r['body']) for r in c.execute('SELECT body FROM lesson_takes')]
   same=[t for t in existing_takes if self.recording_key(t['source_path'])==self.recording_key(source)]
   if same:
    if any(t['lesson_id']!=lid for t in same):raise Conflict('This recording already belongs to another lesson.')
    if any(Path(t['source_path']).resolve()!=source for t in same):return self.state(lid)
   status=self.recorder_idle()
   if data.get('handover_evidence') and not status.get('connected'):raise Conflict('Cannot verify OBS stopped; automatic handover deferred.')
   if status.get('recording'):raise Conflict('OBS is still recording. Finish recording manually before import.')
   if data.get('handover_evidence'):
    fresh=self.session_evidence(bound)
    if not fresh['valid'] or fresh['ambiguous'] or fresh['completed'].get(self.recording_key(source))!=data['handover_evidence']:raise Conflict('OBS evidence changed before import; recording remains untouched.')
   a=source.stat()
   if str(source) in bound['existing_paths'] or a.st_mtime_ns<int(bound['prepared_ns']):raise Conflict('This file predates lesson preparation and cannot be associated as a new take.')
   if a.st_size!=data['size'] or a.st_mtime_ns!=int(data['mtime_ns']):raise Conflict('Recording changed since selection. Refresh and select it again.')
   if time.time()-a.st_mtime<5:raise Conflict('Recording was just modified. Wait five seconds and retry.')
   destdir=(self.archive/e['track_key']/('part-'+e['code'].split('.')[0])/(e['code']+'-'+e['slug'])).resolve()
   if not destdir.is_relative_to(self.archive):raise ValueError('Invalid lesson folder')
   destdir.mkdir(parents=True,exist_ok=True);tid=str(uuid.uuid4());partial=destdir/(tid+'.partial')
   with completed_file(source) as reader:
    time.sleep(.25);b=source.stat()
    if (a.st_size,a.st_mtime_ns)!=(b.st_size,b.st_mtime_ns):raise Conflict('Recording is still changing')
    h=hashlib.sha256()
    with partial.open('xb') as writer:
     for block in iter(lambda:reader.read(1024*1024),b''):writer.write(block);h.update(block)
     writer.flush();os.fsync(writer.fileno())
   if partial.stat().st_size!=a.st_size:raise Conflict('Incomplete copy retained for inspection; original unchanged')
   probe=subprocess.run([self.config['ffmpeg'],'-hide_banner','-nostdin','-i',str(partial),'-map','0:v:0','-t','0.05','-f','null','-'],capture_output=True,text=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
   match=re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)',probe.stderr);duration=sum(float(v)*m for v,m in zip(match.groups(),[3600,60,1])) if match else 0
   if probe.returncode or duration<=0:raise ValueError('Recording could not be validated as a finished playable video. Original untouched; partial copy retained for inspection.')
   with self.store.connect() as c:
    c.execute('BEGIN IMMEDIATE');existing=[json.loads(r['body']) for r in c.execute('SELECT body FROM lesson_takes WHERE lesson_id=?',(lid,))]
    if any(self.recording_key(t['source_path'])==self.recording_key(source) and t['sha256']==h.hexdigest() for t in existing):
     # A repeated import is a no-op, including on disk. This is only the
     # temporary copy made by this request; the source and saved take stay intact.
     partial.unlink()
     return self.state(lid)
    num=len(existing)+1
    while True:
     filename=datetime.now().strftime('%Y-%m-%d')+'_'+e['track_key']+'-'+e['code']+'_'+e['slug']+'_take%02d'%num+source.suffix.lower();dest=destdir/filename
     if not dest.exists():break
     num+=1
    partial.rename(dest)
    take=dict(id=tid,lesson_id=lid,binding_id=bound['id'],path=str(dest),name=dest.name,source_path=str(source),size=a.st_size,sha256=h.hexdigest(),duration=duration,recorded_at=now(),approved_at=None,uploaded=None,student_access_verified=None)
    if data.get('handover_evidence'):take['handover']=dict(method='obs_log_and_prepared_session',evidence=data['handover_evidence'])
    c.execute('INSERT INTO lesson_takes VALUES(?,?,?)',(tid,lid,json.dumps(take)))
    row=c.execute('SELECT body FROM lesson_state WHERE id=?',(lid,)).fetchone()
    if row:
     state=json.loads(row['body']) # Keep prior completion history; readiness is derived from validated content.
     c.execute('UPDATE lesson_state SET body=? WHERE id=?',(json.dumps(state),lid))
   self.audio.ensure(take)
   return self.state(lid)
  finally:self.lock.release()
 def take_action(self,lid,data):
  if data.get('action') in {'audio_check','audition'}:
   take=next((t for t in self.state(lid)['takes'] if t['id']==data.get('take_id')),None)
   if not take:raise ValueError('Take not found in this lesson')
   if data['action']=='audio_check':self.audio.ensure(take,retry=True)
   else:self.audio.audition(take,data)
   return self.state(lid)
  with self.lock:return self._take_action(lid,data)
 def _take_action(self,lid,data):
  e=self.entry(lid);action=data['action'];tid=data['take_id']
  if action in {'complete','prefer','approve','uploaded','student_access_verified'}:
   with self.store.connect() as db:row=db.execute('SELECT body FROM lesson_takes WHERE id=? AND lesson_id=?',(tid,lid)).fetchone()
   if not row:raise ValueError('Take not found in this lesson')
   self.audio.require(json.loads(row['body']))
  if action=='complete':
   if data.get('confirmed') is not True:raise ValueError('Confirm the reviewed recording before marking the lesson done.')
   self.recorder_idle()
  with self.store.connect() as c:
   c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT body FROM lesson_takes WHERE id=? AND lesson_id=?',(tid,lid)).fetchone()
   if not row:raise ValueError('Take not found in this lesson')
   t=json.loads(row['body'])
   if not Path(t['path']).is_file():raise ValueError('Recording file is missing')
   if action=='complete':
    target=Path(t['path']).resolve();expected=(self.archive/e['track_key']/('part-'+e['code'].split('.')[0])/(e['code']+'-'+e['slug'])).resolve()
    if target.parent!=expected or target.name!=t['name'] or target.stat().st_size!=t['size'] or digest(target)!=t['sha256']:raise Conflict('Saved take path or content changed. Review the archive before confirming.')
    row=c.execute('SELECT body FROM lesson_state WHERE id=?',(lid,)).fetchone();s=json.loads(row['body']) if row else dict(id=lid,planned_date='')
    self.recorder_idle()
    if s.get('recorded_completion',{}).get('take_id')!=tid:
     s.update(preferred_take=tid,recorded_completion=dict(status='recorded',take_id=tid,path=t['path'],name=t['name'],sha256=t['sha256'],confirmed_at=now(),scope='recording_only',binding_id=t.get('binding_id'),verification='explicit_review_of_saved_take'))
     c.execute('INSERT OR REPLACE INTO lesson_state VALUES(?,?)',(lid,json.dumps(s)))
   elif action=='prefer':
    row=c.execute('SELECT body FROM lesson_state WHERE id=?',(lid,)).fetchone();s=json.loads(row['body']) if row else dict(id=lid,planned_date='');s['preferred_take']=tid;s['take_selection']=dict(mode='manual',take_id=tid,selected_at=now())
    if s.get('recorded_completion',{}).get('take_id')!=tid:s.pop('recorded_completion',None)
    c.execute('INSERT OR REPLACE INTO lesson_state VALUES(?,?)',(lid,json.dumps(s)))
   elif action=='open':
    os.startfile(t['path'])
   elif action=='approve':
    if data.get('confirmed') is not True:raise ValueError('Review the take before approving')
    t['approved_at']=now()
   elif action in ['uploaded','student_access_verified']:
    url=data.get('url','');note=data.get('note','').strip()
    if not re.match(r'^https://[^/\s]+(?:/|$)',url) or not note or data.get('confirmed') is not True:raise ValueError('Provide the actual destination URL, verification note and confirmation')
    if action=='student_access_verified' and not t['uploaded']:raise ValueError('Record the uploaded destination first')
    t[action]=dict(at=now(),url=url,note=note,evidence='user-confirmed manual check')
    if action=='uploaded':t['student_access_verified']=None
   else:raise ValueError('Unknown take action')
   c.execute('UPDATE lesson_takes SET body=? WHERE id=?',(json.dumps(t),tid))
  return self.state(lid)
 def open_source(self,lid,key):
  e=self.entry(lid);allowed={'script':e.get('script'),'folder':str(Path(e['script']).parent) if e.get('script') else None}
  allowed.update({str(i):x['path'] for i,x in enumerate(e.get('materials',[]))})
  if key not in allowed or not allowed[key]:raise ValueError('Source is not mapped')
  p=Path(allowed[key]).resolve()
  if not p.exists() or (p.is_file() and p.suffix.lower() not in {'.md','.txt','.html','.htm','.pdf','.pptx'}):raise ValueError('Source file is unavailable')
  os.startfile(str(p));return dict(opened=True)

 def use_latest(self,lid):
  with self.lock:
   state=self.state(lid);state.pop('takes');state.pop('take_selection',None);state.pop('recorded_completion',None);state['preferred_take']=None
   with self.store.connect() as c:c.execute('INSERT OR REPLACE INTO lesson_state VALUES(?,?)',(lid,json.dumps(state)))
  return self.state(lid)
 def written_lesson(self,lid,data):
  self.entry(lid);status=data.get('status','not_started');value=data.get('path','').strip()
  if status not in {'not_started','draft','ready'}:raise ValueError('Choose a written lesson status')
  if value:
   p=Path(value).resolve()
   if not p.is_file() or p.suffix.lower() not in {'.md','.txt','.pdf','.docx','.html','.htm','.pptx'}:raise ValueError('Choose an existing written lesson file: PDF, Word, Markdown, text, HTML or PowerPoint.')
   value=str(p)
  if status=='ready' and not value:raise ValueError('Link the finished written lesson file before marking it ready.')
  with self.lock:
   state=self.state(lid);state.pop('takes');state['written_lesson']=dict(status=status,path=value,updated_at=now())
   with self.store.connect() as c:c.execute('INSERT OR REPLACE INTO lesson_state VALUES(?,?)',(lid,json.dumps(state)))
  return self.state(lid)
 def delivery_plan(self,lid):
  state=self.state(lid);selected=next((t for t in state['takes'] if t['id']==state.get('preferred_take')),None);written=state.get('written_lesson',{});files=[];blockers=['Choose a Google Drive destination before uploading. Drive upload is not connected in this app yet.']
  if not state.get('recorded_completion'):blockers.append('Confirm a reviewed saved take before uploading.')
  if selected:
   if Path(selected['path']).is_file():
    try:self.audio.require(selected)
    except Conflict as error:blockers.append(str(error))
    else:files.append(dict(kind='recording',path=selected['path'],name=selected['name'],take_id=selected['id'],audio_validation=selected['audio_validation']))
   else:blockers.append('The selected recording file is missing.')
  else:blockers.append('No recording take has been selected.')
  if written.get('status')=='ready' and written.get('path') and Path(written['path']).is_file():files.append(dict(kind='written_lesson',path=written['path'],name=Path(written['path']).name))
  else:blockers.append('Link a finished written lesson and mark it ready to include it.')
  return dict(status='setup_needed',can_upload=False,files=files,blockers=blockers,uploaded=False)
