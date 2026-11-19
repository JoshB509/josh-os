"""Narrow Laia dispatch of an existing owner-approved private-draft image job.

No chat approval, file-sharing grant, browser primitives or broadcast creation.
Owner delegation is a separate same-origin action, exact-job-bound and expires.
"""
import json,re,time,uuid
from pathlib import Path
from .kit_visual_browser import BrowserUnavailable
from .kit_visual_insertions import OwnerReview,digest

DELEGATE='delegate_exact_private_kit_image_job_once'
PROPS={'kit_visual_job':{'type':['object','null'],'description':'Run only an existing owner-approved image job after Josh explicitly asks to run that approved image job for the exact email. This never grants approval, shares files, creates/adopts a broadcast, changes audience, sends or schedules. The host returns actual outcome. Omit when showing an email or doing another action.','additionalProperties':False,'required':['packet_id','digest','email_id','revision'],'properties':{'packet_id':{'type':'string'},'digest':{'type':'string'},'email_id':{'type':'string'},'revision':{'type':'integer'}}}}

class KitVisualDispatch:
    def __init__(self,service):
        self.service=service;self.root=Path(service.store.root)/'kit-visual-dispatch'

    def _file(self,row,suffix):
        uuid.UUID(row['id'])
        if not re.fullmatch('[a-f0-9]{64}',row['digest']):raise ValueError('Invalid job digest.')
        return self.root/(row['id']+'-'+row['digest']+suffix)

    def _validate(self,row):
        if row['state']!='not_started':raise ValueError('This image job already started. Inspect its existing result; never retry.')
        if digest(row['packet'])!=row['digest']:raise ValueError('The reviewed image job changed.')
        self.service._check_approval(row);self.service._check_packet(row)
        if row['packet']['method']!='media_gallery':raise ValueError('Only the exact Media Gallery job can be delegated.')

    def grant(self,row,review):
        self._validate(row)
        if not isinstance(review,OwnerReview) or (review.actor_id,review.action,review.packet_id,review.packet_digest)!=(self.service.owner_id,DELEGATE,row['id'],row['digest']) or review.channel!='authenticated-owner-browser' or not review.event_id:
            raise PermissionError('Only the authenticated owner review can delegate this exact job.')
        # No promise that approving grants extension file access or a usable adapter.
        self.service._available('media_gallery')
        self.root.mkdir(exist_ok=True)
        body=dict(packet_id=row['id'],digest=row['digest'],email_id=row['packet']['email_id'],revision=row['packet']['email_revision'],expires=time.time()+1800,actor_id=review.actor_id,event_id=review.event_id,action=DELEGATE)
        file=self._file(row,'.grant.json')
        if self._file(row,'.claimed.json').exists():raise ValueError('This delegated job was already attempted.')
        try:
            with file.open('x',encoding='utf8') as f:json.dump(body,f)
        except FileExistsError:raise ValueError('This exact job already has an owner delegation. Prepare a new review if it expired.') from None
        return body

    def state(self,row):
        if self._file(row,'.claimed.json').exists():return 'attempted'
        file=self._file(row,'.grant.json')
        if not file.exists():return 'owner_confirmation_required'
        grant=json.loads(file.read_text(encoding='utf8'))
        return 'authorized_once' if grant['expires']>time.time() else 'expired'

    def context(self):
        if not self.root.exists():return []
        out=[]
        for file in self.root.glob('*.grant.json'):
            grant=json.loads(file.read_text(encoding='utf8'))
            try:
                row=self.service.get(grant['packet_id'])
                if self.state(row)=='authorized_once' and row['state']=='not_started':
                    out.append({k:grant[k] for k in ('packet_id','digest','email_id','revision')})
            except (ValueError,KeyError):continue
        return out[:16]

    def requested(self,message,email,context):
        text=str(message or '').strip().rstrip('.!').strip()
        text=re.sub(r'\s+in kit$','',text,flags=re.I)
        match=re.fullmatch(r'(?:please\s+)?(?:run|execute) (?:the )?approved (?:image|visual) (?:job|insertion)(?: for (.+))?',text,re.I)
        if not match:return False
        target=(match.group(1) or '').strip().strip('"')
        if target==email['id']:return True
        if target.casefold()==email['subject'].casefold():
            return sum(e['subject'].casefold()==target.casefold() for e in self.service.emails.all())==1
        return target.casefold() in ('','this email') and (context or {}).get('kind')=='email' and context.get('id')==email['id']

    def dispatch(self,args,*,role,message,context=None,proactive=False,request_id=''):
        if role!='laia' or proactive:raise PermissionError('Only Josh-requested Laia jobs can be dispatched.')
        if not isinstance(args,dict) or set(args)!={'packet_id','digest','email_id','revision'} or type(args['revision']) is not int:
            raise ValueError('Use the exact existing image job and saved revision.')
        row=self.service.get(args['packet_id']);packet=row['packet']
        if (row['digest'],packet['email_id'],packet['email_revision'])!=(args['digest'],args['email_id'],args['revision']):raise ValueError('The job source or approval digest changed.')
        self._validate(row)
        email=self.service.emails.get(args['email_id'])
        if not self.requested(message,email,context):raise PermissionError('Ask to run the approved image job for this exact email.')
        if self.state(row)!='authorized_once':raise PermissionError('The owner review must allow Laia to run this exact job once; no authority was inferred from chat.')
        grant=json.loads(self._file(row,'.grant.json').read_text(encoding='utf8'))
        if any(grant[k]!=args[k] for k in args) or grant.get('action')!=DELEGATE or grant.get('actor_id')!=self.service.owner_id:
            raise PermissionError('Delegation does not match this exact job.')
        self.service._available('media_gallery')
        with self._file(row,'.claimed.json').open('x',encoding='utf8') as f:
            json.dump(dict(packet_id=row['id'],digest=row['digest'],request_id=str(request_id)[:200],at=time.time()),f)
        # Existing service owns reservation, exact revision, remote baseline,
        # single-use image phases and saved/readback verification. Never retry.
        result=self.service.execute(row['id'],row['digest'])
        if result['state']!='verified_complete':raise BrowserUnavailable('The existing image job is not verified complete. Inspect it without retrying.')
        return dict(packet_id=row['id'],state=result['state'],broadcast_id=packet['broadcast_id'],url=packet['destination'],note='The approved images were verified in the existing private unsent Kit draft. No audience change, scheduling or sending.')
