"""Launch the existing editor unchanged, with isolated requests and cancellation."""
import json,os,subprocess,sys,threading,time,uuid
from pathlib import Path
from .store import now,Conflict

class Jobs:
    def __init__(self,store,app_root,command=None):
        self.store=store;self.root=Path(app_root).resolve();self.lock=threading.RLock();self.processes={};self.started={};self.command=command
    def status(self,j):
        j=dict(j);path=Path(j['path']);p=self.processes.get(j['id']);state={}
        try:state=json.loads((path/'status.json').read_text(encoding='utf-8-sig'))
        except (OSError,ValueError):pass
        if j.get('launch_error') and p is None:state=dict(state='failed',message=j['launch_error'])
        if p is not None and p.poll() is None:
            if (path/'status.json').exists() and (path/'status.json').stat().st_mtime<self.started.get(j['id'],0):state={}
            state.setdefault('state','starting')
        elif state.get('state') in ['running','starting'] or not state:
            # A worker can outlive this server; the OS job lock is authoritative.
            state['state']='running' if self.locked(path) else 'interrupted'
            state.setdefault('message','Worker interrupted. Resume uses its saved request.')
        j['status']=state;j['cancel_requested']=(path/'CANCEL').exists() or j.get('cancel_requested',False)
        if state.get('state')=='running' and state.get('stage')=='keying':
            try:state.update(json.loads((path/'keying'/'progress.json').read_text(encoding='utf8')))
            except (OSError,ValueError):pass
        j['outputs']={k:(path/k).is_file() for k in ['EDITABLE_DRAFT.xml','REVIEW_PREVIEW.mp4','CLEAN_AUDIO.wav','audio_joins.json','KEYING_PREVIEW.mp4','keying/source.png','exceptions.csv','timing_map.json']}
        return j
    def locked(self,path):
        f=Path(path)/'.run.lock'
        if not f.exists():return False
        try:
            with f.open('r+b') as h:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(h.fileno(),msvcrt.LK_NBLCK,1);msvcrt.locking(h.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(h.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(h.fileno(),fcntl.LOCK_UN)
            return False
        except OSError:return True
    def all(self,pid=None):return [self.status(j) for j in self.store.jobs(pid)]
    def active(self,pid):
        return next((j for j in self.all(pid) if j['status'].get('state') in ['starting','running']),None)
    def start(self,pid,expected_revision=None,keying_only=False):
        with self.lock:
            active=self.active(pid)
            if active:
                active['already_running']=True
                return active
            p=self.store.get(pid)
            if expected_revision is not None and p['revision']!=expected_revision:raise Conflict('Project changed before edit launch; no process started')
            if p['archived']:raise ValueError('Restore the project before starting an edit')
            media={k:v.strip().strip('"') for k,v in p['media'].items() if v.strip()}
            if not media.get('video'):raise ValueError('Choose a source video first')
            for k,v in media.items():
                if not Path(v).is_file():raise ValueError('Missing '+k+': '+v)
            if media.get('shots') and Path(media['shots']).suffix.lower() not in ['.json','.csv']:raise ValueError('The editing engine needs a JSON or CSV shot list')
            if media.get('deck') and Path(media['deck']).suffix.lower() not in ['.html','.htm']:raise ValueError('Choose an HTML animation deck')
            jid=str(uuid.uuid4());reqdir=self.root/'Requests';reqdir.mkdir(exist_ok=True)
            cfg=dict(media,preview=False,start=0)
            from dajoshi.keying import settings as keysettings
            cfg['keying']=keysettings(p.get('keying',{}));cfg['keying']['preview']=keying_only
            if keying_only and not cfg['keying']['enabled']:raise ValueError('Enable CorridorKey first')
            if cfg['keying']['enabled'] and not keying_only:cfg['render_review_preview']=True
            if p['script'].strip():
                script=self.store.root/'scripts'/(pid+'-'+str(p['revision'])+'.txt');script.parent.mkdir(exist_ok=True)
                if not script.exists():script.write_text(p['script'],encoding='utf-8')
                cfg['script']=str(script)
            request=reqdir/('studio-'+jid+'.json');request.write_text(json.dumps(cfg,indent=2),encoding='utf-8')
            job=dict(id=jid,project_id=pid,path=str(self.root/'Runs'/('Studio-'+jid)),request=str(request),created_at=now(),cancel_requested=False)
            self.store.put_job(job)
            try:
                # Commit the revision guard before starting a costly process.
                self.store.save(pid,{} if keying_only else {'stage':'Editing'},p['revision'])
                self._spawn(job,False)
            except Exception as e:
                job['launch_error']=str(e);self.store.put_job(job);raise
            return self.status(job)
    def _spawn(self,j,resume):
        if self.command:cmd=list(self.command)
        else:
            settings=json.loads((self.root/'settings.json').read_text(encoding='utf-8-sig'))
            cmd=[settings['python'],str(self.root/'worker.py')]
        cmd+=['--job',j['path']]+(['--resume'] if resume else ['--request',j['request']])
        logs=self.store.root/'logs';logs.mkdir(exist_ok=True)
        log=(logs/(j['id']+'.log')).open('ab')
        self.started[j['id']]=time.time()
        try:p=subprocess.Popen(cmd,cwd=self.root,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        finally:log.close()
        self.processes[j['id']]=p
        threading.Thread(target=self._watch,args=(j['id'],p),daemon=True).start()
    def start_staged(self,pid,revision,cfg):
        with self.lock:
            if self.active(pid):raise Conflict('This project already has a running job')
            p=self.store.get(pid)
            if p['revision']!=revision:raise Conflict('Project changed before staged launch')
            if p['archived']:raise ValueError('Restore this project before launching')
            jid=str(uuid.uuid4());requests=self.root/'Requests';requests.mkdir(exist_ok=True)
            request=requests/('staged-'+jid+'.json');request.write_text(json.dumps(cfg,indent=2),encoding='utf-8')
            job=dict(id=jid,project_id=pid,path=str(self.root/'Runs'/('Staged-'+jid)),request=str(request),created_at=now(),cancel_requested=False,workflow_mode=cfg['workflow_mode'],artifact_id=cfg['workflow']['artifact_id'],artifact_digest=cfg['workflow']['digest'])
            self.store.put_job(job)
            try:self._spawn(job,False)
            except Exception as e:
                job['launch_error']=str(e);self.store.put_job(job);raise
            return self.status(job)
    def _watch(self,jid,p):
        while p.poll() is None:
            j=self.store.job(jid)
            if j and j.get('cancel_requested') and Path(j['path']).is_dir():(Path(j['path'])/'CANCEL').touch()
            time.sleep(.1)
        j=self.store.job(jid)
        if j:j['exit_code']=p.returncode;self.store.put_job(j)
    def cancel(self,jid):
        with self.lock:
            j=self.store.job(jid)
            if not j:raise KeyError('Run not found')
            if self.status(j)['status'].get('state') not in ['running','starting']:return self.status(j)
            j['cancel_requested']=True;self.store.put_job(j)
            if Path(j['path']).is_dir():(Path(j['path'])/'CANCEL').touch()
            return self.status(j)
    def resume(self,jid):
        with self.lock:
            j=self.store.job(jid)
            if not j:raise KeyError('Run not found')
            if self.active(j['project_id']):raise Conflict('This project already has a running edit')
            if not (Path(j['path'])/'request.json').exists():raise ValueError('Run did not initialize. Start a new edit instead.')
            if self.status(j)['status'].get('state')=='complete':return self.status(j)
            j['cancel_requested']=False;self.store.put_job(j);self._spawn(j,True);return self.status(j)
