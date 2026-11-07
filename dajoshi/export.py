from pathlib import Path
from fractions import Fraction
import xml.etree.ElementTree as E
from .timing import rate,frame,split_spans
from .media import run,check,probe
from .interchange import validate

def xml_rate(parent,fps):
    fps=rate(fps); ntsc=fps.denominator==1001 and fps.numerator%1000==0
    if fps.denominator!=1 and not ntsc:
        raise ValueError('This source rate is preserved in timing_map.json but this XML exporter cannot represent it exactly. Native arbitrary-rate assembly is not implemented; no misleading XML emitted.')
    r=E.SubElement(parent,'rate'); E.SubElement(r,'timebase').text=str(fps.numerator//1000 if ntsc else fps.numerator); E.SubElement(r,'ntsc').text='TRUE' if ntsc else 'FALSE'

def local_file_url(path):
    # Premiere 25.3 on Windows misinterprets file:///E:/ as a UNC path.
    # Explicit localhost is also part of the XMEML pathurl contract.
    url=Path(path).resolve().as_uri()
    return 'file://localhost/'+url[len('file:///'):] if url.startswith('file:///') else url

def put(p,name,value): E.SubElement(p,name).text=str(value)

def motion(clip,scale,x,y):
    e=E.SubElement(E.SubElement(clip,'filter'),'effect')
    for k,v in [('name','Basic Motion'),('effectid','basic'),('effectcategory','motion'),('effecttype','motion'),('mediatype','video')]: put(e,k,v)
    p=E.SubElement(e,'parameter'); put(p,'parameterid','scale');put(p,'name','Scale');put(p,'value',scale)
    p=E.SubElement(e,'parameter');put(p,'parameterid','center');put(p,'name','Center');v=E.SubElement(p,'value');put(v,'horiz',x);put(v,'vert',y)

def layout_at(shots,f):
    return next((s for s in shots if s['out_start']<=f<s['out_end']),dict(layout='full'))

def export_xml(job,mapping,video,audio,shots,flags,asset_metadata=None):
    original_video=video
    if mapping.get('presentation_video'):
        if mapping.get('keying'):raise ValueError('Choose one approved presentation source; layered keying and flattened Pass 1 cannot both be active')
        video=mapping['presentation_video']
        if rate(video['fps'])!=rate(mapping['fps']):raise ValueError('Presentation cadence differs from timing authority')
    root=E.Element('xmeml',version='5'); seq=E.SubElement(root,'sequence',id='sequence-1')
    put(seq,'name','DaJoshiResolve - EDITABLE DRAFT'); put(seq,'duration',mapping['duration_frames']);xml_rate(seq,mapping['fps'])
    put(seq,'in',0);put(seq,'out',mapping['duration_frames'])
    media=E.SubElement(seq,'media');v=E.SubElement(media,'video');format_=E.SubElement(v,'format'); chars=E.SubElement(format_,'samplecharacteristics');xml_rate(chars,mapping['fps'])
    for k,value in [('width',1920),('height',1080),('anamorphic','FALSE'),('pixelaspectratio','square'),('fielddominance','none')]:put(chars,k,value)
    graphics=E.SubElement(v,'track');camera=E.SubElement(v,'track');a=E.SubElement(media,'audio');put(a,'channelcount',audio['channels'])
    audio_tracks=[E.SubElement(a,'track') for _ in range(audio['channels'])]
    defined=set(); fileids={}; fps=float(rate(mapping['fps']))
    def file(parent,info,kind):
        key=info['path']; fid=fileids.setdefault(key,'file-'+str(len(fileids)+1));f=E.SubElement(parent,'file',id=fid)
        if key in defined:return
        defined.add(key);put(f,'name',Path(key).name);put(f,'pathurl',local_file_url(key));xml_rate(f,info.get('fps') or mapping['fps']);put(f,'duration',info.get('duration_frames',frame(info['duration'],info.get('fps') or mapping['fps'],'floor')))
        m=E.SubElement(f,'media')
        if info.get('fps'):
            vv=E.SubElement(m,'video');sc=E.SubElement(vv,'samplecharacteristics');xml_rate(sc,info['fps'])
            for k,val in [('width',info['width']),('height',info['height']),('anamorphic','FALSE'),('pixelaspectratio','square'),('fielddominance','none')]:put(sc,k,val)
        if info['channels']:
            aa=E.SubElement(m,'audio');sc=E.SubElement(aa,'samplecharacteristics');put(sc,'depth',16);put(sc,'samplerate',info['sample_rate']);put(aa,'channelcount',info['channels'])
    def clip(track,cid,info,kind,begin,end,source,channel=1,enabled=True):
        c=E.SubElement(track,'clipitem',id=cid)
        put(c,'name',Path(info['path']).name);put(c,'enabled','TRUE' if enabled else 'FALSE');put(c,'duration',info.get('duration_frames',frame(info['duration'],mapping['fps'],'floor')));xml_rate(c,mapping['fps'])
        for k,val in [('start',begin),('end',end),('in',source),('out',source+end-begin)]:put(c,k,val)
        file(c,info,kind);st=E.SubElement(c,'sourcetrack');put(st,'mediatype',kind);put(st,'trackindex',channel)
        return c
    spans=split_spans(mapping,[x for s in shots for x in [s['out_start'],s['out_end']]])
    for i,s in enumerate(spans,1):
        shot=layout_at(shots,s['out_in_frame']); layout=shot['layout'];start,end=s['out_in_frame'],s['out_out_frame']
        vc=clip(camera,'video-'+str(i),video,'video',start,end,s['source_in_frame'],enabled=layout!='graphic')
        fit=min(1920/video['width'],1080/video['height']);scale,x,y=(100*fit,0,0)
        if layout=='pip':scale,x,y=32*fit,.31,.30
        if layout=='side':scale,x,y=50*fit,.25,0
        # XMEML center offsets are normalized to the source dimensions;
        # Premiere converts them to sequence-space Motion positions on import.
        motion(vc,scale,x*1920/video['width'],y*1080/video['height'])
        links=[('video-'+str(i),'video',2,i)]+[('audio-%d-%d'%(i,ch),'audio',ch,i) for ch in range(1,len(audio_tracks)+1)]
        clips=[vc]
        for ch,track in enumerate(audio_tracks,1): clips.append(clip(track,'audio-%d-%d'%(i,ch),audio,'audio',start,end,frame(s['audio_in_seconds'],mapping['fps']),ch))
        for c in clips:
            for ref,kind,trackno,index in links:
                link=E.SubElement(c,'link');put(link,'linkclipref',ref);put(link,'mediatype',kind);put(link,'trackindex',trackno);put(link,'clipindex',index);put(link,'groupindex',1)
    for i,s in enumerate(shots,1):
        if not s.get('asset'):continue
        info=asset_metadata[s['asset']] if asset_metadata is not None else probe(s['asset']);c=clip(graphics,'graphic-'+str(i),info,'video',s['out_start'],s['out_end'],0)
        motion(c,50 if s['layout']=='side' else 100,-.25 if s['layout']=='side' else 0,0)
    for f in flags:
        if f.get('out_frame') is None:continue
        mark=E.SubElement(seq,'marker');put(mark,'name',f['kind']);put(mark,'comment',f['reason']);put(mark,'in',f['out_frame']);put(mark,'out',f['out_frame'])
    if mapping.get('keying'):
        from .keying import validate_coverage
        validate_coverage(mapping)
        keyed=mapping['keying'];settings=keyed['settings'];info=probe(keyed['masked_media'])
        info['duration_frames']=keyed['frames']
        presenter=E.SubElement(v,'track')
        for i,s in enumerate(spans,1):
            original=camera.findall('clipitem')[i-1];original.find('enabled').text='FALSE'
            c=E.SubElement(presenter,'clipitem',id='presenter-'+str(i))
            put(c,'name','Keyed presenter - editable source framing');put(c,'enabled','FALSE' if layout_at(shots,s['out_in_frame'])['layout']=='graphic' else 'TRUE');put(c,'duration',keyed['frames']);xml_rate(c,mapping['fps'])
            for field,value in [('start',s['out_in_frame']),('end',s['out_out_frame']),('in',s['source_in_frame']-keyed['first_frame']),('out',s['source_out_frame']-keyed['first_frame'])]:put(c,field,value)
            nest=E.SubElement(c,'sequence',id='presenter-source')
            if i==1:
                put(nest,'name','Presenter source framing - mask is rerenderable in DaJoshiResolve');put(nest,'duration',keyed['frames']);xml_rate(nest,mapping['fps'])
                nm=E.SubElement(nest,'media');nv=E.SubElement(nm,'video')
                import copy
                nv.append(copy.deepcopy(format_));nt=E.SubElement(nv,'track')
                nc=clip(nt,'keyed-source',info,'video',0,keyed['frames'],0);put(nc,'alphatype','straight')
                motion(nc,100*min(1920/info['width'],1080/info['height'])*settings['scale'],settings['x']*1920/info['width'],settings['y']*1080/info['height'])
            layout=layout_at(shots,s['out_in_frame'])['layout']
            scale,x,y={'pip':(32,.31,.30),'side':(50,.25,0)}.get(layout,(100,0,0));motion(c,scale,x,y)
            # Keyed presenter, recoverable original and microphone move together.
            links=[('video-'+str(i),'video',2,i),('presenter-'+str(i),'video',3,i)]+[('audio-%d-%d'%(i,ch),'audio',ch,i) for ch in range(1,len(audio_tracks)+1)]
            for target in [original,c]+[track.findall('clipitem')[i-1] for track in audio_tracks]:
                for old in list(target.findall('link')):target.remove(old)
                for ref,kind,trackno,index in links:
                    link=E.SubElement(target,'link');put(link,'linkclipref',ref);put(link,'mediatype',kind);put(link,'trackindex',trackno);put(link,'clipindex',index);put(link,'groupindex',1)
    if mapping.get('presentation_video'):
        # Approved uncut appearance is the visible source. Original frames remain
        # linked on a disabled recovery track at the very same source clock.
        recovery=E.SubElement(v,'track')
        for i,s in enumerate(spans,1):
            original=clip(recovery,'recovery-'+str(i),original_video,'video',s['out_in_frame'],s['out_out_frame'],s['source_in_frame'],enabled=False)
            links=[('video-'+str(i),'video',2,i),('recovery-'+str(i),'video',3,i)]+[('audio-%d-%d'%(i,ch),'audio',ch,i) for ch in range(1,len(audio_tracks)+1)]
            for target in [camera.findall('clipitem')[i-1],original]+[track.findall('clipitem')[i-1] for track in audio_tracks]:
                for old in list(target.findall('link')):target.remove(old)
                for ref,kind,trackno,index in links:
                    link=E.SubElement(target,'link');put(link,'linkclipref',ref);put(link,'mediatype',kind);put(link,'trackindex',trackno);put(link,'clipindex',index);put(link,'groupindex',1)
    E.indent(root)
    data=b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'+E.tostring(root,encoding='utf-8')
    validation=validate(data)
    import json
    (Path(job)/'xml_validation.json').write_text(json.dumps(validation,indent=2),encoding='utf-8')
    path=Path(job)/'EDITABLE_DRAFT.xml';partial=path.with_suffix('.partial.xml')
    partial.write_bytes(data);partial.replace(path)
    return path

def preview(job,mapping,video,audio,shots,ffmpeg):
    """Preview uses the same frame spans, audio offsets, and shot splits as XML."""
    job=Path(job);folder=job/'preview_parts';folder.mkdir(exist_ok=True);fps=float(rate(mapping['fps']))
    spans=split_spans(mapping,[x for s in shots for x in [s['out_start'],s['out_end']]])
    parts=[]
    for i,s in enumerate(spans):
        check(job);dest=folder/('%05d.mkv'%i);partial=dest.with_suffix('.partial.mkv');n=s['out_out_frame']-s['out_in_frame'];duration=n/fps
        shot=layout_at(shots,s['out_in_frame']);layout=shot['layout']
        key=mapping.get('keying');source=key['masked_media'] if key else mapping.get('presentation_video',video)['path'];origin=key['first_frame'] if key else 0
        cmd=[ffmpeg,'-v','error','-nostdin','-y','-threads','2','-ss',str((s['source_in_frame']-origin)/fps),'-i',source,'-threads','2','-ss',str(s['audio_in_seconds']),'-i',audio['path']]
        graph='[0:v]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1[v]'
        if shot.get('asset'):
            cmd+=['-ss',str((s['out_in_frame']-shot['out_start'])/fps),'-i',shot['asset']]
            if layout=='pip':graph='[0:v]scale=308:173,setsar=1[c];[2:v]scale=960:540,setsar=1[g];[g][c]overlay=x=624:y=345[v]'
            elif layout=='side':graph='[0:v]scale=480:270,setsar=1[c];[2:v]scale=480:270,pad=960:540:0:135,setsar=1[g];[g][c]overlay=x=480:y=135[v]'
            elif layout=='graphic':graph='[2:v]scale=960:540,setsar=1[v]'
        if key and layout!='graphic':
            ks=key['settings'];fit=min(960/key['width'],540/key['height'])*ks['scale'];sw=round(key['width']*fit/2)*2;sh=round(key['height']*fit/2)*2
            x=480+ks['x']*960-sw/2;y=270+ks['y']*540-sh/2
            prefix=f'color=c=black@0:s=960x540:r={mapping["fps"]},format=rgba[clear];[0:v]format=rgba,scale={sw}:{sh}[fg];[clear][fg]overlay=x={x}:y={y}:format=auto:shortest=1,format=rgba[framed];'
            graph=prefix+graph.replace('[0:v]','[framed]')
            # Render transparency over a neutral background for review only.
            graph=graph.replace('[v]','[trans]')+f';color=c=0xeeeeee:s=960x540:r={mapping["fps"]}[bg];[bg][trans]overlay=shortest=1:format=auto[v]'
        cmd+=['-filter_complex_threads','1','-filter_complex',graph,'-map','[v]','-map','1:a:0','-frames:v',str(n),'-t',str(duration),'-r',mapping['fps'],'-c:v','libx264','-threads','2','-preset','ultrafast','-crf','25','-pix_fmt','yuv420p','-c:a','pcm_s16le','-ar','48000',str(partial)]
        if not dest.exists():
            print('Preview part %d/%d'%(i+1,len(spans)),flush=True);run(cmd,job,'Render preview part',None);partial.replace(dest)
        parts.append(dest)
    listing=folder/'concat.txt';listing.write_text(''.join("file '%s'\n"%p.name for p in parts),encoding='utf-8')
    output=job/'REVIEW_PREVIEW.partial.mp4'
    run([ffmpeg,'-v','error','-nostdin','-y','-f','concat','-safe','0','-i',str(listing),'-c:v','copy','-c:a','aac','-b:a','128k','-movflags','+faststart',str(output)],job,'Join preview')
    output.replace(job/'REVIEW_PREVIEW.mp4')
