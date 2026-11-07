"""Re-plan a saved local transcript/PCM cache and reuse completed animations."""
import csv
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
import numpy as np
from dajoshi.editor import plan
from dajoshi.media import probe,check
from dajoshi.timing import build_map,map_source,rate,frame
from dajoshi.export import export_xml,xml_rate
from xml.etree.ElementTree import Element


def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def write(p,d):
    p=Path(p);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(d,indent=2),encoding='utf-8');tmp.replace(p)


def reedit_saved(job,cfg,load_shots):
    job=Path(job).resolve();source=Path(cfg['reedit_from']).resolve()
    if source==job or source in job.parents or job in source.parents:raise ValueError('Cached source and new job must be separate folders')
    if any(p.name not in ('request.json','.run.lock','pipeline_identity.json') for p in job.iterdir()):raise ValueError('Re-edit requires a fresh job folder; previous results are preserved')
    def status(stage,message):
        check(job);write(job/'status.json',dict(state='running',stage=stage,message=message));print(message,flush=True)
    status('validate','Checking saved transcript, PCM, timing, placements and animation contracts')
    for name in ('request.json','inputs.json','timing_map.json','decisions.json','transcript.json','microphone.f32','shots.json'):
        if not (source/name).is_file():raise ValueError('Missing cached stage: '+name)
    previous=read(source/'timing_map.json');oldcfg=read(source/'request.json');inputs=read(source/'inputs.json')
    for kind in ('video','audio','script','shots','deck'):
        if kind not in inputs:continue
        p=Path(inputs[kind]['path'])
        if not p.is_file() or p.stat().st_size!=inputs[kind]['size'] or p.stat().st_mtime_ns!=inputs[kind]['mtime_ns']:
            raise ValueError('Cached input changed: '+kind)
    if not inputs.get('script'):raise ValueError('An approved script is required for script-aligned re-edit')
    video=probe(previous['source_video']['path'])
    audio=video.copy() if previous['source_audio']['path']==video['path'] else probe(previous['source_audio']['path'])
    fps=video['fps'];xml_rate(Element('preflight'),fps)
    if not previous['sync']['verified']:raise ValueError('Cached sync is not verified')
    offset=previous['audio_minus_video_seconds']
    segments=read(source/'transcript.json');segments=segments.get('segments',[]) if isinstance(segments,dict) else segments
    words=[dict(w,segment_start=i==0) for s in segments for i,w in enumerate(s.get('words',[]))]
    if not words:raise ValueError('Cached transcript has no word timing')
    pcm=np.fromfile(source/'microphone.f32',dtype='<f4')
    if abs(len(pcm)/16000-audio['duration'])>.5:raise ValueError('Cached PCM duration does not match source audio')
    script=Path(inputs['script']['path']).read_text(encoding='utf-8-sig')
    overrides=oldcfg.get('overrides_inline',{})
    if oldcfg.get('overrides'):overrides=read(oldcfg['overrides'])
    status('plan','Aligning saved word transcript to approved script and checking restart cut boundaries')
    start=float(oldcfg.get('start',0) or 0);end=min(float(oldcfg.get('end',video['duration']) or video['duration']),video['duration'])
    keeps,decisions,flags,metrics=plan(words,pcm,16000,offset,start,end,overrides,script=script,fps=float(rate(fps)))
    mapping=build_map(keeps,fps,offset,video['duration'],audio['duration'])
    mapping.update(source_video=video,source_audio=audio,sync=previous['sync'])
    if previous.get('keying'):
        from dajoshi.keying import validate_coverage
        mapping['keying']=previous['keying'];validate_coverage(mapping)
    flags+=oldcfg.get('regression_flags',[])
    conversion=Path(inputs['shots']['path']).parent/'conversion_report.json' if inputs.get('shots') else None
    if conversion and conversion.is_file():
        converted=read(conversion)
        known={(f['kind'],f['reason']) for f in flags}
        for f in converted.get('exceptions',[]):
            if (f['kind'],f['reason']) not in known:flags.append(f)
        shutil.copy2(conversion,job/'conversion_report.json')
    shots=load_shots(inputs.get('shots',{}).get('path'),words,offset,mapping,inputs.get('deck',{}).get('path'),flags)
    for f in flags:f['out_frame']=map_source(mapping,f['source_seconds']) if 'source_seconds' in f else None
    oldshots=read(source/'shots.json');lookup={(s['source_start'],s['source_end'],s.get('beat'),s['layout']):(i,s) for i,s in enumerate(oldshots)}
    metadata={};reused=[];(job/'animations').mkdir()
    status('assets','Reusing completed animation files against the updated timing map; no rendering')
    for shot in shots:
        check(job)
        if shot['layout']=='full':continue
        key=(shot['source_start'],shot['source_end'],shot.get('beat'),shot['layout'])
        if key not in lookup:raise ValueError('No completed animation exists for revised shot')
        index,old=lookup[key];asset=Path(old['asset']);req=source/'animations'/('render-%04d.json'%index);report=asset.with_suffix('.json')
        if not asset.is_file() or not asset.stat().st_size or not req.is_file() or not report.is_file():raise ValueError('Missing cached animation or completion sidecar')
        rendered=read(req);done=read(report)
        if rendered['beat']!=shot['beat'] or rendered['frames']!=old['out_end']-old['out_start'] or done.get('errors') or done.get('blocked_requests') or done.get('hold_handle_seconds',0)<30:raise ValueError('Animation contract is inconsistent')
        oldrate=rate(rendered['fps']);newrate=rate(fps)
        if abs(oldrate-newrate)*(rendered['frames']/float(oldrate)+30)>.05:raise ValueError('Animation clock needs explicit retiming')
        available=rendered['frames']+frame(29,fps,'floor')
        if shot['out_end']-shot['out_start']>available:raise ValueError('Revised placement exceeds available animation hold')
        target=job/'animations'/asset.name;shutil.copy2(asset,target);shutil.copy2(report,target.with_suffix('.json'));shutil.copy2(req,target.parent/req.name)
        if target.stat().st_size!=asset.stat().st_size:raise ValueError('Animation copy size mismatch')
        shot['asset']=str(target)
        metadata[str(target)]=dict(path=str(target),fps=fps,duration=available/float(newrate),duration_frames=available,width=1920,height=1080,channels=0,sample_rate=None,
                                  metadata_basis='Completed render contract; existence and size only; no clip probe',reported_render_fps=rendered['fps'])
        reused.append(asset.name)
    for name in ('request.json','timing_map.json','decisions.json','shots.json','pipeline_identity.json'):
        if (source/name).exists():shutil.copy2(source/name,job/('prior_'+name))
    for name in ('inputs.json','transcript.json','sync.json','deck_contract.json'):
        if (source/name).exists():shutil.copy2(source/name,job/name)
    write(job/'timing_map.json',mapping);write(job/'shots.json',shots);write(job/'asset_metadata.json',metadata)
    write(job/'decisions.json',dict(automatic=decisions,metrics=metrics,overrides=overrides));write(job/'exceptions.json',flags)
    write(job/'script_alignment.json',metrics['script_alignment'])
    changes=['Script-aligned restart changes (original source seconds)','',
             'Computed from saved word timestamps, approved script and cached PCM. No human listening was performed.',
             'The alignment is dynamic-programming token alignment; restart selection is conservative heuristic logic.','']
    for row in metrics['script_alignment']['candidates']:
        changes += [('REMOVED' if row['removed'] else 'FLAGGED / RETAINED')+' %.2fs -> restart %.2fs'%(row['abandoned_start'],row['restart_start']),
                    'Earlier: '+row['before'],'Continue: '+row['after'],row['reason'],'']
    (job/'RETAKE_CHANGES.txt').write_text('\n'.join(changes),encoding='utf-8')
    (job/'IMPORT_IN_PREMIERE.txt').write_text(
        'Import EDITABLE_DRAFT.xml using Premiere File > Import, and save as a new project.\n'
        'The original recording remains referenced; separate animation clips are in animations/. Keep their paths intact.\n'
        'V1: separate animation clips. V2: original camera, editable Basic Motion transforms. A1/A2: original stereo channels.\n'
        'Cuts and placements use timing_map.json. Retake changes are listed in RETAKE_CHANGES.txt and script_alignment.json.\n'
        'Three unmatched cues remain unplaced; see conversion_report.json. Uncertain edits remain in exceptions.csv.\n'
        'No full preview was rendered. Premiere import, playback, sync perception and transform fidelity remain untested.\n',encoding='utf-8')
    for name,rows,fields in [('exceptions.csv',flags,['kind','source_seconds','out_frame','reason']),('retained_ranges.csv',mapping['spans'],list(mapping['spans'][0]))]:
        with (job/name).open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    status('export','Writing updated editable XML with the normal installed exporter')
    xml=export_xml(job,mapping,video,audio,shots,flags,asset_metadata=metadata)
    result=dict(state='complete',stage='done',message='Script-aligned editable XML exported; native Premiere playback unverified',xml=str(xml),
                source_run=str(source),duration_seconds=mapping['duration_frames']/float(rate(fps)),duration_frames=mapping['duration_frames'],
                previous_duration_seconds=previous['duration_frames']/float(rate(previous['fps'])),
                decision_counts=dict(Counter(d['kind'] for d in decisions)),retained_ranges=len(mapping['spans']),layout_placements=len(shots),assets_reused=len(reused),
                unmatched_cues=sum(f['kind']=='unmatched_animation_cue' for f in flags),preview_created=False,
                native_import_tested=False,native_playback_tested=False,human_listening=False,
                script_alignment_method=metrics['script_alignment']['method'],
                script_sha256=hashlib.sha256(Path(inputs['script']['path']).read_bytes()).hexdigest(),
                transcript_sha256=hashlib.sha256((source/'transcript.json').read_bytes()).hexdigest())
    write(job/'export_report.json',result);write(job/'status.json',result);print(json.dumps(result),flush=True)
    return result
