"""Measured local artifact verification; does not certify Premiere or editorial taste."""
import json,sys
from pathlib import Path
from fractions import Fraction
import av,numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dajoshi.keying import identity,digest,validate_coverage
from dajoshi.interchange import validate
root=Path(__file__).resolve().parents[1];job=Path(sys.argv[1]).resolve();key=json.loads((job/'keying/manifest.json').read_text());mapping=json.loads((job/'timing_map.json').read_text())
assert identity(key['source']['path'])==key['source'];validate_coverage(mapping)
results={}
for name in ['raw_media','masked_media']:
    path=Path(key[name]);samples=[];count=0;pts=[]
    with av.open(str(path)) as container:
        stream=container.streams.video[0];stream.thread_count=2
        assert str(stream.average_rate)==key['fps'];assert stream.codec_context.name=='prores'
        pixfmt=stream.codec_context.format.name;assert 'a' in pixfmt,pixfmt
        for frame in container.decode(stream):
            pts.append(float(frame.pts*frame.time_base));count+=1
            if count in [1,key['frames']//2,key['frames']]:
                rgba=frame.to_ndarray(format='rgba');a=rgba[:,:,3];samples.append(dict(frame=count,zero=int((a==0).sum()),opaque=int((a==255).sum()),partial=int(((a>0)&(a<255)).sum())))
                expected=Image.open(job/'keying'/('raw_png' if name=='raw_media' else 'masked_png')/(f'{count:06d}.png')).getchannel('A')
                error=np.abs(a.astype(float)-np.array(expected).astype(float));assert np.mean(error)<2,(name,count,float(np.mean(error)))
                if name=='masked_media':assert (a==0).mean()>.1 and (a==255).mean()>.01
        assert count==key['frames'];assert all(abs(t-i/float(Fraction(key['fps'])))<.0001 for i,t in enumerate(pts))
    results[name]=dict(frames=count,fps=key['fps'],pixel_format=pixfmt,alpha_samples=samples,sha256=digest(path),alpha_decode_checked=True)
xml=validate((job/'EDITABLE_DRAFT.xml').read_bytes());assert key['source']['path'].replace('\\','/') in [x.replace('\\','/') for x in xml['referenced_files']]
with av.open(str(job/'REVIEW_PREVIEW.mp4')) as c:
    count=sum(1 for _ in c.decode(video=0));assert count==mapping['duration_frames'];assert len(c.streams.audio)==1
report=dict(source_unchanged=True,transparent_media=results,xml=xml,review_frames=count,output_duration_seconds=count/float(Fraction(mapping['fps'])),sync=mapping['sync'],cancel_status=json.loads((job/'cancel-test-status.json').read_text()),reused_frames=key['reused_frames'],native_premiere_import=False,native_playback=False,human_listening=False,production_approved=False)
(job/'integration-verification.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ['sync','transparent_media','xml']}))
