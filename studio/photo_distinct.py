"""Distinct-link automatic preparation from structured saved card link context.
Selectors are NOT proof. Both exact read-only previews authenticate every link.
No free-text inference, shared-link authorization, link creation or enrollment.
"""
import hashlib,json,re
from pathlib import Path
from .photo_link_receipts import read_preview,verified_existing,LinkReceiptResolver
from .photo_binding import BoundPhotoReceipts

class DistinctPhotoReceipts:
    def __init__(self,tracker,root,read=None):self.tracker=tracker;self.root=Path(root);self.read=read or read_preview;self.card=None;self.context=None
    def __call__(self,post_id,offer_id):
        card=self.tracker.get(post_id)
        if card.get('owner')!='sam' or card.get('archived') or card.get('stage') in ('Scheduled','Posted'):raise ValueError('Choose an editable Sam carousel draft.')
        # Only a whole structured planner record is accepted, never prose or a
        # model-authored assertion of authorization. Fresh source previews follow.
        try:plan=json.loads(card.get('notes') or '{}')['link_context']
        except (ValueError,KeyError,TypeError):raise ValueError('This carousel has no saved separate caption/QR link context. I can make its photo draft, but cannot add a verified QR yet.') from None
        if not isinstance(plan,dict) or set(plan)!={'post_id','revision','caption','qr'} or plan['post_id']!=post_id or plan['revision']!=card['revision']:raise ValueError('Saved link context does not match this carousel revision.')
        allowed={'asset_id','source_type','placement','campaign','label'}
        for inp in (plan['caption'],plan['qr']):
            if not isinstance(inp,dict) or set(inp)!=allowed or inp['asset_id']!=offer_id or inp['source_type']!='community_post' or inp['label']!=card['title']:raise ValueError('Link context must match exact card title and offer.')
        if plan['caption']['placement']==plan['qr']['placement']:raise ValueError('Separate QR placement is missing; shared links require owner review.')
        cap_raw=self.read(self.root,plan['caption']);qr_raw=self.read(self.root,plan['qr'])
        cap=verified_existing(cap_raw,plan['caption']);qr=verified_existing(qr_raw,plan['qr'])
        if cap['tracked_url']==qr['tracked_url']:raise ValueError('Shared links require owner review.')
        # Existing current caption must corroborate post identity, not just name.
        urls=re.findall(r'https://linklr\.app/[A-Za-z0-9_-]+',card.get('caption',''))
        if cap['tracked_url'] not in urls:raise ValueError('Verified caption link does not match the current carousel.')
        catalog=json.loads((self.root/'reference_sources'/'lead-magnets.json').read_text(encoding='utf-8'))['magnets']
        matches=[x for x in catalog if x.get('id')==offer_id]
        if len(matches)!=1:raise ValueError('Canonical offer is not in the local catalog.')
        offer=matches[0]
        for row in (cap,qr):
            if row['destination_url']!=offer.get('landing_url') or row['attribution']['asset_name']!=offer.get('name'):raise ValueError('Canonical offer/destination mismatch.')
        # Neutral approved Josh cover avoids mislabeled old product artwork.
        from .photo_carousels import load_builder
        m=load_builder();hashes={}
        for bg in ('bg1','bg2'):
            p=m.ROOT/'assets'/bg/'cover-1.png'
            if p.is_symlink() or not p.is_file():raise ValueError('Approved neutral Josh cover missing.')
            hashes[bg]=hashlib.sha256(p.read_bytes()).hexdigest()
        self.card=card;self.context=plan;self.hashes=hashes
        canonical=dict(destination=offer['landing_url'],name=offer['name'],description='Scan the code to learn more.',button='VIEW TOOLKIT',template='Neutral')
        b=dict(post_id=post_id,offer_id=offer_id,revision=card['revision'],label=card['title'],canonical=canonical,caption_input=plan['caption'],qr_input=plan['qr'])
        receipts={json.dumps(plan['caption'],sort_keys=True):cap_raw,json.dumps(plan['qr'],sort_keys=True):qr_raw}
        def cached(root,inp):return receipts[json.dumps(inp,sort_keys=True)]
        return LinkReceiptResolver(self.root,b,cached)(post_id,offer_id)
    def commit_check(self,c):
        if not self.card:raise ValueError('No verified Photo context.')
        row=c.execute('SELECT body FROM carousels WHERE id=?',(self.card['id'],)).fetchone()
        if not row or json.loads(row['body'])!=self.card:raise ValueError('Carousel changed during rendering.')
        from .photo_carousels import load_builder
        m=load_builder()
        for bg,digest in self.hashes.items():
            p=m.ROOT/'assets'/bg/'cover-1.png'
            if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise ValueError('Photo artwork changed during rendering.')
