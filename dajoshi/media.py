import json, os, subprocess, time
from pathlib import Path
import numpy as np
import av
from fractions import Fraction
from scipy.signal import correlate
from .export_clock import probe_clock

class Cancelled(Exception): pass

def check(job):
    if (Path(job)/'CANCEL').exists(): raise Cancelled('Cancelled; completed stages can be resumed')

def run(command, job, label, output=None, timeout=1800):
    check(job)
    with (Path(job)/'process.log').open('ab') as log:
        log.write(('\n'+label+'\n'+json.dumps(command)+'\n').encode()); log.flush()
        target=open(output,'wb') if output else log
        p=subprocess.Popen(command,stdout=target,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        started=time.monotonic()
        try:
            while p.poll() is None:
                check(job)
                if time.monotonic()-started>timeout:raise RuntimeError(label+' exceeded its safety timeout; see process.log')
                time.sleep(.1)
            if p.returncode: raise RuntimeError(label+' failed; see process.log')
        finally:
            if p.poll() is None:
                # Only this child process tree belongs to this stage. Never use
                # process-name termination (Chrome/OBS/Premiere can be user-owned).
                if os.name=='nt':
                    stopped=subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
                    if stopped.returncode and p.poll() is None:p.terminate()
                else:p.terminate()
                try: p.wait(timeout=5)
                except subprocess.TimeoutExpired: p.kill(); p.wait()
            if output: target.close()

def probe(path):
    with av.open(str(path)) as c:
        v=next(iter(c.streams.video),None); a=next(iter(c.streams.audio),None)
        duration=c.duration/av.time_base if c.duration else max(float(s.duration*s.time_base) for s in c.streams if s.duration)
        fps,clock=probe_clock(v.average_rate,v.base_rate,v.guessed_rate,duration) if v else (None,None)
        return dict(path=str(Path(path).resolve()),duration=duration,
                    fps=fps,clock=clock,
                    width=v.width if v else 0,height=v.height if v else 0,
                    sample_rate=a.rate if a else None,channels=a.channels if a else 0)

def decode(path, output, job, ffmpeg):
    partial=Path(output).with_suffix('.partial')
    run([ffmpeg,'-v','error','-nostdin','-threads','2','-i',str(path),'-vn','-ac','1','-ar','16000','-f','f32le','-'],job,'Decode '+Path(path).name,partial)
    partial.replace(output)
    return np.fromfile(output,dtype='<f4')

def envelope(pcm):
    n=len(pcm)//160*160
    return np.sqrt(np.mean(pcm[:n].reshape(-1,160)**2,axis=1)+1e-12)

def sync(camera, mic):
    """Broad-search normalized energy correlation: no supplied/hardcoded lag."""
    a,b=envelope(camera),envelope(mic); rows=[]; window=min(1200,len(a)//6)
    if window<200: raise ValueError('At least 12 seconds of reference audio is needed for reliable automatic sync')
    for fraction in [.12,.5,.85]:
        start=min(int(len(a)*fraction),len(a)-window); x=a[start:start+window]; x=x-x.mean()
        if np.std(x)<.0001: continue
        n=len(x); sums=np.r_[0,np.cumsum(b,dtype=float)]; squares=np.r_[0,np.cumsum(b*b,dtype=float)]
        c=correlate(b,x,mode='valid',method='fft')
        denom=np.sqrt(np.maximum(squares[n:]-squares[:-n]-(sums[n:]-sums[:-n])**2/n,1e-12)*np.sum(x*x))
        scores=c/denom; k=int(np.argmax(scores)); score=float(scores[k])
        alternative=scores.copy(); alternative[max(0,k-100):k+101]=-1
        second=float(alternative.max())
        coarse=(k-start)/100
        # Refine the envelope candidate with phase-insensitive raw correlation.
        # This avoids 10ms envelope-bin bias at an editorial frame boundary.
        lo=max(0,int((start/100+coarse-.05)*16000));hi=min(len(mic),int((start/100+coarse+window/100+.05)*16000))
        raw=camera[start*160:(start+window)*160].astype(float);raw-=raw.mean();candidate=mic[lo:hi].astype(float)
        refined=coarse;raw_score=0.0
        if len(candidate)>=len(raw):
            dots=correlate(candidate,raw,mode='valid',method='fft');nn=len(raw)
            sums2=np.r_[0,np.cumsum(candidate*candidate)];den=np.sqrt(np.maximum(sums2[nn:]-sums2[:-nn],1e-14)*np.sum(raw*raw))
            normalized=np.abs(dots)/den;best=int(np.argmax(normalized));raw_score=float(normalized[best])
            if raw_score>.15:refined=(lo+best)/16000-start/100
        rows.append(dict(camera_seconds=start/100,mic_minus_camera_seconds=refined,envelope_offset_seconds=coarse,raw_correlation=raw_score,correlation=score,alternative=second))
    if len(rows)<3: raise ValueError('Not enough distinct audible reference windows for sync')
    offsets=[r['mic_minus_camera_seconds'] for r in rows]
    verified=min(r['correlation'] for r in rows)>.45 and max(offsets)-min(offsets)<=.03 and all(r['correlation']-r['alternative']>.04 for r in rows)
    return dict(verified=verified,offset_seconds=float(np.median(offsets)),checks=rows,
                method='Three independent 10ms energy-envelope correlations, full lag search',native_lip_sync_verified=False)

def visual_offset(composite, reference, job):
    """Conservative local ±5s visual matching; uncertain matches stop assembly."""
    import cv2
    def frames(path,t,duration):
        result=[]
        with av.open(str(path)) as c:
            s=c.streams.video[0]; s.thread_count=1
            c.seek(max(0,int(t/float(s.time_base))),stream=s,backward=True)
            for f in c.decode(s):
                if f.time is None or f.time<t: continue
                if f.time>t+duration: break
                result.append((f.time,cv2.resize(f.to_ndarray(format='gray'),(480,270))))
        return result
    duration=min(probe(composite)['duration'],probe(reference)['duration']); rows=[]
    for fraction in [.12,.5,.85]:
        check(job); t=duration*fraction
        gs=frames(composite,t,.5); orig=frames(reference,max(0,t-5),10.8)
        if len(gs)<3 or len(orig)<3: raise ValueError('Insufficient composite/reference video frames')
        g=gs[len(gs)//2]; detector=cv2.SIFT_create(nfeatures=800)
        kp,des=detector.detectAndCompute(g[1],None)
        middle=orig[len(orig)//2]; kp2,des2=detector.detectAndCompute(middle[1],None)
        if des is None or des2 is None: raise ValueError('Cannot spatially align composite and reference')
        pairs=cv2.BFMatcher().knnMatch(des,des2,k=2)
        good=[m for pair in pairs if len(pair)==2 for m,n in [pair] if m.distance<.65*n.distance and 120<kp[m.queryIdx].pt[0]<360]
        if len(good)<12: raise ValueError('Composite/reference feature alignment is uncertain')
        H,inliers=cv2.estimateAffinePartial2D(np.float32([kp2[m.trainIdx].pt for m in good]),np.float32([kp[m.queryIdx].pt for m in good]),method=cv2.RANSAC)
        if H is None or int(inliers.sum())<10: raise ValueError('Composite/reference registration failed')
        stack=np.stack([x[1].astype(float) for x in gs]); variance=stack.var(axis=0)
        mask=variance>max(20,np.percentile(variance,95)); mask[:35,:]=False; mask[:, :100]=False; mask[:,380:]=False
        if mask.sum()<100: raise ValueError('Composite has insufficient motion for temporal verification')
        scores=[]
        for ts,f in orig:
            aligned=cv2.warpAffine(f,H,(480,270)).astype(float)
            scores.append((float(np.mean((g[1].astype(float)[mask]-aligned[mask])**2)),ts-g[0]))
        scores.sort(); best=scores[0]; alternatives=[s for s in scores if abs(s[1]-best[1])>.15]
        ratio=alternatives[0][0]/max(best[0],1e-6)
        rows.append(dict(composite_seconds=g[0],original_minus_composite_seconds=best[1],mse=best[0],separation_ratio=ratio,inliers=int(inliers.sum())))
    offsets=[x['original_minus_composite_seconds'] for x in rows]
    tolerance=max(1/float(Fraction(probe(composite)['fps'])),1/float(Fraction(probe(reference)['fps'])))+.005
    return dict(verified=max(offsets)-min(offsets)<=tolerance and min(r['separation_ratio'] for r in rows)>1.08,
                offset_seconds=float(np.median(offsets)),checks=rows,method='SIFT spatial registration and moving-pixel temporal match ±5s')

def transcribe(path, model_dir, job):
    os.environ['HF_HUB_OFFLINE']='1'; os.environ['TRANSFORMERS_OFFLINE']='1'
    from faster_whisper import WhisperModel
    if not (Path(model_dir)/'model.bin').is_file(): raise ValueError('Local Whisper model is unavailable; downloads are disabled')
    model=WhisperModel(model_dir,device='cpu',compute_type='int8',cpu_threads=2,num_workers=1,local_files_only=True)
    segments,info=model.transcribe(str(path),beam_size=3,word_timestamps=True,vad_filter=True,language='en')
    out=[]
    for s in segments:
        check(job)
        out.append(dict(start=s.start,end=s.end,text=s.text,words=[dict(start=w.start,end=w.end,word=w.word,probability=w.probability) for w in s.words]))
        print('Transcribed %.1fs'%s.end,flush=True)
    return out
