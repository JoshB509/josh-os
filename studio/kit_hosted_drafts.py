"""Host-only preparation/gate for existing, verified Kit-hosted PNGs.

No upload, browser, credential or network implementation exists here. No agent tool
or HTTP route exposes receipt registration or authorization. A trusted host must
supply actual supported-read evidence and an explicitly authorized origin.
"""
import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .email_visuals import sha
from .email_visual_imports import png_size
from .kit import body_html,content_hash
from .kit_drafts import audience
from .kit_draft_guard import private_draft
from .visual_spec import MAX_VISUALS

ACTION='save_exact_private_unsent_image_draft'
META=('id','status','public','send_at','published_at','public_url','subject','preview_text','content','subscriber_filter')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def copy_json(value):return json.loads(json.dumps(value))


def opaque(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',value):raise ValueError('Use an opaque evidence identity.')
    return value


def hosted_url(value):
    if not isinstance(value,str) or len(value)>2048:raise ValueError('Invalid hosted asset URL.')
    u=urlsplit(value)
    if (u.scheme!='https' or u.netloc!='embed.filekitcdn.com' or u.query or u.fragment
            or not re.fullmatch(r'/e/[A-Za-z0-9_-]+/[A-Za-z0-9_./-]+',u.path)
            or any(part in ('.','..') for part in u.path.split('/'))):
        raise ValueError('Use a verified Kit-hosted asset URL, without redirects or arbitrary hosts.')
    return value


def source(email):
    if email.get('archived') or email.get('status') not in ('Draft','Ready') or (email.get('kit') or {}).get('status') in ('scheduled','sending','completed'):
        raise ValueError('Only an active unsent local draft is eligible.')
    if type(email.get('revision')) is not int or email['revision']<1:raise ValueError('An exact saved revision is required.')
    return dict(email_id=email['id'],revision=email['revision'],content_hash=content_hash(email),audience=email.get('audience',''),linked_broadcast_id=(email.get('kit') or {}).get('id'))


def verified_asset(emails,email_id,revision,index,url,hosted_png,evidence_id):
    """Trusted intake: host supplies downloaded/observed hosted bytes, never a claim.

    This function does not fetch URLs. Different/compressed bytes require a separate
    reviewed receipt format; v1 deliberately accepts only the exact original PNG.
    """
    email=emails.get(email_id);binding=source(email)
    if revision!=email['revision'] or type(index) is not int or not 1<=index<=len(email.get('images',[])):raise ValueError('Stale source revision or invalid image index.')
    image=email['images'][index-1]
    original=emails.visuals.read_png(email_id,image['version'],str(image['index']))
    if hosted_png!=original or sha(original)!=image['png_hash']:raise ValueError('Hosted bytes do not match the approved original PNG.')
    width,height=png_size(original)
    return dict(source=binding,ordinal=index,image=copy_json(image),url=hosted_url(url),width=width,height=height,evidence_id=opaque(evidence_id))


def select_target(email,discovery,baseline):
    # Discovery is supplied by the host's supported read adapter, never agent prose.
    if not isinstance(discovery,dict) or discovery.get('complete') is not True or not isinstance(discovery.get('broadcasts'),list):raise ValueError('Complete duplicate discovery is required.')
    opaque(discovery.get('evidence_id'))
    subject=email['subject'].strip().casefold()
    matches=[b for b in discovery['broadcasts'] if str(b.get('subject','')).strip().casefold()==subject]
    if len(matches)>1:raise ValueError('Multiple matching broadcasts require review; never create another.')
    linked=(email.get('kit') or {}).get('id')
    target=linked or (matches[0].get('id') if matches else None)
    if target is not None:
        if type(target) is not int or target<=0 or (matches and matches[0].get('id')!=target):raise ValueError('Broadcast identity conflicts with the saved link.')
        if not private_draft(baseline) or baseline.get('id')!=target:raise ValueError('Read the exact existing private unsent draft first.')
        return target,{k:copy_json(baseline.get(k)) for k in META}
    if baseline is not None:raise ValueError('Unexpected baseline without an existing target.')
    return None,None


def prepare(email,assets,discovery,*,baseline=None,audience_rule=None):
    binding=source(email);images=email.get('images',[])
    if not 1<=len(images)<=MAX_VISUALS or not isinstance(assets,list) or len(assets)!=len(images):raise ValueError('Every original image needs a verified hosted receipt, up to sixteen.')
    by_line={};seen=set()
    for ordinal,(image,asset) in enumerate(zip(images,assets),1):
        if asset.get('source')!=binding or asset.get('ordinal')!=ordinal or asset.get('image')!=image:raise ValueError('Hosted image approval is stale or belongs to another source/placement/alt text.')
        identity=(image['version'],image['index'])
        if identity in seen:raise ValueError('Duplicate visual identity.')
        seen.add(identity);opaque(asset.get('evidence_id'));url=hosted_url(asset.get('url'))
        if type(image.get('after_line')) is not int or not 0<=image['after_line']<=len(email['body'].split('\n')):raise ValueError('Invalid image placement.')
        if not isinstance(image.get('alt'),str) or not image['alt'].strip() or len(image['alt'])>1000:raise ValueError('Invalid image alt text.')
        w,h=asset.get('width'),asset.get('height')
        if type(w) is not int or type(h) is not int or not 320<=w<=4096 or not 320<=h<=4096 or w*h>8_000_000:raise ValueError('Invalid receipt dimensions.')
        by_line.setdefault(image['after_line'],[]).append(f'<p><img src="{html.escape(url,quote=True)}" alt="{html.escape(image["alt"],quote=True)}" width="{w}" height="{h}" style="max-width:100%;height:auto"></p>')
    # Reuse exact link and Liquid escaping; this temporary copy is never saved.
    paragraphs=re.findall(r'<p>.*?</p>',body_html(dict(email,images=[])),re.S)
    if len(paragraphs)!=len(email['body'].split('\n')):raise ValueError('Unexpected paragraph composition.')
    content=''.join(by_line.get(0,[]))+''.join(p+''.join(by_line.get(i,[])) for i,p in enumerate(paragraphs,1))
    target,before=select_target(email,discovery,baseline)
    payload=dict(subject=email['subject'],preview_text=email.get('preview',''),content=content,public=False,send_at=None)
    intent=None if audience_rule is None else audience(audience_rule)
    if intent is not None:payload['subscriber_filter']=intent
    # Omitted audience is an intent, not evidence of Kit's default behavior. The
    # adapter must prove that it can leave selection unset and verify it afterwards.
    packet=dict(action=ACTION,source=binding,assets=copy_json(assets),broadcast_id=target,expected_before=before,audience_intent=intent,payload=payload)
    return dict(packet,digest=digest(packet))


def authorize(packet,origin):
    """Trusted authenticated host only; do not expose this method to chat tools."""
    if not isinstance(origin,dict) or set(origin)!={'actor_id','channel','message_id'}:raise ValueError('An authenticated exact draft request is required.')
    for v in origin.values():opaque(v)
    return dict(action=ACTION,packet_digest=packet['digest'],source=copy_json(packet['source']),broadcast_id=packet['broadcast_id'],origin=copy_json(origin))


class UnavailableDraftTransport:
    def capabilities(self):return dict(image_html_drafts=False,upload=False,audience_unset=False,conditional_update=False)
    def inspect(self,packet):raise ValueError('No supported image-draft transport is installed.')
    def save(self,command):raise ValueError('No supported image-draft transport is installed.')
    def read(self,broadcast_id):raise ValueError('No supported image-draft transport is installed.')


class DraftWriteGate:
    """Durable single-attempt gate for an injected trusted supported transport.

    Transport.inspect returns fresh complete discovery and the exact baseline.
    Transport.save accepts only the issued command, honors expected_before on an
    update, and returns an integer id. Transport.read returns raw supported-read
    broadcast data plus audience_selection='unset' when that is verified.
    No production transport is implemented or wired by this module.
    """
    def __init__(self,emails,transport=None):
        self.emails=emails;self.transport=transport or UnavailableDraftTransport()
        self.root=Path(emails.store.root)/'kit-image-draft-attempts'

    def verified_read(self,packet,bid):
        result=self.transport.read(bid)
        if not private_draft(result) or result.get('id')!=bid:raise ValueError('Private unsent status was not verified.')
        if any(result.get(k)!=packet['payload'][k] for k in ('subject','preview_text','content')):raise ValueError('Saved content differs from the approved draft.')
        if packet['audience_intent'] is None:
            if result.get('audience_selection')!='unset':raise ValueError('Audience was not verified unset.')
        elif result.get('subscriber_filter')!=packet['audience_intent']:raise ValueError('Audience differs from approval.')
        return result

    def execute(self,packet,authorization):
        if set(packet)!={'action','source','assets','broadcast_id','expected_before','audience_intent','payload','digest'} or packet.get('action')!=ACTION or digest({k:v for k,v in packet.items() if k!='digest'})!=packet.get('digest'):raise ValueError('Packet changed after review.')
        expected=authorize(packet,authorization.get('origin'))
        if authorization!=expected:raise ValueError('Authorization is not for this exact unsent draft.')
        email=self.emails.get(packet['source']['email_id'])
        if source(email)!=packet['source']:raise ValueError('The local revision or content changed.')
        for a in packet['assets']:
            im=a['image'];png=self.emails.visuals.read_png(email['id'],im['version'],str(im['index']))
            if sha(png)!=im['png_hash']:raise ValueError('Original PNG changed.')
        caps=self.transport.capabilities()
        if caps.get('image_html_drafts') is not True:raise ValueError('Supported image-rich draft transport is unavailable; nothing was uploaded or written.')
        if packet['audience_intent'] is None and caps.get('audience_unset') is not True:raise ValueError('This transport has not verified leaving audience unset.')
        if packet['broadcast_id'] is not None and caps.get('conditional_update') is not True:raise ValueError('Existing draft updates need a supported exact-baseline guard.')
        self.root.mkdir(parents=True,exist_ok=True);file=self.root/(packet['digest']+'.json')
        if file.exists():
            saved=json.loads(file.read_text())
            if saved.get('state')=='verified':return self.verified_read(packet,saved['result']['id'])
            raise ValueError('A prior attempt is in progress or uncertain; inspect it instead of retrying.')
        observation=self.transport.inspect(copy_json(packet))
        rule=None if packet['audience_intent'] is None else packet['audience_intent'][0]
        fresh=prepare(email,packet['assets'],observation['discovery'],baseline=observation.get('baseline'),audience_rule=rule)
        if fresh!=packet:raise ValueError('The target or live baseline changed; no write made.')
        if source(self.emails.get(email['id']))!=packet['source']:raise ValueError('The local email changed during inspection; no write made.')
        with file.open('x',encoding='utf8') as f:json.dump(dict(state='reserved',packet_digest=packet['digest']),f)
        try:
            command=dict(operation='update' if packet['broadcast_id'] else 'create',broadcast_id=packet['broadcast_id'],expected_before=packet['expected_before'],payload=packet['payload'],idempotency_key=packet['digest'])
            bid=self.transport.save(copy_json(command))
            if type(bid) is not int or bid<=0 or (packet['broadcast_id'] is not None and bid!=packet['broadcast_id']):raise ValueError('Transport returned the wrong draft identity.')
            result=self.verified_read(packet,bid)
            file.write_text(json.dumps(dict(state='verified',packet_digest=packet['digest'],result=result)),encoding='utf8')
            return result
        except Exception:
            file.write_text(json.dumps(dict(state='uncertain',packet_digest=packet['digest'])),encoding='utf8')
            raise
