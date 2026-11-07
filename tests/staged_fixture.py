"""End-to-end HTTP + owned worker + real FFmpeg fixture. Never production media."""
import hashlib,json,sys,threading,time,urllib.request,urllib.error,wave
from pathlib import Path
import numpy as np
import av
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

if '--fixture-worker' in sys.argv:
    # Only this dedicated synthetic-data worker bypasses the recording guard.
    # Normal installed worker.py always keeps the guard. No application control.
    allowed=Path(sys.argv[sys.argv.index('--fixture-worker')+1]).resolve()
    args=sys.argv[sys.argv.index('--fixture-worker')+2:]
    job=Path(args[args.index('--job')+1]).resolve()
    assert job.is_relative_to(allowed/'Runs') and 'StagedFixture-' in str(allowed)
    import worker
    def fixture_guard():
        cfg=worker.read(job/'request.json')
        assert Path(cfg['workflow']['data_root']).resolve()==allowed/'Data'
        from dajoshi.staged_cut import context
        w,p,a=context(cfg,ROOT)
        for identity in w.source(p)['files'].values():
            path=Path(identity['path']).resolve();assert path.is_relative_to(allowed/'media')
            assert av.open(str(path)).duration<=9_000_000
    worker.guard_recording=fixture_guard;sys.argv=[str(ROOT/'worker.py')]+args
    sys.exit(worker.main())

from studio.server import Server
from studio.jobs import Jobs
from dajoshi.media import run,probe
from dajoshi.timing import build_map
from dajoshi.clean_cut import digest
import xml.etree.ElementTree as ET
stamp=time.strftime('%Y%m%d-%H%M%S');base=ROOT/'Evidence'/('StagedFixture-'+stamp);media=base/'media';media.mkdir(parents=True)
ff=r'E:\Videos\DaJoshiResolve\tools\ffmpeg.exe';checks=[]
def passed(name):checks.append(name);print('PASS '+name,flush=True)
sr=48000;pcm=np.zeros(8*sr,dtype=np.float32)
for a,b,hz in [(.35,.85,440),(2.35,3.45,880),(5.35,6.45,880)]:
    x,y=round(a*sr),round(b*sr);pcm[x:y]=.15*np.sin(2*np.pi*hz*np.arange(y-x)/sr)
audio=media/'microphone.wav'
with wave.open(str(audio),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(sr);f.writeframes((pcm*32767).astype('<i2').tobytes())
for name,color in [('original','green'),('pass1','blue')]:
    run([ff,'-v','error','-nostdin','-y','-f','lavfi','-i',f'color=red:s=320x180:r=25:d=2','-f','lavfi','-i',f'color={color}:s=320x180:r=25:d=6','-filter_complex_threads','1','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-c:v','libx264','-threads','2','-pix_fmt','yuv420p',str(media/(name+'.mp4'))],base,'Create synthetic fixture')
words=[dict(word=t,start=a,end=b) for t,a,b in [('Start',.4,.8),('This',2.5,2.7),('works',2.8,3.2),('This',5.5,5.7),('works',5.8,6.2)]]
transcript=media/'transcript.json';transcript.write_text(json.dumps([dict(words=words)]));sync=media/'sync.json';sync.write_text(json.dumps(dict(verified=True,offset_seconds=0,method='Synthetic identical clocks')))
server=Server(ROOT,base/'Data',command=[sys.executable,str(Path(__file__).resolve()),'--fixture-worker',str(base)])
# Keep fixture runs/requests under the allowlisted fixture root while preserving
# code/settings lookup through the installed ROOT in the dedicated worker.
server.jobs.root=base
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
def api(path,data=None,method=None):
    req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,data=None if data is None else json.dumps(data).encode(),headers={'X-DJ-Token':server.token,'Content-Type':'application/json'},method=method)
    with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
def rejected(path,data):
    try:api(path,data);raise AssertionError('Expected gate rejection')
    except urllib.error.HTTPError as e:assert e.code in (400,409)
def wait(jid,allowed=('complete',),seconds=90):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        j=server.jobs.status(server.store.job(jid));state=j['status']['state']
        if state not in ('starting','running'):
            assert state in allowed,(state,j['status']);return j
        time.sleep(.1)
    raise AssertionError('Fixture worker timeout')
report={}
try:
    p=api('/api/projects',dict(name='Synthetic staged clean-cut fixture'))
    p=api('/api/projects/'+p['id'],dict(revision=p['revision'],changes=dict(media=dict(video=str(media/'original.mp4'),audio=str(audio)))),method='PUT')
    path='/api/projects/'+p['id']+'/workflow/';rev=p['revision']
    rejected(path+'start-cut',dict(revision=rev));passed('HTTP launch blocked before Pass 1 approval')
    a=api(path+'pass1',dict(revision=rev,report=dict(review_media=str(media/'pass1.mp4'),transcript=str(transcript),sync_report=str(sync),note='Synthetic fixture only')))
    api(path+'approve',dict(revision=rev,artifact_id=a['id'],digest=a['digest'],checks=dict(sync=True,appearance=True,words=True),note='Synthetic fixture attestation only'))
    job=api(path+'start-cut',dict(revision=rev));job=wait(job['id']);passed('Owned staged worker completes real silence analysis, audio, preview and XML')
    candidate=api(path+'import-cut',dict(revision=rev,job_id=job['id']));assert len(candidate['payload']['words'])==5
    assert candidate['payload']['duration_frames']<200;passed('Measured silence removed while intentional repeated words remain')
    approvals=dict(semantic_sweep=True,all_joins_listened=True,transcript_checked=True,native_playback=True)
    api(path+'approve',dict(revision=rev,artifact_id=candidate['id'],digest=candidate['digest'],checks=approvals,note='Synthetic gate test; not human review of real footage'))
    rejected(path+'start-cut',dict(revision=rev));passed('Approved timing cannot be sent back through automatic cutter')
    new=api(path+'reject',dict(revision=rev,artifact_id=candidate['id'],digest=candidate['digest'],decisions=[dict(start=0,end=2,reason='Synthetic incomplete start',coordinate_space='source_video_seconds')]))
    assert not server.workflow.approved(server.store.get(p['id']),candidate)
    assert len(new['payload']['words'])==4 and new['payload']['words'][0]['word']=='This';passed('Semantic rejection creates new canonical word map and invalidates old approval')
    folder=Path(new['payload']['folder']);mapping=json.loads((folder/'timing_map.json').read_text())
    assert all(s['source_in_frame']>=50 for s in mapping['spans'])
    xml=ET.parse(folder/'EDITABLE_DRAFT.xml');tracks=xml.findall('.//sequence/media/video/track')
    assert len(tracks)==3 and all(c.findtext('enabled')=='FALSE' for c in tracks[2].findall('clipitem'))
    assert all(int(c.findtext('in'))>=50 for t in tracks for c in t.findall('clipitem'));passed('Rejected video frames absent from XML; original recovery track retained disabled')
    review=api(path+'render-cut',dict(revision=rev,artifact_id=new['id'],digest=new['digest'],review_fade_ms=0));review=wait(review['id']);rdir=Path(review['path'])
    assert digest(json.loads((rdir/'timing_map.json').read_text()))==new['payload']['map_digest'];passed('Exact candidate render preserves map and manual frame clock without replanning')
    with wave.open(str(rdir/'CLEAN_AUDIO.wav')) as f:
        assert f.getnframes()==round(mapping['duration_frames']/25*sr);samples=np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').astype(float)/32768
    freq=np.fft.rfftfreq(len(samples),1/sr);power=np.abs(np.fft.rfft(samples));p440=power[(freq>435)&(freq<445)].max();p880=power[(freq>875)&(freq<885)].max()
    assert p440<p880*.01 and p880>1;passed('Rejected 440 Hz speech marker absent from actual lossless audio; retained 880 Hz remains')
    with av.open(str(rdir/'REVIEW_PREVIEW.mp4')) as c:
        frames=list(c.decode(video=0));assert len(frames)==mapping['duration_frames']
        for frame_ in frames:
            rgb=frame_.to_ndarray(format='rgb24').mean(axis=(0,1));assert rgb[2]>rgb[0]*2 and rgb[2]>rgb[1]*2
    passed('Actual preview has exact frame count and approved blue Pass 1; rejected red frames absent')
    original=probe(media/'original.mp4');aud=probe(audio);manual=build_map([[.2,1.1],[2.2,3.6],[5.2,6.7]],'25',0,8,8);manual.update(source_video=original,source_audio=aud,sync=dict(verified=True,offset_seconds=0,timeline_offset_seconds=0))
    imported=api(path+'import-map',dict(revision=rev,mapping=manual,source_identities=server.workflow.source(p)['files'],note='Synthetic manually polished frame spans'))
    got=json.loads((Path(imported['payload']['folder'])/'timing_map.json').read_text());assert got['spans']==manual['spans'] and got['duration_frames']==manual['duration_frames'];passed('Manual timing import preserves every frame span exactly')
    # Cancellation/recovery uses a fixture-only owned process and unchanged snapshot.
    cancel=api(path+'render-cut',dict(revision=rev,artifact_id=imported['id'],digest=imported['digest']));api('/api/jobs/'+cancel['id']+'/cancel',{})
    cancel=wait(cancel['id'],allowed=('cancelled','complete'))
    assert cancel['status']['state']=='cancelled','Fixture completed before cancellation could be exercised'
    api('/api/jobs/'+cancel['id']+'/resume',{});resumed=wait(cancel['id']);assert resumed['status']['state']=='complete';passed('Cancelled owned job resumes from unchanged exact snapshot')
    before=server.store.get(p['id']);p=api('/api/projects/'+p['id'],dict(revision=rev,changes=dict(notes='New project revision')),method='PUT')
    rejected(path+'render-cut',dict(revision=rev,artifact_id=imported['id'],digest=imported['digest']));passed('Stale project revision is rejected before a new process starts')
    report=dict(checks=checks,fixture_only=True,project=p['id'],directory=str(base),audio_marker_ratio=float(p440/p880),native_playback_verified=False,human_listening_verified=False,review_job=str(rdir),no_external_api=True)
finally:
    server.shutdown();server.server_close();thread.join()
    (base/'verification.json').write_text(json.dumps(report or dict(checks=checks,incomplete=True),indent=2))
print(json.dumps(report,indent=2),flush=True)
