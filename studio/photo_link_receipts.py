"""No-new-credential Linklr read transport; never trusts model final answers.
Only exact preview inputs supplied by host binding are allowed. Not deployed.
"""
import json,os,subprocess,sys,tempfile
from pathlib import Path
from .photo_link_guard import PREVIEW

def raw_preview(stream,exact_input):
    calls=set();results=[]
    for line in stream.splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        message=event.get('message')
        if not isinstance(message,dict):continue
        for item in message.get('content') or []:
            if not isinstance(item,dict):continue
            if item.get('type')=='tool_use' and item.get('name')==PREVIEW and item.get('input')==exact_input:calls.add(item.get('id'))
            elif item.get('type')=='tool_result' and item.get('tool_use_id') in calls:
                if item.get('is_error'):raise ValueError('Linklr preview failed.')
                results.append(item)
    if len(results)!=1:raise ValueError('Expected one exact authenticated Linklr preview result.')
    return results[0]

def read_preview(root,exact_input):
    from .agent import find_claude
    claude=find_claude(Path(root))
    if not claude:raise ValueError('Existing Claude integration unavailable.')
    if not isinstance(exact_input,dict) or not exact_input:raise ValueError('Host-bound preview inputs required.')
    with tempfile.TemporaryDirectory() as tmp:
        policy=Path(tmp)/'linklr-read.json';policy.write_text(json.dumps({'input':exact_input}),encoding='utf-8')
        hook=subprocess.list2cmdline([Path(sys.executable).as_posix(),Path(__file__).with_name('photo_link_guard.py').resolve().as_posix(),policy.as_posix()])
        cmd=[claude,'-p','Use '+PREVIEW+' exactly once with this input: '+json.dumps(exact_input)+'. Discover with ToolSearch if needed. Do not create or modify anything.', '--output-format','stream-json','--verbose','--no-session-persistence','--tools','ToolSearch','--allowedTools',PREVIEW+',ToolSearch','--permission-mode','dontAsk','--settings',json.dumps({'hooks':{'PreToolUse':[{'matcher':'*','hooks':[{'type':'command','command':hook}]}]}})]
        result=subprocess.run(cmd,cwd=str(root),capture_output=True,text=True,encoding='utf-8',timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('Linklr preview did not complete.')
    return raw_preview(result.stdout,exact_input)

def verified_existing(raw,exact_input):
    """Parse observed preview shape only; no guessed fallback or newly minted URL."""
    content=raw.get('content')
    if raw.get('is_error') or not isinstance(content,list) or len(content)!=1 or content[0].get('type')!='text':raise ValueError('Malformed Linklr result.')
    try:r=json.loads(content[0]['text'])
    except (ValueError,KeyError,TypeError):raise ValueError('Malformed Linklr JSON.') from None
    if r.get('valid') is not True or r.get('problems')!=[] or r.get('will_reuse_existing') is not True:raise ValueError('No verified existing link.')
    e=r.get('existing') or {};a=e.get('attribution') or {};p=r.get('proposed') or {}
    if e.get('reused_existing') is not True:raise ValueError('Existing link required.')
    for k,v in exact_input.items():
        if a.get(k)!=v or p.get(k)!=v:raise ValueError('Attribution mismatch: '+k)
    for k in ('tracked_url','destination_url'):
        if not isinstance(e.get(k),str) or e[k]!=p.get(k):raise ValueError('Destination/link mismatch.')
    if not e.get('link_id') or not e.get('slug') or e['tracked_url']!='https://linklr.app/'+e['slug']:raise ValueError('Link identity mismatch.')
    if a.get('asset_name')!=p.get('asset_name') or not a.get('asset_name'):raise ValueError('Canonical offer name missing.')
    return e

class LinkReceiptResolver:
    """Host-only binding: exact immutable card snapshot, verified canonical asset map,
    and optional owner-authorized shared-link decision. Never construct binding
    from model output. Caller must re-read card revision before committing.
    Canonical map must include destination, name, template, description and button;
    template printed product labels must be separately reviewed against the offer.
    """
    def __init__(self,root,binding,read=None):self.root=root;self.binding=binding;self.read=read or read_preview
    def __call__(self,post_id,offer_id):
        from datetime import datetime,timezone
        import hashlib
        b=self.binding
        if b['post_id']!=post_id or b['offer_id']!=offer_id:raise ValueError('Wrong post binding.')
        canon=b['canonical'];inputs=b['caption_input']
        if inputs['asset_id']!=offer_id or inputs['label']!=b['label']:raise ValueError('Wrong caption binding.')
        cap=verified_existing(self.read(self.root,inputs),inputs)
        def offer_ok(row):
            if row['destination_url']!=canon['destination'] or row['attribution']['asset_name']!=canon['name']:raise ValueError('Canonical destination/offer mismatch.')
        offer_ok(cap)
        shared=b.get('shared_authorization')
        if shared:
            if shared!={'post_id':post_id,'revision':b['revision'],'label':b['label'],'url':cap['tracked_url'],'mode':'shared','owner_message_id':b.get('owner_message_id')} or not b.get('owner_message_id'):raise ValueError('Shared URL lacks exact owner authorization.')
            qr=cap;mode='shared'
        else:
            qi=b.get('qr_input')
            if not qi or qi['label']!=b['label'] or qi['asset_id']!=offer_id or qi['placement']==inputs['placement']:raise ValueError('Distinct QR placement required.')
            qr=verified_existing(self.read(self.root,qi),qi);offer_ok(qr);mode='separate'
            if qr['tracked_url']==cap['tracked_url']:raise ValueError('Separate attribution cannot share a URL.')
        receipt=dict(post_id=post_id,offer_id=offer_id,destination=canon['destination'],label=canon['name'],description=canon['description'],button=canon['button'],template=canon['template'],source_response_id=hashlib.sha256(json.dumps([cap,qr],sort_keys=True).encode()).hexdigest(),verified_at=datetime.now(timezone.utc).isoformat(),attribution_mode=mode)
        for placement,row in [('caption',cap),('qr',qr)]:receipt[placement]=dict(url=row['tracked_url'],post_id=post_id,placement=placement,destination=canon['destination'],offer_id=offer_id)
        receipt['source_attribution']={'caption':cap['attribution'],'qr':qr['attribution'],'shared_authorization':shared}
        return receipt
