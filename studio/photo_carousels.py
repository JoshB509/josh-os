"""Host-only Photo carousel draft rendering; no remote or publication actions.
Existing approved BG1/BG2 templates, never arbitrary model asset paths.
Promotional endings require a host-authenticated post/placement receipt.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid
from PIL import Image
from . import carousels, carousel_jobs

BUILDER = Path(r'E:\CareerOS\03_Community_Posts\Carousel-Engine\02_Builder\build.py')
SCHEMA = {'type':'object','additionalProperties':False,
 'required':['title','style','cover','slides','caption'], 'properties':{
 'title':{'type':'string','minLength':1,'maxLength':240},
 'style':{'type':'string','enum':['Photo BG1','Photo BG2']},
 'cover':{'type':'string','enum':['cover-1','cover-2','cover-3']},
 'caption':{'type':'string','maxLength':5000},
 'slides':{'type':'array','minItems':2,'maxItems':10,'items':{'type':'object','additionalProperties':False,
 'required':['headline','body'],'properties':{'headline':{'type':'string','minLength':1,'maxLength':220},'body':{'type':'string','maxLength':700}}}}}}

SCHEMA['properties']['promotion'] = {'type':'object','additionalProperties':False,'required':['post_id','offer_id'],'properties':{'post_id':{'type':'string'},'offer_id':{'type':'string'}}}

def requested(owner, message, proactive=False):
    # Positive current creation request, plus explicit photo-carousel intent.
    from .local_asset_chat import requested as visual_request
    return owner == 'sam' and not proactive and bool(re.search(r'\b(?:photo|photos|bg1|bg2)\b',message,re.I)) and bool(re.search(r'\bcarousels?\b',message,re.I)) and visual_request(re.sub(r'\bcarousels?\b','slides',message,flags=re.I))

def validate(data):
    if not isinstance(data,dict) or not set(SCHEMA['required']) <= set(data) or set(data)-set(SCHEMA['required'])-{'promotion'}:raise ValueError('Use only the Photo draft fields.')
    if data['style'] not in ('Photo BG1','Photo BG2') or data['cover'] not in ('cover-1','cover-2','cover-3'):raise ValueError('Choose an approved Photo style and cover.')
    for key,limit in [('title',240),('caption',5000)]:
        if not isinstance(data[key],str) or len(data[key])>limit:raise ValueError('Invalid '+key)
    if not data['title'].strip():raise ValueError('Title required.')
    if not isinstance(data['slides'],list) or not 2<=len(data['slides'])<=10:raise ValueError('Use 2 to 10 slides.')
    for s in data['slides']:
        if not isinstance(s,dict) or set(s)!={'headline','body'}:raise ValueError('Use headline and body only.')
        for k,n in [('headline',220),('body',700)]:
            if not isinstance(s[k],str) or len(s[k])>n:raise ValueError('Invalid slide '+k)
        if not s['headline'].strip():raise ValueError('Every slide needs a headline.')
    if 'promotion' in data:
        pr=data['promotion']
        if not isinstance(pr,dict) or set(pr)!={'post_id','offer_id'} or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',v) for v in pr.values()):raise ValueError('Exact post and offer IDs required.')
    text=json.dumps(data)
    if re.search(r'https?://|www\.|\[QR\]|<|>|\n-{3}',text,re.I):raise ValueError('URLs, QR codes and markup need a separate verified workflow.')
    return data

def load_builder():
    spec=importlib.util.spec_from_file_location('approved_photo_builder',BUILDER)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def render(data, output, receipt=None):
    validate(data)
    from playwright.sync_api import sync_playwright
    m=load_builder();bg='bg1' if data['style']=='Photo BG1' else 'bg2'
    # Only these fixed template paths are read, no user supplied files or URLs.
    for name in (data['cover']+'.png','text.png'):
        p=m.ROOT/'assets'/bg/name
        if not p.is_file() or p.is_symlink():raise ValueError('Approved photo missing.')
    slides=[dict(n=i+1,kind='hook' if i==0 else 'body',headline=s['headline'],body=s['body'],eyebrow='',button='') for i,s in enumerate(data['slides'])]
    if data.get('promotion'):
        from .photo_promotion import validate_receipt
        receipt=validate_receipt(data,receipt)
        if receipt['template']=='Neutral':m.ENDINGS['Neutral']={k:('cover-1.png',m.BG_TONE[k],520) for k in ('bg1','bg2')}
        end=m.ROOT/'assets'/bg/m.ENDINGS[receipt['template']][bg][0]
        if not end.is_file() or end.is_symlink():raise ValueError('Approved ending missing.')
        slides.append(dict(n=len(slides)+1,kind='close',headline=receipt['label'],body=receipt['description'],eyebrow='',button=receipt['button']))
    html=m.page({'slides':slides,'magnet':receipt['template'] if receipt else 'SCR'},bg,data['cover']+'.png')
    with sync_playwright() as pw:
        try:browser=pw.chromium.launch()
        except Exception:browser=pw.chromium.launch(channel='chrome')
        try:
            page=browser.new_page(viewport={'width':1080,'height':1080})
            page.set_content(html,wait_until='load');page.evaluate(m.FIT_JS)
            if receipt:
                from .photo_promotion import qr_data_uri
                page.eval_on_selector('#qr', '(el,src)=>{el.style.borderRadius=0;el.innerHTML="";const img=document.createElement("img");img.src=src;img.style.width="100%";img.style.height="100%";el.appendChild(img)}',qr_data_uri(receipt['qr']['url']))
                page.wait_for_function('Array.from(document.images).every(i=>i.complete)')
            els=page.query_selector_all('.slide')
            if len(els)!=len(slides):raise ValueError('Incomplete Photo layout.')
            for i,el in enumerate(els):
                if el.get_attribute('data-overflow')=='1':raise ValueError('Photo text overflows; shorten slide '+str(i+1))
                el.screenshot(path=str(output/f'slide_{i+1:02d}.png'),type='png')
            if receipt:
                from .photo_promotion import verify_final
                verify_final(output/f'slide_{len(slides):02d}.png',receipt['qr']['url'])
        finally:browser.close()

class PhotoDrafts:
    def __init__(self,tracker,receipt_resolver=None):self.tracker=tracker;self.receipt_resolver=receipt_resolver
    def apply(self,data,*,owner,message,proactive=False,request_id=None):
        if request_id is None:
            return self._apply(data,owner=owner,message=message,proactive=proactive)
        if owner != 'sam':
            raise ValueError('Only Sam can create Photo carousel drafts.')
        # One immutable request batch across Photo and ordinary carousel dispatch.
        _,job,fresh=self.tracker.jobs.batch(request_id,[{'photo_draft':data}],None)[0]
        if fresh:
            try:
                with job.active():
                    job.mark('preparing')
                    self._apply(data,owner=owner,message=message,proactive=proactive,
                                target_id=job.record['carousel_id'])
            except Exception as exc:
                job.fail(exc)
        return job.receipt()

    def _apply(self,data,*,owner,message,proactive=False,target_id=None):
        if not requested(owner,message,proactive):raise ValueError('Ask Sam to create a Photo carousel in this message.')
        validate(data)
        receipt=None
        if data.get('promotion'):
            from .photo_promotion import validate_receipt
            if self.receipt_resolver is None:raise ValueError('Authenticated Linklr receipt adapter not connected; no promotional draft saved.')
            receipt=validate_receipt(data,self.receipt_resolver(data['promotion']['post_id'],data['promotion']['offer_id']))
        key=hashlib.sha256(json.dumps({'data':data,'receipt':{k:v for k,v in receipt.items() if k!='verified_at'} if receipt else None},sort_keys=True).encode()).hexdigest()
        cid=target_id or str(uuid.uuid5(uuid.NAMESPACE_URL,'joshos-photo-draft:'+key))
        try:
            existing=self.tracker.get(cid)
        except KeyError:
            existing=None
        if existing is not None:
            if target_id is not None:
                raise carousels.Conflict('This Photo draft was created in another window.')
            if existing.get('design',{}).get('request_hash') != key:
                raise ValueError('Draft identity conflict.')
            return existing
        root=Path(carousels.SLIDE_ROOT).resolve()/'_PhotoDrafts'
        root.mkdir(exist_ok=True)
        folder=root/uuid.uuid4().hex;folder.mkdir()
        # New version directory only. Failed output is retained, never attached.
        job=folder/'job.json';job.write_text(json.dumps({'data':data,'receipt':receipt}),encoding='utf-8')
        carousel_jobs.mark('rendering')
        try:
            run=subprocess.run([sys.executable,'-m','studio.photo_carousels',str(job)],cwd=str(Path(__file__).resolve().parent.parent),capture_output=True,text=True,timeout=180)
        except subprocess.TimeoutExpired:
            raise ValueError('Photo rendering timed out; no draft saved.') from None
        if run.returncode:raise ValueError('Photo render failed: '+run.stderr[-400:])
        expected=[folder/f'slide_{i+1:02d}.png' for i in range(len(data['slides'])+bool(receipt))]
        if sorted(folder.glob('*.png'))!=expected:raise ValueError('Incomplete Photo images.')
        hashes=[]
        for p in expected:
            with Image.open(p) as image:
                if image.format!='PNG' or image.size!=(1080,1080):raise ValueError('Invalid Photo output.')
                image.verify()
            hashes.append(hashlib.sha256(p.read_bytes()).hexdigest())
        if receipt:
            from .photo_promotion import verify_final
            verify_final(expected[-1],receipt['qr']['url'])
        (folder/'manifest.json').write_text(json.dumps({'id':cid,'slides':hashes,'request':key,'draft_only':True,'link_receipt':receipt}),encoding='utf-8')
        carousel_jobs.mark('saving')
        return self.tracker.create(dict(id=cid,owner='sam',title=data['title'],stage='Drafting',
            slides='\n---\n'.join(s['headline']+'\n'+s['body'] for s in data['slides']),hook=data['slides'][0]['headline'],
            caption=data['caption']+(' '+receipt['caption']['url'] if receipt else ''),folder=str(folder),tags={'style':data['style'],'slide_count':len(expected)},
            notes='Photo draft only. '+('Post-scoped QR verified from final slide; no scheduling or publication.' if receipt else 'No CTA/QR, link, schedule or publication has been created.'),
            design={'style':data['style'],'photo_version':folder.name,'request_hash':key}), commit_check=getattr(self.receipt_resolver,'commit_check',None) if receipt else None)

if __name__=='__main__':
    job=Path(sys.argv[1]);payload=json.loads(job.read_text(encoding='utf-8'));render(payload.get('data',payload),job.parent,payload.get('receipt'))
