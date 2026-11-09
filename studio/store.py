"""Persistent local project library. No media discovery or remote services."""
import base64,hashlib,io,json,re,sqlite3,uuid
from datetime import date,datetime,timezone
from pathlib import Path
from contextlib import contextmanager
from PIL import Image

STAGES=['Idea','Scripting','Ready to record','Recording','Recorded','Editing','Review','Scheduled','Published']
FIELDS=['name','stage','publish_date','notes','script','titles','chosen_title','thumbnails','top_three','media','archived','packaging','lead_magnet','keying','locks']
# Pieces of a video Josh has locked in on the video page, each with when it was locked.
LOCKS={'title','thumbnail','script','recording','edit','animations','publish'}
def now():return datetime.now(timezone.utc).isoformat()
class Conflict(ValueError):pass

class Store:
    def __init__(self,root):
        self.folders=None
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        (self.root/'assets').mkdir(exist_ok=True);self.db=self.root/'studio.sqlite'
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,filename TEXT NOT NULL,mime TEXT NOT NULL);
            PRAGMA user_version=1;''')
        from .writing import schema
        schema(self)
    @contextmanager
    def connect(self):
        c=sqlite3.connect(str(self.db),timeout=10);c.row_factory=sqlite3.Row
        try:
            c.execute('PRAGMA journal_mode=WAL')
            with c:yield c
        finally:c.close()
    def projects(self):
        with self.connect() as c:return [dict(json.loads(x['body']),packaging=json.loads(x['body']).get('packaging',{})) for x in c.execute('SELECT body FROM projects ORDER BY rowid DESC')]
    def get(self,pid):
        with self.connect() as c:r=c.execute('SELECT body FROM projects WHERE id=?',(pid,)).fetchone()
        if not r:raise KeyError('Project not found')
        p=json.loads(r['body']);p.setdefault('packaging',{});return p
    def create(self,name,pid=None):
        pid=pid or str(uuid.uuid4());uuid.UUID(pid)
        p=dict(id=pid,revision=1,name=name,stage='Idea',publish_date='',notes='',script='',titles=[],chosen_title='',thumbnails=[],top_three=[],media={},archived=False,packaging={},lead_magnet={'decision':'unknown'},created_at=now(),updated_at=now())
        self.validate(p)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            existing=c.execute('SELECT body FROM projects WHERE id=?',(pid,)).fetchone()
            if existing:return json.loads(existing['body'])
            if self.folders:self.folders.provision(c,p)
            c.execute('INSERT INTO projects VALUES(?,?,?)',(pid,1,json.dumps(p)))
        return p
    def validate(self,p):
        if 'keying' in p:
            from dajoshi.keying import settings
            p['keying']=settings(p['keying'])
        if not isinstance(p['name'],str) or not p['name'].strip() or len(p['name'])>240:raise ValueError('Give the project a name (1Ã¢â‚¬â€œ240 characters)')
        if p['stage'] not in STAGES:raise ValueError('Unknown stage')
        if p['publish_date']:date.fromisoformat(p['publish_date'])
        locks=p.get('locks') or {}
        if not isinstance(locks,dict) or set(locks)-LOCKS or any(not isinstance(v,str) or len(v)>40 for v in locks.values()):raise ValueError('Invalid locked-in pieces')
        if 'locks' in p:p['locks']=locks
        for field,limit in [('notes',50000),('script',200000)]:
            if not isinstance(p[field],str) or len(p[field])>limit:raise ValueError(field+' is too long')
        packaging=p.get('packaging',{})
        if not isinstance(packaging,dict) or set(packaging)-{'thumbnail_concept','thumbnail_reference','curiosity_question','concrete_payoff'}:raise ValueError('Invalid packaging brief')
        if any(not isinstance(v,str) or len(v)>5000 for v in packaging.values()):raise ValueError('Packaging fields must be text, up to 5,000 characters')
        magnet=p.get('lead_magnet',{})
        if not isinstance(magnet,dict) or set(magnet)-{'decision','name','contents','result','free_status','destination','evidence'}:raise ValueError('Invalid lead magnet brief')
        if magnet.get('decision','unknown') not in ('unknown','none','yes'):raise ValueError('Lead magnet decision must be unknown, none or yes')
        if any(not isinstance(v,str) or len(v)>5000 for v in magnet.values()):raise ValueError('Lead magnet fields must be text, up to 5,000 characters')
        if not isinstance(p['archived'],bool):raise ValueError('Invalid archive value')
        if not isinstance(p['titles'],list) or len(p['titles'])>200:raise ValueError('Too many title ideas')
        ids=[]
        for t in p['titles']:
            uuid.UUID(t['id']);ids.append(t['id'])
            if not isinstance(t['text'],str) or not t['text'].strip() or len(t['text'])>500:raise ValueError('Title ideas need text, up to 500 characters')
        if len(set(ids))!=len(ids):raise ValueError('Duplicate title identifier')
        if p['chosen_title'] and p['chosen_title'] not in ids:raise ValueError('Selected title is missing')
        tids=[]
        for t in p['thumbnails']:
            if not re.fullmatch('[a-f0-9]{64}',t['asset']):raise ValueError('Invalid thumbnail')
            self.asset(t['asset']);tids.append(t['asset'])
            if t.get('title_id') and t['title_id'] not in ids:raise ValueError('Thumbnail title is missing')
        if len(tids)!=len(set(tids)):raise ValueError('Duplicate thumbnail')
        top=p['top_three']
        if not isinstance(top,list) or len(top)>3 or len(top)!=len(set(top)) or any(x not in tids for x in top):raise ValueError('Choose up to three different imported thumbnails')
        if not isinstance(p['media'],dict):raise ValueError('Invalid editing inputs')
        for k,v in p['media'].items():
            if k not in ['video','audio','reference','shots','deck','transcript'] or not isinstance(v,str) or len(v)>2000:raise ValueError('Invalid editing input')
    def save(self,pid,changes,revision):
        if any(k not in FIELDS for k in changes):raise ValueError('Unknown project field')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT revision,body FROM projects WHERE id=?',(pid,)).fetchone()
            if not row:raise KeyError('Project not found')
            previous=json.loads(row['body'])
            if self.folders:changes=self.folders.rebase_save(c,pid,changes,revision,previous)
            elif row['revision']!=revision:raise Conflict('This project changed in another window. Reload before saving.')
            p=dict(previous);p.update(changes);self.validate(p);p['name']=p['name'].strip();p['revision']+=1;p['updated_at']=now()
            if self.folders:self.folders.track_dismissals(c,pid,previous,p)
            latest=c.execute('SELECT body FROM script_versions WHERE project_id=? ORDER BY project_revision DESC,rowid DESC LIMIT 1',(pid,)).fetchone()
            if p['script']!=previous['script'] or not latest or json.loads(latest['body'])['content']!=p['script']:
                from .writing import record
                if not c.execute('SELECT 1 FROM script_versions WHERE project_id=?',(pid,)).fetchone():record(self,c,previous,'Before first edit','migration')
                from .writing import refresh_draft
                if not refresh_draft(c,latest,p):record(self,c,p,'Saved in editor','editor')
            c.execute('UPDATE projects SET revision=?,body=? WHERE id=?',(p['revision'],json.dumps(p),pid))
        return p
    def import_image(self,name,data):
        if len(data)>20*1024*1024:raise ValueError('Thumbnail exceeds 20 MB')
        try:
            with Image.open(io.BytesIO(data)) as im:
                if im.format not in ['PNG','JPEG','WEBP'] or im.width*im.height>40000000:raise ValueError('Use a PNG, JPEG or WebP image under 40 megapixels')
                fmt=im.format;im.verify()
        except Exception as e:raise ValueError('Cannot read this thumbnail. Use PNG, JPEG or WebP.') from e
        aid=hashlib.sha256(data).hexdigest();ext={'PNG':'.png','JPEG':'.jpg','WEBP':'.webp'}[fmt];path=self.root/'assets'/(aid+ext)
        if not path.exists():
            partial=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
            try:
                partial.write_bytes(data);partial.replace(path)
            finally:
                partial.unlink(missing_ok=True)
        with self.connect() as c:c.execute('INSERT OR IGNORE INTO assets VALUES(?,?,?)',(aid,path.name,Image.MIME[fmt]))
        return dict(asset=aid,name=Path(name).name,title_id='')
    def asset(self,aid):
        with self.connect() as c:r=c.execute('SELECT filename,mime FROM assets WHERE id=?',(aid,)).fetchone()
        if not r:raise ValueError('Thumbnail file not found')
        p=(self.root/'assets'/r['filename']).resolve()
        if p.parent!=self.root/'assets' or not p.is_file():raise ValueError('Thumbnail file unavailable')
        return p,r['mime']
    def put_job(self,job):
        with self.connect() as c:c.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?)',(job['id'],job['project_id'],json.dumps(job)))
    def jobs(self,pid=None):
        with self.connect() as c:
            rows=c.execute('SELECT body FROM jobs'+(' WHERE project_id=?' if pid else '')+' ORDER BY rowid DESC',(pid,) if pid else ()).fetchall()
        return [json.loads(r['body']) for r in rows]
    def job(self,jid):
        return next((j for j in self.jobs() if j['id']==jid),None)
