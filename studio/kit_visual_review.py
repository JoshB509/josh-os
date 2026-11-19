"""Owner-only visual review HTTP controller. No model JSON can supply identity or evidence.

Wire route() after Handler.authorized(). It independently requires the authenticated
browser cookie and rejects API-token callers. Approval/execute/recovery use separate
single-use session-bound nonces. No browser worker starts on module import or page load.
"""
import hashlib
import json
import secrets
import threading
import time
import uuid
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .kit_visual_insertions import ACTION, OwnerReview
from .store import Conflict


class KitVisualReview:
    def __init__(self,service):
        self.service=service;self.nonces={};self.lock=threading.Lock()

    def principal(self,h,write=False):
        if h.headers.get('X-DJ-Token') or parse_qs(urlsplit(h.path).query).get('token'):
            raise PermissionError('Use the signed-in owner review page; agent API tokens cannot approve uploads.')
        cookie=SimpleCookie()
        try:cookie.load(h.headers.get('Cookie',''))
        except Exception:raise PermissionError('A signed-in owner browser is required.') from None
        local='127.0.0.1:'+str(h.server.server_port)
        pairing_origin=h.pairing_origin()
        if h.headers.get('Host')==local:
            origin='http://'+local
            valid='dj_session' in cookie and secrets.compare_digest(cookie['dj_session'].value,h.server.token)
            secret=cookie['dj_session'].value if valid else ''
        else:
            origin=pairing_origin
            valid=bool(origin) and h.device_cookie_valid(cookie)
            secret=cookie['__Host-dj_device'].value if valid else ''
        if not valid:raise PermissionError('A signed-in owner browser is required.')
        if h.headers.get('Sec-Fetch-Site') not in ('same-origin','none'):
            raise PermissionError('Open the review in CareerOS, not a cross-site frame or request.')
        if write and (h.headers.get('Sec-Fetch-Site')!='same-origin' or h.headers.get('Origin')!=origin):
            raise PermissionError('Approve from this same-origin owner review page.')
        if h.headers.get('Origin') not in (None,origin):raise PermissionError('Wrong review origin.')
        return hashlib.sha256((origin+'\0'+secret).encode()).hexdigest()

    def issue(self,principal,action,pid,digest):
        token=secrets.token_urlsafe(32);stamp=time.monotonic()
        with self.lock:
            self.nonces={k:v for k,v in self.nonces.items() if v['expires']>stamp}
            while len(self.nonces)>=128:self.nonces.pop(next(iter(self.nonces)))
            self.nonces[token]=dict(principal=principal,action=action,pid=pid,digest=digest,expires=stamp+600)
        return token

    def consume(self,principal,action,pid,digest,token):
        with self.lock:item=self.nonces.pop(token,None)
        if not item or item['expires']<=time.monotonic() or (item['principal'],item['action'],item['pid'],item['digest'])!=(principal,action,pid,digest):
            raise ValueError('This review expired or belongs to another session/action/version. Open it again.')

    def packet_view(self,row,principal):
        approval=bool(row.get('approval'))
        action='recover' if row['state']=='insertion_uncertain' else ('execute' if approval else 'approve')
        out=dict(row,readiness=self.service.readiness(),review_action=action)
        dispatcher=getattr(self,'dispatch',None)
        if dispatcher is not None and approval and row['state']=='not_started':
            out['delegation_state']=dispatcher.state(row)
            if out['delegation_state']=='owner_confirmation_required':
                out['delegate_nonce']=self.issue(principal,'delegate',row['id'],row['digest'])
        if row['state']!='verified_complete':out['review_nonce']=self.issue(principal,action,row['id'],row['digest'])
        out['visual_urls']=['/api/emails/'+row['packet']['email_id']+'/visuals/'+a['visual']['version']+'/'+str(a['visual']['index']) for a in row['packet']['assets']]
        return out

    def route(self,h,method,path):
        if path not in ('/kit-visuals','/kit-visual-review.js','/kit-visual-review.css') and not path.startswith('/api/kit-visual-review/'):
            return False
        try:
            principal=self.principal(h,write=method!='GET')
            if method=='GET' and path in ('/kit-visuals','/kit-visual-review.js','/kit-visual-review.css'):
                name={'/kit-visuals':'kit-visual-review.html'}.get(path,path[1:])
                mime='text/html' if name.endswith('.html') else 'text/css' if name.endswith('.css') else 'text/javascript'
                h.send((Path(__file__).parent/'web'/name).read_bytes(),mime=mime+'; charset=utf-8');return True
            parts=path.strip('/').split('/')
            if method=='GET' and len(parts)==4 and parts[:3]==['api','kit-visual-review','email']:
                email=self.service.emails.get(parts[3]);bid=self.service.drafts.made_by_us(email['id'])
                with self.service.store.connect() as c:rows=[json.loads(r[0]) for r in c.execute('SELECT body FROM kit_visual_insertions WHERE email_id=? ORDER BY rowid DESC',(email['id'],))]
                h.send(dict(email=email,broadcast_id=bid,packets=[dict(id=r['id'],state=r['state'],digest=r['digest']) for r in rows],readiness=self.service.readiness(),
                            prepare_nonce=self.issue(principal,'prepare',email['id'],str(email['revision']))));return True
            if method=='GET' and len(parts)==4 and parts[:3]==['api','kit-visual-review','packet']:
                h.send(self.packet_view(self.service.get(parts[3]),principal));return True
            if method!='POST' or len(parts)!=3:raise KeyError('Unknown visual review action.')
            length=int(h.headers.get('Content-Length','0'))
            if not 0<length<=8192:raise ValueError('Invalid review request size.')
            data=json.loads(h.rfile.read(length));action=parts[2]
            if not isinstance(data,dict):raise ValueError('Invalid review request.')
            if action=='prepare':
                if set(data)!={'email_id','revision','broadcast_id','method','nonce'}:raise ValueError('Unexpected preparation field.')
                self.consume(principal,'prepare',data['email_id'],str(data['revision']),data['nonce'])
                row=self.service.prepare(data['email_id'],data['revision'],data['broadcast_id'],method=data['method'])
            else:
                if set(data)!={'id','digest','nonce'} or action not in ('approve','execute','recover','delegate'):raise ValueError('Unexpected review action or field.')
                self.consume(principal,action,data['id'],data['digest'],data['nonce'])
                row=self.service.get(data['id'])
                if row['digest']!=data['digest']:raise Conflict('The reviewed packet changed.')
                if action=='approve':
                    origin=OwnerReview(self.service.owner_id,'authenticated-owner-browser',str(uuid.uuid4()),row['id'],row['digest'],ACTION)
                    row=self.service.approve(row['id'],row['digest'],origin)
                elif action=='delegate':
                    from .kit_visual_dispatch import DELEGATE
                    dispatcher=getattr(self,'dispatch',None)
                    if dispatcher is None:raise ValueError('Delegated image jobs are not wired.')
                    dispatcher.grant(row,OwnerReview(self.service.owner_id,'authenticated-owner-browser',str(uuid.uuid4()),row['id'],row['digest'],DELEGATE))
                elif action=='execute':row=self.service.execute(row['id'],row['digest'])
                else:row=self.service.recover(row['id'])
            h.send(self.packet_view(row,principal));return True
        except PermissionError as exc:h.send({'error':str(exc)},403)
        except Conflict as exc:h.send({'error':str(exc)},409)
        except (ValueError,KeyError,TypeError) as exc:h.send({'error':str(exc)},400)
        return True
