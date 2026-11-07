"""Saved keying contract and isolated runner handoff; original time stays authoritative."""
import hashlib,json,math,os,subprocess,shutil
from pathlib import Path
from fractions import Fraction
from .timing import rate,frame
from .media import run,check,probe

POLYGON=[[.31,.02],[.62,.02],[.70,.42],[.79,.84],[1,.86],[1,1],[0,1],[0,.86],[.17,.84],[.24,.42]]
def defaults():
    return dict(enabled=False,start=0.,end=5.,handles=1.,height=1080,polygon=[p[:] for p in POLYGON],feather=4.,crop_bottom=.10,scale=1.1,x=.025,y=0.,despill=.5,preview=False)

def settings(value):
    if not isinstance(value,dict):raise ValueError('Keying settings must be an object')
    d=defaults()
    if set(value)-set(d):raise ValueError('Unknown keying setting')
    d.update(value)
    for k in ('enabled','preview'):
        if type(d[k]) is not bool:raise ValueError(k+' must be true or false')
    for k,lo,hi in [('start',0,86400),('end',0,86400),('handles',0,10),('feather',0,50),('crop_bottom',0,.45),('scale',.1,4),('x',-1,1),('y',-1,1),('despill',0,1)]:
        if type(d[k]) not in (int,float) or not math.isfinite(d[k]) or not lo<=d[k]<=hi:raise ValueError('Invalid keying '+k)
    if d['end']<=d['start']:raise ValueError('Keying end must be after start')
    if d['height'] not in (540,1080,2160):raise ValueError('Choose 540, 1080 or 2160 keying height')
    ps=d['polygon']
    if not isinstance(ps,list) or not 3<=len(ps)<=24:raise ValueError('Garbage mask needs 3 to 24 points')
    for p in ps:
        if not isinstance(p,list) or len(p)!=2 or any(type(n) not in (int,float) or not math.isfinite(n) or not 0<=n<=1 for n in p):raise ValueError('Mask points must be normalized source coordinates')
    area=abs(sum(ps[i][0]*ps[(i+1)%len(ps)][1]-ps[(i+1)%len(ps)][0]*ps[i][1] for i in range(len(ps))))/2
    if area<.01:raise ValueError('Garbage mask area is too small')
    return d

def digest(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for block in iter(lambda:f.read(1048576),b''):h.update(block)
    return h.hexdigest()
def identity(path):
    p=Path(path);s=p.stat();h=hashlib.sha256()
    with p.open('rb') as f:
        h.update(f.read(1048576));f.seek(max(0,s.st_size-1048576));h.update(f.read(1048576))
    return dict(path=str(p.resolve()),size=s.st_size,mtime_ns=s.st_mtime_ns,edge_sha256=h.hexdigest())
def write(p,data):
    p=Path(p);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(data,indent=2),encoding='utf8');tmp.replace(p)

def clock_audit(path,fps,first,last):
    """Audit only requested source PTS, before GPU work. Reject irregular VFR."""
    import av
    fps=rate(fps);expected=[Fraction(i,1)/fps for i in range(first,last)]
    with av.open(str(path)) as c:
        s=c.streams.video[0];tb=s.time_base
        # This installed workflow was verified with SDR BT.709 only. Reject HDR
        # or unknown transfers instead of silently interpreting them as SDR.
        if s.codec_context.color_trc!=1 or s.codec_context.color_primaries!=1:raise ValueError('CorridorKey input currently requires tagged BT.709 SDR media')
        if (s.start_time or 0)!=0:raise ValueError('Keying currently requires zero-start video timestamps; no silent timestamp reset')
        c.seek(int(expected[0]/tb),stream=s,backward=True)
        found=[]
        for p in c.demux(s):
            if p.pts is not None:
                t=p.pts*tb
                if expected[0]-tb/2<=t<Fraction(last,1)/fps-tb/2:found.append(t)
            if p.dts is not None and p.dts*tb>Fraction(last,1)/fps+2:break
        found.sort()
        if len(found)!=len(expected) or any(abs(a-b)>max(tb,Fraction(1,1000)) for a,b in zip(found,expected)) or len(set(found))!=len(found):
            raise ValueError('Selected source timestamps do not match its frame grid; irregular VFR keying is not implemented')
    return dict(frames=len(found),first_frame=first,end_frame_exclusive=last,fps=str(fps),packet_audit=True)

def prepare(root,job,video,config,ffmpeg,engine_path=None):
    root=Path(root);job=Path(job);k=settings(config);fps=rate(video['fps'])
    engine=Path(engine_path) if engine_path else root/'Engines'/'CorridorKey';python=engine/'venv'/'Scripts'/'python.exe'
    model=engine/'models'/'CorridorKey.safetensors';source=engine/'source'
    for p in (python,model,source/'CorridorKeyModule'/'inference_engine.py'):
        if not p.is_file():raise ValueError('Isolated CorridorKey installation is missing: '+str(p))
    if k['end']>video['duration']:raise ValueError('Keying range exceeds source duration')
    a=max(0,frame(k['start']-k['handles'],fps,'floor'));b=min(frame(video['duration'],fps,'floor'),frame(k['end']+k['handles'],fps,'ceil'))
    audit=clock_audit(video['path'],fps,a,b)
    height=min(video['height'],k['height']);height-=height%2;width=round(video['width']*height/video['height']/2)*2
    # Conservative headroom for source PNGs, EXRs, both transparent PNG streams,
    # review frames and encoded assets. Never fill the drive during a long key.
    needed=(b-a)*width*height*24+512*1024*1024
    for location in {root,job}:
        if shutil.disk_usage(location).free<needed:raise ValueError('Keying needs approximately %.1f GB of free working space here; shorten the range or lower keying height.'%(needed/1e9))
    fingerprint=identity(video['path'])
    # Raw-cache identity excludes post-key source mask/crop and framing so these
    # can be changed without inference. The coarse hint remains independently fixed.
    key=dict(source=fingerprint,first=a,last=b,fps=str(fps),width=width,height=height,despill=k['despill'],model_sha256=digest(model),engine_sha256=digest(source/'CorridorKeyModule'/'inference_engine.py'),runner_sha256=digest(root/'keying_runner.py'),hint_version='green-excess-broad-region-v1',input_transfer='bt709-to-srgb')
    cacheid=hashlib.sha256(json.dumps(key,sort_keys=True).encode()).hexdigest()
    folder=job/'keying';folder.mkdir(exist_ok=True)
    request=dict(job=str(job),engine=str(engine),ffmpeg=str(ffmpeg),cache=str(root/'Data'/'key-cache'/cacheid),identity=key,settings=k,clock_audit=audit)
    write(folder/'request.json',request)
    run([str(python),'-B',str(root/'keying_runner.py'),str(folder/'request.json')],job,'CorridorKey: source key, garbage mask and framing',timeout=max(600,(b-a)*30))
    if identity(video['path'])!=fingerprint:raise RuntimeError('Source changed while keying')
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf8'))
    for name in ('masked_media','raw_media'):
        p=Path(manifest[name]);info=probe(p)
        if info['fps']!=str(fps) or abs(info['duration']-(b-a)/float(fps))>1/float(fps):raise ValueError('Keyed asset clock mismatch')
        if digest(p)!=manifest[name+'_sha256']:raise ValueError('Keyed asset checksum mismatch')
    return manifest

def validate_coverage(mapping):
    k=mapping.get('keying')
    if not k:return
    for span in mapping['spans']:
        if span['source_in_frame']<k['first_frame'] or span['source_out_frame']>k['end_frame_exclusive']:raise ValueError('Edit exceeds keyed media coverage; key the needed source handles first')
    for name in ('masked_media','raw_media'):
        if not Path(k[name]).is_file() or digest(k[name])!=k[name+'_sha256']:raise ValueError('Keyed media missing or changed')
