"""Explicit YouTube project/take binding. No discovery, recording or power control."""
import contextlib,ctypes,hashlib,json,os,re,subprocess,threading,time,uuid
from pathlib import Path
from .store import Conflict,now
from .lesson_audio import stable_reader,inspect_audio,identity,sha256
from .lessons import OBS

FLAGS=getattr(subprocess,'CREATE_NO_WINDOW',0)

def idle_obs():
    try:
        with OBS() as obs:
            obs.idle()
            if obs.call('GetProfileList').get('currentProfileName')!='YouTube':
                raise Conflict('Select the YouTube OBS setup before locking a YouTube take.')
    except Conflict:raise
    except Exception:raise Conflict('Cannot verify OBS is idle. Open/reconnect OBS and finish recording/remux first.') from None

def plain_path(value):
    raw=Path(value.strip().strip('"'))
    if not raw.is_absolute():raise ValueError('Use an absolute local folder/file path.')
    for p in [raw,*raw.parents]:
        if p.exists() and (p.is_symlink() or getattr(p,'is_junction',lambda:False)()):
            raise ValueError('Linked folders/files are not supported for recording moves.')
    return raw.resolve()

@contextlib.contextmanager
def move_handle(path):
    """Hold the source exclusively through the no-overwrite Windows rename."""
    if os.name!='nt':raise Conflict('Lock Take file moves require Windows.')
    import msvcrt
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
    k.CreateFileW.restype=ctypes.c_void_p
    k.SetFileInformationByHandle.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32]
    k.SetFileInformationByHandle.restype=ctypes.c_int
    h=k.CreateFileW(str(path),0x80000000|0x10000,0,None,3,0x80,None)
    if h==ctypes.c_void_p(-1).value:raise Conflict('Take is open in another app or still recording/remuxing. Close the file and retry.')
    try:fd=msvcrt.open_osfhandle(h,os.O_RDONLY|os.O_BINARY)
    except Exception:
        k.CloseHandle(ctypes.c_void_p(h));raise
    with os.fdopen(fd,'rb') as reader:
        def rename(dest):
            name=str(dest)
            class Rename(ctypes.Structure):
                _fields_=[('ReplaceIfExists',ctypes.c_byte),('RootDirectory',ctypes.c_void_p),('FileNameLength',ctypes.c_uint32),('FileName',ctypes.c_wchar*len(name))]
            info=Rename();info.ReplaceIfExists=0;info.RootDirectory=None;info.FileNameLength=len(name.encode('utf-16-le'));info.FileName=name
            if not k.SetFileInformationByHandle(h,3,ctypes.byref(info),ctypes.sizeof(info)):
                raise Conflict('Take was not moved (destination collision, permissions or different disk). Original retained. Windows error '+str(ctypes.get_last_error()))
        yield reader,rename

class Recording:
    def __init__(self,store,root,input_dir='E:/Videos/Input',folder_roots=None,idle=None,validator=None,opener=None):
        self.store,self.root=store,Path(root)
        self.input=Path(input_dir).resolve()
        self.folder_roots=[Path(p).resolve() for p in (folder_roots or ['E:/CareerOS/12_YouTube','E:/Videos'])]
        self.idle=idle or idle_obs;self.validator=validator or self.validate_media
        self.opener=opener or (lambda p:os.startfile(str(p)))
        self.lock=threading.Lock()
        with store.connect() as c:c.executescript('''
            CREATE TABLE IF NOT EXISTS youtube_recording_active(singleton INTEGER PRIMARY KEY CHECK(singleton=1),body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS youtube_recording_projects(project_id TEXT PRIMARY KEY,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS youtube_recording_takes(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,body TEXT NOT NULL);
        ''')

    def active(self,c):
        r=c.execute('SELECT body FROM youtube_recording_active WHERE singleton=1').fetchone()
        return json.loads(r['body']) if r else None
    def binding(self,c,pid):
        r=c.execute('SELECT body FROM youtube_recording_projects WHERE project_id=?',(pid,)).fetchone()
        return json.loads(r['body']) if r else {}
    def eligible(self,p):
        if p['archived'] or p['stage']!='Recording':raise Conflict('Choose an unarchived video at Recording stage in JoshOS.')
    def no_lesson(self,c):
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='lesson_binding'").fetchone() and c.execute('SELECT 1 FROM lesson_binding').fetchone():
            raise Conflict('Finish/release the existing lesson recording session in JoshOS first. It was not changed.')
    def destination(self,folder):
        p=plain_path(folder)
        if not p.is_dir() or not any(p.is_relative_to(r) and p!=r for r in self.folder_roots):
            raise ValueError('Choose an existing video project folder inside E:\\CareerOS\\12_YouTube or E:\\Videos.')
        if p.is_relative_to(self.root) or p==self.input or p.is_relative_to(self.input) or self.input.is_relative_to(p):
            raise ValueError('Choose the video project folder, not the input folder or JoshOS application folder.')
        dest=plain_path(str(p/'Recorded'/'Input'))
        if dest.drive.lower()!=self.input.drive.lower():raise ValueError('Project and recording input must be on the same disk for a safe move.')
        return dest
    def state(self,pid):
        p=self.store.get(pid)
        with self.store.connect() as c:
            a=self.active(c);b=self.binding(c,pid)
            rows=c.execute('SELECT body FROM youtube_recording_takes WHERE project_id=? ORDER BY rowid DESC',(pid,)).fetchall()
        return dict(project_id=pid,active=bool(a and a['project_id']==pid),active_project_id=a['project_id'] if a else None,
            eligible=not p['archived'] and p['stage']=='Recording',binding=b,takes=[json.loads(r['body']) for r in rows])
    def activate(self,pid,revision,folder):
        dest=self.destination(folder)
        with self.lock,self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');p=json.loads(c.execute('SELECT body FROM projects WHERE id=?',(pid,)).fetchone()['body'])
            self.eligible(p)
            if p['revision']!=revision:raise Conflict('Save/reload the video before setting it active.')
            b=self.binding(c,pid)
            if b.get('destination') and b['destination']!=str(dest) and b.get('selection'):raise Conflict('A take is already selected for the saved destination. Finish it before changing folders.')
            b.update(project_folder=str(plain_path(folder)),destination=str(dest))
            c.execute('INSERT OR REPLACE INTO youtube_recording_projects VALUES(?,?)',(pid,json.dumps(b)))
            c.execute('INSERT OR REPLACE INTO youtube_recording_active VALUES(1,?)',(json.dumps(dict(project_id=pid,selected_at=now())),))
        return self.state(pid)
    def select(self,pid,revision,path):
        source=plain_path(path)
        if source.parent!=self.input or source.suffix.lower() not in ('.mp4','.mkv','.mov','.webm') or not source.is_file():
            raise ValueError('Choose one exact finalized recording in E:\\Videos\\Input. No latest-file selection is used.')
        with self.lock,self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');a=self.active(c);p=self.store.get(pid);self.eligible(p);self.no_lesson(c)
            if not a or a['project_id']!=pid or p['revision']!=revision:raise Conflict('Save this video and set it as the active recording project first.')
            b=self.binding(c,pid)
            if b.get('selection'):
                r=c.execute('SELECT body FROM youtube_recording_takes WHERE id=?',(b['selection']['id'],)).fetchone()
                if r and json.loads(r['body'])['phase']!='complete':raise Conflict('Recover the pending Lock Take before selecting another source.')
            for row in c.execute('SELECT body FROM youtube_recording_projects WHERE project_id<>?',(pid,)):
                other=json.loads(row['body']).get('selection',{})
                if other.get('source',{}).get('path')==str(source):raise Conflict('This exact source is already selected for another video.')
            b['selection']=dict(id=str(uuid.uuid4()),source=identity(source),selected_at=now())
            c.execute('UPDATE youtube_recording_projects SET body=? WHERE project_id=?',(json.dumps(b),pid))
        return self.state(pid)
    def validate_media(self,path):
        settings=json.loads((self.root/'settings.json').read_text(encoding='utf-8-sig'));ffmpeg=settings['ffmpeg']
        result=inspect_audio(path,ffmpeg)
        if result['status']!='passed':raise Conflict('Lock Take audio check: '+result['message'])
        r=subprocess.run([ffmpeg,'-hide_banner','-nostdin','-v','error','-xerror','-err_detect','explode','-i',str(path),'-map','0:v:0','-an','-sn','-dn','-f','null','-'],capture_output=True,text=True,errors='replace',timeout=7200,creationflags=FLAGS)
        if r.returncode or r.stderr.strip():raise Conflict('Video did not decode completely. Keep the original and finish recording/remux before retrying.')
        from dajoshi.media import probe
        info=probe(path)
        if not info.get('width') or not info.get('duration',0)>0:raise Conflict('No finalized video stream found.')
        return dict(audio=result,duration=info['duration'])
    def shared_source(self,c,pid,source):
        for row in c.execute('SELECT id,body FROM projects WHERE id<>?',(pid,)):
            if any(v and Path(v).resolve()==source for v in json.loads(row['body']).get('media',{}).values()):raise Conflict('Another project references this source. It was not moved.')
        for table in ('jobs','workflow_artifacts','pipeline_artifacts','lesson_takes'):
            if not c.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():continue
            for row in c.execute('SELECT body FROM '+table):
                if str(source).replace('\\','/').casefold() in row['body'].replace('\\\\','/').casefold():raise Conflict('An existing take/edit artifact references this source. Original retained.')
                if table=='jobs':
                    job=json.loads(row['body']);inputs=Path(job.get('path',''))/'inputs.json'
                    if inputs.is_file():
                        text=inputs.read_text(encoding='utf-8-sig')
                        if str(source).replace('\\','/').casefold() in text.replace('\\\\','/').casefold():raise Conflict('An existing edit run references this source. Original retained.')
    def _commit(self,c,p,r):
        media=dict(p['media'])
        for key,value in media.items():
            if value and Path(value).resolve()==Path(r['source']['path']):media[key]=r['destination']
        media['video']=r['destination']
        p.update(media=media,stage='Recorded',revision=p['revision']+1,updated_at=now())
        self.store.validate(p)
        r.update(phase='complete',completed_at=now(),project_revision=p['revision'])
        c.execute('UPDATE projects SET revision=?,body=? WHERE id=?',(p['revision'],json.dumps(p),p['id']))
        c.execute('UPDATE youtube_recording_takes SET body=? WHERE id=?',(json.dumps(r),r['id']))
    def lock_take(self):
        if not self.lock.acquire(False):raise Conflict('Lock Take is already checking this recording. Wait for it to finish.')
        try:
            self.idle()
            with self.store.connect() as c:
                a=self.active(c)
                if not a:raise Conflict('Set one YouTube video active at Recording stage in JoshOS first.')
                pid=a['project_id'];p=self.store.get(pid);b=self.binding(c,pid);s=b.get('selection');self.no_lesson(c)
                if not s:raise Conflict('Choose the exact finished take in the active video in JoshOS first.')
                row=c.execute('SELECT body FROM youtube_recording_takes WHERE id=?',(s['id'],)).fetchone();r=json.loads(row['body']) if row else None
            if r and r['phase']=='complete':
                dest=Path(r['destination'])
                if p['media'].get('video')!=str(dest) or not dest.is_file() or sha256(dest)!=r['sha256']:raise Conflict('The previously locked take changed. No files were moved.')
                return r
            self.eligible(p)
            destdir=self.destination(b['project_folder'])
            if str(destdir)!=b['destination']:raise Conflict('Saved destination changed.')
            source=plain_path(s['source']['path'])
            if source.parent!=self.input:raise Conflict('Selected recording is outside the configured input folder.')
            prior_video=p['media'].get('video')
            if (not prior_video or Path(prior_video).resolve()!=source) and any(p['media'].get(k) for k in ('audio','reference','transcript','shots')):
                raise Conflict('Existing editing inputs may belong to another take. Rebind or clear those inputs in this video before locking a replacement recording.')
            if r and Path(r['destination']).exists():
                # Durable journal recovers a crash after rename, before DB commit.
                dest=plain_path(r['destination'])
                if source.exists() or dest.parent!=destdir or sha256(dest)!=r['sha256']:raise Conflict('Recovery collision or changed take. Files were left untouched.')
                with self.store.connect() as c:
                    c.execute('BEGIN IMMEDIATE');fresh=self.store.get(pid)
                    if fresh['revision']!=r['expected_revision']:raise Conflict('Project changed during recovery. Saved media is preserved at '+str(dest))
                    self._commit(c,fresh,r)
                return r
            if not source.is_file() or identity(source)!=s['source']:raise Conflict('Selected take changed. Select its finalized file again in JoshOS.')
            if time.time()-source.stat().st_mtime<5:raise Conflict('Take was just modified. Wait for recording/remux to finish.')
            # Reject a still-present alternate container with the same basename.
            # OBS keeps the original .mkv beside the .mp4 it remuxes from it, so that one pair is expected and the .mkv stays put.
            if any(x!=source and x.is_file() and x.stem==source.stem and x.suffix.lower() in ('.mp4','.mkv','.mov','.webm','.tmp','.partial') and not (source.suffix.lower()=='.mp4' and x.suffix.lower()=='.mkv') for x in self.input.iterdir()):
                raise Conflict('Multiple containers or a remux/temp sibling exist for this take. Resolve the ambiguity before locking.')
            with stable_reader(source):
                initial=identity(source);time.sleep(.3)
                evidence=self.validator(source);hash_value=sha256(source)
                if initial!=identity(source):raise Conflict('Recording changed during validation.')
            self.idle()
            with move_handle(source) as (reader,rename):
                h=hashlib.sha256()
                for block in iter(lambda:reader.read(1024*1024),b''):h.update(block)
                stat=os.fstat(reader.fileno())
                if h.hexdigest()!=hash_value or stat.st_size!=initial['size'] or str(stat.st_mtime_ns)!=initial['mtime_ns']:raise Conflict('Take changed before move. Original retained.')
                destdir.mkdir(parents=True,exist_ok=True)
                slug=re.sub(r'[^a-z0-9]+','-',p['name'].lower()).strip('-')[:70] or 'video'
                dest=destdir/(slug+'-'+s['id'][:12]+source.suffix.lower())
                if dest.exists():raise Conflict('Destination already exists. No overwrite attempted.')
                with self.store.connect() as c:
                    c.execute('BEGIN IMMEDIATE');fresh=self.store.get(pid)
                    if fresh['revision']!=p['revision'] or self.active(c)!=a or self.binding(c,pid).get('selection')!=s:raise Conflict('Active video or take selection changed during validation.')
                    self.no_lesson(c);self.shared_source(c,pid,source)
                    if r and (r['sha256']!=hash_value or r['destination']!=str(dest)):raise Conflict('Recovery request no longer matches the selected take.')
                    r=dict(id=s['id'],project_id=pid,source=s['source'],destination=str(dest),sha256=hash_value,expected_revision=p['revision'],phase='prepared',validation=evidence,prepared_at=now())
                    c.execute('INSERT OR REPLACE INTO youtube_recording_takes VALUES(?,?,?)',(r['id'],pid,json.dumps(r)))
                # Recheck idle after long validation; no record/stop commands exist here.
                self.idle()
                with self.store.connect() as c:
                    c.execute('BEGIN IMMEDIATE');fresh=self.store.get(pid)
                    if fresh['revision']!=p['revision'] or self.active(c)!=a:raise Conflict('Project changed before move. Original retained.')
                    self.no_lesson(c)
                    self.shared_source(c,pid,source)
                    rename(dest)
                    self._commit(c,fresh,r)
            return r
        finally:self.lock.release()
    def invoke(self):
        r=self.lock_take()
        try:self.opener(Path(r['destination']).parent)
        except Exception:return dict(take=r,folder_opened=False,message='Take is saved. Folder could not be opened; use the saved path.')
        return dict(take=r,folder_opened=True)
