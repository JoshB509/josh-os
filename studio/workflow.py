"""Local review artifacts, immutable snapshots and honest stage gates.

No model client, media job, external publishing or production-project mutation.
"""
import copy
import hashlib
import json
import math
import uuid
from pathlib import Path
from .store import Conflict,now
from dajoshi.clean_cut import digest,validate_map,reject_ranges,remap_words
from dajoshi.export import export_xml
from dajoshi.timing import rate,frame

STEPS=[('source','Sources'),('pass1','Pass 1'),('clean','Clean cut'),('visuals','Visual plan'),
       ('assembly','Assemble'),('xml','XML export'),('package','Package')]

def file_identity(value):
    p=Path(value).resolve()
    if not p.is_file():raise ValueError('Missing local file: '+str(p))
    st=p.stat();h=hashlib.sha256()
    with p.open('rb') as f:
        h.update(f.read(1048576));f.seek(max(0,st.st_size-1048576));h.update(f.read(1048576))
    return dict(path=str(p),size=st.st_size,mtime_ns=st.st_mtime_ns,edge_sha256=h.hexdigest())

class Workflow:
    def __init__(self,store,root,jobs):
        self.store,self.root,self.jobs=store,Path(root),jobs
        with store.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS workflow_artifacts
                (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_approvals
                (artifact_id TEXT PRIMARY KEY,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_schema(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL);''')
            c.execute('INSERT OR IGNORE INTO workflow_schema VALUES(1,?)',(now(),))

    def source(self,p):
        if not p['media'].get('video'):raise ValueError('Choose a source video first')
        files={k:file_identity(p['media'][k].strip().strip('"')) for k in ('video','audio','reference') if p['media'].get(k)}
        return dict(files=files,keying=p.get('keying',{}))

    def get(self,pid,aid):
        with self.store.connect() as c:r=c.execute('SELECT body FROM workflow_artifacts WHERE id=? AND project_id=?',(aid,pid)).fetchone()
        if not r:raise KeyError('Workflow artifact not found in this project')
        return json.loads(r['body'])

    def artifacts(self,pid):
        with self.store.connect() as c:rows=c.execute('SELECT body FROM workflow_artifacts WHERE project_id=? ORDER BY rowid DESC',(pid,)).fetchall()
        return [json.loads(r['body']) for r in rows]

    def _current(self,pid,revision):
        p=self.store.get(pid)
        if p['revision']!=revision:raise Conflict('Project changed; save/reload before this action')
        if p['archived']:raise ValueError('Restore this project before editing')
        return p

    def _put(self,p,kind,payload,parent=None):
        body=dict(id=str(uuid.uuid4()),project_id=p['id'],kind=kind,created_at=now(),
                  source=self.source(p),parent=parent,payload=payload)
        body['digest']=digest(body)
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT revision FROM projects WHERE id=?',(p['id'],)).fetchone()['revision']!=p['revision']:
                raise Conflict('Project changed while preparing review artifact')
            c.execute('INSERT INTO workflow_artifacts VALUES(?,?,?,?)',(body['id'],p['id'],kind,json.dumps(body)))
        return body

    def health(self,p,a):
        original={k:v for k,v in a.items() if k!='digest'}
        if digest(original)!=a['digest']:return 'Artifact content changed'
        try:
            if self.source(p)!=a['source']:return 'Source media or keying settings changed'
            for identity in a['payload'].get('files',{}).values():
                if file_identity(identity['path'])!=identity:return 'Review evidence changed on disk'
            if a['kind']=='clean':
                folder=Path(a['payload']['folder'])
                for name,sha in a['payload']['hashes'].items():
                    if hashlib.sha256((folder/name).read_bytes()).hexdigest()!=sha:return 'Clean-cut artifact changed on disk'
            if a.get('parent'):
                parent=self.get(p['id'],a['parent'])
                if not self.approved(p,parent):return 'Upstream approval is missing or stale'
        except (OSError,ValueError,KeyError):return 'Required evidence is unavailable'
        return None

    def approved(self,p,a):
        with self.store.connect() as c:
            r=c.execute('SELECT body FROM workflow_approvals WHERE artifact_id=?',(a['id'],)).fetchone()
            latest=c.execute('SELECT id FROM workflow_artifacts WHERE project_id=? AND kind=? ORDER BY rowid DESC LIMIT 1',(p['id'],a['kind'])).fetchone()
        if not latest or latest['id']!=a['id']:return False
        return bool(r and json.loads(r['body'])['digest']==a['digest'] and not self.health(p,a))

    def status(self,pid):
        p=self.store.get(pid);artifacts=self.artifacts(pid);latest={}
        for a in artifacts:latest.setdefault(a['kind'],a)
        source_error=None
        try:self.source(p)
        except (OSError,ValueError) as e:source_error=str(e)
        stages=[]
        for key,label in STEPS:
            a=latest.get(key);reason='';state='not_started'
            if key=='source':state='ready' if not source_error else 'needs_input';reason=source_error or 'Local source files found; sync is not yet approved'
            elif a:
                reason=self.health(p,a) or '';state='stale' if reason else 'approved' if self.approved(p,a) else 'review'
            else:
                reason={'pass1':'Short key previews available. Full Pass 1 review uses an explicit local report.',
                        'clean':'Approve Pass 1, then run a silence-only candidate or import exact saved timing.',
                        'visuals':'Requires an approved clean cut. Automated visual assembly is not connected yet.',
                        'assembly':'New workflow assembly is not connected. Existing runs remain below.',
                        'xml':'Clean-cut XML candidates are available in review bundles; final visual export is pending.',
                        'package':'Local preparation only. No publishing or Linklr write connection.'}[key]
            stages.append(dict(key=key,label=label,state=state,reason=reason,artifact=a))
        previews=[j for j in self.jobs.all(pid) if j.get('workflow_mode')=='review' and j['status'].get('state')=='complete']
        return dict(project_id=pid,project_revision=p['revision'],stages=stages,review_jobs=previews,
                    approved_milestones=sum(s['state']=='approved' for s in stages),
                    progress_kind='milestones, not rendering percent',active_job=self.jobs.active(pid),
                    history=[dict(id=a['id'],kind=a['kind'],created_at=a['created_at']) for a in artifacts],
                    capabilities=dict(automatic_pass1=False,source_key_preview=True,semantic_model=False,
                                      staged_clean_runner=True,canonical_word_remapping=True,manual_map_import=True,
                                      explicit_semantic_removals=True,audio_crossfades=False,review_audio_fades=True,visual_assembly=False,
                                      assistant_bridge=False,publishing=False))

    def register_pass1(self,pid,revision,report):
        p=self._current(pid,revision)
        if set(report)!={'review_media','transcript','sync_report','note'}:raise ValueError('Pass 1 report needs review_media, transcript, sync_report and note')
        if not isinstance(report['note'],str) or not report['note'].strip():raise ValueError('Describe the reviewed Pass 1')
        files={k:file_identity(report[k]) for k in ('review_media','transcript','sync_report')}
        from dajoshi.media import probe
        source=probe(self.source(p)['files']['video']['path']);review=probe(files['review_media']['path'])
        if not review.get('fps') or rate(review['fps'])!=rate(source['fps']) or abs(review['duration']-source['duration'])>1/float(rate(source['fps'])):
            raise ValueError('Pass 1 review must cover the uncut source duration, not a short key sample')
        sync=json.loads(Path(report['sync_report']).read_text(encoding='utf-8-sig'))
        if sync.get('verified') is not True or not math.isfinite(float(sync.get('offset_seconds',float('nan')))):
            raise ValueError('Supplied sync report must contain verified alignment and a finite offset_seconds')
        segments=json.loads(Path(report['transcript']).read_text(encoding='utf-8-sig'))
        words=[w for s in (segments.get('segments',[]) if isinstance(segments,dict) else segments) for w in s.get('words',[])]
        if not words:raise ValueError('Pass 1 needs a word-timestamp transcript')
        audio=probe(self.source(p)['files'].get('audio',self.source(p)['files']['video'])['path'])
        previous=-1
        for w in words:
            if not 0<=float(w['start'])<float(w['end'])<=audio['duration'] or float(w['start'])<previous or not str(w.get('word','')).strip():raise ValueError('Invalid word timestamps')
            previous=float(w['start'])
        return self._put(p,'pass1',dict(files=files,note=report['note'],sync=sync,word_count=len(words),source_header=source,review_header=review,audio_header=audio,
            validation='Source duration/header and supplied word/sync report checked; visual and listening review remain manual'))

    def import_cut(self,pid,revision,job_id):
        p=self._current(pid,revision)
        parent=next((a for a in self.artifacts(pid) if a['kind']=='pass1'),None)
        if not parent or not self.approved(p,parent):raise ValueError('Approve the current Pass 1 revision before clean-cut review')
        j=self.store.job(job_id)
        if not j or j['project_id']!=pid:raise ValueError('Choose an existing run belonging to this project')
        state=self.jobs.status(j)['status']['state']
        if state in ('starting','running'):raise ValueError('Wait for the source run to stop before importing')
        folder=Path(j['path']);mapping=json.loads((folder/'timing_map.json').read_text(encoding='utf-8-sig'))
        if j.get('workflow_mode'):
            if state!='complete' or j['workflow_mode']!='silence':raise ValueError('Only a completed silence candidate can be imported')
            cfg=json.loads((folder/'request.json').read_text())
            from dajoshi.staged_cut import context
            context(cfg,self.root)
            hashes=json.loads((folder/'workflow_outputs.json').read_text())
            for name,sha in hashes.items():
                if Path(name).name!=name or hashlib.sha256((folder/name).read_bytes()).hexdigest()!=sha:raise Conflict('Staged output changed after completion')
        validate_map(mapping)
        source=self.source(p)['files']
        for k,mk in (('video','source_video'),('audio','source_audio')):
            wanted=source.get(k,source['video']);actual=file_identity(mapping[mk]['path'])
            if actual!=wanted:raise ValueError('Saved run does not match current '+k)
        # Require original run input fingerprints, not merely a matching pathname.
        inputs=json.loads((folder/'inputs.json').read_text(encoding='utf-8-sig'))
        for k in ('video','audio'):
            if k in inputs and any(inputs[k].get(f)!=source.get(k,source['video']).get(f) for f in ('path','size','mtime_ns','edge_sha256')):
                raise ValueError('Saved run input fingerprint is stale')
        if abs(float(parent['payload']['sync']['offset_seconds'])-float(mapping['sync']['offset_seconds']))>1/float(rate(mapping['fps'])):
            raise ValueError('Saved cut sync differs from approved Pass 1')
        expected=frame(parent['payload']['sync']['offset_seconds'],mapping['fps'])/float(rate(mapping['fps']))
        if abs(expected-float(mapping['audio_minus_video_seconds']))>1e-8:raise ValueError('Saved cut timeline alignment differs from approved Pass 1')
        if parent['payload'].get('source_header'):
            if rate(mapping['fps'])!=rate(parent['payload']['source_header']['fps']):raise ValueError('Saved cut cadence differs from approved Pass 1')
            if mapping['source_video']!=parent['payload']['source_header'] or mapping['source_audio']!=parent['payload']['audio_header']:raise ValueError('Saved cut media headers differ from approved Pass 1')
        segments=json.loads((folder/'transcript.json').read_text(encoding='utf-8-sig'))
        words=[w for s in (segments.get('segments',[]) if isinstance(segments,dict) else segments) for w in s.get('words',[])]
        return self._cut(p,mapping,words,[],parent['id'],dict(job_id=job_id,source_run=str(folder),timing_replanned=False),[])

    def launch(self,pid,revision,mode,aid=None,expected_digest=None,fade_ms=0):
        p=self._current(pid,revision)
        if type(fade_ms) not in (int,float) or not math.isfinite(fade_ms) or not 0<=fade_ms<=20:raise ValueError('Review fade must be 0-20 ms')
        if mode=='silence':
            a=next((x for x in self.artifacts(pid) if x['kind']=='pass1'),None)
            if not a or not self.approved(p,a):raise ValueError('Approve the current Pass 1 before starting the cutter')
            if any(x['kind']=='clean' and self.approved(p,x) for x in self.artifacts(pid)):
                raise Conflict('An approved clean cut already owns timing. Render that map; do not rerun automatic cuts.')
        elif mode=='review':
            a=self.get(pid,aid)
            if a['kind']!='clean' or a['digest']!=expected_digest:raise Conflict('Choose the exact clean candidate to render')
            if next(x['id'] for x in self.artifacts(pid) if x['kind']=='clean')!=aid:raise Conflict('Render the latest candidate')
            error=self.health(p,a)
            if error:raise Conflict(error)
        else:raise ValueError('Unknown workflow mode')
        cfg=dict(workflow_mode=mode,review_fade_ms=fade_ms,workflow=dict(project_id=pid,project_revision=revision,artifact_id=a['id'],digest=a['digest'],data_root=str(self.store.root)))
        return self.jobs.start_staged(pid,revision,cfg)

    def import_map(self,pid,revision,mapping,identities,note):
        p=self._current(pid,revision)
        parent=next((a for a in self.artifacts(pid) if a['kind']=='pass1'),None)
        if not parent or not self.approved(p,parent):raise ValueError('Approve Pass 1 before importing manual timing')
        if not isinstance(note,str) or not note.strip():raise ValueError('Describe the source of the manually edited timing map')
        source=self.source(p)['files']
        if identities!=source:raise Conflict('Manual map source fingerprints differ from this project')
        validate_map(mapping)
        from dajoshi.media import probe
        for key,mk in [('video','source_video'),('audio','source_audio')]:
            expected=source.get(key,source['video'])
            if file_identity(mapping[mk]['path'])!=expected or probe(expected['path'])!=mapping[mk]:raise ValueError('Manual map source headers differ')
        if rate(mapping['fps'])!=rate(mapping['source_video']['fps']):raise ValueError('Manual map cadence differs')
        expected=frame(parent['payload']['sync']['offset_seconds'],mapping['fps'])/float(rate(mapping['fps']))
        if abs(expected-float(mapping['audio_minus_video_seconds']))>1e-8:raise ValueError('Manual map alignment differs from approved Pass 1')
        if mapping.get('keying'):raise ValueError('Manual maps use the approved Pass 1 composite; key layers need separate validation')
        mapping=copy.deepcopy(mapping)
        mapping['presentation_video']=probe(parent['payload']['files']['review_media']['path'])
        segments=json.loads(Path(parent['payload']['files']['transcript']['path']).read_text(encoding='utf-8-sig'))
        words=[w for s in (segments.get('segments',[]) if isinstance(segments,dict) else segments) for w in s.get('words',[])]
        return self._cut(p,mapping,words,[],parent['id'],dict(manual_timing=True,note=note,timing_replanned=False,imported_map_digest=digest(mapping)),[])

    def _cut(self,p,mapping,source_words,decisions,parent,provenance,prior_decisions):
        result,rejected=reject_ranges(mapping,decisions);words,issues=remap_words(result,source_words)
        # A fresh immutable directory; never write into the source run.
        folder=self.store.root/'workflow'/p['id']/str(uuid.uuid4());folder.mkdir(parents=True)
        values={'timing_map.json':result,'source_words.json':source_words,'words.json':words,
                'review.json':dict(rejected_ranges=prior_decisions+rejected,issues=issues,
                                   semantic_sweep='manual review required',listening='not verified',
                                   audio_joins='hard joins; crossfades not implemented')}
        values['semantic_decisions.json']=dict(coordinate_space='source_video_seconds',base_map_digest=digest(mapping),result_map_digest=digest(result),rejected_ranges=prior_decisions+rejected,
            actual_omission_verified=all(all(s['source_out_frame']<=d['source_in_frame'] or s['source_in_frame']>=d['source_out_frame'] for s in result['spans']) for d in prior_decisions+rejected))
        for name,value in values.items():(folder/name).write_text(json.dumps(value,indent=2),encoding='utf-8')
        xml=export_xml(folder,result,result['source_video'],result['source_audio'],[],[])
        hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in folder.iterdir() if f.is_file()}
        return self._put(p,'clean',dict(folder=str(folder),hashes=hashes,map_digest=digest(result),
            duration_frames=result['duration_frames'],fps=result['fps'],span_count=len(result['spans']),
            words=words,issues=issues,removals=prior_decisions+rejected,provenance=provenance,
            review_status='New XML candidate; listening/native review required',audio_crossfades=False),parent)

    def revise_cut(self,pid,revision,aid,expected_digest,decisions):
        p=self._current(pid,revision);a=self.get(pid,aid)
        if a['kind']!='clean' or a['digest']!=expected_digest:raise Conflict('Review the exact clean-cut revision before changing it')
        error=self.health(p,a)
        if error:raise Conflict(error)
        if not isinstance(decisions,list) or not decisions or len(decisions)>500:raise ValueError('Supply 1-500 explicit rejected ranges')
        folder=Path(a['payload']['folder']);mapping=json.loads((folder/'timing_map.json').read_text())
        source_words=json.loads((folder/'source_words.json').read_text())
        return self._cut(p,mapping,source_words,decisions,a['parent'],dict(previous_clean_cut=aid),a['payload']['removals'])

    def approve(self,pid,revision,aid,expected_digest,checks,note):
        p=self._current(pid,revision);a=self.get(pid,aid)
        if a['digest']!=expected_digest:raise Conflict('Artifact changed; review its exact current snapshot')
        latest=next(x for x in self.artifacts(pid) if x['kind']==a['kind'])
        if latest['id']!=aid:raise Conflict('Only the latest candidate can be approved')
        error=self.health(p,a)
        if error:raise Conflict(error)
        required={'pass1':['sync','appearance','words'], 'clean':['semantic_sweep','all_joins_listened','transcript_checked','native_playback']}[a['kind']]
        if any(checks.get(k) is not True for k in required):raise ValueError('Complete every review check before approval')
        if a['payload'].get('issues'):raise ValueError('Resolve transcript boundary issues before approval; retranscription import is not connected yet')
        if not isinstance(note,str) or not note.strip():raise ValueError('Record a short review note')
        approval=dict(artifact_id=aid,digest=a['digest'],approved_at=now(),note=note,checks=checks,
                      evidence='Explicit local user attestation; not automated listening/native verification')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT revision FROM projects WHERE id=?',(pid,)).fetchone()['revision']!=revision:raise Conflict('Project changed before approval')
            latest_id=c.execute('SELECT id FROM workflow_artifacts WHERE project_id=? AND kind=? ORDER BY rowid DESC LIMIT 1',(pid,a['kind'])).fetchone()['id']
            if latest_id!=aid:raise Conflict('A newer candidate was created; review it first')
            c.execute('INSERT OR IGNORE INTO workflow_approvals VALUES(?,?)',(aid,json.dumps(approval)))
        return approval

    def handoff(self,pid):
        p=self.store.get(pid);status=self.status(pid)
        clean=next((s['artifact'] for s in status['stages'] if s['key']=='clean'),None)
        return dict(project_id=pid,project_revision=p['revision'],name=p['name'],script=p['script'],
                    selected_title=next((t['text'] for t in p['titles'] if t['id']==p['chosen_title']),None),
                    clean_cut=clean,source_identities=self.source(p) if p['media'].get('video') else None,stage_status=[{k:s[k] for k in ('key','state','reason')} for s in status['stages']],
                    instructions='Paste into the existing dot chat. No message has been sent. Return explicit source_video_seconds rejection ranges with reasons; preserve deliberate repetition. Never overwrite approved snapshots.')
