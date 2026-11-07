"""Local script versions and an explicit, idempotent main-chat handoff."""
import hashlib,json,uuid
from pathlib import Path
from .store import Conflict,now


def schema(store):
    with store.connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS script_versions(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,project_revision INTEGER NOT NULL,body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS script_imports(id TEXT PRIMARY KEY,digest TEXT NOT NULL,body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS script_proposals(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS writing_contexts(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,body TEXT NOT NULL);
        PRAGMA user_version=2;
        ''')
        for row in c.execute('SELECT body FROM projects').fetchall():
            p=json.loads(row['body'])
            if not c.execute('SELECT 1 FROM script_versions WHERE project_id=?',(p['id'],)).fetchone():
                record(store,c,p,'Existing script','migration')


def record(store,c,project,label,source):
    vid=str(uuid.uuid4());content=project['script'];sha=hashlib.sha256(content.encode('utf-8')).hexdigest()
    project_folder=store.folders.path(project['id'],c) if store.folders else None
    if project_folder:
        from .project_folders import plain
        folder=plain(project_folder/'script'/'versions')
        if not folder.is_relative_to(project_folder):raise ValueError('Script folder is outside the project.')
    else:folder=store.root/'scripts'/project['id']/'versions'
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/(vid+'.txt')
    with path.open('x',encoding='utf-8') as f:f.write(content)
    version=dict(id=vid,project_id=project['id'],project_revision=project['revision'],created_at=now(),label=label,
                 source=source,content=content,sha256=sha,path=str(path),approval='draft',approved_at=None,approval_note='')
    c.execute('INSERT INTO script_versions VALUES(?,?,?,?)',(vid,project['id'],project['revision'],json.dumps(version)))
    return version


class Writing:
    def __init__(self,store):self.store=store
    def context(self,root,pid,revision,start,end,job,pass_id=None,scope='selection'):
        from .writing_context import Context
        context=Context(root).build(self.store.get(pid),revision,start,end,job,pass_id,scope)
        context['context_id']=str(uuid.uuid4())
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT revision FROM projects WHERE id=?',(pid,)).fetchone()['revision']!=revision:raise Conflict('Project changed while preparing context')
            c.execute('INSERT INTO writing_contexts VALUES(?,?,?)',(context['context_id'],pid,json.dumps(context)))
        return context
    def version(self,pid,vid):
        with self.store.connect() as c:row=c.execute('SELECT body FROM script_versions WHERE id=? AND project_id=?',(vid,pid)).fetchone()
        if not row:raise KeyError('Script version not found in this project')
        return json.loads(row['body'])
    def workspace(self,pid):
        project=self.store.get(pid)
        with self.store.connect() as c:rows=c.execute('SELECT body FROM script_versions WHERE project_id=? ORDER BY project_revision DESC,rowid DESC',(pid,)).fetchall()
        versions=[json.loads(r['body']) for r in rows]
        with self.store.connect() as c:proposals=[json.loads(r['body']) for r in c.execute('SELECT body FROM script_proposals WHERE project_id=? ORDER BY rowid DESC',(pid,)).fetchall()]
        current=versions[0] if versions and versions[0]['content']==project['script'] else None
        return dict(project_id=pid,project_revision=project['revision'],current_version=current,
                    unversioned_script=current is None and bool(project['script']),
                    proposals=proposals,
                    versions=[{k:v for k,v in r.items() if k!='content'} for r in versions],
                    ai=dict(configured=False,status='Main-chat handoff; no live AI connection'),
                    guidance=dict(status='pending',message='Writing guidance awaits the reviewed skill specification.'))
    def import_script(self,request):
        allowed={'import_id','project_id','expected_revision','content','label','source'}
        if set(request)-allowed:raise ValueError('Unknown import field. Imports are drafts; approval is a separate explicit action.')
        for key in ('import_id','project_id'):uuid.UUID(request[key])
        if not isinstance(request['content'],str) or len(request['content'])>200000:raise ValueError('Script must be text, up to 200,000 characters')
        if not isinstance(request['expected_revision'],int):raise ValueError('Expected project revision is required')
        label=request.get('label','Imported from main chat');source=request.get('source','main-chat')
        if not isinstance(label,str) or len(label)>240 or not isinstance(source,str) or len(source)>240:raise ValueError('Invalid import label/source')
        digest=hashlib.sha256(json.dumps(request,sort_keys=True).encode('utf-8')).hexdigest()
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            prior=c.execute('SELECT digest,body FROM script_imports WHERE id=?',(request['import_id'],)).fetchone()
            if prior:
                if prior['digest']!=digest:raise Conflict('Import ID was already used with different content or destination')
                receipt=json.loads(prior['body']);receipt['duplicate']=True;return receipt
            row=c.execute('SELECT revision,body FROM projects WHERE id=?',(request['project_id'],)).fetchone()
            if not row:raise KeyError('Target project not found; no project is selected by title or folder guess')
            if row['revision']!=request['expected_revision']:raise Conflict('Project changed since handoff. Reload its revision and review before importing.')
            project=json.loads(row['body']);project['script']=request['content'];project['revision']+=1;project['updated_at']=now();self.store.validate(project)
            if not c.execute('SELECT 1 FROM script_versions WHERE project_id=?',(project['id'],)).fetchone():record(self.store,c,json.loads(row['body']),'Before first import','migration')
            version=record(self.store,c,project,label,source)
            c.execute('UPDATE projects SET revision=?,body=? WHERE id=?',(project['revision'],json.dumps(project),project['id']))
            receipt=dict(import_id=request['import_id'],project_id=project['id'],project_revision=project['revision'],version_id=version['id'],sha256=version['sha256'],path=version['path'],approval='draft',duplicate=False)
            c.execute('INSERT INTO script_imports VALUES(?,?,?)',(request['import_id'],digest,json.dumps(receipt)))
            return receipt
    def approve(self,pid,vid,revision,note):
        if not isinstance(note,str) or not note.strip() or len(note)>1000:raise ValueError('Record the explicit approval in a short note')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT revision,body FROM projects WHERE id=?',(pid,)).fetchone()
            if not row:raise KeyError('Project not found')
            if row['revision']!=revision:raise Conflict('Project changed before approval; review its current script')
            vr=c.execute('SELECT body FROM script_versions WHERE id=? AND project_id=?',(vid,pid)).fetchone()
            latest=c.execute('SELECT id FROM script_versions WHERE project_id=? ORDER BY project_revision DESC,rowid DESC LIMIT 1',(pid,)).fetchone()
            if not vr or not latest or latest['id']!=vid:raise Conflict('Only the current script version can be approved')
            project=json.loads(row['body']);version=json.loads(vr['body'])
            if not project['script'].strip():raise ValueError('A blank script cannot be approved')
            if project['script']!=version['content']:raise Conflict('Script changed; save and review the new version')
            version.update(approval='approved',approved_at=now(),approval_note=note.strip())
            project['revision']+=1;project['updated_at']=now()
            c.execute('UPDATE script_versions SET body=? WHERE id=?',(json.dumps(version),vid))
            c.execute('UPDATE projects SET revision=?,body=? WHERE id=?',(project['revision'],json.dumps(project),pid))
            return dict(project=project,version=version)
    def restore(self,pid,vid,revision):
        version=self.version(pid,vid)
        return self.import_script(dict(import_id=str(uuid.uuid4()),project_id=pid,expected_revision=revision,content=version['content'],label='Restored '+version['created_at'],source='version-restore'))
    def propose(self,request):
        required={'proposal_id','project_id','expected_revision','document_sha256','start','end','original','replacement'}
        if not required<=set(request) or set(request)-required-{'reason','pass_id','context_id','writable_scope'}:raise ValueError('Invalid scoped proposal fields')
        uuid.UUID(request['proposal_id']);uuid.UUID(request['project_id'])
        p=self.store.get(request['project_id']);text=p['script'];a,b=request['start'],request['end']
        if p['revision']!=request['expected_revision'] or hashlib.sha256(text.encode()).hexdigest()!=request['document_sha256']:raise Conflict('Proposal was written for an older document revision')
        if not isinstance(a,int) or not isinstance(b,int) or not 0<=a<=b<=len(text) or text[a:b]!=request['original']:raise Conflict('Proposal does not match its exact selected text')
        if not isinstance(request['replacement'],str) or len(text)-b+a+len(request['replacement'])>200000:raise ValueError('Replacement is too long')
        if any(k in request for k in ('pass_id','context_id','writable_scope')):
            if not all(k in request for k in ('pass_id','context_id','writable_scope')):raise ValueError('Pass proposals require context_id, pass_id and declared writable_scope')
            with self.store.connect() as c:row=c.execute('SELECT body FROM writing_contexts WHERE id=? AND project_id=?',(request['context_id'],p['id'])).fetchone()
            if not row:raise ValueError('Export a current scoped context before submitting a pass proposal')
            context=json.loads(row['body']);selection=context['selection']
            if context['project_revision']!=p['revision'] or context['document_sha256']!=request['document_sha256']:raise Conflict('Writing context is stale; refresh the affected scope')
            if request['pass_id']!=context['requested_pass'] or request['writable_scope'] not in context['pass_contract']['writable_scope']:raise ValueError('Pass cannot write this declared scope; final flow is read-only')
            if not selection['start']<=a<=b<=selection['end']:raise ValueError('Proposal extends outside the selected context')
            if context['generation_blocked']:raise ValueError('Resolve the recorded preflight blockers before proposing this pass')
            if request['pass_id']=='lead_magnets' and context['lead_magnet']['decision']=='none':raise ValueError('Lead magnet decision is none; no insertion is allowed')
        proposal=dict(request,status='pending',created_at=now(),offset_unit='unicode_code_points',next_checks=['affected owner scope','final_flow'],readiness='not_assessed')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');current=c.execute('SELECT revision FROM projects WHERE id=?',(p['id'],)).fetchone()
            if current['revision']!=p['revision']:raise Conflict('Project changed while preparing the proposal')
            existing=c.execute('SELECT body FROM script_proposals WHERE id=?',(proposal['proposal_id'],)).fetchone()
            if existing:
                prior=json.loads(existing['body'])
                if any(prior[k]!=request[k] for k in request):raise Conflict('Proposal ID already used for different content')
                return prior
            c.execute('INSERT INTO script_proposals VALUES(?,?,?)',(proposal['proposal_id'],p['id'],json.dumps(proposal)))
        return proposal
    def resolve_proposal(self,pid,proposal_id,accept):
        if not isinstance(accept,bool):raise ValueError('Accept must be true or false')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT body FROM script_proposals WHERE id=? AND project_id=?',(proposal_id,pid)).fetchone()
            if not row:raise KeyError('Proposal not found')
            proposal=json.loads(row['body'])
            if proposal['status']!='pending':return proposal
            if accept:
                row=c.execute('SELECT revision,body FROM projects WHERE id=?',(pid,)).fetchone();p=json.loads(row['body']);text=p['script']
                if p['revision']!=proposal['expected_revision'] or hashlib.sha256(text.encode()).hexdigest()!=proposal['document_sha256']:raise Conflict('Script changed after this proposal. Existing edits are preserved; request a fresh proposal.')
                a,b=proposal['start'],proposal['end']
                if text[a:b]!=proposal['original']:raise Conflict('Selected text no longer matches')
                p['script']=text[:a]+proposal['replacement']+text[b:];p['revision']+=1;p['updated_at']=now();self.store.validate(p)
                version=record(self.store,c,p,'Accepted scoped proposal','main-chat-proposal')
                c.execute('UPDATE projects SET revision=?,body=? WHERE id=?',(p['revision'],json.dumps(p),pid))
                proposal.update(status='accepted',version_id=version['id'],result_revision=p['revision'])
            else:proposal['status']='rejected'
            proposal['resolved_at']=now();c.execute('UPDATE script_proposals SET body=? WHERE id=?',(json.dumps(proposal),proposal_id))
        return proposal
