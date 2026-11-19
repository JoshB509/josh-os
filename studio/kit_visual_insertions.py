"""Exact reviewed browser image insertions into an existing owned private Kit draft.

Default is unavailable. No browser tools, connector writes, uploads or worker start here.
The injected adapter is trusted host code, never selected or constructed by a model.
V1 accepts a linear paragraph-only baseline matching the local linked body exactly.
Uncertain attempts can only be inspected, never automatically retried.
"""
import copy
import hashlib
import ipaddress
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .kit import body_html, content_hash
from .kit_draft_guard import private_draft
from .kit_visual_browser import BrowserUnavailable, InsertCommand, Snapshot, UploadReceipt, UnavailableKitVisualBrowser
from .store import Conflict, now

ACTION = 'insert_visuals_into_existing_private_kit_draft'
METHODS = {'media_gallery': ('upload', 'insert'), 'media_gallery_auto': ('upload_insert',), 'clipboard': ('paste',)}
META = ('id', 'status', 'public', 'send_at', 'published_at', 'public_url', 'subject', 'preview_text', 'subscriber_filter')
BLOCKED = ('insertion_uncertain', 'verified_complete')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def assert_api_update_allowed(connection, email_id, broadcast_id=None):
    """Called inside the API writer's own transaction too, to close reservation races.

    Keep this lock even if someone removes local image references or replans the email.
    There is deliberately no model-callable unlock or content-replacement bypass.
    """
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kit_visual_insertions'").fetchone():
        return
    if connection.execute("SELECT 1 FROM kit_visual_insertions WHERE (email_id=? OR broadcast_id=?) AND state IN (?,?)",
                          (email_id, broadcast_id, *BLOCKED)).fetchone():
        raise ValueError('This draft has browser image insertion evidence or an uncertain insertion. API content replacement is blocked so it cannot strip images. Review it through the dedicated visual workflow.')


class Document(HTMLParser):
    """Canonical, inert paragraph document. No rendering, resource fetching or inferred HTML."""
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.nodes=[];self.stack=[]
        if not isinstance(source,str) or len(source)>400000:raise ValueError('Invalid editor content.')
        self.feed(source);self.close()
        if self.stack:raise ValueError('Incomplete editor HTML.')
        self.nodes=self.canonical(self.nodes)

    @classmethod
    def canonical(cls,nodes):
        """Ignore only semantically transparent wrappers, never text or link changes.

        Empty-attribute spans and divs grouping paragraphs are harmless. Unknown
        Kit classes/styles are retained for review, not stripped speculatively.
        Paragraph boundaries, whitespace inside text, hrefs and images remain exact.
        """
        out=[]
        for node in nodes:
            if isinstance(node,str):items=[node]
            else:
                node=dict(node,children=cls.canonical(node['children']))
                if node['tag']=='p' and node['children'] in (['\u200b'],['']):node['children']=[]
                if node['tag']=='a' and node['attrs'].get('target')=='_blank' and node['attrs'].get('rel')=='noopener noreferrer':
                    node['attrs']={k:v for k,v in node['attrs'].items() if k not in ('target','rel')}
                if node['tag']=='span' and not node['attrs']:items=node['children']
                elif node['tag']=='div' and not node['attrs'] and all(isinstance(c,dict) and c['tag'] in ('p','div') for c in node['children']):
                    items=node['children']
                else:
                    node['tag']={'b':'strong','i':'em'}.get(node['tag'],node['tag']);items=[node]
            for item in items:
                if isinstance(item,str) and out and isinstance(out[-1],str):out[-1]+=item
                else:out.append(item)
        return out

    def add(self,node):
        (self.stack[-1]['children'] if self.stack else self.nodes).append(node)

    def handle_starttag(self,tag,attrs):
        if tag not in ('p','div','a','span','strong','em','b','i','u','br','img') or any(k.startswith('on') for k,v in attrs):
            raise ValueError('This editor layout is not supported by the constrained visual workflow.')
        if len({k for k,v in attrs})!=len(attrs):raise ValueError('Ambiguous HTML attributes.')
        node=dict(tag=tag,attrs=dict(sorted(attrs)),children=[])
        self.add(node)
        if tag not in ('img','br'):self.stack.append(node)

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in ('img','br'):self.handle_endtag(tag)

    def handle_endtag(self,tag):
        if not self.stack or self.stack[-1]['tag']!=tag:raise ValueError('Ambiguous editor HTML.')
        self.stack.pop()

    def handle_data(self,data):
        if not self.stack and not data.strip():return
        target=self.stack[-1]['children'] if self.stack else self.nodes
        if target and isinstance(target[-1],str):target[-1]+=data
        else:target.append(data)

    def handle_comment(self,data):raise ValueError('Editor comments need manual review.')
    def handle_decl(self,data):raise ValueError('Editor declarations need manual review.')
    def handle_pi(self,data):raise ValueError('Editor processing instructions need manual review.')


def image_node(node):
    if isinstance(node,dict) and node['tag']=='p' and not node['attrs'] and len(node['children'])==1:
        node=node['children'][0]
    if not isinstance(node,dict) or node['tag']!='img' or node['children']:
        raise ValueError('Expected one isolated image at the approved paragraph boundary.')
    attrs=node['attrs']
    if set(attrs)-{'src','alt','width','height','style','class'} or not isinstance(attrs.get('alt'),str):
        raise ValueError('Unexpected inserted image properties.')
    return attrs


def source_url(value):
    if not isinstance(value,str) or len(value)>4096:raise ValueError('Invalid observed image URL.')
    url=urlsplit(value);host=(url.hostname or '').lower().rstrip('.')
    if (url.scheme!='https' or not host or url.username or url.password or url.port not in (None,443)
            or host in ('localhost','work.career-os.ai') or host.endswith(('.localhost','.local'))):
        raise ValueError('Image source must be the Kit upload observed by the trusted browser adapter.')
    try:
        if not ipaddress.ip_address(host).is_global:raise ValueError('Private image sources are not allowed.')
    except ValueError:
        # Invalid IP literals are host names; explicit private IPs still fail below.
        if host.replace('.','').isdigit() or ':' in host:raise
    return value


@dataclass(frozen=True)
class OwnerReview:
    """Created only by an authenticated owner-review handler, never from model JSON."""
    actor_id: str
    channel: str
    event_id: str
    packet_id: str
    packet_digest: str
    action: str


class KitVisualInsertions:
    def __init__(self, emails, drafts, *, owner_id, browser=None):
        if not isinstance(owner_id,str) or not owner_id:raise ValueError('Configure the authenticated owner identity.')
        self.emails,self.drafts,self.store,self.owner_id=emails,drafts,emails.store,owner_id
        self.browser=browser if browser is not None else UnavailableKitVisualBrowser()
        with self.store.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS kit_visual_insertions(
                id TEXT PRIMARY KEY,email_id TEXT NOT NULL,broadcast_id INTEGER NOT NULL,state TEXT NOT NULL,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS kit_visual_events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,insertion_id TEXT NOT NULL,event TEXT NOT NULL,body TEXT NOT NULL);''')

    def readiness(self):return self.browser.readiness()

    def _available(self,method):
        r=self.readiness()
        if r.get('available') is not True or method not in r.get('methods',[]):
            raise BrowserUnavailable(r.get('reason') or 'The approved image insertion method is unavailable.')

    def get(self,pid):
        with self.store.connect() as c:row=c.execute('SELECT body FROM kit_visual_insertions WHERE id=?',(pid,)).fetchone()
        if not row:raise KeyError('Visual insertion packet not found.')
        return json.loads(row['body'])

    def events(self,pid):
        self.get(pid)
        with self.store.connect() as c:return [dict(event=r['event'],body=json.loads(r['body'])) for r in c.execute(
            'SELECT event,body FROM kit_visual_events WHERE insertion_id=? ORDER BY seq',(pid,))]

    def _event(self,pid,event,body):
        with self.store.connect() as c:c.execute('INSERT INTO kit_visual_events(insertion_id,event,body) VALUES(?,?,?)',
                                               (pid,event,json.dumps(dict(at=now(),evidence=body))))

    def _save(self,c,row):
        c.execute('UPDATE kit_visual_insertions SET state=?,body=? WHERE id=?',(row['state'],json.dumps(row),row['id']))

    def _local(self,eid,revision,bid):
        p=self.emails.get(eid)
        if type(revision) is not int or p['revision']!=revision:raise Conflict('The saved email revision changed. Prepare and review a new packet.')
        if type(bid) is not int or bid<=0 or self.drafts.made_by_us(eid)!=bid:
            raise ValueError('Only the exact existing broadcast owned by this email can be edited.')
        if (p.get('kit') or {}).get('id') not in (None,bid) or p.get('archived') or self.drafts.protected(p):
            raise ValueError('The local draft is archived, protected or linked to a different broadcast.')
        if not p.get('images'):raise ValueError('Generate and review saved email visuals first.')
        self.emails.visuals.validate(p)
        data=[self.emails.visuals.read_png(eid,v['version'],str(v['index'])) for v in p['images']]
        return p,data

    def _snapshot(self,snapshot,bid,*,api_required=False,reload=True):
        if not isinstance(snapshot,Snapshot):raise ValueError('A trusted browser observation is required.')
        if snapshot.url!=f'https://app.kit.com/campaigns/{bid}/draft' or (reload and snapshot.reloaded is not True):
            raise ValueError('Reload and inspect the exact approved draft in Kit.')
        try:age=(datetime.now(timezone.utc)-datetime.fromisoformat(snapshot.captured_at)).total_seconds()
        except (ValueError,TypeError):raise ValueError('Invalid browser observation time.') from None
        if not -5<=age<=60 or not snapshot.capture_id:raise ValueError('A fresh browser observation is required.')
        m=copy.deepcopy(snapshot.metadata)
        if not isinstance(m,dict) or any(k not in m for k in META) or type(m['id']) is not int or m['id']!=bid or not private_draft(m):
            raise ValueError('Kit must explicitly show this exact private unsent draft, public false and send_at null.')
        doc=Document(snapshot.content_html).nodes
        if snapshot.api_broadcast is not None:
            api=snapshot.api_broadcast
            if 'content_tree' in api:
                from .kit_editor_observation import tree_body
                api_doc=Document(tree_body(api)['html']).nodes
            else:api_doc=Document(api.get('content')).nodes
            if not private_draft(api) or any(api.get(k)!=m[k] for k in META) or api_doc!=doc:
                raise ValueError('Read-only Kit evidence does not match the browser draft.')
        elif api_required or not snapshot.api_unavailable_reason:
            raise ValueError('The required read-only Kit verification is missing.')
        api=snapshot.api_broadcast or {};template_keys=('email_template','theme','built_with_badge')
        template=digest({k:api.get(k) for k in template_keys}) if any(k in api for k in template_keys) else None
        return dict(metadata=m,document=doc,api_required=snapshot.api_broadcast is not None,
                    template_hash=template,footer_hash=(snapshot.content_proof or {}).get('footer_sha256'))

    def _read(self,bid,**kw):
        snap=self.browser.inspect(bid,reload=True)
        return snap,self._snapshot(snap,bid,**kw)

    def _check_packet(self,row):
        packet=row['packet']
        if digest(packet)!=row['digest'] or packet['action']!=ACTION:raise ValueError('The reviewed insertion packet changed.')
        p,data=self._local(packet['email_id'],packet['email_revision'],packet['broadcast_id'])
        if content_hash(p)!=packet['email_content_hash'] or p['images']!=[a['visual'] for a in packet['assets']]:
            raise Conflict('The image, alt text, position or email content changed after review.')
        if any(hashlib.sha256(b).hexdigest()!=a['visual']['png_hash'] for b,a in zip(data,packet['assets'])):
            raise ValueError('An approved PNG changed.')
        return p,data

    def _check_approval(self,row):
        a=row.get('approval') or {}
        if (a.get('actor_id'),a.get('packet_id'),a.get('packet_digest'),a.get('action'))!=(self.owner_id,row['id'],row['digest'],ACTION) or not a.get('channel') or not a.get('event_id'):
            raise ValueError('Approve this exact visual insertion packet first.')

    def prepare(self,email_id,revision,broadcast_id,*,method='media_gallery'):
        if method not in METHODS:raise ValueError('Choose supported Media Gallery upload or clipboard paste.')
        ready=self.readiness()
        if not (ready.get('can_inspect') is True and method in ready.get('review_methods',[])):
            self._available(method)
        p,data=self._local(email_id,revision,broadcast_id)
        snap,baseline=self._read(broadcast_id)
        local=Document(body_html(dict(p,images=[]))).nodes
        if baseline['document']!=local:raise ValueError('The remote body differs from the saved linked body; reconcile it before insertion.')
        pairs=[(digest(local[i-1]) if i else None,digest(local[i]) if i<len(local) else None) for i in range(len(local)+1)]
        assets=[];positions=set();hashes=set()
        for visual,png in zip(p['images'],data):
            pos=visual['after_line'];pair=pairs[pos]
            if pairs.count(pair)!=1 or pos in positions or visual['png_hash'] in hashes:
                raise ValueError('Each image needs a unique paragraph boundary and unique PNG; ambiguous or duplicate insertions require manual review.')
            positions.add(pos);hashes.add(visual['png_hash'])
            assets.append(dict(visual=visual,bytes=len(png),anchor=dict(after_line=pos,left_hash=pair[0],right_hash=pair[1])))
        assets.sort(key=lambda a:a['visual']['after_line'])
        # Keep saved image order too; it is part of the approval hash and must not be silently reordered.
        if [a['visual'] for a in assets]!=p['images']:raise ValueError('Save visuals in body order before preparing insertion.')
        pid=str(uuid.uuid4())
        packet=dict(action=ACTION,email_id=email_id,email_revision=revision,email_content_hash=content_hash(p),
                    broadcast_id=broadcast_id,destination=snap.url,method=method,assets=assets,baseline=baseline)
        row=dict(id=pid,state='not_started',packet=packet,digest=digest(packet),approval=None,created_at=now())
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');assert_api_update_allowed(c,email_id,broadcast_id)
            c.execute('INSERT INTO kit_visual_insertions VALUES(?,?,?,?,?)',(pid,email_id,broadcast_id,row['state'],json.dumps(row)))
        self._event(pid,'prepared_baseline',asdict(snap))
        return row

    def approve(self,pid,given_digest,review):
        row=self.get(pid);packet=row['packet']
        if not isinstance(review,OwnerReview) or (review.actor_id,review.packet_id,review.packet_digest,review.action)!=(self.owner_id,pid,row['digest'],ACTION):
            raise ValueError('An explicit authenticated owner review of this exact insertion packet is required.')
        if not review.event_id or not review.channel or given_digest!=row['digest']:
            raise ValueError('Approval must identify the reviewed packet and originating owner action.')
        if row['state']!='not_started':raise ValueError('An insertion already started; inspect it without retrying.')
        self._available(packet['method']);self._check_packet(row)
        snap,current=self._read(packet['broadcast_id'],api_required=packet['baseline']['api_required'])
        if current!=packet['baseline']:raise ValueError('The Kit draft changed after packet review.')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            saved=json.loads(c.execute('SELECT body FROM kit_visual_insertions WHERE id=?',(pid,)).fetchone()[0])
            if saved!=row:raise Conflict('This insertion packet changed.')
            assert_api_update_allowed(c,packet['email_id'],packet['broadcast_id'])
            row['approval']=dict(asdict(review),approved_at=now());self._save(c,row)
        self._event(pid,'owner_approved',dict(review=asdict(review),capture_id=snap.capture_id))
        return row

    def _receipts(self,pid):
        receipts={}
        for event in self.events(pid):
            if event['event']=='upload_source':
                receipt=event['body']['evidence'];ordinal=receipt['ordinal']
                if ordinal in receipts and receipts[ordinal]!=receipt:raise ValueError('Conflicting upload evidence.')
                receipts[ordinal]=receipt
        return receipts

    def _matches(self,packet,snapshot,receipts,count,*,reload=True):
        current=self._snapshot(snapshot,packet['broadcast_id'],api_required=packet['baseline']['api_required'],reload=reload)
        if current['metadata']!=packet['baseline']['metadata']:raise ValueError('Kit draft metadata changed.')
        if any(current.get(k)!=packet['baseline'].get(k) for k in ('template_hash','footer_hash')):
            raise ValueError('The Kit template or existing footer changed.')
        doc=current['document'];expected=[]
        for boundary in range(len(packet['baseline']['document'])+1):
            for ordinal,asset in enumerate(packet['assets'][:count]):
                if asset['anchor']['after_line']==boundary:expected.append(('image',ordinal))
            if boundary<len(packet['baseline']['document']):expected.append(('text',packet['baseline']['document'][boundary]))
        if len(doc)!=len(expected):raise ValueError('The draft has missing, duplicate or unrelated content.')
        for node,(kind,want) in zip(doc,expected):
            if kind=='text':
                if node!=want:raise ValueError('Existing body wording, links, formatting or placement changed.')
            else:
                attrs=image_node(node);source_url(attrs.get('src'))
                receipt=receipts.get(want);asset=packet['assets'][want]
                if (not receipt or attrs['alt']!=asset['visual']['alt'] or
                        snapshot.image_resources.get(attrs['src'])!=receipt['resource_id']):
                    raise ValueError('Image source, alt text or placement lacks trusted upload evidence.')
        return current

    def execute(self,pid,given_digest):
        row=self.get(pid);packet=row['packet']
        if row['state']!='not_started':raise ValueError('Insertion already started. Use read-only recovery; never retry blindly.')
        self._check_approval(row)
        if given_digest!=row['digest']:raise ValueError('Approve this exact visual insertion packet first.')
        self._available(packet['method']);self._check_packet(row)
        # Reserve atomically before any write. Also blocks ordinary Kit API updates.
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            saved=json.loads(c.execute('SELECT body FROM kit_visual_insertions WHERE id=?',(pid,)).fetchone()[0])
            if saved!=row:raise Conflict('This packet changed or another execution already started.')
            assert_api_update_allowed(c,packet['email_id'],packet['broadcast_id'])
            if any(json.loads(r[0]).get('status') in ('writing','uncertain') for r in c.execute('SELECT body FROM kit_drafts WHERE email_id=?',(packet['email_id'],))):
                raise ValueError('A Kit API write is in progress or uncertain; resolve it first.')
            row.update(state='insertion_uncertain',started_at=now());self._save(c,row)
        self._event(pid,'reserved',dict(packet_digest=row['digest']))
        try:
            for ordinal,asset in enumerate(packet['assets']):
                self._check_packet(row)
                snap,_=self._read(packet['broadcast_id'],api_required=packet['baseline']['api_required'])
                self._matches(packet,snap,self._receipts(pid),ordinal)
                phases=[]
                session={'active':True,'captures':{snap.capture_id}}
                def before_mutation(phase,observed,_session=session):
                    if not _session['active']:raise ValueError('This image mutation capability expired.')
                    expected=METHODS[packet['method']]
                    if len(phases)>=len(expected) or phase!=expected[len(phases)]:raise ValueError('Only the approved image UI phases are permitted, once each.')
                    if phase=='insert' and ordinal not in self._receipts(pid):raise ValueError('Record the uploaded source before inserting it.')
                    self._check_packet(row)
                    self._matches(packet,observed,self._receipts(pid),ordinal,reload=False)
                    if observed.capture_id in _session['captures']:raise ValueError('Reinspect the current draft immediately before the UI mutation.')
                    _session['captures'].add(observed.capture_id)
                    self._event(pid,'ui_phase_authorized',dict(ordinal=ordinal,phase=phase,capture_id=observed.capture_id))
                    phases.append(phase)
                def record_upload(receipt,_session=session):
                    if not _session['active']:raise ValueError('This upload evidence capability expired.')
                    if not phases or not isinstance(receipt,UploadReceipt):raise ValueError('Only trusted adapter upload evidence is accepted.')
                    expected=(pid,ordinal,asset['visual']['png_hash'],packet['method'])
                    if (receipt.packet_id,receipt.ordinal,receipt.png_hash,receipt.method)!=expected or not receipt.resource_id or not receipt.evidence_id:
                        raise ValueError('Upload evidence does not match the approved PNG.')
                    source_url(receipt.observed_src)
                    if ordinal in self._receipts(pid):raise ValueError('This image already has upload evidence; do not upload twice.')
                    self._event(pid,'upload_source',asdict(receipt))
                _,data=self._check_packet(row)
                command=InsertCommand(pid,row['digest'],packet['broadcast_id'],packet['destination'],packet['method'],ordinal,
                                      'email-visual-'+asset['visual']['png_hash']+'.png',asset['visual']['png_hash'],asset['visual']['alt'],asset['anchor'])
                try:
                    self.browser.insert_image(command,data[ordinal],before_mutation,record_upload)
                finally:
                    session['active']=False
                if tuple(phases)!=METHODS[packet['method']]:raise ValueError('The adapter did not provide all required mutation evidence.')
                after,_=self._read(packet['broadcast_id'],api_required=packet['baseline']['api_required'])
                self._matches(packet,after,self._receipts(pid),ordinal+1)
                self._event(pid,'image_verified_after_reload',asdict(after))
            return self.recover(pid)
        except Exception as exc:
            self._event(pid,'insertion_uncertain',dict(reason=str(exc)[:300]))
            # Persisted state remains uncertain even when an exception appears to precede autosave.
            raise ValueError('Image insertion is uncertain. Inspect this exact draft; no automatic retry is permitted.') from exc

    def recover(self,pid):
        """Read-only, including after interruption. Never uploads, inserts or resets an approval."""
        row=self.get(pid);packet=row['packet']
        if row['state']=='not_started':raise ValueError('This insertion has not started.')
        self._available(packet['method'])
        if digest(packet)!=row['digest']:raise ValueError('The original packet changed.')
        self._check_approval(row)
        if self.drafts.made_by_us(packet['email_id'])!=packet['broadcast_id']:raise ValueError('Draft ownership changed.')
        try:
            snapshot,_=self._read(packet['broadcast_id'],api_required=packet['baseline']['api_required'])
            receipts=self._receipts(pid)
            if set(receipts)!=set(range(len(packet['assets']))):raise ValueError('Upload evidence is incomplete; inspect manually before a newly approved plan.')
            phases=[e['body']['evidence'] for e in self.events(pid) if e['event']=='ui_phase_authorized']
            for ordinal in range(len(packet['assets'])):
                if tuple(e['phase'] for e in phases if e['ordinal']==ordinal)!=METHODS[packet['method']]:
                    raise ValueError('The approved UI mutation audit is incomplete.')
            self._matches(packet,snapshot,receipts,len(packet['assets']))
            # These observations, stored separately from any model reply, are the completion proof.
            self._event(pid,'verified_persistence',asdict(snapshot))
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE');row=self.get(pid)
                row.update(state='verified_complete',verified_at=now(),local_changed=self.emails.get(packet['email_id'])['revision']!=packet['email_revision'])
                self._save(c,row)
            return row
        except Exception as exc:
            self._event(pid,'recovery_unresolved',dict(reason=str(exc)[:300]))
            raise ValueError('Persistence is not verified. No retry or duplicate insertion is allowed; review the exact draft manually.') from exc
