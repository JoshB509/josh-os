"""Artifact-backed local status and explicit desktop task control. No networking."""
import hashlib,json,uuid
from pathlib import Path
from .store import Conflict,now
from .writing import Writing
from .jobs import Jobs

KINDS={'greenscreen','html'}


def fingerprint(path):
    p=Path(path).resolve()
    if not p.is_file():raise ValueError('Artifact file is missing: '+str(p))
    stat=p.stat();h=hashlib.sha256()
    # Small documents get a full hash. Large media uses a declared identity
    # fingerprint; do not read gigabytes just to display a status card.
    with p.open('rb') as f:
        if stat.st_size<=20*1024*1024:
            h.update(f.read());method='sha256-full'
        else:
            h.update(f.read(65536));f.seek(max(0,stat.st_size-65536));h.update(f.read(65536));method='sha256-size-mtime-edges'
    body=dict(path=str(p),size=stat.st_size,mtime_ns=stat.st_mtime_ns,content_digest=h.hexdigest(),method=method)
    body['identity']=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
    return body


class Control:
    def __init__(self,store,root,jobs=None):
        self.store=store;self.root=Path(root);self.jobs=jobs or Jobs(store,root)
        with store.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS pipeline_artifacts(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS control_requests(id TEXT PRIMARY KEY,digest TEXT NOT NULL,body TEXT NOT NULL);''')
    def register(self,pid,kind,path,revision):
        if kind not in KINDS:raise ValueError('Unknown artifact kind')
        if kind=='html' and Path(path).suffix.lower() not in ('.html','.htm'):raise ValueError('Choose an HTML document')
        if kind=='greenscreen' and Path(path).suffix.lower() not in ('.mp4','.mov','.mkv','.avi','.webm'):raise ValueError('Choose a prepared composite video')
        identity=fingerprint(path)
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');p=c.execute('SELECT revision FROM projects WHERE id=?',(pid,)).fetchone()
            if not p:raise KeyError('Project not found')
            if p['revision']!=revision:raise Conflict('Project changed before artifact registration')
            artifact=dict(id=str(uuid.uuid4()),project_id=pid,kind=kind,fingerprint=identity,created_at=now(),approval='pending',approved_at=None)
            c.execute('INSERT INTO pipeline_artifacts VALUES(?,?,?,?)',(artifact['id'],pid,kind,json.dumps(artifact)))
        return artifact
    def approve(self,pid,aid,identity):
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT body FROM pipeline_artifacts WHERE id=? AND project_id=?',(aid,pid)).fetchone()
            if not row:raise KeyError('Artifact not found in this project')
            a=json.loads(row['body'])
            if a['kind'] not in ('html','greenscreen'):raise ValueError('This artifact is readiness evidence, not an approval step')
            actual=fingerprint(a['fingerprint']['path'])
            if actual['identity']!=identity or identity!=a['fingerprint']['identity']:raise Conflict('Artifact changed; register and review its new version')
            latest=c.execute('SELECT id FROM pipeline_artifacts WHERE project_id=? AND kind=? ORDER BY rowid DESC LIMIT 1',(pid,a['kind'])).fetchone()
            if latest['id']!=aid:raise Conflict('A newer artifact version is awaiting review')
            a.update(approval='approved',approved_at=now());c.execute('UPDATE pipeline_artifacts SET body=? WHERE id=?',(json.dumps(a),aid))
            return a
    def artifacts(self,pid):
        with self.store.connect() as c:rows=c.execute('SELECT body FROM pipeline_artifacts WHERE project_id=? ORDER BY rowid DESC',(pid,)).fetchall()
        latest={}
        for row in rows:
            a=json.loads(row['body']);latest.setdefault(a['kind'],a)
        return latest
    def artifact_status(self,a):
        if not a:return dict(state='not_provided')
        try:current=fingerprint(a['fingerprint']['path']);valid=current['identity']==a['fingerprint']['identity']
        except (OSError,ValueError):valid=False
        return dict(state=('approved' if a['approval']=='approved' else 'awaiting_approval') if valid else 'stale',
                    artifact_id=a['id'],identity=a['fingerprint']['identity'],path=a['fingerprint']['path'],approval_valid=valid and a['approval']=='approved',hash_method=a['fingerprint']['method'])
    def status(self,pid):
        p=self.store.get(pid);artifacts=self.artifacts(pid);pending=[]
        media=p['media'];missing=[k for k,v in media.items() if v and not Path(v).is_file()]
        raw=dict(state='ready' if media.get('video') and Path(media['video']).is_file() and not missing else 'needs_inputs',missing=missing,source=media.get('video'))
        script=Writing(self.store).workspace(pid)['current_version']
        script_state=dict(state=script['approval'] if script and script['content'].strip() else 'draft' if p['script'].strip() else 'missing',version_id=script['id'] if script else None,sha256=script['sha256'] if script else None)
        if script_state['state']=='draft':pending.append(dict(kind='script',version_id=script['id'] if script else None,action='Review and approve the saved script'))
        green=self.artifact_status(artifacts.get('greenscreen'));green['generation_available']=False
        html=self.artifact_status(artifacts.get('html'))
        if media.get('deck'):
            if not Path(media['deck']).is_file():html=dict(state='missing',path=media['deck'])
            elif not artifacts.get('html'):html=dict(state='prepared_unregistered',path=media['deck'])
            elif Path(media['deck']).resolve()!=Path(artifacts['html']['fingerprint']['path']).resolve():html=dict(state='stale',path=media['deck'],reason='Selected HTML differs from approved artifact')
        for kind,value in [('greenscreen',green),('html',html)]:
            if value['state'] in ('awaiting_approval','prepared_unregistered','stale'):
                pending.append(dict(kind=kind,artifact_id=value.get('artifact_id'),identity=value.get('identity'),action='Review the current '+kind+' artifact'))
        jobs=self.jobs.all(pid);latest=jobs[0] if jobs else None
        result=dict(project_id=pid,name=p['name'],revision=p['revision'],planning_stage=p['stage'],raw_inputs=raw,script=script_state,
                    greenscreen=green,html=html,clean_cut=dict(state='not_ready'),animations=dict(state='not_ready'),timing_assembly=dict(state='not_ready'),
                    edit_job=dict(state=latest['status']['state'],id=latest['id'],path=latest['path']) if latest else dict(state='not_started'),xml=dict(state='not_exported'),
                    pending_decisions=pending,capabilities=dict(greenscreen_generation=False,live_ai=False,external_tracker_sync=False,explicit_local_edit_launch=True))
        if latest:
            folder=Path(latest['path']);source_matches=False
            try:
                inputs=json.loads((folder/'inputs.json').read_text());v=inputs['video'];sp=Path(v['path'])
                source_matches=sp.resolve()==Path(media.get('video','')).resolve() and sp.is_file() and sp.stat().st_size==v['size'] and sp.stat().st_mtime_ns==v['mtime_ns']
            except (OSError,ValueError,KeyError):pass
            result['edit_job']['source_matches_current_project']=source_matches
            if source_matches:
                try:
                    mapping=json.loads((folder/'timing_map.json').read_text());cursor=0
                    for span in mapping['spans']:
                        if span['out_in_frame']!=cursor or span['out_out_frame']<=cursor:raise ValueError('Invalid timing map')
                        cursor=span['out_out_frame']
                    if cursor!=mapping['duration_frames'] or cursor<=0:raise ValueError('Incomplete timing map')
                    result['clean_cut']=dict(state='ready',path=str(folder/'timing_map.json'),retained_ranges=len(mapping['spans']))
                    result['timing_assembly']=dict(state='timing_ready',duration_frames=cursor,fps=mapping['fps'])
                except (OSError,ValueError,KeyError):pass
                try:
                    shots=json.loads((folder/'shots.json').read_text());needed=[s for s in shots if s['layout']!='full'];present=sum(bool(s.get('asset')) and Path(s['asset']).is_file() and Path(s['asset']).stat().st_size>0 for s in needed)
                    result['animations']=dict(state='ready' if needed and present==len(needed) else 'not_required' if not needed else 'incomplete',required=len(needed),present=present)
                except (OSError,ValueError,KeyError):pass
                xml=folder/'EDITABLE_DRAFT.xml'
                if xml.is_file() and xml.stat().st_size:
                    result['xml']=dict(state='exported',path=str(xml),native_playback_verified=False)
                    try:
                        validation=json.loads((folder/'xml_validation.json').read_text());result['xml']['structural_validation_passed']=bool(validation['dtd_valid'] and validation['semantic_checks'])
                    except (OSError,ValueError,KeyError):result['xml']['structural_validation_passed']=False
            elif latest['status']['state']=='complete':result['xml']=dict(state='stale',reason='Completed run does not match current source media')
        return result
    def attach_run(self,pid,folder,revision):
        folder=Path(folder).resolve()
        if not folder.is_relative_to((self.root/'Runs').resolve()):raise ValueError('Only this app\'s existing run folders can be attached')
        inputs=json.loads((folder/'inputs.json').read_text());status=json.loads((folder/'status.json').read_text())
        if status.get('state') not in ('complete','failed','cancelled'):raise ValueError('Only an existing finished run can be attached')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT revision,body FROM projects WHERE id=?',(pid,)).fetchone()
            if not row or row['revision']!=revision:raise Conflict('Project changed before run attachment')
            p=json.loads(row['body']);video=Path(p['media'].get('video','')).resolve()
            if video!=Path(inputs['video']['path']).resolve() or not video.is_file():raise ValueError('Run source does not match this project')
            if video.stat().st_size!=inputs['video']['size'] or video.stat().st_mtime_ns!=inputs['video']['mtime_ns']:raise ValueError('Run source has changed')
            jid=str(uuid.uuid5(uuid.NAMESPACE_URL,pid+str(folder)))
            job=dict(id=jid,project_id=pid,path=str(folder),request=str(folder/'request.json'),created_at=now(),cancel_requested=False,attached_existing_run=True)
            c.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?)',(jid,pid,json.dumps(job)))
        return job
    def start(self,pid,revision,request_id):
        uuid.UUID(request_id);payload=dict(project_id=pid,expected_revision=revision,request_id=request_id)
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');prior=c.execute('SELECT digest,body FROM control_requests WHERE id=?',(request_id,)).fetchone()
            if prior:
                if prior['digest']!=digest:raise Conflict('Launch request ID already used for another request')
                return json.loads(prior['body'])
            project=c.execute('SELECT revision FROM projects WHERE id=?',(pid,)).fetchone()
            if not project or project['revision']!=revision:raise Conflict('Project identity/revision changed; inspect current status before starting')
            receipt=dict(request_id=request_id,project_id=pid,state='launch_pending')
            c.execute('INSERT INTO control_requests VALUES(?,?,?)',(request_id,digest,json.dumps(receipt)))
        try:
            job=self.jobs.start(pid,expected_revision=revision);receipt.update(state='already_running' if job.get('already_running') else 'launched',job_id=job['id'],path=job['path'])
        except Exception as e:
            receipt.update(state='failed',error=str(e))
        with self.store.connect() as c:c.execute('UPDATE control_requests SET body=? WHERE id=?',(json.dumps(receipt),request_id))
        return receipt
