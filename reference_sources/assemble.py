"""Build an editable Premiere assembly from Josh's saved v15 effect graph.
No Adobe UI, plugin installation, or original-project modification is performed.
Usage: python assemble.py input/new-video.json
"""
from pathlib import Path
import argparse,copy,csv,gzip,json,math,re,subprocess,uuid,xml.etree.ElementTree as E
KIT=Path(__file__).resolve().parent
T=254016000000; F=T//25

def tick(v):
 n=float(v)*25
 if abs(n-round(n))>1e-5:raise ValueError(f'{v}s is not aligned to a 25 fps frame')
 return round(n)*F

def put(e,path,value):
 n=e.find(path)
 if n is None:
  parent=e
  for tag in path.split('/'):
   q=parent.find(tag)
   if q is None:q=E.SubElement(parent,tag)
   parent=q
  n=parent
 n.text=str(value)

def build(config_path):
 cfg=json.loads(config_path.read_text(encoding='utf-8-sig')); base=config_path.parent
 def path(v):return (base/v).resolve()
 camera=path(cfg['camera']); audio=path(cfg['audio']); out=path(cfg['output'])
 if out.exists():raise ValueError('Output already exists; choose a new name. Nothing overwritten.')
 for p in [camera,audio]:
  if not p.is_file():raise ValueError(f'Missing input: {p}')
 edits=list(csv.DictReader(path(cfg['edits']).open(encoding='utf-8-sig',newline='')))
 shots=list(csv.DictReader(path(cfg['shots']).open(encoding='utf-8-sig',newline='')))
 if not edits:raise ValueError('edits.csv needs at least one source range')
 ranges=[];duration=0;offset=tick(cfg['audio_offset_seconds'])
 for row in edits:
  a,b=tick(row['source_in']),tick(row['source_out'])
  if a<0 or b<=a or a+offset<0:raise ValueError('Invalid camera/audio range')
  ranges.append((duration,a,b));duration+=b-a
 # v15 contains 879.84 seconds of exact lighting/drift curves; longer edits hold the last values.
 if duration>tick(879.84):raise ValueError('This exact-curve template supports up to 14:39:21. Extend the lighting curves in Premiere for longer videos.')
 windows=[]
 for row in shots:
  a,b=tick(row['start']),tick(row['end']);kind=row['type'].strip().lower()
  if kind not in ['talk','pip','half','full','end']:raise ValueError(f'Unknown shot type: {kind}')
  if a<0 or b<=a or b>duration:raise ValueError('Shot outside edited duration')
  if windows and a<windows[-1][1]:raise ValueError('Shots must be in order without overlap')
  if kind in ['half','end'] and b-a<tick(3.0):raise ValueError('Half/end shot needs at least 3 seconds')
  windows.append((a,b,kind,row))
 animations={}
 for a,b,kind,row in windows:
  if kind in ['pip','half','full']:
   slot=int(row['animation']);p=path(cfg['animations'][str(slot)])
   if not 1<=slot<=21 or not p.is_file():raise ValueError(f'Missing animation slot {slot}')
   animations[slot]=p
 ff=Path(cfg.get('ffmpeg',str(KIT/'tools/ffmpeg.exe')))
 def probe(p,video=False):
  z=subprocess.run([str(ff),'-hide_banner','-i',str(p)],capture_output=True,text=True,errors='replace').stderr
  m=re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)',z)
  if not m:raise ValueError(f'Cannot read media duration: {p}')
  sec=int(m[1])*3600+int(m[2])*60+float(m[3])
  if video:
   dim='3840x2160' if p==camera else '1920x1080'
   if dim not in z or not re.search(r'25(?:\.0+)? fps',z):raise ValueError(f'{p.name}: expected {dim}, 25 fps. Convert or use Premiere to adapt before reusing geometry.')
  elif p==audio and '48000 Hz' not in z:raise ValueError('Clean audio must be 48 kHz')
  return sec
 media_seconds={str(camera):probe(camera,True),str(audio):probe(audio)}
 if max(b for _,a,b in ranges)>media_seconds[str(camera)]*T+F:raise ValueError('Camera range beyond source duration')
 if max(b+offset for _,a,b in ranges)>media_seconds[str(audio)]*T+F:raise ValueError('Audio range beyond source duration')
 for slot,p in animations.items():media_seconds[str(p)]=probe(p,True)
 for a,b,kind,row in windows:
  if kind in ['pip','half','full']:
   delay=tick(0.48 if kind=='pip' else 1.60 if kind=='half' else 0)
   tail=tick(1.28 if kind=='half' else 0)
   need=(b-a-delay-tail)/T+float(row.get('animation_in') or 0)
   if need<=0 or media_seconds[str(animations[int(row['animation'])])]+0.02<need:raise ValueError('Animation needs more prepared hold/handle duration')
 r=E.fromstring(gzip.decompress((KIT/'source/four-shot-donor.prproj').read_bytes()))
 # Rebase copied reusable assets to THIS kit when it is copied to the next project.
 for m in r.findall('Media'):
  old=m.findtext('FilePath','');name=Path(old).name
  asset=KIT/'assets'/('PiP_Frame_Gold_v01.png' if name=='PiP_Frame.png' else name)
  if asset.is_file():
   for n in m:
    if n.tag in ['FilePath','ActualMediaFilePath','RelativePath']:n.text=str(asset.resolve())
 ids={x.get('ObjectID'):x for x in r if x.get('ObjectID')};uids={x.get('ObjectUID'):x for x in r if x.get('ObjectUID')};nextid=max(map(int,ids))+1
 def add(n):
  nonlocal nextid
  n.set('ObjectID',str(nextid));nextid+=1;r.append(n);ids[n.get('ObjectID')]=n;return n
 def clone_graph(oid,memo=None):
  if memo is None:memo={}
  if oid in memo:return ids[memo[oid]]
  n=add(copy.deepcopy(ids[oid]));memo[oid]=n.get('ObjectID')
  for z in n.iter():
   ref=z.get('ObjectRef')
   if ref and ref in ids:z.set('ObjectRef',clone_graph(ref,memo).get('ObjectID'))
  return n
 def clip(oid,start,end,source_in=None):
  old=ids[str(oid)];n=add(copy.deepcopy(old));ct=n.find('ClipTrackItem')
  for tag in ['HeadTransition','TailTransition']:
   for e in ct.findall(tag):ct.remove(e)
  chain=ct.find('./ComponentOwner/Components');chain.set('ObjectRef',clone_graph(chain.get('ObjectRef')).get('ObjectID'))
  sc=add(copy.deepcopy(ids[ct.find('SubClip').get('ObjectRef')]));ct.find('SubClip').set('ObjectRef',sc.get('ObjectID'))
  src=add(copy.deepcopy(ids[sc.find('Clip').get('ObjectRef')]));sc.find('Clip').set('ObjectRef',src.get('ObjectID'))
  put(src,'Clip/ClipID',uuid.uuid4());put(n,'ClipTrackItem/TrackItem/Start',start);put(n,'ClipTrackItem/TrackItem/End',end)
  sin=int(src.findtext('Clip/InPoint','0')) if source_in is None else source_in
  put(src,'Clip/InPoint',sin);put(src,'Clip/OutPoint',sin+end-start)
  return n
 def params(item,effect):
  ch=ids[item.find('./ClipTrackItem/ComponentOwner/Components').get('ObjectRef')]
  for ref in ch.findall('./ComponentChain/Components/Component'):
   fx=ids[ref.get('ObjectRef')]
   if fx.findtext('./Component/DisplayName')==effect:return {ids[p.get('ObjectRef')].findtext('Name'):ids[p.get('ObjectRef')] for p in fx.findall('./Component/Params/Param')}
  return {}
 def key(p,points):
  sk=p.findtext('StartKeyframe');tail=','.join(sk.split(',')[2:]);put(p,'Keyframes',''.join(f'{t},{v},{tail};' for t,v in points));put(p,'IsTimeVarying','true')
 def tracks(seq,kind):
  ans=[]
  for ref in seq.findall('./TrackGroups/TrackGroup/Second'):
   g=ids[ref.get('ObjectRef')]
   if g.tag==kind+'TrackGroup':ans=[uids[t.get('ObjectURef')] for t in g.findall('./TrackGroup/Tracks/Track')]
  return ans
 def clear(tr):
  for typ in ['ClipItems','TransitionItems']:
   c=tr.find('ClipTrack/'+typ)
   if c is not None:
    for e in c.findall('TrackItems'):c.remove(e)
 def insert(tr,item):
  c=tr.find('ClipTrack/ClipItems');arr=c.find('TrackItems')
  if arr is None:arr=E.SubElement(c,'TrackItems',Version='1')
  E.SubElement(arr,'TrackItem',Index=str(len(arr)),ObjectRef=item.get('ObjectID'))
 seq={s.findtext('Name'):s for s in r.findall('Sequence')};main=seq['FBI - Dialogue Edit'];room=seq['FBI - Room Composite'];subject=seq['FBI - Keyed Subject']
 mainv=tracks(main,'Video');maina=tracks(main,'Audio');keyv=tracks(subject,'Video');roomv=tracks(room,'Video')
 for s in seq.values():
  for tr in tracks(s,'Video')+tracks(s,'Audio'):clear(tr)
  for z in s.findall('./Node/Properties/*'):
   if z.tag in ['MZ.EditLine','MZ.WorkInPoint','Monitor.ProgramZoomIn','TL.SQVisibleBaseTime']:z.text='0'
   if z.tag in ['MZ.OutPoint','MZ.InPoint']:z.text='-101606400000000000'
   if z.tag in ['MZ.WorkOutPoint','Monitor.ProgramZoomOut']:z.text=str(duration)
 for outin,a,b in ranges:
  for track,donor in zip(keyv,[638,673]):insert(track,clip(donor,outin,outin+b-a,a))
  insert(maina[0],clip(525,outin,outin+b-a,a+offset))
 for tr,donor in zip(roomv,[601,602,603,604]):insert(tr,clip(donor,0,duration))
 # The whole room moves together, preserving saved scale drift, grain and brightness.
 mainroom=clip(385,0,duration,0);insert(mainv[0],mainroom)
 position=params(mainroom,'Motion')['Position'];moves={0:'0.5:0.5',duration:'0.5:0.5'}
 originalpos=ids['2099'].findtext('Keyframes');entry=[]
 for v in originalpos.split(';'):
  if not v:continue
  parts=v.split(',');t=int(parts[0])-tick(54.16)
  if 0<=t<=tick(1.28):entry.append((t,parts[1]))
 panelentry=[]
 for v in ids['3150'].findtext('Keyframes').split(';'):
  if v:
   p=v.split(',');t=int(p[0])
   if 0<=t<=tick(1.28):panelentry.append((t,p[1]))
 markerlist=ids['293'].find('Markers');markerlist.clear();markerlist.set('Version','1')
 for start,end,kind,row in windows:
  guid=str(uuid.uuid4());m=add(copy.deepcopy(ids['344']));put(m,'DVAMarker',json.dumps({'DVAMarker':{'mMarkerID':guid,'mName':row.get('label') or kind.upper(),'mStartTime':{'ticks':start},'mType':'Comment'}}));q=E.SubElement(markerlist,'Marker',Version='1',Index=str(len(markerlist)));put(q,'First',guid);E.SubElement(q,'Second',ObjectRef=m.get('ObjectID'))
  if kind=='pip':
   insert(mainv[1],clip(450,start,start+tick(.48),0))
   insert(mainv[3],clip(486,start,end,0));insert(mainv[4],clip(499,start,end,start));insert(mainv[5],clip(512,start,end,0))
  if kind in ['half','end']:
   for t,v in entry:moves[start+t]=v
   if kind=='half':
    moves[end-tick(1.28)]='0.75:0.5'
    for t,v in entry:moves[end-t]=v
   else:moves[end]='0.75:0.5'
   panel=clip(451,start,end,0);pv={t:v for t,v in panelentry}
   if kind=='half':
    pv[end-start-tick(1.28)]='0.5:0.5'
    for t,v in panelentry:pv[end-start-t]=v
   else:pv[end-start]='0.5:0.5'
   key(params(panel,'Motion')['Position'],sorted(pv.items()));insert(mainv[1],panel)
  if kind in ['pip','half','full']:
   slot=int(row['animation']);a=start+tick(.48 if kind=='pip' else 1.6 if kind=='half' else 0);b=end-tick(1.28 if kind=='half' else 0)
   anim=clip(466 if kind=='half' else 465,a,b,tick(row.get('animation_in') or 0))
   # Point to the chosen animation's existing master/source; geometry comes from the layout donor.
   asc=ids[anim.find('./ClipTrackItem/SubClip').get('ObjectRef')];don=ids[ids[str(464+slot)].find('./ClipTrackItem/SubClip').get('ObjectRef')]
   asc.find('MasterClip').set('ObjectURef',don.find('MasterClip').get('ObjectURef'));put(asc,'Name',animations[slot].name)
   ac=ids[asc.find('Clip').get('ObjectRef')];dc=ids[don.find('Clip').get('ObjectRef')];ac.find('Clip/Source').set('ObjectRef',dc.find('Clip/Source').get('ObjectRef'));insert(mainv[2],anim)
 key(position,sorted(moves.items()))
 # Relink duplicate camera/audio objects together; retain exact saved stream colour settings.
 for m in r.findall('Media'):
  old=m.findtext('FilePath','');name=Path(old).name;new=None
  if name in ['C0322.MP4','camera-sample.mp4']:new=camera
  elif name in ['FBI_clean_dialogue_levelled.wav','audio-sample.wav']:new=audio
  else:
   match=re.fullmatch(r'Animation_(\d+)\.mp4',name)
   if match and int(match[1]) in animations:new=animations[int(match[1])]
  if new:
   for e in m:
    if e.tag in ['FilePath','ActualMediaFilePath','RelativePath']:e.text=str(new)
    if e.tag.startswith('MediaFileHistory'):e.text=str(new)
   put(m,'Title',new.name);put(m,'FileKey',uuid.uuid4())
   if name in ['C0322.MP4','camera-sample.mp4']:put(m,'Start',0)
   for ref in list(m):
    if ref.tag in ['VideoStream','AudioStream'] and ref.get('ObjectRef'):
     put(ids[ref.get('ObjectRef')],'Duration',round(media_seconds[str(new)]*T))
 for x in r:
  if x.tag in ['VideoMediaSource','AudioMediaSource']:
   ref=x.find('./MediaSource/Media')
   if ref is not None:
    m=uids[ref.get('ObjectURef')];p=m.findtext('FilePath','')
    if p in media_seconds:put(x,'OriginalDuration',round(media_seconds[p]*T))
  if x.tag in ['VideoSequenceSource','AudioSequenceSource']:put(x,'OriginalDuration',duration)
 names={'FBI - Dialogue Edit':cfg.get('title','NEW VIDEO - Four shots'),'FBI - Room Composite':'REUSE - Room Composite','FBI - Keyed Subject':'REPLACE - Keyed Camera','C0322':'Unused original camera sequence','FBI - Animation Assets':'Unused original assets sequence'}
 for x in r.iter():
  if x.tag=='Name' and x.text in names:x.text=names[x.text]
  if x.tag in ['ConformedAudioPath','PeakFilePath']:x.text=''
  if x.tag.startswith('MediaFileHistory'):x.text=''
  if x.tag=='RelativePath' and x.text and ':\\' in x.text:x.text=str(Path(x.text))
 # Keep only working sequences visible in Sequences bin; immutable reference stays in separate file.
 for b in r.findall('BinProjectItem'):
  if b.findtext('ProjectItem/Name')=='Sequences':
   arr=b.find('ProjectItemContainer/Items')
   for it in list(arr):
    obj=uids[it.get('ObjectURef')]
    if obj.findtext('ProjectItem/Name','').startswith('Unused original'):arr.remove(it)
   for i,it in enumerate(arr):it.set('Index',str(i))
 out.parent.mkdir(parents=True,exist_ok=True)
 data=E.tostring(r,encoding='utf-8',xml_declaration=True)
 with out.open('xb') as f:f.write(gzip.compress(data))
 report={'project':str(out),'duration_seconds':duration/T,'dialogue_cuts':len(ranges),'shots':[{'type':k,'start':a/T,'end':b/T} for a,b,k,_ in windows],'camera':str(camera),'audio':str(audio),'audio_offset_seconds':offset/T,'validation':'XML and media conform only. Premiere reopen and visual playback still required.','notes':['Camera and clean audio source ranges are explicit. No take selection or speech recognition is performed.','Animation files must already have their entrances/holds conformed to the spoken cues.','Audio joins are hard cuts; add short Constant Power fades and listen in Premiere.','Exact original masks/effects are cloned; new lighting/green-screen changes may require key adjustment.']}
 out.with_suffix('.build.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
 return out
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('config',type=Path,nargs='?',default=KIT/'input/new-video.json');a=p.parse_args()
 try:build(a.config.resolve())
 except Exception as e:raise SystemExit('STOPPED safely: '+str(e))
