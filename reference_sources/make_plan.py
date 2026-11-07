from pathlib import Path
import json,csv,re,shutil
ROOT=Path(__file__).resolve().parent
OUT=ROOT.parent/'outputs'/'Promotion Rule Project'
SRC=Path(r'E:\Videos\Promotion Policy\C0323')
for sub in ['Media/Source','Media/Animations','Media/Graphics','References']: (OUT/sub).mkdir(parents=True,exist_ok=True)
for p in [SRC/'C0323_green.mp4',next(SRC.glob('*.wav'))]:
 dest=OUT/'Media/Source'/p.name
 if not dest.exists():shutil.copy2(p,dest)
for p in SRC.glob('*.md'):shutil.copy2(p,OUT/'References'/p.name)
for p in SRC.glob('*.html'):shutil.copy2(p,OUT/'References'/p.name)
q=lambda t:round(t*25)/25
# Removed source-audio intervals: abandoned takes, duplicate phrases and long waits.
removed=[
 (32.04,33.88,'Repeated and'),(37.76,50.72,'First promise take and spoken correction'),
 (63.60,68.60,'Long list pause'),(81.61,84.68,'Section pause'),
 (142.50,148.50,'Abandoned stays-with-people line'),(170.10,173.18,'Section pause'),
 (236.40,241.12,'Two failed rule-working starts'),(247.48,260.44,'Abandoned promotion-policy list; retain last complete take'),
 (292.67,296.13,'Section pause'),(300.88,302.28,'Someone turns up restart'),
 (330.83,342.20,'Unfair-from-outside abandoned passage'),(363.96,369.45,'Section pause'),
 (379.04,380.12,'You take it restart'),(409.03,411.83,'Section pause'),
 (458.55,461.76,'Wrong-person restart'),(468.31,470.40,'Exec recap restart'),
 (508.81,509.83,'Section pause'),(537.20,540.80,'Competence quote restart'),
 (558.07,566.58,'Permission argument first take'),(585.20,587.10,'Exact-words signpost restart'),
 (593.80,596.40,'Section pause'),(619.40,623.43,'Renegotiation false start'),
 (625.97,628.75,'Contract date restart'),(654.01,669.42,'Rejected explanation and do-that-again'),
 (696.44,698.04,'Assessed word correction'),(712.72,715.76,'Business reason restart'),
 (718.72,719.76,'Which-is-can restart')
]
removed=[(q(a),q(b),reason) for a,b,reason in removed]
start=q(18.80);end=q(753.16);pos=start;cursor=0;edits=[]
for a,b,reason in removed+[(end,end,'end')]:
 if a>pos:
  edits.append({'audio_in':pos,'audio_out':a,'source_in':q(pos-2.44),'source_out':q(a-2.44),'timeline_start':q(cursor),'duration':q(a-pos)})
  cursor=q(cursor+a-pos)
 pos=b
def timeline(t):
 t=q(t)
 for e in edits:
  if e['audio_in']<=t<=e['audio_out']+1e-5:return q(e['timeline_start']+t-e['audio_in'])
 raise ValueError(f'Cue {t} falls in removed audio')
# Cues in the original microphone recording, manually conformed to the actual delivery.
specs=[
 (1,26.04,37.76,[26.04,27.06,29.06,34.34]),
 (2,52.66,73.23,[52.66,58.18,68.79]),
 (3,89.74,101.44,[89.74,91.10,94.10]),
 (4,107.40,125.20,[107.40,109.96,112.68,117.38]),
 (5,157.42,170.10,[157.42,161.60,166.32]),
 (6,184.32,197.10,[184.32,187.84,190.64,193.00]),
 (7,211.16,236.40,[211.16,223.96,231.50,234.74]),
 (8,244.30,269.12,[244.30,261.54,263.08,264.48,266.24]),
 (9,272.74,282.47,[272.74,274.47,278.47]),
 (10,296.17,316.55,[296.17,302.28,307.90,313.13]),
 (11,373.85,389.87,[373.85,377.03,380.12,386.77]),
 (12,397.10,409.03,[397.10,403.00,406.53]),
 (13,425.57,468.31,[425.57,432.59,436.91,438.15,447.63,451.49,463.65]),
 (14,474.61,492.40,[474.61,477.43,480.91,487.27,489.83]),
 (15,512.13,585.20,[512.13,514.85,531.37,534.95,540.85,554.53,567.10,568.62,574.40,578.36]),
 (16,596.52,608.54,[596.52,601.08]),
 (17,609.78,654.01,[609.78,613.40,628.77,633.30,638.23,643.21,645.20,650.99]),
 (18,669.48,703.52,[669.48,674.38,678.40,682.20,685.26,688.12,692.12,700.70]),
 (19,703.52,722.40,[703.52,706.40,709.64,715.76])
]
html=next(SRC.glob('*.html')).read_text(encoding='utf-8')
scenes=re.findall(r'<section class="scene (pip|fs)" data-from="(\d+)" data-to="(\d+)">',html)
animations=[]
for (num,a,b,cues),(typ,first,last) in zip(specs,scenes):
 ta=timeline(a);tb=timeline(b);first=int(first);last=int(last)
 assert len(cues)==last-first+1
 animations.append({'number':num,'type':'pip' if typ=='pip' else 'full','start':ta,'end':tb,'layout_start':q(ta-.48 if typ=='pip' else ta),'duration':q(tb-ta),'cues':[{'beat':first+i,'time':q(timeline(t)-ta),'audio_time':t} for i,t in enumerate(cues)],'file':str(OUT/'Media/Animations'/f'Animation_{num:02d}.mp4')})
plan={'audio_offset_seconds':2.44,'fps':25,'width':3840,'height':2160,'duration':cursor,'edits':edits,'removed':[{'audio_start':a,'audio_end':b,'reason':reason} for a,b,reason in removed],'animations':animations,'end_screen_start':timeline(743.36),'end_screen_duration':1.28}
(ROOT/'edit_plan.json').write_text(json.dumps(plan,indent=2))
with (OUT/'References/edits.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=['source_in','source_out']);w.writeheader();w.writerows({k:e[k] for k in w.fieldnames} for e in edits)
with (OUT/'References/shots.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=['start','end','type','animation','animation_in','label']);w.writeheader()
 for a in animations:w.writerow({'start':a['layout_start'],'end':a['end'],'type':a['type'],'animation':a['number'],'animation_in':0,'label':f"Animation {a['number']:02d}"})
 w.writerow({'start':plan['end_screen_start'],'end':cursor,'type':'end','label':'YouTube watch-next - blank left half'})
config={'camera':'../Media/Source/C0323_green.mp4','audio':'../Media/Source/'+next(SRC.glob('*.wav')).name,'output':'../Promotion Rule - Edit v01.prproj','edits':'edits.csv','shots':'shots.csv','audio_offset_seconds':2.44,'title':'Promotion Rule - 4K YouTube Edit','animations':{str(a['number']):'../Media/Animations/'+Path(a['file']).name for a in animations}}
(OUT/'References/new-video.json').write_text(json.dumps(config,indent=2))
words=[w for s in json.loads((ROOT/'transcript.json').read_text()) for w in s['words']]
lines=[]
for e in edits:
 txt=' '.join(w['word'].strip() for w in words if e['audio_in']<=w['start']<e['audio_out'])
 lines.append(f"{e['timeline_start']:.2f} [{e['audio_in']:.2f}-{e['audio_out']:.2f}] {txt}")
(ROOT/'selected_transcript.txt').write_text('\n\n'.join(lines),encoding='utf-8')
print('Duration',cursor,'seconds; clips',len(edits),'removed',q(758.104666-cursor))
print('Animation durations',[(a['number'],a['duration']) for a in animations])
