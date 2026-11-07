"""Kit (ConvertKit) v4 API client for scheduling broadcasts from the email planner.

The API key lives in Data/kit.json on this computer and is never sent to the browser.
Kit's API accepts one audience group per broadcast, so emails go to one Kit segment
(for example Newsletter, minus cold subscribers and customers)."""
import hashlib,html,json,os,re,threading,urllib.error,urllib.request
from datetime import datetime,timedelta,timezone
from pathlib import Path

API=os.environ.get('DJ_KIT_API','https://api.kit.com/v4')
DUBAI=timezone(timedelta(hours=4))
DEFAULT_TEMPLATE=3719972  # "Text only", a Classic template

def content_hash(p):
    raw=json.dumps([p.get('subject',''),p.get('preview',''),p.get('body',''),[(l.get('label'),l.get('href')) for l in p.get('links') or []]],ensure_ascii=False)
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()

def send_at(p):
    """Planned date plus send time (default 5pm) in Dubai, as UTC ISO8601."""
    if not p.get('planned_date'):raise ValueError('Give this email a send date before it goes to Kit')
    hh,mm=(p.get('send_time') or '17:00').split(':')
    local=datetime.fromisoformat(p['planned_date']).replace(hour=int(hh),minute=int(mm),tzinfo=DUBAI)
    if local<datetime.now(DUBAI)+timedelta(minutes=10):raise ValueError('The send time has passed. Pick a later date or time before it goes to Kit')
    return local.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

URL=re.compile(r'https://[^\s<>"]+')
def body_html(p):
    """Each line becomes a paragraph, like Kit's editor. CTA labels become their tracked links."""
    body=p.get('body') or ''
    if not body.strip():raise ValueError('This email has no body yet')
    marks={};used=set()
    for i,l in enumerate(p.get('links') or []):
        if l.get('label') and str(l.get('href','')).startswith('https://'):marks[l['label']]=l['href']
    out=[]
    for line in body.split('\n'):
        if not line.strip():out.append('<p>​</p>');continue
        done=False
        for label,href in marks.items():
            if label in used:continue
            at=line.find(label)
            if at>=0:
                used.add(label)
                text=html.escape(line[:at])+'<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>'%(html.escape(href),html.escape(label))+html.escape(line[at+len(label):])
                out.append('<p>%s</p>'%text);done=True;break
        if done:continue
        text=html.escape(line,quote=False)
        text=URL.sub(lambda m:'<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>'%(m.group(0).replace('"','&quot;'),m.group(0)),text)
        out.append('<p>%s</p>'%text)
    missing=[l for l in marks if l not in used]
    if missing:raise ValueError('The link text "%s" is no longer in the email. Put it back or update the link before it goes to Kit'%missing[0])
    return ''.join(out)

class Kit:
    def __init__(self,data_root):
        self.file=Path(data_root)/'kit.json';self.lock=threading.Lock()

    def config(self):
        try:return json.loads(self.file.read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}

    @property
    def connected(self):
        c=self.config();return bool(c.get('api_key') and c.get('segment_id'))

    def status(self):
        c=self.config()
        return dict(connected=self.connected,has_key=bool(c.get('api_key')),key_end=(c.get('api_key') or '')[-4:],account=c.get('account',''),
                    segment_id=c.get('segment_id'),segment_name=c.get('segment_name',''),segments=c.get('segments',[]),last_sync=c.get('last_sync'),last_error=c.get('last_error',''))

    def _save(self,c):
        tmp=self.file.with_suffix('.tmp');tmp.write_text(json.dumps(c,indent=2),encoding='utf-8');tmp.replace(self.file)

    def note(self,**kw):
        c=self.config()
        if c:c.update(kw);self._save(c)

    def request(self,method,path,body=None,key=None):
        key=key or self.config().get('api_key')
        if not key:raise ValueError('Kit is not connected yet')
        data=None if body is None else json.dumps(body).encode('utf-8')
        req=urllib.request.Request(API+path,data=data,method=method,headers={'X-Kit-Api-Key':key,'Accept':'application/json','Content-Type':'application/json','User-Agent':'DaJoshiResolve'})
        try:
            with urllib.request.urlopen(req,timeout=30) as r:raw=r.read()
        except urllib.error.HTTPError as e:
            try:msg=json.loads(e.read()).get('errors') or ''
            except Exception:msg=''
            if isinstance(msg,list):msg='; '.join(map(str,msg))
            if e.code==401:msg='Kit rejected the API key. Check it under Settings, Developer in Kit'
            raise KitError(e.code,msg or 'Kit returned an error (%d)'%e.code)
        except urllib.error.URLError as e:raise KitError(0,'Could not reach Kit. Check your internet connection')
        return json.loads(raw) if raw else {}

    def segments(self,key=None):
        out=[];after=''
        while True:
            r=self.request('GET','/segments?per_page=100'+('&after='+after if after else ''),key=key)
            out+= [dict(id=s['id'],name=s['name']) for s in r.get('segments',[])]
            pg=r.get('pagination') or {}
            if not pg.get('has_next_page'):return out
            after=pg['end_cursor']

    def connect(self,data):
        c=self.config()
        key=(data.get('api_key') or '').strip() or c.get('api_key')
        if not key:raise ValueError('Paste your Kit API key first')
        if not re.fullmatch(r'[A-Za-z0-9_\-]{10,200}',key):raise ValueError('That does not look like a Kit API key')
        acct=self.request('GET','/account',key=key)
        segs=self.segments(key)
        c.update(api_key=key,account=(acct.get('account') or {}).get('name') or (acct.get('user') or {}).get('email',''),segments=segs,last_error='')
        sid=data.get('segment_id')
        if sid not in (None,''):
            sid=int(sid);match=[s for s in segs if s['id']==sid]
            if not match:raise ValueError('That segment is not in your Kit account')
            c.update(segment_id=sid,segment_name=match[0]['name'])
        self._save(c);return self.status()

    def disconnect(self):
        if self.file.exists():self.file.unlink()
        return self.status()

    def get(self,kid):return self.request('GET','/broadcasts/%d'%kid).get('broadcast') or {}

    def push(self,p,schedule=True):
        """Create or update the Kit broadcast for this email. Returns the new kit link record."""
        c=self.config();k=p.get('kit') or {}
        if not c.get('segment_id'):raise ValueError('Choose the Kit audience segment first')
        when=send_at(p) if schedule else None
        h=content_hash(p)
        payload=dict(subject=p['subject'],preview_text=p.get('preview',''),description=p.get('description') or p['subject'],public=False,
                     published_at=when or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),send_at=when,
                     email_template_id=c.get('template_id',DEFAULT_TEMPLATE),
                     subscriber_filter=[{'all':[{'type':'segment','ids':[c['segment_id']]}],'any':None,'none':None}])
        own=True
        if k.get('id'):
            cur=self.get(k['id'])
            if cur.get('status') in ('sending','completed'):raise ValueError('This email has already gone out from Kit')
            if (cur.get('email_template') or {}).get('id'):payload['email_template_id']=cur['email_template']['id']
            # Keep an audience set up in Kit. Drafts and emails the app scheduled use the chosen segment.
            if cur.get('status')!='draft' and not k.get('segment_id') and cur.get('subscriber_filter'):payload['subscriber_filter']=cur['subscriber_filter'];own=False
            if k.get('pushed_hash')!=h:payload['content']=body_html(p)
            b=self.request('PUT','/broadcasts/%d'%k['id'],payload).get('broadcast') or {}
            if 'content' not in payload and len(re.sub(r'<[^>]+>|\s|\u200b','',b.get('content') or ''))<20:
                # Kit cleared the body when it was left out, so put Kit's previous copy back.
                payload['content']=cur.get('content') or body_html(p)
                b=self.request('PUT','/broadcasts/%d'%k['id'],payload).get('broadcast') or {}
            if when and b.get('status') not in (None,'scheduled'):raise ValueError('Kit saved the email but did not schedule it (status %s). Open it in Kit to check'%b.get('status'))
        else:
            payload['content']=body_html(p)
            b=self.request('POST','/broadcasts',payload).get('broadcast') or {}
        kid=int(b.get('id') or k['id'])
        from .store import now
        return dict(k,id=kid,url='https://app.kit.com/campaigns/%d/draft'%kid,status=b.get('status') or ('scheduled' if when else 'draft'),
                    send_at=b.get('send_at',when),published_at=b.get('published_at'),pushed_hash=h,segment_id=c['segment_id'] if own else None,
                    scheduled_by_app=bool(when),synced_at=now())

class KitError(ValueError):
    def __init__(self,code,msg):super().__init__(msg);self.code=code
