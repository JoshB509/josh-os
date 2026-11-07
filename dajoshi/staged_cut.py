"""Staged local cut/review jobs. No model, publication, keying or inferred approval."""
import copy,hashlib,json,math,wave
from fractions import Fraction
from pathlib import Path
import numpy as np
from .timing import rate,frame,build_map,subtract
from .clean_cut import validate_map,remap_words,digest
from .media import probe,decode,run,check
from .export import export_xml,preview

def write(path,value):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,indent=2),encoding='utf-8');tmp.replace(path)

def context(cfg,root):
    from studio.store import Store,Conflict
    from studio.jobs import Jobs
    from studio.workflow import Workflow
    snap=cfg['workflow'];store=Store(snap['data_root']);w=Workflow(store,root,Jobs(store,root))
    p=w._current(snap['project_id'],snap['project_revision']);a=w.get(p['id'],snap['artifact_id'])
    if a['digest']!=snap['digest']:raise Conflict('Workflow snapshot changed; start a new run')
    error=w.health(p,a)
    if error:raise Conflict(error)
    latest=next(x for x in w.artifacts(p['id']) if x['kind']==a['kind'])
    if latest['id']!=a['id']:raise Conflict('Newer workflow candidate exists; start a new run')
    if cfg['workflow_mode']=='silence':
        if a['kind']!='pass1' or not w.approved(p,a):raise Conflict('Current Pass 1 approval is required')
    elif cfg['workflow_mode']=='review':
        if a['kind']!='clean':raise ValueError('Review requires an exact clean-cut candidate')
    else:raise ValueError('Unknown staged mode')
    return w,p,a

def silence_map(video,audio,words,pcm,offset):
    """Remove only long measured quiet intervals outside padded words.

    Begin with ALL source media, so untranscribed sounds and deliberate repeats
    survive. This does not invoke legacy repeated-prefix/script heuristics.
    """
    sr=16000;hop=160;fps=rate(video['fps']);start=max(0,-offset);end=min(video['duration'],audio['duration']-offset)
    if len(pcm)<hop:raise ValueError('Microphone decode is too short')
    energy=np.sqrt(np.mean(pcm[:len(pcm)//hop*hop].reshape(-1,hop)**2,axis=1)+1e-14)
    threshold=max(.0005,min(.002,float(np.percentile(energy,20))*2))
    protected=[[max(start,float(w['start'])-offset-.18),min(end,float(w['end'])-offset+.22)] for w in words]
    quiet=energy<threshold;edges=np.diff(np.r_[False,quiet,False].astype(np.int8));removes=[];decisions=[]
    for lo,hi in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)):
        a=max(start,lo/100-offset);b=min(end,hi/100-offset)
        if b-a<.75:continue
        for x,y in subtract([[a+.16,b-.16]],protected):
            if y-x<.35:continue
            removes.append([x,y]);decisions.append(dict(kind='measured_silence',start=x,end=y,coordinate_space='source_video_seconds',reason='Continuous low RMS outside protected word handles'))
    keeps=subtract([[start,end]],removes)
    mapping=build_map(keeps,video['fps'],offset,video['duration'],audio['duration'])
    mapping.update(source_video=video,source_audio=audio)
    return mapping,dict(automatic=decisions,semantic_decisions=[],threshold_rms=threshold,
        method='Silence only; untranscribed sound and repeated wording retained',word_handles_seconds=[.18,.22])

def sample_map(mapping,sample_rate):
    fps=rate(mapping['fps']);offset=Fraction(str(mapping['audio_minus_video_seconds']))
    nearest=lambda n:math.floor(n+Fraction(1,2))
    rows=[]
    for s in mapping['spans']:
        x=nearest(Fraction(s['out_in_frame']*sample_rate,1)/fps);y=nearest(Fraction(s['out_out_frame']*sample_rate,1)/fps)
        a=nearest((Fraction(s['source_in_frame'],1)/fps+offset)*sample_rate)
        boundary=nearest((Fraction(s['source_out_frame'],1)/fps+offset)*sample_rate)
        rows.append(dict(output_start=x,output_end=y,source_start=a,source_end=min(boundary,a+y-x),padding_samples=max(0,y-x-(boundary-a))))
    return rows,nearest(Fraction(mapping['duration_frames']*sample_rate,1)/fps)

def join_audio(mapping,pcm,sr,words,rejections,fade_ms=0):
    """Fixed-clock linear fades using explicit quiet pre/post handles only.

    The video map never changes. Both sides of the whole envelope must be quiet,
    outside speech and semantic rejections. A failed check retains a hard join.
    XMEML does NOT carry these envelopes; this is rendered review audio only.
    """
    if type(fade_ms) not in (int,float) or not math.isfinite(fade_ms) or not 0<=fade_ms<=20:raise ValueError('Review fade must be 0-20 ms')
    validate_map(mapping);pcm=np.asarray(pcm)
    if pcm.ndim==1:pcm=pcm[:,None]
    rows,total=sample_map(mapping,sr);output=np.zeros((total,pcm.shape[1]),dtype=pcm.dtype);joins=[]
    for s in rows:
        if s['source_start']<0 or s['source_end']>len(pcm):raise ValueError('Decoded audio does not cover exact retained sample map')
        output[s['output_start']:s['output_start']+s['source_end']-s['source_start']]=pcm[s['source_start']:s['source_end']]
    h=round(fade_ms*sr/2000);offset=float(mapping['audio_minus_video_seconds'])
    forbidden=[(float(w['start']),float(w['end'])) for w in words]
    fps=float(rate(mapping['fps']))
    forbidden += [(d.get('source_in_frame',d['start']*fps)/fps+offset,d.get('source_out_frame',d['end']*fps)/fps+offset) for d in rejections]
    for left,right in zip(rows,rows[1:]):
        t=left['output_end'];a=left['source_end'];b=right['source_start'];reason='Disabled; hard join';applied=False
        if h:
            reason='Insufficient or overlapping handles'
            if a-2*h>=left['source_start'] and a+h<=len(pcm) and b-h>=0 and b+2*h<=right['source_end'] and b-a>=2*h and not left['padding_samples'] and not right['padding_samples']:
                intervals=[((a-h)/sr,(a+h)/sr),((b-h)/sr,(b+h)/sr)]
                reason='Handle touches a word or rejected speech'
                if not any(x<v and y>u for x,y in intervals for u,v in forbidden):
                    l=pcm[a-h:a+h];r=pcm[b-h:b+h];reason='Handle is not acoustically quiet'
                    if max(float(np.max(np.abs(l))),float(np.max(np.abs(r))))<=.002:
                        ramp=np.linspace(0,1,2*h)[:,None]
                        output[t-h:t+h]=l*(1-ramp)+r*ramp;applied=True;reason='Quiet source handles; fixed output clock'
        joins.append(dict(output_sample=t,requested_duration_ms=fade_ms,applied=applied,reason=reason,
                          curve='linear',half_handle_samples=h,left_source_boundary=a,right_source_boundary=b,
                          xml_behavior='Hard join; audio envelopes are not exported by XMEML'))
    return output,dict(sample_rate=sr,output_samples=total,sample_map=rows,joins=joins,
                       xml_audio_fades_supported=False,video_timing_changed=False,
                       precision='Nearest-sample rational boundaries; at most one zero padding sample per span prevents borrowing rejected audio; no cumulative rounding')

def render_audio(job,mapping,words,rejections,ffmpeg,fade_ms):
    audio=mapping['source_audio'];sr=int(audio['sample_rate']);channels=int(audio['channels'])
    dest=job/'selected_audio.f32'
    run([ffmpeg,'-v','error','-nostdin','-y','-threads','2','-i',audio['path'],'-map','0:a:0','-f','f32le','-acodec','pcm_f32le','-ar',str(sr),'-ac',str(channels),str(dest)],job,'Decode exact audio samples')
    pcm=np.fromfile(dest,dtype='<f4').reshape(-1,channels)
    samples,report=join_audio(mapping,pcm,sr,words,rejections,fade_ms)
    with wave.open(str(job/'CLEAN_AUDIO.wav'),'wb') as output:
        output.setnchannels(channels);output.setsampwidth(2);output.setframerate(sr)
        output.writeframes((np.clip(samples,-1,1)*32767).astype('<i2').tobytes())
    write(job/'audio_joins.json',report);return report

def run_staged(job,cfg,root,status):
    job=Path(job);root=Path(root);settings=json.loads((root/'settings.json').read_text(encoding='utf-8-sig'))
    w,p,a=context(cfg,root);write(job/'workflow_snapshot.json',cfg['workflow'])
    status('validate','Checking exact workflow snapshot and media clocks')
    if cfg['workflow_mode']=='silence':
        source=w.source(p)['files'];video=probe(source['video']['path']);audio=probe(source.get('audio',source['video'])['path'])
        presentation=probe(a['payload']['files']['review_media']['path'])
        if rate(presentation['fps'])!=rate(video['fps']) or abs(presentation['duration']-video['duration'])>1/float(rate(video['fps'])):raise ValueError('Pass 1 presentation must preserve source cadence and duration')
        if not audio['channels']:raise ValueError('Selected audio has no channels')
        segments=json.loads(Path(a['payload']['files']['transcript']['path']).read_text(encoding='utf-8-sig'))
        words=[dict(word) for seg in (segments.get('segments',[]) if isinstance(segments,dict) else segments) for word in seg.get('words',[])]
        sync=copy.deepcopy(a['payload']['sync']);measured=float(sync['offset_seconds']);offset=frame(measured,video['fps'])/float(rate(video['fps']))
        sync.update(timeline_offset_seconds=offset,quantization_error_ms=(offset-measured)*1000)
        status('decode','Decoding selected microphone for silence evidence')
        pcm=decode(audio['path'],job/'microphone.f32',job,settings['ffmpeg'])
        status('plan','Removing measured silence; semantic decisions remain explicit')
        mapping,decisions=silence_map(video,audio,words,pcm,offset);mapping['sync']=sync
        if Path(presentation['path']).resolve()!=Path(video['path']).resolve():mapping['presentation_video']=presentation
        mapping['workflow_parent']=dict(id=a['id'],digest=a['digest']);mapping['timing_authority']='New silence candidate; not approved'
        rejections=[]
        write(job/'inputs.json',source);write(job/'transcript.json',segments);write(job/'decisions.json',decisions)
    else:
        folder=Path(a['payload']['folder']);mapping=json.loads((folder/'timing_map.json').read_text());words=json.loads((folder/'source_words.json').read_text());rejections=a['payload']['removals']
        write(job/'decisions.json',dict(semantic_rejections=rejections,replanned=False,authority_digest=a['payload']['map_digest']))
    validate_map(mapping);mapped,issues=remap_words(mapping,words)
    write(job/'timing_map.json',mapping);write(job/'source_words.json',words);write(job/'words.json',mapped)
    write(job/'review.json',dict(issues=issues,semantic_sweep='manual required',transcription='Source words remapped, not fresh ASR',human_listening=False,native_playback=False))
    status('export','Writing XML from exact retained spans')
    export_xml(job,mapping,mapping['source_video'],mapping['source_audio'],[],[])
    status('audio','Rendering fixed-clock review audio and checking join handles')
    audio_report=render_audio(job,mapping,words,rejections,settings['ffmpeg'],cfg.get('review_fade_ms',0))
    status('preview','Rendering candidate review; no approval is created')
    preview(job,mapping,mapping['source_video'],mapping['source_audio'],[],settings['ffmpeg'])
    # Video is copied unchanged; reviewed audio uses the exact sample map above.
    mux=job/'REVIEW_SYNCED.partial.mp4'
    run([settings['ffmpeg'],'-v','error','-nostdin','-y','-i',str(job/'REVIEW_PREVIEW.mp4'),'-i',str(job/'CLEAN_AUDIO.wav'),'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-movflags','+faststart',str(mux)],job,'Mux fixed-clock review audio')
    mux.replace(job/'REVIEW_PREVIEW.mp4')
    context(cfg,root);check(job)
    names=['timing_map.json','source_words.json','words.json','decisions.json','review.json','EDITABLE_DRAFT.xml','CLEAN_AUDIO.wav','audio_joins.json','REVIEW_PREVIEW.mp4']
    hashes={name:hashlib.sha256((job/name).read_bytes()).hexdigest() for name in names};write(job/'workflow_outputs.json',hashes)
    write(job/'status.json',dict(state='complete',stage='review',message='Candidate ready for explicit semantic, listening and native review',workflow_mode=cfg['workflow_mode'],artifact_id=a['id'],artifact_digest=a['digest'],map_digest=digest(mapping),duration_frames=mapping['duration_frames'],fps=mapping['fps'],issues=len(issues),approved=False,native_playback_tested=False,human_listening=False,xml_audio_fades_supported=False,applied_review_fades=sum(j['applied'] for j in audio_report['joins'])))
