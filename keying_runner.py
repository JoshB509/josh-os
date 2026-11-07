"""Offline isolated CorridorKey worker. Invoked only with a saved local request."""
import os,sys,json,time,hashlib,socket,subprocess
from pathlib import Path
from contextlib import contextmanager
from fractions import Fraction

def write(p,value):
    p=Path(p);t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(value,indent=2),encoding='utf8');t.replace(p)
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for block in iter(lambda:f.read(1048576),b''):h.update(block)
    return h.hexdigest()
@contextmanager
def lock(path):
    import msvcrt
    with Path(path).open('a+b') as f:
        if not f.tell():f.write(b'0');f.flush()
        f.seek(0)
        try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise RuntimeError('Another keying job owns the GPU/cache. Resume after it finishes.')
        try:yield
        finally:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)

def main():
    req=json.loads(Path(sys.argv[1]).read_text(encoding='utf8'));job=Path(req['job']);folder=job/'keying';engine_root=Path(req['engine']);cache=Path(req['cache']);cache.mkdir(parents=True,exist_ok=True)
    ident=req['identity'];k=req['settings'];count=ident['last']-ident['first'];fps=Fraction(ident['fps']);w,h=ident['width'],ident['height'];ff=req['ffmpeg']
    def check(obs=False):
        if (job/'CANCEL').exists():raise RuntimeError('Cancelled; verified frame cache can be resumed')
        if obs:
            p=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',"[System.Diagnostics.Process]::GetProcessesByName('obs64').Count"],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
            if p.returncode or p.stdout.strip()!='0':raise RuntimeError('OBS is open or unavailable to inspect. Keying paused to protect recording.')
    def progress(stage,n):
        write(folder/'progress.json',dict(stage=stage,frames_complete=n,total_frames=count,message=f'{stage}: {n}/{count} frames'))
    def command(args):
        check(True)
        with (folder/'runner.log').open('ab') as log:
            p=subprocess.Popen([ff,'-v','error','-nostdin','-y']+args,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                while p.poll() is None:check();time.sleep(.1)
                if p.returncode:raise RuntimeError('FFmpeg keying stage failed; see keying/runner.log')
            finally:
                if p.poll() is None:p.terminate();p.wait(timeout=10)
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',CORRIDORKEY_SKIP_COMPILE='1',OPENCV_IO_ENABLE_OPENEXR='1',HF_HOME=str(engine_root/'cache'/'huggingface'),TORCH_HOME=str(engine_root/'cache'/'torch'))
    def deny(*a,**kw):raise RuntimeError('Network access is disabled in the isolated keying worker')
    socket.socket.connect=deny;socket.create_connection=deny
    sys.path.insert(0,str(engine_root/'source'))
    import cv2
    import numpy as np
    import torch
    from CorridorKeyModule import CorridorKeyEngine
    from CorridorKeyModule.core import color_utils as cu
    cv2.setNumThreads(2);torch.set_num_threads(2)
    if not cv2.haveImageWriter('.exr'):raise RuntimeError('Installed EXR codec unavailable')
    def png(p,rgb):
        data=np.round(np.clip(rgb,0,1)*65535).astype(np.uint16)
        data=data[:,:,[2,1,0,3]] if data.shape[2]==4 else data[:,:,::-1]
        if not cv2.imwrite(str(p),data):raise RuntimeError('PNG write failed')
    with lock(engine_root/'integration-gpu.lock'):
        check(True)
        if (cache/'identity.json').exists() and json.loads((cache/'identity.json').read_text())!=ident:raise RuntimeError('Cache identity mismatch')
        write(cache/'identity.json',ident)
        for name in ['decoded','raw']:(cache/name).mkdir(exist_ok=True)
        for name in ['raw_png','masked_png','review']:(folder/name).mkdir(exist_ok=True)
        # Decode again on resume; frames are cheap, and stale partial PNGs are never trusted.
        progress('Decoding source',0)
        command(['-threads','2','-ss',str(float(ident['first']/fps)),'-i',ident['source']['path'],'-an','-frames:v',str(count),'-vf',f'scale={w}:{h}:flags=lanczos','-fps_mode','passthrough','-threads','2',str(cache/'decoded'/'%06d.png')])
        entries={};index=cache/'frames.json'
        if index.exists():entries=json.loads(index.read_text())
        roi=np.zeros((h,w),np.uint8)
        fixed=np.array([[.31,.02],[.62,.02],[.70,.42],[.79,.84],[1,.86],[1,1],[0,1],[0,.86],[.17,.84],[.24,.42]])
        cv2.fillPoly(roi,[(fixed*[w,h]).astype(np.int32)],255)
        garbage=np.zeros((h,w),np.float32);cv2.fillPoly(garbage,[(np.array(k['polygon'])*[w,h]).astype(np.int32)],1)
        if k['feather']:garbage=cv2.GaussianBlur(garbage,(0,0),k['feather']*h/1080)
        garbage[int(h*(1-k['crop_bottom'])):]=0
        model=None;reused=0;started=time.monotonic()
        for i in range(1,count+1):
            check(i%10==1);name=f'{i:06d}';src=cache/'decoded'/(name+'.png');raw=cache/'raw'/(name+'.exr')
            pixels=cv2.imread(str(src),cv2.IMREAD_UNCHANGED)
            if pixels is None or pixels.shape!=(h,w,3):raise RuntimeError('Decoded source frame missing or wrong size')
            rgb=pixels[:,:,::-1].astype(np.float32)/255
            lin=np.where(rgb<.081,rgb/4.5,((rgb+.099)/1.099)**(1/.45));rgb=np.clip(cu.linear_to_srgb(lin),0,1)
            entry=entries.get(name,{});source_sha=sha(src)
            if raw.exists() and entry.get('input_sha256')==source_sha and entry.get('raw_sha256')==sha(raw):
                rgba=cv2.imread(str(raw),cv2.IMREAD_UNCHANGED)[:,:,[2,1,0,3]];reused+=1
            else:
                if model is None:
                    if not torch.cuda.is_available():raise RuntimeError('CUDA is unavailable; no silent CPU fallback')
                    checkpoint=engine_root/'models'/'CorridorKey.safetensors'
                    if sha(checkpoint)!=ident['model_sha256']:raise RuntimeError('Model checksum changed')
                    model=CorridorKeyEngine(str(checkpoint),device='cuda',img_size=2048,mixed_precision=True)
                hint=(1-np.clip((rgb[:,:,1]-np.maximum(rgb[:,:,0],rgb[:,:,2])-.025)/.13,0,1))*roi/255
                hint=cv2.GaussianBlur(hint,(5,5),0)
                with torch.inference_mode():result=model.process_frame(rgb,hint,input_is_linear=False,despill_strength=k['despill'],auto_despeckle=False,generate_comp=False,post_process_on_gpu=False,screen_channel=1)
                rgba=result['processed'];rgba[:,:,3]=np.clip(rgba[:,:,3],0,1)
                if not np.isfinite(rgba).all():raise RuntimeError('Non-finite model output')
                temp=raw.with_suffix('.partial.exr')
                if not cv2.imwrite(str(temp),rgba[:,:,[2,1,0,3]].astype(np.float32),[cv2.IMWRITE_EXR_TYPE,cv2.IMWRITE_EXR_TYPE_HALF]):raise RuntimeError('EXR write failed')
                temp.replace(raw);entries[name]=dict(input_sha256=source_sha,raw_sha256=sha(raw));write(index,entries)
                # Use persisted half-float master consistently on first runs and resumes.
                rgba=cv2.imread(str(raw),cv2.IMREAD_UNCHANGED)[:,:,[2,1,0,3]]
            alpha=np.clip(rgba[:,:,3:4],0,1);straight=cu.linear_to_srgb(np.maximum(rgba[:,:,:3],0)/np.maximum(alpha,1e-6));straight=np.clip(straight,0,1)
            masked=alpha*garbage[:,:,None]
            png(folder/'raw_png'/(name+'.png'),np.concatenate([straight,alpha],2));png(folder/'masked_png'/(name+'.png'),np.concatenate([straight,masked],2))
            # Framing is preview-only here. Editable source framing lives inside the XML nest.
            fit=min(960/w,540/h)*k['scale'];matrix=np.array([[fit,0,480+k['x']*960-w*fit/2],[0,fit,270+k['y']*540-h*fit/2]],np.float32)
            premult=cu.srgb_to_linear(straight)*masked
            fg=cv2.warpAffine(premult,matrix,(960,540));a=cv2.warpAffine(masked,matrix,(960,540))[:,:,None]
            light=cu.linear_to_srgb(fg+cu.srgb_to_linear(np.float32(.9))*(1-a));dark=cu.linear_to_srgb(fg+cu.srgb_to_linear(np.float32(.06))*(1-a))
            tiles=[cv2.resize(rgb,(960,540)),light,dark,np.repeat(a,3,axis=2)]
            grid=np.concatenate([np.concatenate(tiles[:2],1),np.concatenate(tiles[2:],1)],0)
            cv2.imwrite(str(folder/'review'/(name+'.png')),np.round(np.clip(grid[:,:,::-1],0,1)*255).astype(np.uint8))
            if i==1:png(folder/'source.png',rgb)
            progress('Keying and source mask',i)
        progress('Encoding transparent media',count)
        for name in ['raw','masked']:
            target=folder/(name+'.mov');partial=folder/(name+'.partial.mov')
            command(['-framerate',str(fps),'-i',str(folder/(name+'_png')/'%06d.png'),'-frames:v',str(count),'-an','-c:v','prores_ks','-profile:v','4','-pix_fmt','yuva444p10le','-alpha_bits','16','-threads','2','-color_primaries','bt709','-color_trc','iec61966-2-1','-colorspace','bt709',str(partial)]);partial.replace(target)
        partial=job/'KEYING_PREVIEW.partial.mp4'
        command(['-framerate',str(fps),'-i',str(folder/'review'/'%06d.png'),'-frames:v',str(count),'-an','-c:v','libx264','-preset','fast','-crf','21','-threads','2','-pix_fmt','yuv420p','-movflags','+faststart',str(partial)]);partial.replace(job/'KEYING_PREVIEW.mp4')
        manifest=dict(first_frame=ident['first'],end_frame_exclusive=ident['last'],fps=str(fps),frames=count,width=w,height=h,settings=k,raw_media=str(folder/'raw.mov'),masked_media=str(folder/'masked.mov'),raw_media_sha256=sha(folder/'raw.mov'),masked_media_sha256=sha(folder/'masked.mov'),cache=str(cache),reused_frames=reused,seconds=time.monotonic()-started,alpha='straight',transfer='sRGB',source=ident['source'],native_import_verified=False,approved=False)
        write(folder/'manifest.json',manifest);progress('Keying ready',count)
        print(json.dumps(dict(frames=count,reused_frames=reused,manifest=str(folder/'manifest.json'))),flush=True)

if __name__=='__main__':main()
