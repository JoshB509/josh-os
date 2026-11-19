"""Persisted host-owned Photo bindings. No chat/model write API.
Enrollment must be by an authenticated owner UI handler, not agent output. The
entry includes exact source card revision and canonical offer/artwork reviewed
by owner. Missing bindings fail closed; no credential or permission changes.
"""
import hashlib,json
from pathlib import Path
from .photo_link_receipts import LinkReceiptResolver

class BoundPhotoReceipts:
    def __init__(self,tracker,root,read=None):self.tracker=tracker;self.root=root;self.read=read;self.binding=None
    def __call__(self,post_id,offer_id):
        with self.tracker.store.connect() as c:
            exists=c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='photo_host_bindings'").fetchone()
            if not exists:raise ValueError('Photo promotion needs a saved verified post/offer binding; no draft saved.')
            row=c.execute('SELECT body FROM photo_host_bindings WHERE post_id=? AND offer_id=?',(post_id,offer_id)).fetchone()
        if not row:raise ValueError('No verified Photo binding for this post and offer. Nonpromotional Photo drafts remain available.')
        b=json.loads(row['body']);self.binding=b
        self.check_artwork(b)
        with self.tracker.store.connect() as c:self.commit_check(c)
        resolver=LinkReceiptResolver(self.root,b,**({'read':self.read} if self.read else {}))
        return resolver(post_id,offer_id)
    @staticmethod
    def check_artwork(b):
        from .photo_carousels import load_builder
        m=load_builder();canon=b['canonical'];proof=b.get('artwork') or {}
        if proof.get('offer_id')!=b['offer_id'] or proof.get('destination')!=canon['destination'] or proof.get('canonical_name')!=canon['name'] or not proof.get('owner_review_id'):raise ValueError('Offer artwork not reviewed against canonical destination.')
        for bg in ('bg1','bg2'):
            p=m.ROOT/'assets'/bg/m.ENDINGS[canon['template']][bg][0]
            if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=proof.get('sha256',{}).get(bg):raise ValueError('Canonical offer artwork changed or missing.')
    def commit_check(self,c):
        b=self.binding
        if not b:raise ValueError('No Photo binding loaded.')
        row=c.execute('SELECT revision,body FROM carousels WHERE id=?',(b['post_id'],)).fetchone()
        if not row or row['revision']!=b['revision']:raise ValueError('Source carousel changed; verify Photo binding again.')
        card=json.loads(row['body'])
        if card.get('owner')!='sam' or card.get('archived') or card.get('stage') in ('Scheduled','Posted'):raise ValueError('Source card is not an editable Sam draft.')
        # Entire snapshot, not only its label, binds owner-approved attribution.
        digest=hashlib.sha256(json.dumps(card,sort_keys=True).encode()).hexdigest()
        if digest!=b.get('card_sha256'):raise ValueError('Photo binding does not match exact card.')
        current=c.execute('SELECT body FROM photo_host_bindings WHERE post_id=? AND offer_id=?',(b['post_id'],b['offer_id'])).fetchone()
        if not current or json.loads(current['body'])!=b:raise ValueError('Photo authorization changed during render.')
        self.check_artwork(b)
