"""Email planning. When Kit is connected, Ready emails are scheduled in Kit automatically (see kit.py)."""
import hashlib,json,threading,uuid
from datetime import date
from pathlib import Path
from .store import Conflict,now
from .kit import Kit,KitError,content_hash

FIELDS={'subject','preview','body','planned_date','audience','status','tags','source_ids','notes','archived','description','type','send_time','kit','results'}
# How a sent email did, read from Kit by Laia: recipients, opens, clicks, unsubscribes and the rates, plus when it was checked.
RESULT_KEYS={'recipients','opens','open_rate','clicks','click_rate','unsubscribes','new_people','kit_broadcast_id','checked_at'}
# Draft: being written. Ready: Josh has agreed it and it can go to Kit.
# Scheduled: it is in Kit. Published: Josh has checked it in Kit and scheduled it. Sent: it has gone out.
STATUSES=['Draft','Ready','Scheduled','Published','Sent']
KIT_STATUS={'draft':'Scheduled','scheduled':'Published','sending':'Published','completed':'Sent','aborted':'Scheduled'}

def normalize(p):
    if p.get('status') in ('Ready for review','Processing'):p['status']={'Ready for review':'Ready','Processing':'Published'}[p['status']]
    p.setdefault('description','');p.setdefault('type','Newsletter');p.setdefault('send_time','');p.setdefault('kit',None);p.setdefault('results',{})
    return p

class Emails:
    def __init__(self,store,root):
        self.store,self.root=store,Path(root)
        self._references=None
        self.kit=Kit(store.root);self.kit_lock=threading.Lock()
        with store.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS email_drafts(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS email_versions(email_id TEXT NOT NULL,revision INTEGER NOT NULL,body TEXT NOT NULL,PRIMARY KEY(email_id,revision));
            CREATE TABLE IF NOT EXISTS email_imports(import_key TEXT PRIMARY KEY,email_id TEXT NOT NULL,payload_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS email_schema(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL);''')
            c.execute('INSERT OR IGNORE INTO email_schema VALUES(1,?)',(now(),))
            if not c.execute('SELECT 1 FROM email_schema WHERE version=2').fetchone():
                # Five statuses: move every saved email onto the new meanings once.
                old={'Scheduled':'Published','Published':'Sent','Processing':'Published'}
                for r in c.execute('SELECT id,revision,body FROM email_drafts').fetchall():
                    p=json.loads(r['body']);k=p.get('kit') or {}
                    s=KIT_STATUS.get(k.get('status')) if k.get('status') else old.get(p.get('status'),p.get('status'))
                    if s and s!=p.get('status'):
                        p['status']=s;p['revision']=r['revision']+1;p['updated_at']=now();blob=json.dumps(p,ensure_ascii=False)
                        c.execute('UPDATE email_drafts SET revision=?,body=? WHERE id=?',(p['revision'],blob,r['id']))
                        c.execute('INSERT OR IGNORE INTO email_versions VALUES(?,?,?)',(r['id'],p['revision'],blob))
                c.execute('INSERT INTO email_schema VALUES(2,?)',(now(),))
            if not c.execute('SELECT 1 FROM email_schema WHERE version=3').fetchone():
                # Emails already in Kit hold the same words as here, so Kit's own editable copy is kept.
                for r in c.execute('SELECT id,body FROM email_drafts').fetchall():
                    p=json.loads(r['body'])
                    if p.get('kit') and not p['kit'].get('pushed_hash'):
                        p['kit']['pushed_hash']=content_hash(p);c.execute('UPDATE email_drafts SET body=? WHERE id=?',(json.dumps(p,ensure_ascii=False),r['id']))
                c.execute('INSERT INTO email_schema VALUES(3,?)',(now(),))

    def all(self):
        with self.store.connect() as c:return [normalize(json.loads(r['body'])) for r in c.execute('SELECT body FROM email_drafts ORDER BY rowid')]

    def get(self,eid):
        with self.store.connect() as c:r=c.execute('SELECT body FROM email_drafts WHERE id=?',(eid,)).fetchone()
        if not r:raise KeyError('Email not found')
        return normalize(json.loads(r['body']))

    def history(self,eid):
        self.get(eid)
        with self.store.connect() as c:return [json.loads(r['body']) for r in c.execute('SELECT body FROM email_versions WHERE email_id=? ORDER BY revision DESC',(eid,))]

    def validate(self,p):
        for key,limit in [('subject',500),('preview',1000),('body',200000),('audience',2000),('notes',20000)]:
            if not isinstance(p[key],str) or len(p[key])>limit:raise ValueError(key+' must be text within '+str(limit)+' characters')
        if not p['subject'].strip():raise ValueError('Give this email a subject or working name')
        if not isinstance(p['planned_date'],str):raise ValueError('Invalid planned date')
        if p['planned_date']:
            if len(p['planned_date'])!=10:raise ValueError('Use a YYYY-MM-DD local planning date')
            date.fromisoformat(p['planned_date'])
        normalize(p)
        if p['status'] not in STATUSES:raise ValueError('Choose a status: '+', '.join(STATUSES))
        r=p.get('results') or {}
        if not isinstance(r,dict) or set(r)-RESULT_KEYS or any(not isinstance(v,(int,float,str)) or isinstance(v,bool) for v in r.values()):raise ValueError('Results must be numbers: '+', '.join(sorted(RESULT_KEYS)))
        p['results']=r
        for key,limit in [('description',1000),('type',80)]:
            if not isinstance(p[key],str) or len(p[key])>limit:raise ValueError(key+' must be text within '+str(limit)+' characters')
        p['type']=p['type'].strip() or 'Newsletter'
        if not isinstance(p['send_time'],str) or (p['send_time'] and not (len(p['send_time'])==5 and p['send_time'][2]==':' and p['send_time'].replace(':','').isdigit() and int(p['send_time'][:2])<24 and int(p['send_time'][3:])<60)):raise ValueError('Use a HH:MM send time')
        k=p['kit']
        if k is not None and (not isinstance(k,dict) or not isinstance(k.get('id'),int) or not str(k.get('url','')).startswith('https://app.kit.com/')):raise ValueError('Invalid Kit link')
        if type(p['archived']) is not bool:raise ValueError('Invalid archive value')
        for key in ['tags','source_ids']:
            if not isinstance(p[key],list) or len(p[key])>30 or any(not isinstance(v,str) or not v.strip() or len(v)>120 for v in p[key]):raise ValueError('Invalid '+key)
            p[key]=list(dict.fromkeys(v.strip() for v in p[key]))
        if any(x not in self.references() for x in p['source_ids']):raise ValueError('Email inspiration source is unavailable')
        if p['status']!='Draft' and not p['body'].strip():raise ValueError('Add the email body before moving it past Draft')

    def new(self,changes,eid=None,provenance=None,reported_schedule=None):
        if not isinstance(changes,dict) or set(changes)-FIELDS:raise ValueError('Unknown email field')
        eid=eid or str(uuid.uuid4());uuid.UUID(eid)
        p=dict(id=eid,revision=1,subject='Untitled email',preview='',body='',planned_date='',audience='',status='Draft',tags=[],source_ids=[],notes='',archived=False,description='',type='Newsletter',send_time='',kit=None,approval_status='Pending final approval',remote_status='Not connected',created_at=now(),updated_at=now(),provenance=provenance or {},reported_schedule=reported_schedule)
        p.update(changes)
        if reported_schedule:p['remote_status']='User-reported scheduled - unverified'
        self.validate(p);return p

    def _insert(self,c,p):
        blob=json.dumps(p,ensure_ascii=False)
        c.execute('INSERT INTO email_drafts VALUES(?,?,?)',(p['id'],p['revision'],blob))
        c.execute('INSERT INTO email_versions VALUES(?,?,?)',(p['id'],p['revision'],blob))

    def create(self,data):
        if set(data)-{'id','changes'}:raise ValueError('Unknown create option')
        p=self.new(data.get('changes',{}),data.get('id'))
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT body FROM email_drafts WHERE id=?',(p['id'],)).fetchone()
            if old:return json.loads(old['body'])
            self._insert(c,p)
        return p

    def save(self,eid,changes,revision,*,sync_remote=True):
        if not isinstance(changes,dict) or set(changes)-FIELDS:raise ValueError('Unknown email field')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT revision,body FROM email_drafts WHERE id=?',(eid,)).fetchone()
            if not row:raise KeyError('Email not found')
            if row['revision']!=revision:raise Conflict('This email changed in another window. Reload before saving; your text is still here.')
            p=normalize(json.loads(row['body']));p.update(changes);self.validate(p)
            p.update(revision=revision+1,updated_at=now(),approval_status='Pending final approval')
            blob=json.dumps(p,ensure_ascii=False)
            c.execute('UPDATE email_drafts SET revision=?,body=? WHERE id=?',(p['revision'],blob,eid))
            c.execute('INSERT INTO email_versions VALUES(?,?,?)',(eid,p['revision'],blob))
        return self.kit_step(p) if sync_remote else p

    def _apply(self,eid,fn):
        """Write a change made by the app itself (Kit results), on top of whatever is saved now."""
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT revision,body FROM email_drafts WHERE id=?',(eid,)).fetchone()
            cur=normalize(json.loads(row['body']))
            if fn(cur) is False:return cur
            self.validate(cur);cur.update(revision=row['revision']+1,updated_at=now())
            blob=json.dumps(cur,ensure_ascii=False)
            c.execute('UPDATE email_drafts SET revision=?,body=? WHERE id=?',(cur['revision'],blob,eid))
            c.execute('INSERT INTO email_versions VALUES(?,?,?)',(eid,cur['revision'],blob))
        return cur

    def kit_step(self,p):
        """Bring Kit in line with this email's status. Ready goes to Kit and is scheduled for its date.
        Edits to an email already in Kit are sent over. Moving one back to Draft unschedules it in Kit."""
        if not self.kit.connected or p.get('archived'):return p
        k=p.get('kit') or {};ks=k.get('status')
        if ks in ('completed','sending'):return p
        st=p['status'];action=None
        if st=='Ready':action='schedule'
        elif st in ('Scheduled','Published') and k:
            if ks!='scheduled' or content_hash(p)!=k.get('pushed_hash') or (p['planned_date'],p['send_time'] or '17:00')!=(k.get('local_date'),k.get('local_time')):action='schedule'
        elif st=='Draft' and ks=='scheduled':action='unschedule'
        if not action:
            if p.get('kit_error'):return self._apply(p['id'],lambda cur:cur.pop('kit_error',None) and None)
            return p
        with self.kit_lock:
            try:kit=self.kit.push(p,schedule=action=='schedule')
            except (KitError,ValueError) as e:
                msg=str(e)
                def fail(cur):
                    if cur.get('kit_error')==msg:return False
                    cur['kit_error']=msg
                return self._apply(p['id'],fail)
        kit.update(local_date=p['planned_date'],local_time=p['send_time'] or '17:00')
        def ok(cur):
            cur['kit']=kit;cur.pop('kit_error',None)
            if action=='schedule':
                if cur['status']=='Ready':cur['status']='Scheduled'
                elif cur['status']=='Published' and content_hash(cur)!=(p.get('kit') or {}).get('pushed_hash'):cur['status']='Scheduled'
                if not cur['send_time']:cur['send_time']='17:00'
        return self._apply(p['id'],ok)

    def kit_cycle(self):
        """Refresh Kit's state for linked emails, then schedule anything waiting. Safe to run often."""
        if not self.kit.connected:return dict(connected=False)
        from datetime import datetime,timedelta
        changed=0;errors=[]
        for p in self.all():
            k=p.get('kit') or {}
            if p.get('archived') or not k.get('id') or p['status']=='Sent':continue
            try:b=self.kit.get(k['id'])
            except KitError as e:
                if e.code==404:
                    def gone(cur):cur['kit']=None;cur['kit_error']='This email was deleted in Kit';cur['status']='Ready' if cur['status']!='Draft' else 'Draft'
                    p=self._apply(p['id'],gone);changed+=1;continue
                errors.append(str(e));break
            bs=b.get('status') or ''
            def mirror(cur,bs=bs,b=b):
                kk=dict(cur.get('kit') or {});before=json.dumps([cur['status'],kk.get('status'),kk.get('send_at'),kk.get('local_date'),kk.get('local_time')])
                kk.update(status=bs,send_at=b.get('send_at'),published_at=b.get('published_at'),synced_at=now())
                if bs=='completed':cur['status']='Sent'
                elif bs=='sending' and cur['status']!='Sent':cur['status']='Published'
                elif bs=='draft' and kk.get('scheduled_by_app') and cur['status'] in ('Scheduled','Published') and (cur.get('kit') or {}).get('status')=='scheduled':
                    cur['status']='Draft';cur['kit_error']='Unscheduled in Kit, so it is back to Draft here';kk['scheduled_by_app']=False
                if bs=='scheduled' and b.get('send_at'):
                    t=datetime.fromisoformat(b['send_at'].replace('Z','+00:00'))+timedelta(hours=4)
                    cur['planned_date'],cur['send_time']=t.strftime('%Y-%m-%d'),t.strftime('%H:%M');kk.update(local_date=cur['planned_date'],local_time=cur['send_time'])
                cur['kit']=kk
                if json.dumps([cur['status'],kk.get('status'),kk.get('send_at'),kk.get('local_date'),kk.get('local_time')])==before:return False
            before=p['revision'];p=self._apply(p['id'],mirror)
            if p['revision']!=before:changed+=1
        if not errors:
            for p in self.all():
                if p.get('archived'):continue
                k=p.get('kit') or {}
                waiting=p['status']=='Ready' or (p['status'] in ('Scheduled','Published') and k.get('status')=='draft')
                if waiting and not p.get('kit_error'):
                    r=self.kit_step(p)
                    if r['revision']!=p['revision']:changed+=1
                    if r.get('kit_error'):errors.append(r['subject']+': '+r['kit_error'])
        self.kit.note(last_sync=now(),last_error=errors[0] if errors else '')
        return dict(connected=True,changed=changed,errors=errors)

    def kit_push(self,eid):
        """Try again now, for one email."""
        p=self._apply(eid,lambda cur:cur.pop('kit_error',None) and None)
        return self.kit_step(p)

    def import_records(self,data):
        """Atomic, idempotent, non-overwriting import of local drafts and reported dates."""
        if set(data)!={'records'} or not isinstance(data['records'],list) or not 1<=len(data['records'])<=100:raise ValueError('Provide 1-100 records')
        ready=[];seen=set()
        for r in data['records']:
            if not isinstance(r,dict) or set(r)-{'import_key','changes','provenance','reported_schedule'}:raise ValueError('Unknown import field')
            key=r.get('import_key','')
            if not isinstance(key,str) or not key.strip() or len(key)>240 or key in seen:raise ValueError('Each record needs a unique stable import_key')
            seen.add(key)
            prov=r.get('provenance',{})
            if not isinstance(prov,dict) or len(json.dumps(prov))>20000:raise ValueError('Invalid provenance')
            report=r.get('reported_schedule')
            if report is not None:
                if not isinstance(report,dict) or set(report)!={'date','source'} or not isinstance(report['source'],str) or not report['source'].strip() or len(report['source'])>2000:raise ValueError('Reported schedules require date and source only; verification cannot be imported')
                date.fromisoformat(report['date'])
            p=self.new(r.get('changes',{}),str(uuid.uuid5(uuid.NAMESPACE_URL,'dajoshi-email:'+key)),prov,report)
            digest=hashlib.sha256(json.dumps(r,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            ready.append((key,digest,p))
        result=[]
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for key,digest,p in ready:
                old=c.execute('SELECT * FROM email_imports WHERE import_key=?',(key,)).fetchone()
                if old:
                    if old['payload_hash']!=digest:raise Conflict('Import key already exists with different content; existing email preserved: '+key)
                    result.append(dict(id=old['email_id'],created=False));continue
                self._insert(c,p)
                c.execute('INSERT INTO email_imports VALUES(?,?,?)',(key,p['id'],digest))
                result.append(dict(id=p['id'],created=True))
        return dict(records=result)

    def sync_kit(self,data):
        """Mirror Kit broadcast state. Body is only filled when the local copy is empty."""
        from datetime import datetime,timedelta
        recs=data.get('broadcasts') if isinstance(data,dict) else None
        if not isinstance(recs,list) or not 1<=len(recs)<=200:raise ValueError('Provide 1-200 broadcasts')
        out=[]
        rows={p['id']:p for p in self.all()}
        for b in recs:
            kid=int(b['id']);local=None
            if b.get('send_at'):
                t=datetime.fromisoformat(b['send_at'].replace('Z','+00:00'))+timedelta(hours=4)
                day,clock=t.strftime('%Y-%m-%d'),t.strftime('%H:%M')
            else:day,clock='',''
            match=[p for p in rows.values() if (p.get('kit') or {}).get('id')==kid] or ([rows[b['match']]] if b.get('match') in rows else [])
            kit=dict(id=kid,url=b.get('url') or 'https://app.kit.com/campaigns/%d/draft'%kid,status=b.get('status',''),published_at=b.get('published_at'),send_at=b.get('send_at'),synced_at=now())
            changes=dict(subject=b.get('subject') or 'Untitled email',preview=b.get('preview_text') or '',description=b.get('description') or '',status=KIT_STATUS.get(b.get('status'),'Draft'),kit=kit)
            if day:changes.update(planned_date=day,send_time=clock)
            for key in ('type','audience','send_time'):
                if b.get(key):changes[key]=b[key]
            if match:
                p=match[0]
                if b.get('body') and not p['body'].strip():changes['body']=b['body']
                if b.get('links') and not p.get('links'):p['links']=b['links']
                if p['subject'].startswith('Existing email'):changes['tags']=[t for t in p['tags'] if t not in ('Existing schedule','User-reported')]
                with self.store.connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    row=c.execute('SELECT revision,body FROM email_drafts WHERE id=?',(p['id'],)).fetchone()
                    cur=normalize(json.loads(row['body']));cur.update(changes)
                    if b.get('links') and not cur.get('links'):cur['links']=b['links']
                    self.validate(cur);cur.update(revision=row['revision']+1,updated_at=now())
                    blob=json.dumps(cur,ensure_ascii=False)
                    c.execute('UPDATE email_drafts SET revision=?,body=? WHERE id=?',(cur['revision'],blob,cur['id']))
                    c.execute('INSERT INTO email_versions VALUES(?,?,?)',(cur['id'],cur['revision'],blob))
                out.append(dict(id=cur['id'],kit_id=kid,updated=True))
            else:
                if b.get('body'):changes['body']=b['body']
                p=self.new(changes,str(uuid.uuid5(uuid.NAMESPACE_URL,'kit-broadcast:%d'%kid)),dict(source='Kit broadcast %d'%kid))
                if b.get('links'):p['links']=b['links']
                with self.store.connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    if c.execute('SELECT 1 FROM email_drafts WHERE id=?',(p['id'],)).fetchone():continue
                    self._insert(c,p)
                rows[p['id']]=p;out.append(dict(id=p['id'],kit_id=kid,created=True))
        return dict(records=out)

    def references(self):
        if self._references is not None:return self._references
        refs={}
        topics={'Communication':['communicat','speak','presentat','meeting'],'Career':['career','promot','salary','job'],'Storytelling':['story','stories','narrative'],'Video':['youtube','video','filmmak'],'Writing':['writing','writer','newsletter'],'Business':['business','customer','client'],'Offer':['offer','discount','sale','buy'],'Mindset':['mindset','confidence','habit']}
        for author,folder,file,bodykey,idkey,urlkey in [('Jon Brosio','JonBrosio','corpus.json','body','id','gmail_url'),('Ed Lawrence','EdLawrence','newsletters.json','body_text','message_id','source_gmail_url')]:
            path=self.root/'References/EmailExamples'/folder/file
            if not path.is_file():continue
            for r in json.loads(path.read_text(encoding='utf-8-sig')):
                body=r.get(bodykey,'');body=body if isinstance(body,str) else json.dumps(body,ensure_ascii=False)
                rid=folder+':'+str(r[idkey]);text=(r.get('subject','')+' '+body).lower()
                refs[rid]=dict(id=rid,author=author,subject=r.get('subject','Untitled'),date=r.get('date',''),body=body,source_url=r.get(urlkey,''),tags=[tag for tag,words in topics.items() if any(word in text for word in words)][:5],tag_basis='Suggested from source text',source_file=str(path.relative_to(self.root)),source_id=r[idkey],body_sha256=hashlib.sha256(body.encode()).hexdigest())
        self._references=refs;return refs

    def library(self,query=''):
        usage={}
        for p in self.all():
            for ref in p['source_ids']:usage.setdefault(ref,[]).append(dict(id=p['id'],subject=p['subject'],archived=p['archived']))
        q=query.casefold().strip()
        return [dict({k:v for k,v in r.items() if k!='body'},excerpt=r['body'][:180],used_in=usage.get(r['id'],[])) for r in self.references().values() if not q or q in (' '.join([r['author'],r['subject'],r['body'],*r['tags']])).casefold()]

    def reference(self,rid):
        r=self.references().get(rid)
        if r is None:raise KeyError('Source email not found')
        return r
