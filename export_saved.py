"""Export completed edit/render stages without calling transcription or rendering."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
from dajoshi.export_clock import normalize_saved
from dajoshi.export import export_xml
from dajoshi.timing import frame,rate
from dajoshi.media import check


def read(p): return json.loads(p.read_text(encoding='utf-8'))
def write(p,d): p.write_text(json.dumps(d,indent=2),encoding='utf-8')


def export_saved(source,destination,nominal_fps,initialized=False):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if destination.exists():
        if not initialized or any(p.name not in ('request.json','.run.lock','pipeline_identity.json') for p in destination.iterdir()):
            raise ValueError('Choose a new export folder; existing results are preserved')
    required=['timing_map.json','shots.json','decisions.json','exceptions.json','request.json','inputs.json','sync.json','transcript.json']
    for name in required:
        if not (source/name).is_file(): raise ValueError('Missing saved stage: '+name)
    original=read(source/'timing_map.json');mapping=normalize_saved(original,nominal_fps)
    shots=read(source/'shots.json');flags=read(source/'exceptions.json')
    for k in ('source_video','source_audio'):
        p=Path(mapping[k]['path'])
        if not p.is_file() or not p.stat().st_size: raise ValueError('Missing original media: '+str(p))
    inputs=read(source/'inputs.json')
    video=Path(mapping['source_video']['path'])
    if video.stat().st_size != inputs['video']['size'] or video.stat().st_mtime_ns != inputs['video']['mtime_ns']:
        raise ValueError('Original source identity changed since saved edit')
    metadata={};assets=[];cursor=0
    for index,s in enumerate(shots):
        if not (cursor<=s['out_start']<s['out_end']<=mapping['duration_frames']):
            raise ValueError('Saved layout placements are inconsistent')
        cursor=s['out_end']
        if s['layout']=='full': continue
        if not s.get('asset'): raise ValueError('Missing rendered asset reference')
        asset=Path(s['asset']);request=source/'animations'/('render-%04d.json'%index)
        report=asset.with_suffix('.json')
        if not asset.is_file() or asset.stat().st_size==0 or not request.is_file() or not report.is_file():
            raise ValueError('Missing or empty rendered asset/sidecar: '+str(asset))
        req=read(request);rep=read(report);length=s['out_end']-s['out_start']
        if req['frames']!=length or rate(req['fps'])!=rate(original['fps']) or req['beat']!=s['beat'] or rep['beat']!=s['beat'] or Path(req['out'])!=asset:
            raise ValueError('Render contract disagrees with saved placement: '+asset.name)
        if rep['errors'] or rep['blocked_requests'] or rep['hold_handle_seconds']<30:
            raise ValueError('Render completion report is incomplete: '+asset.name)
        # Reuse the completed renderer's duration contract. No media files are
        # opened/probed here. Reserve one second of its 30s handle conservatively.
        available=length+frame(29,nominal_fps,'floor')
        target=destination/'animations'/asset.name
        metadata[str(target)]={
            'path':str(target),'fps':str(rate(nominal_fps)),
            'duration':available/float(rate(nominal_fps)), 'duration_frames':available,
            'width':1920,'height':1080,'channels':0,'sample_rate':None,
            'metadata_basis':'Completed render request/report; file existence/size only',
            'reported_render_fps':req['fps'],'media_header_inspected':False,
        }
        assets.append((asset,report,request,target));s['asset']=str(target)
    if not assets: raise ValueError('No completed animation placements were found')
    destination.mkdir(parents=True,exist_ok=initialized);(destination/'animations').mkdir()
    write(destination/'status.json',{'state':'exporting','preview_created':False})
    try:
        for name in required:
            shutil.copy2(source/name,destination/('saved_'+name if name in ('timing_map.json','shots.json','request.json','sync.json') else name))
        for name in ('exceptions.csv','deck_contract.json','pipeline_identity.json'):
            if (source/name).exists(): shutil.copy2(source/name,destination/('saved_'+name if name=='pipeline_identity.json' else name))
        conversion=source/'conversion_report.json'
        if not conversion.exists() and inputs.get('shots'):
            conversion=Path(inputs['shots']['path']).parent/'conversion_report.json'
        if conversion.is_file():shutil.copy2(conversion,destination/'conversion_report.json')
        for asset,report,request,target in assets:
            check(destination)
            shutil.copy2(asset,target);shutil.copy2(report,target.with_suffix('.json'));shutil.copy2(request,target.parent/request.name)
            if target.stat().st_size!=asset.stat().st_size: raise ValueError('Asset copy size mismatch')
        write(destination/'timing_map.json',mapping);write(destination/'shots.json',shots)
        write(destination/'sync.json',mapping['sync'])
        write(destination/'request.json',{'mode':'export_saved','source_run':str(source),'nominal_fps':nominal_fps,'preview':False})
        write(destination/'asset_metadata.json',metadata)
        with (destination/'retained_ranges.csv').open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=list(mapping['spans'][0]));writer.writeheader();writer.writerows(mapping['spans'])
        path=export_xml(destination,mapping,mapping['source_video'],mapping['source_audio'],shots,flags,asset_metadata=metadata)
        provenance={
            'source_run':str(source),'assets_copied':len(assets),'layout_placements':len(shots),
            'retained_ranges':len(mapping['spans']),'exceptions_preserved':len(flags),
            'duration_frames':mapping['duration_frames'],'duration_seconds':mapping['duration_frames']/float(rate(mapping['fps'])),
            'clock':mapping['clock_normalization'],'media_validation':'Existence/size and saved JSON contract only; no clip playback or header probes',
            'source_stage_sha256':{n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in required},
            'preview_created':False,'native_import_tested':False,'native_playback_tested':False,'human_listening':False,
        }
        write(destination/'export_report.json',provenance)
        (destination/'IMPORT_IN_PREMIERE.txt').write_text(
            'Import EDITABLE_DRAFT.xml using Premiere File > Import. Save as a new Premiere project.\n'
            'Keep this folder and the original source media in their current locations.\n'
            'V1 contains 88 separate animation clips; V2 contains original camera clips with editable Basic Motion.\n'
            'A1/A2 contain original embedded stereo channels, linked to camera clips. Full source handles remain.\n'
            'Animations advertise 29 seconds of extra hold from the completed 30-second render contract.\n'
            'No clips were reviewed or decoded during export. No full preview was rendered.\n'
            'Import, playback, sync perception and transform fidelity in Premiere remain untested.\n'
            'Existing automatic editorial choices and generic PiP/side transforms are preserved.\n'
            'Three unmatched shot-list cues remain unresolved; see conversion_report.json if present.\n'
            'See exceptions.csv for the retained automatic-edit warnings.\n',encoding='utf-8')
        status={'state':'complete','message':'Editable XML exported; Premiere import/playback unverified','xml':str(path),**provenance}
        write(destination/'status.json',status)
        return status
    except Exception as e:
        write(destination/'status.json',{'state':'failed','message':str(e),'preview_created':False});raise


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--destination',required=True);p.add_argument('--nominal-fps',required=True)
    a=p.parse_args();result=export_saved(a.source,a.destination,a.nominal_fps)
    print(json.dumps({k:result[k] for k in ('state','xml','assets_copied','retained_ranges','duration_seconds','preview_created')},indent=2))
