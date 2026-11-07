"""Offline XMEML schema and semantic validation. Does not certify Premiere."""
import io
from pathlib import Path
from urllib.parse import urlsplit,unquote
from lxml import etree

DTD=Path(__file__).resolve().parents[1]/'specs'/'fcp-xml-v5.dtd'

def validate(data,require_files=True):
    root=etree.fromstring(data,etree.XMLParser(resolve_entities=False,no_network=True))
    schema=etree.DTD(str(DTD));schema.assertValid(root)
    seq=root.find('sequence')
    if seq is None:raise ValueError('Export must contain one sequence')
    duration=int(seq.findtext('duration'));definitions={};clips={};locations={}
    for f in root.findall('.//file'):
        if len(f):
            if f.get('id') in definitions:raise ValueError('Duplicate full file definition')
            definitions[f.get('id')]=f
    for kind in ['video','audio']:
        for ti,track in enumerate(seq.findall('media/'+kind+'/track'),1):
            for ci,c in enumerate(track.findall('clipitem'),1):
                cid=c.get('id')
                if cid in clips:raise ValueError('Duplicate clip ID')
                clips[cid]=c;locations[cid]=(kind,ti,ci)
    for cid,c in clips.items():
        a,b,i,o=[int(c.findtext(k)) for k in ['start','end','in','out']]
        if not 0<=a<b<=duration or not 0<=i<o or b-a!=o-i:raise ValueError('Invalid clip bounds '+cid)
        source=c.find('file')
        if source is not None:
            fid=source.get('id')
            if fid not in definitions:raise ValueError('Unresolved file '+fid)
            f=definitions[fid]
        else:
            source=c.find('sequence')
            if source is None:raise ValueError('Clip has no source')
            matches=[n for n in root.findall('.//sequence') if n.get('id')==source.get('id') and len(n)]
            if len(matches)!=1:raise ValueError('Missing/duplicate nested sequence definition')
            f=matches[0]
        if o>int(f.findtext('duration')):raise ValueError('Clip exceeds full source duration '+cid)
        for link in c.findall('link'):
            target=link.findtext('linkclipref')
            if target not in locations:raise ValueError('Dangling clip link')
            declared=(link.findtext('mediatype'),int(link.findtext('trackindex')),int(link.findtext('clipindex')))
            if declared!=locations[target]:raise ValueError('Link points at the wrong track/clip')
    # Source-framing nests may contain only bounded local file clips. Reject
    # recursive/unsupported nesting instead of silently overlooking its timing.
    for nested in root.findall('.//clipitem/sequence'):
        if not len(nested):continue
        nd=int(nested.findtext('duration'))
        for c in nested.findall('.//clipitem'):
            if c.find('sequence') is not None:raise ValueError('Recursive nesting is unsupported')
            a,b,i,o=[int(c.findtext(k)) for k in ['start','end','in','out']]
            f=definitions.get(c.find('file').get('id'))
            if f is None or not 0<=a<b<=nd or not 0<=i<o<=int(f.findtext('duration')) or b-a!=o-i:raise ValueError('Invalid nested source bounds')
    referenced=[]
    for f in definitions.values():
        url=f.findtext('pathurl');u=urlsplit(url)
        if u.scheme!='file' or u.netloc not in ['', 'localhost']:raise ValueError('Only local file URLs are allowed')
        path=unquote(u.path)
        if len(path)>3 and path[0]=='/' and path[2]==':':path=path[1:]
        if require_files and not Path(path).is_file():raise ValueError('Missing referenced media: '+path)
        referenced.append(path)
    for track in seq.findall('media/audio/track')+seq.findall('media/video/track')[1:]:
        cursor=0
        for c in track.findall('clipitem'):
            if int(c.findtext('start'))!=cursor:raise ValueError('Unexpected gap/overlap in camera or speech track')
            cursor=int(c.findtext('end'))
        if cursor!=duration:raise ValueError('Camera/speech track does not cover sequence')
    return dict(dtd_valid=True,semantic_checks=True,clip_count=len(clips),file_count=len(definitions),referenced_files=referenced,native_import_verified=False)
