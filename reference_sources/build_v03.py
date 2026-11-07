from pathlib import Path
import json,gzip,copy,uuid,hashlib,xml.etree.ElementTree as E
W=Path(r'C:\Users\Anigma PC\Documents\Codex\2026-10-13\ok-x20\work');P=W.parent/'outputs/Promotion Rule Project v02';O=Path('v03');T=254016000000;F=T//25
oldplan=json.loads((W/'edit_plan_v02.json').read_text());plan=json.loads((O/'edit_plan_v03.json').read_text());parts=plan['edits']
def tick(x):return round(x*25)*F
def mp(t):
 sec=t/T
 for i,e in enumerate(oldplan['edits'],1):
  if sec<e['timeline_start']+e['duration']-.00001:
   n=next((p for p in parts if p['take_v02']==i),None)
   if n is None:return tick(next(p for p in parts if p['take_v02']>i)['timeline_start'])
   return tick(n['timeline_start']+min(max(sec-n['v02_map_in'],0),n['duration']))
 return tick(plan['duration'])
raw=(P/'Promotion Rule - Editable v02.prproj').read_bytes();r=E.fromstring(gzip.decompress(raw));d={x.get('ObjectID'):x for x in r if x.get('ObjectID')};u={x.get('ObjectUID'):x for x in r if x.get('ObjectUID')};nid=max(map(int,d))+1
def put(x,path,val):
 z=x
 for k in path.split('/'):
  q=z.find(k)
  if q is None:q=E.SubElement(z,k)
  z=q
 z.text=str(val)
def add(x):
 global nid
 n=copy.deepcopy(x);n.set('ObjectID',str(nid));nid+=1;r.append(n);d[n.get('ObjectID')]=n;return n
def graph(oid,memo=None):
 if memo is None:memo={}
 if oid in memo:return d[memo[oid]]
 n=add(d[oid]);memo[oid]=n.get('ObjectID')
 for z in n.iter():
  ref=z.get('ObjectRef')
  if ref in d:z.set('ObjectRef',graph(ref,memo).get('ObjectID'))
 return n
def source(it):
 sc=d[it.find('ClipTrackItem/SubClip').get('ObjectRef')];cl=d[sc.find('Clip').get('ObjectRef')];return sc,cl,d[cl.find('Clip/Source').get('ObjectRef')]
def clone(it):
 n=add(it);ct=n.find('ClipTrackItem');ch=ct.find('ComponentOwner/Components');ch.set('ObjectRef',graph(ch.get('ObjectRef')).get('ObjectID'));sc,cl,src=source(it);nsc=add(sc);ncl=add(cl);ct.find('SubClip').set('ObjectRef',nsc.get('ObjectID'));nsc.find('Clip').set('ObjectRef',ncl.get('ObjectID'));put(ncl,'Clip/ClipID',uuid.uuid4())
 if n.tag=='AudioClipTrackItem':put(n,'ID',uuid.uuid4())
 return n
def remap_keys(n):
 ch=d[n.find('ClipTrackItem/ComponentOwner/Components').get('ObjectRef')]
 for a in ch.findall('ComponentChain/Components/Component'):
  fx=d[a.get('ObjectRef')]
  for b in fx.findall('Component/Params/Param'):
   param=d[b.get('ObjectRef')];ks=param.find('Keyframes')
   if ks is None or not ks.text:continue
   vals={}
   for row in ks.text.split(';'):
    if not row:continue
    cols=row.split(',');tm=mp(int(cols[0]));cols[0]=str(tm);vals[tm]=','.join(cols)
   ks.text=';'.join(vals[k] for k in sorted(vals))+';'
counts=[];active=[]
for sq in r.findall('Sequence'):
 for gr in sq.findall('TrackGroups/TrackGroup/Second'):
  group=d[gr.get('ObjectRef')]
  for link in group.findall('TrackGroup/Tracks/Track'):
   tr=u[link.get('ObjectURef')];arr=tr.find('ClipTrack/ClipItems/TrackItems')
   if arr is None:continue
   original=[d[z.get('ObjectRef')] for z in arr];arr.clear();arr.set('Version','1');name=tr.findtext('ClipTrack/Track/Name','')
   is_dialogue=(tr.tag=='AudioClipTrack' and len(original)==30) or ('Camera - tightened' in name)
   for part in parts:
    ns=tick(part['timeline_start']);old_a=tick(part['v02_map_in']);old_b=tick(part['v02_map_out'])
    if is_dialogue:
     donor=original[part['take_v02']-1];n=clone(donor);_,cl,_=source(n);si=tick(part['audio_in'] if tr.tag=='AudioClipTrack' else part['source_in']);ne=ns+tick(part['duration'])
     put(n,'ClipTrackItem/TrackItem/Start',ns);put(n,'ClipTrackItem/TrackItem/End',ne);put(cl,'Clip/InPoint',si);put(cl,'Clip/OutPoint',si+ne-ns);E.SubElement(arr,'TrackItem',Index=str(len(arr)),ObjectRef=n.get('ObjectID'));active.append(n);continue
    for it in original:
     s=int(it.findtext('ClipTrackItem/TrackItem/Start'));e=int(it.findtext('ClipTrackItem/TrackItem/End'));a=max(old_a,s);b=min(old_b,e)
     if b<=a:continue
     sc,cl,src=source(it);sin=int(cl.findtext('Clip/InPoint','0'));nested=src.tag in ['VideoSequenceSource','AudioSequenceSource'];n=clone(it);_,ncl,_=source(n);nstart=ns+a-old_a;nend=ns+b-old_a;nsi=nstart if nested else sin+a-s
     put(n,'ClipTrackItem/TrackItem/Start',nstart);put(n,'ClipTrackItem/TrackItem/End',nend);put(ncl,'Clip/InPoint',nsi);put(ncl,'Clip/OutPoint',nsi+nend-nstart)
     if nested:remap_keys(n)
     E.SubElement(arr,'TrackItem',Index=str(len(arr)),ObjectRef=n.get('ObjectID'));active.append(n)
   # Keep continuous nested room/layout layers continuous; cuts belong in the camera nest.
   if name in ['Room and watch-next move','Keyed Josh'] and len(original)==1 and len(arr):
    keep=d[arr[0].get('ObjectRef')];_,cl,_=source(keep);put(keep,'ClipTrackItem/TrackItem/End',tick(plan['duration']));put(cl,'Clip/OutPoint',tick(plan['duration']));arr[:]=[arr[0]];arr[0].set('Index','0')
   counts.append({'sequence':sq.findtext('Name'),'track':name,'clips':len(arr)})
   if 'Camera - tightened' in name:put(tr,'ClipTrack/Track/Name','Camera - v03 editorial source cuts')
newend=tick(plan['duration'])
for x in r:
 if x.tag in ['VideoSequenceSource','AudioSequenceSource']:put(x,'OriginalDuration',newend)
for x in r.iter():
 if x.tag in ['MZ.WorkOutPoint','Monitor.ProgramZoomOut']:x.text=str(newend)
 if x.tag in ['MZ.EditLine','TL.SQVisibleBaseTime']:x.text='0'
 if x.tag in ['ConformedAudioPath','PeakFilePath'] or x.tag.startswith('MediaFileHistory'):x.text=''
 if x.tag=='Name' and x.text=='Promotion Rule - 4K YouTube Edit v02':x.text='Promotion Rule - 4K YouTube Edit v03 REVIEW'
 if x.tag.startswith('ExportSettings.Info.') and x.text:
  try:
   data=json.loads(x.text)
   for k in ['OutPoint','CustomOut','WorkOut']:data[k]=newend
   data['CTI']=0
   if 'OutPath' in data:data['OutPath']=str(Path(r'E:\CareerOS\12_YouTube\08_promotion-rule\2026-10-14_edit_v03')/'Promotion Rule - v03 review export.mp4')
   x.text=json.dumps(data)
  except ValueError:pass
 if x.tag=='DVAMarker' and x.text:
  try:
   data=json.loads(x.text);m=data.get('DVAMarker',{});m['mStartTime']['ticks']=mp(m['mStartTime']['ticks']);x.text=json.dumps(data)
  except (ValueError,KeyError):pass
# Use known existing absolute media locations; make misleading relocation relatives explicit.
for x in r:
 fp=x.find('FilePath')
 if fp is not None and fp.text:
  rp=x.find('RelativePath')
  if rp is not None:rp.text=fp.text
out=O/'Promotion Rule - Editable v03.prproj';out.write_bytes(gzip.compress(E.tostring(r,encoding='utf-8',xml_declaration=True)))
# Validate active source durations, frame-alignment, graph and media paths.
for it in active:
 s=int(it.findtext('ClipTrackItem/TrackItem/Start'));e=int(it.findtext('ClipTrackItem/TrackItem/End'));_,cl,src=source(it);si=int(cl.findtext('Clip/InPoint'));so=int(cl.findtext('Clip/OutPoint'))
 assert s>=0 and e>s and si>=0 and so-si==e-s
 assert all(v%F==0 for v in [s,e,si,so])
refs={x.get('ObjectID') for x in r.iter() if x.get('ObjectID')};missing=[x.get('ObjectRef') for x in r.iter() if x.get('ObjectRef') and x.get('ObjectRef') not in refs]
base=E.fromstring(gzip.decompress(raw));baserefs={x.get('ObjectID') for x in base.iter() if x.get('ObjectID')};base_missing={x.get('ObjectRef') for x in base.iter() if x.get('ObjectRef') and x.get('ObjectRef') not in baserefs}
assert set(missing)<=base_missing,missing[:10]
paths=sorted(set(x.text for x in r.iter('FilePath') if x.text));assert all(Path(p).is_file() for p in paths)
report={'project':str(out.resolve()),'v02_sha256':hashlib.sha256(raw).hexdigest(),'v03_sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'duration_seconds':plan['duration'],'frame_count':round(plan['duration']*25),'camera_mic_ranges':len(parts),'media_paths_verified':len(paths),'graph_references':'pass','source_ranges_frame_alignment':'pass','native_premiere_open_and_play':'NOT VERIFIED - supported native UI tool unavailable','listening':'NOT PERFORMED - no listening tool','tracks':counts}
(O/'Project validation v03.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
