"""Validate generated artifacts without opening or modifying Premiere."""
import json,sys,xml.etree.ElementTree as E
from pathlib import Path
from fractions import Fraction
import av
from dajoshi.media import probe
from worker import read,write,fingerprint
from dajoshi.timing import split_spans

job=Path(sys.argv[1]).resolve();mapping=read(job/'timing_map.json');shots=read(job/'shots.json');root=E.parse(job/'EDITABLE_DRAFT.xml')
fps=float(Fraction(mapping['fps']));expected=mapping['duration_frames']
video_paths=[e.text for e in root.findall('.//pathurl')]
assert Path(mapping['source_video']['path']).as_uri() in video_paths
assert Path(mapping['source_audio']['path']).as_uri() in video_paths
clips=root.findall('.//clipitem');ids={e.get('id') for e in clips}
for link in root.findall('.//linkclipref'):assert link.text in ids
for c in clips:
    assert int(c.findtext('out'))>int(c.findtext('in'))>=0
    assert int(c.findtext('end'))-int(c.findtext('start'))==int(c.findtext('out'))-int(c.findtext('in'))
with av.open(str(job/'REVIEW_PREVIEW.mp4')) as c:
    stream=c.streams.video[0];stream.thread_count=2;count=0;stills={0,expected//2,expected-1}
    for f in c.decode(stream):
        if count in stills:f.to_image().save(job/('preview-frame-%04d.jpg'%count))
        count+=1
assert count==expected,(count,expected)
info=probe(job/'REVIEW_PREVIEW.mp4')
assert abs(info['duration']-expected/fps)<.1
audio_lengths=[]
spans=split_spans(mapping,[x for s in shots for x in [s['out_start'],s['out_end']]])
for i,s in enumerate(spans):
    path=job/'preview_parts'/('%05d.mkv'%i)
    with av.open(str(path)) as c:samples=sum(f.samples for f in c.decode(audio=0))
    wanted=round((s['out_out_frame']-s['out_in_frame'])*48000/fps)
    assert abs(samples-wanted)<=1,(path,samples,wanted)
    audio_lengths.append(dict(part=i,samples=samples,expected_samples=wanted))
holds=[]
for s in shots:
    if not s.get('asset'):continue
    actual=probe(s['asset'])['duration'];used=(s['out_end']-s['out_start'])/fps
    assert actual-used>=29.9
    holds.append(dict(beat=s['beat'],used_seconds=used,available_seconds=actual,extra_hold_seconds=actual-used))
sources=read(job/'inputs.json');unchanged=all(fingerprint(p['path'])==p for p in sources.values());assert unchanged
report=dict(structural_validation=True,preview_frames=count,expected_frames=expected,preview_duration_seconds=info['duration'],source_paths_preserved=True,inputs_unchanged=unchanged,linked_clip_ids_valid=True,animation_handles=holds,preview_pcm_lengths=audio_lengths,native_import_tested=False,native_playback_tested=False,human_listening=False)
write(job/'VALIDATION.json',report);print(json.dumps(report,indent=2))
