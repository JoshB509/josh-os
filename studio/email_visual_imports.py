"""Trusted PNG intake and revision-bound attachment. No model-controlled filesystem paths.

Only trusted local code can register reviewed PNG bytes and provenance. The existing
email visuals endpoint consumes the resulting opaque references; it cannot register files.
"""
import io
import json
import re
import uuid
from pathlib import Path

from .store import Conflict
from .visual_spec import MAX_VISUALS, MAX_PIXELS, MAX_BATCH_PIXELS

IMPORT_FIELDS = {'import_id', 'source_hash', 'png_hash', 'alt', 'after_line'}
IMPORT_SCHEMA = dict(type='object', additionalProperties=False, required=sorted(IMPORT_FIELDS),
    description='Attach a PNG already registered by trusted local review for this exact email and revision. Use only returned opaque references; paths, URLs and image bytes are never accepted here.',
    properties=dict(import_id=dict(type='string',pattern='^[0-9a-f]{32}$'),
                    source_hash=dict(type='string',pattern='^[0-9a-f]{64}$'),
                    png_hash=dict(type='string',pattern='^[0-9a-f]{64}$'),
                    alt=dict(type='string',maxLength=1000),after_line=dict(type='integer',minimum=0)))


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf8')


def png_size(png):
    from PIL import Image
    if not isinstance(png,bytes) or not 8 <= len(png) <= 10_000_000 or not png.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('Provide a complete PNG under 10 MB.')
    try:
        with Image.open(io.BytesIO(png)) as im:
            w,h=im.size
            if im.format!='PNG' or not (320<=w<=4096 and 320<=h<=4096 and w*h<=MAX_PIXELS and .25<=w/h<=4) or getattr(im,'n_frames',1)!=1:
                raise ValueError('PNG dimensions or frame count exceed visual limits.')
            im.verify()
        with Image.open(io.BytesIO(png)) as im:im.load()
    except (OSError,SyntaxError) as exc:
        raise ValueError('PNG is incomplete or invalid.') from exc
    return w,h


def location(visuals,eid,aid):
    if not isinstance(eid,str) or str(uuid.UUID(eid))!=eid or not isinstance(aid,str) or not re.fullmatch('[0-9a-f]{32}',aid):
        raise ValueError('Invalid trusted visual identifier.')
    root=Path(visuals.emails.store.root).resolve()/'email-visual-imports'
    path=root/eid/aid
    for candidate in (root,root/eid,path):
        if candidate.is_symlink() or (hasattr(candidate,'is_junction') and candidate.is_junction()) or candidate.resolve()!=candidate:
            raise ValueError('Trusted visuals cannot use redirected paths.')
    return path


def assert_draft(p,revision):
    if type(revision) is not int or p['revision']!=revision:
        raise Conflict('The email changed. Review its current revision before attaching visuals.')
    if p.get('archived') or p.get('status') not in ('Draft','Ready') or (p.get('kit') or {}).get('status') in ('scheduled','sending','completed'):
        raise ValueError('Trusted visuals can only be attached to an active unsent draft.')


def register_png(visuals,*,email_id,revision,png,source,alt,after_line):
    """Trusted local entry point, deliberately absent from agent schemas and HTTP routes."""
    from .email_visuals import metadata,sha
    p=visuals.emails.get(email_id);assert_draft(p,revision)
    metadata(dict(alt=alt,after_line=after_line),p['body'])
    if not isinstance(source,dict) or set(source)!={'source_asset_id','source_content_hash','design_hash','description'}:
        raise ValueError('Supply exact source identity, content hash, design hash and description.')
    if not isinstance(source['source_asset_id'],str) or not re.fullmatch('[A-Za-z0-9_:.-]{1,200}',source['source_asset_id']):
        raise ValueError('Source identity must be an opaque id, not a path or URL.')
    if any(not isinstance(source[k],str) or not re.fullmatch('[0-9a-f]{64}',source[k]) for k in ('source_content_hash','design_hash')):
        raise ValueError('Invalid source or design hash.')
    if not isinstance(source['description'],str) or not 1<=len(source['description'])<=1000:
        raise ValueError('Describe the reviewed visual source.')
    w,h=png_size(png);aid=uuid.uuid4().hex
    provenance=dict(kind='trusted-png',import_id=aid,email_id=email_id,base_revision=revision,
                    body_hash=sha(p['body'].encode('utf8')),width=w,height=h,png_hash=sha(png),source=source)
    ref=dict(import_id=aid,source_hash=sha(canonical(provenance)),png_hash=sha(png),alt=alt,after_line=after_line)
    assert_draft(visuals.emails.get(email_id),revision)
    folder=location(visuals,email_id,aid);folder.mkdir(parents=True,exist_ok=False)
    with (folder/'image.png').open('xb') as f:f.write(png)
    with (folder/'record.json').open('xb') as f:f.write(canonical(dict(source=provenance,reference=ref)))
    return ref


def load_png(visuals,p,item):
    from .email_visuals import metadata,sha
    if not isinstance(item,dict) or set(item)!=IMPORT_FIELDS:
        raise ValueError('Use a complete trusted visual reference, alt text and placement only.')
    metadata(item,p['body'])
    folder=location(visuals,p['id'],item['import_id'])
    for name,limit in [('record.json',20000),('image.png',10_000_000)]:
        f=folder/name
        if f.is_symlink() or f.resolve()!=f or not f.is_file() or f.stat().st_size>limit:
            raise ValueError('Trusted visual file is unavailable or invalid.')
    try:
        record=json.loads((folder/'record.json').read_text(encoding='utf8'))
        source=record['source'];reference=record['reference']
        if any(reference[k]!=item[k] for k in ('import_id','source_hash','png_hash')) or sha(canonical(source))!=item['source_hash']:
            raise ValueError('Trusted visual source identity or hash does not match.')
        if source['email_id']!=p['id'] or source['base_revision']!=p['revision'] or source['body_hash']!=sha(p['body'].encode('utf8')):
            raise Conflict('This trusted visual was reviewed for a different email revision.')
        png=(folder/'image.png').read_bytes()
        if sha(png)!=item['png_hash'] or sha(png)!=source['png_hash'] or png_size(png)!=(source['width'],source['height']):
            raise ValueError('Trusted PNG bytes changed after review.')
    except (OSError,KeyError,TypeError,json.JSONDecodeError) as exc:
        raise ValueError('Trusted visual record is unavailable or invalid.') from exc
    return source,png


def attach_pngs(visuals,p,items):
    from .email_visuals import sha
    assert_draft(p,p['revision'])
    if not isinstance(items,list) or not 1<=len(items)<=MAX_VISUALS:
        raise ValueError('Attach between one and sixteen trusted visuals.')
    sources=[];pngs=[];seen=set()
    for item in items:
        source,png=load_png(visuals,p,item)
        if item['import_id'] in seen:raise ValueError('Duplicate trusted visual.')
        seen.add(item['import_id']);sources.append(source);pngs.append(png)
    if sum(s['width']*s['height'] for s in sources)>MAX_BATCH_PIXELS:
        raise ValueError('Trusted visual set exceeds 40 million pixels.')
    version=uuid.uuid4().hex;folder=visuals.folder(p['id'],version);folder.mkdir(parents=True,exist_ok=False)
    images=[]
    for i,(source,png,item) in enumerate(zip(sources,pngs,items),1):
        file=folder/f'slide_{i:02d}.png'
        with file.open('xb') as f:f.write(png)
        if sha(file.read_bytes())!=item['png_hash']:raise ValueError('Trusted visual copy failed verification.')
        images.append(dict(version=version,index=i,source_hash=item['source_hash'],png_hash=item['png_hash'],alt=item['alt'],after_line=item['after_line']))
    with (folder/'manifest.json').open('xb') as f:f.write(canonical(dict(email_id=p['id'],base_revision=p['revision'],slides=sources,images=images)))
    # The existing transactional save preserves history and invalidates approval.
    # Concurrent edits win. Failed/orphan sets are not served by read_png.
    return visuals.emails.save(p['id'],{'images':images},p['revision'],sync_remote=False)
