"""CLI worker used by the Windows app. No network clients or remote models."""
import argparse,csv,hashlib,json,os,re,sys,traceback,uuid
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from dajoshi import __version__
from dajoshi.timing import rate,frame,build_map,map_source,map_interval
from dajoshi.media import probe,decode,sync,visual_offset,transcribe,check,Cancelled,run
from dajoshi.editor import plan,tokens
from dajoshi.export import export_xml,preview,xml_rate
import xml.etree.ElementTree as ET
from dajoshi.animation import render,inspect_deck

ROOT=Path(__file__).resolve().parent

@contextmanager
def job_lock(job):
    # OS file locks are released automatically after a crash. A second launcher
    # cannot resume or mutate a run while its first worker still owns it.
    with (Path(job)/'.run.lock').open('a+b') as handle:
        if handle.tell()==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise RuntimeError('This run already has an active worker. Nothing was changed.')
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)

def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def write(p,v):
    p=Path(p);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(v,indent=2,ensure_ascii=False),encoding='utf-8');tmp.replace(p)

def fingerprint(path):
    p=Path(path).resolve();s=p.stat();h=hashlib.sha256()
    with p.open('rb') as f:
        h.update(f.read(1024*1024));f.seek(max(0,s.st_size-1024*1024));h.update(f.read(1024*1024))
    return dict(path=str(p),size=s.st_size,mtime_ns=s.st_mtime_ns,edge_sha256=h.hexdigest())

def guard_recording():
    if os.name!='nt':return
    import subprocess
    p=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',"[System.Diagnostics.Process]::GetProcessesByName('obs64').Count"],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    if p.returncode:raise RuntimeError('Cannot check OBS process state; media work paused')
    try:count=int(p.stdout.strip())
    except ValueError:raise RuntimeError('Cannot establish OBS process state; media work paused')
    if count:raise RuntimeError('OBS is open. Media work is paused to protect recording. Close OBS when idle, then Resume; the app never controls OBS.')

def load_shots(path,words,offset,mapping,deck,flags):
    if not path:return []
    p=Path(path)
    if p.suffix.lower()=='.csv':
        with p.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    elif p.suffix.lower()=='.json':rows=read(p)
    else:raise ValueError('Shot list must be CSV or JSON. See examples/shots.csv for its explicit source-time contract.')
    if not isinstance(rows,list):raise ValueError('Shot list JSON must contain a list')
    result=[];last=-1
    for row in rows:
        layout=row.get('layout','full').lower()
        if layout not in ['full','pip','side','graphic']:raise ValueError('Unknown layout '+layout)
        if row.get('phrase'):
            phrase=tokens(row['phrase']);all_tokens=[tokens(w['word']) for w in words];flat=[x[0] if len(x)==1 else '' for x in all_tokens]
            matches=[i for i in range(len(flat)-len(phrase)+1) if flat[i:i+len(phrase)]==phrase]
            if len(matches)!=1:raise ValueError('Cue phrase must match exactly once; use source_start/source_end for ambiguous cue: '+row['phrase'])
            start=float(words[matches[0]]['start'])-offset
        else:start=float(row['source_start'])
        end=float(row['source_end'])
        if start<last or end<=start:raise ValueError('Shot source ranges must be ordered, non-overlapping and positive')
        last=end
        if layout!='full' and (not deck or row.get('beat') in [None,'']):raise ValueError('PiP, side and graphic shots require an HTML deck and beat number')
        pieces=map_interval(mapping,start,end)
        if not pieces:
            flags.append(dict(kind='removed_shot',source_seconds=start,reason='Shot was entirely removed by speech edit'));continue
        # All retained pieces of one source shot become a single continuous
        # output shot. Do not restart its animation at every removed pause.
        a,b=pieces[0][0],pieces[-1][1]
        shot=dict(layout=layout,beat=int(row['beat']) if row.get('beat') not in [None,''] else None,out_start=a,out_end=b,source_start=start,source_end=end)
        if row.get('render_contract') is not None:
            from dajoshi.animation import render_contract
            contract=row['render_contract']
            if isinstance(contract,str):contract=json.loads(contract)
            shot['render_contract']=render_contract(contract,b-a,mapping['fps'])
        result.append(shot)
    return result

def run_job(job):
    job=Path(job).resolve();cfg=read(job/'request.json');settings=read(ROOT/'settings.json')
    def status(stage,message):
        check(job);write(job/'status.json',dict(state='running',stage=stage,message=message,version=__version__));print(message,flush=True)
    check(job)
    code=hashlib.sha256()
    for p in [Path(__file__),ROOT/'export_saved.py',ROOT/'reedit_saved.py',ROOT/'keying_runner.py']+sorted((ROOT/'dajoshi').glob('*.py')):code.update(p.read_bytes())
    identity=dict(code_sha256=code.hexdigest(),request=cfg,settings=settings)
    if (job/'pipeline_identity.json').exists():
        if read(job/'pipeline_identity.json')!=identity:raise ValueError('App code, settings or request changed. Start a NEW run; resume cannot reuse potentially stale outputs.')
    elif (job/'timing_map.json').exists():raise ValueError('Older run has no cache identity; start a new run')
    write(job/'pipeline_identity.json',identity)
    if cfg.get('workflow_mode'):
        guard_recording()
        from dajoshi.staged_cut import run_staged
        run_staged(job,cfg,ROOT,status)
        return
    if cfg.get('reedit_from'):
        from reedit_saved import reedit_saved
        reedit_saved(job,cfg,load_shots)
        return
    if cfg.get('reuse_edit_from'):
        # Export-only stage reuse is explicit and isolated. Never decode or render.
        from export_saved import export_saved
        saved=Path(cfg['reuse_edit_from']).resolve()
        if saved==job or job in saved.parents or saved in job.parents:raise ValueError('Saved run and new job must be separate folders')
        prior=read(saved/'timing_map.json')
        source=probe(prior['source_video']['path'])
        xml_rate(ET.Element('preflight'),source['fps'])
        result=export_saved(saved,job,source['fps'],initialized=True)
        write(job/'request.json',cfg)
        write(job/'pipeline_identity.json',identity)
        result['export_path']='normal worker / shared installed exporter'
        write(job/'status.json',result)
        print(json.dumps({k:result[k] for k in ['state','xml','assets_copied','preview_created']}),flush=True)
        return
    guard_recording()
    paths={k:Path(cfg[k]).resolve() for k in ['video','audio','reference','shots','deck','script','transcript','overrides'] if cfg.get(k)}
    if 'video' not in paths:raise ValueError('Choose source video')
    for k,p in paths.items():
        if not p.is_file():raise ValueError('Missing '+k+': '+str(p))
        if job==p or job in p.parents:raise ValueError('Inputs must live outside this job folder')
    sources={k:fingerprint(v) for k,v in paths.items()}
    if (job/'inputs.json').exists() and read(job/'inputs.json')!=sources:raise ValueError('Source changed since this run. Start a new run; cached results will not be reused.')
    write(job/'inputs.json',sources);status('validate','Inspecting media headers')
    video=probe(paths['video']);audio=probe(paths.get('audio',paths['video']))
    if not video['fps']:raise ValueError('Input has no video stream')
    xml_rate(ET.Element('preflight'),video['fps'])
    keyed=None
    if cfg.get('keying',{}).get('enabled'):
        from dajoshi.keying import prepare,settings as keysettings
        kc=keysettings(cfg['keying']);status('keying','Preparing CorridorKey source assets before speech editing')
        keyed=prepare(ROOT,job,video,kc,settings['ffmpeg'],settings.get('corridorkey_engine'))
        if kc['preview']:
            write(job/'status.json',dict(state='complete',stage='keying',message='Source key preview ready. Review mask and framing; no edit or approval was created.',approved=False,preview_created=True));return
        cfg=dict(cfg,start=kc['start'],end=kc['end'])
    if not audio['channels']:raise ValueError('Source has no audio; provide separate microphone audio')
    # Fail before transcription/rendering when XMEML cannot represent the clock.
    xml_rate(ET.Element('preflight'),video['fps'])
    if audio.get('fps'):xml_rate(ET.Element('preflight'),audio['fps'])
    fps=video['fps'];start=float(cfg.get('start',0) or 0);end=min(float(cfg.get('end',video['duration']) or video['duration']),video['duration'])
    if not 0<=start<end:raise ValueError('Invalid source range')
    flags=[]
    if cfg.get('script'):
        script=paths['script'].read_text(encoding='utf-8-sig')
        if len(script)>200000:raise ValueError('Script exceeds 200,000 characters')
    status('decode','Decoding microphone locally (2 CPU threads)')
    if (job/'microphone.f32').exists():mic=np.fromfile(job/'microphone.f32',dtype='<f4')
    else:mic=decode(audio['path'],job/'microphone.f32',job,settings['ffmpeg'])
    if Path(audio['path'])==Path(video['path']):verification=dict(verified=True,offset_seconds=0,method='Embedded source audio')
    elif (job/'sync.json').exists():verification=read(job/'sync.json')
    else:
        status('sync','Measuring microphone-to-camera sync across three windows')
        reference=probe(paths.get('reference',paths['video']))
        if not reference['channels']:raise ValueError('Silent composite requires the original camera file in Reference to verify microphone sync')
        if (job/'reference.f32').exists():scratch=np.fromfile(job/'reference.f32',dtype='<f4')
        else:scratch=decode(reference['path'],job/'reference.f32',job,settings['ffmpeg'])
        verification=sync(scratch,mic)
        if reference['path']!=video['path']:
            status('visual_sync','Verifying composite-to-original visual timing')
            visual=visual_offset(video['path'],reference['path'],job);verification['visual']=visual
            verification['verified']=verification['verified'] and visual['verified'];verification['offset_seconds']+=visual['offset_seconds']
        write(job/'sync.json',verification)
    if not verification['verified']:raise ValueError('Sync is ambiguous or drifting. See sync.json; automatic export stopped to avoid a silently wrong timeline.')
    measured=verification['offset_seconds'];offset=frame(measured,fps)/float(rate(fps))
    verification['timeline_offset_seconds']=offset;verification['quantization_error_ms']=(offset-measured)*1000;write(job/'sync.json',verification)
    flags.append(dict(kind='sync_precision',source_seconds=start,reason='Measured offset %.6fs; XML frame-grid offset %.6fs, error %.2fms. Native lip-sync unverified.'%(measured,offset,(offset-measured)*1000)))
    status('transcribe','Loading/transcribing words using local cached Whisper')
    transcript_file=job/'transcript.json'
    if 'transcript' in paths:flags.append(dict(kind='provided_transcript',source_seconds=start,reason='Provided transcript assumed to use selected audio seconds; verify provenance'))
    if transcript_file.exists():segments=read(transcript_file)
    elif 'transcript' in paths:
        segments=read(paths['transcript']);write(transcript_file,segments)
    else:
        guard_recording();segments=transcribe(audio['path'],settings['model'],job);write(transcript_file,segments)
    if isinstance(segments,dict):segments=segments.get('segments',[])
    words=[dict(w,segment_start=i==0) for s in segments for i,w in enumerate(s.get('words',[]))]
    if not words:raise ValueError('Transcript must contain word timestamps in selected audio time')
    overrides=read(paths['overrides']) if 'overrides' in paths else cfg.get('overrides_inline',{})
    for kind in ['keep','remove']:
        for a,b in overrides.get(kind,[]):
            if not 0<=float(a)<float(b)<=video['duration']:raise ValueError('Override outside source duration')
    status('plan','Making automatic cuts from words and acoustic boundaries')
    keeps,decisions,uncertain,metrics=plan(words,mic,16000,offset,start,end,overrides,script=script if 'script' in paths else None,fps=float(rate(fps)));flags+=uncertain
    if 'script_alignment' in metrics:write(job/'script_alignment.json',metrics['script_alignment'])
    mapping=build_map(keeps,fps,offset,video['duration'],audio['duration']);mapping['source_video']=video;mapping['source_audio']=audio;mapping['sync']=verification
    if keyed:mapping['keying']=keyed
    shots=load_shots(paths.get('shots'),words,offset,mapping,paths.get('deck'),flags)
    if 'script' in paths:
        expected=set(tokens(script));spoken=set(tokens(' '.join(w['word'] for w in words)))
        missing=sorted(expected-spoken)
        if missing:flags.append(dict(kind='script_coverage',source_seconds=start,reason='Script words absent from transcript (not forced or synthesized): '+', '.join(missing[:40])))
    for known in cfg.get('regression_flags',[]):flags.append(known)
    for f in flags:f['out_frame']=map_source(mapping,f['source_seconds']) if 'source_seconds' in f else None
    write(job/'timing_map.json',mapping);write(job/'decisions.json',dict(automatic=decisions,metrics=metrics,overrides=overrides));write(job/'exceptions.json',flags)
    with (job/'exceptions.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=['kind','source_seconds','out_frame','reason'],extrasaction='ignore');writer.writeheader();writer.writerows(flags)
    with (job/'retained_ranges.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=list(mapping['spans'][0]));writer.writeheader();writer.writerows(mapping['spans'])
    if shots:
        guard_recording();status('animations','Rendering local HTML beat clips with extendable holds');write(job/'deck_contract.json',inspect_deck(paths['deck']))
        assets=job/'animations';assets.mkdir(exist_ok=True);previous=None
        for i,s in enumerate(shots):
            check(job)
            if s['layout']=='full':continue
            out=assets/('beat-%03d-%04d.mp4'%(s['beat'],i));s['asset']=str(out)
            if not out.exists():
                request=assets/('render-%04d.json'%i)
                write(request,dict(deck=str(paths['deck']),beat=s['beat'],previous=previous,frames=s['out_end']-s['out_start'],fps=fps,out=str(out),job=str(job),ffmpeg=settings['ffmpeg'],contract=s.get('render_contract')))
                print('Rendering beat %s (%d/%d)'%(s['beat'],i+1,len(shots)),flush=True)
                run([sys.executable,'-m','dajoshi.animation',str(request)],job,'Isolated HTML renderer',timeout=600)
            previous=s['beat']
    write(job/'shots.json',shots)
    status('export','Writing editable XML with original media references')
    export_xml(job,mapping,video,audio,shots,flags)
    if cfg.get('render_review_preview',False):guard_recording();status('preview','Rendering review preview');preview(job,mapping,video,audio,shots,settings['ffmpeg'])
    for key,p in paths.items():
        if fingerprint(p)!=sources[key]:raise RuntimeError('Input changed during run: '+str(p))
    result=dict(state='complete',stage='done',message='Editable draft and report ready. Premiere import/playback is NOT verified.',version=__version__,duration_seconds=mapping['duration_frames']/float(rate(fps)),clips=len(mapping['spans']),exceptions=len(flags),native_import_tested=False,native_playback_tested=False,human_listening=False,preview_created=(job/'REVIEW_PREVIEW.mp4').exists())
    write(job/'status.json',result);print(json.dumps(result),flush=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request');parser.add_argument('--job',required=True);parser.add_argument('--resume',action='store_true');args=parser.parse_args();job=Path(args.job).resolve()
    if args.resume:
        if not (job/'request.json').is_file():raise ValueError('Not a resumable job')
    else:
        if job.exists():raise ValueError('Run folder already exists; choose a new one or Resume')
        cfg=read(args.request);cfg['preview']=bool(cfg.get('render_review_preview',False));job.mkdir(parents=True);write(job/'request.json',cfg)
    try:
        with job_lock(job):
            if args.resume and (job/'CANCEL').exists():(job/'CANCEL').unlink()
            try:run_job(job)
            except Cancelled as e:write(job/'status.json',dict(state='cancelled',message=str(e)));print(str(e),flush=True);return 2
            except Exception as e:
                write(job/'status.json',dict(state='failed',message=str(e)));(job/'error.txt').write_text(traceback.format_exc(),encoding='utf-8');print(str(e),file=sys.stderr,flush=True);return 1
    except RuntimeError as e:print(str(e),file=sys.stderr,flush=True);return 3
    return 0

if __name__=='__main__':sys.exit(main())
