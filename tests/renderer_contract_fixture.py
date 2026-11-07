"""Actual Chrome + FFmpeg fixture; never opens a production deck or project."""
import json,sys
from pathlib import Path
from PIL import Image
import av
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dajoshi.animation import render,render_contract
root=Path(__file__).resolve().parents[1];out=root/'Evidence'/'RendererContract';out.mkdir(parents=True,exist_ok=True)
deck=out/'fixture.html';deck.write_text('''<!doctype html><style>
body{margin:0}.scene{position:absolute;inset:0;transition:opacity 2s;opacity:0;background:#ff0000}.scene.on{opacity:1}#target{background:#ffffff}
.item{position:absolute;left:30px;top:30px;width:40px;height:40px;background:#00ff00;opacity:0}.item.on{animation:enter .8s both}
#new{left:100px;background:#0000ff}.gone{position:absolute;inset:0;background:#ff00ff}
@keyframes enter{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
#ticker{position:absolute;left:200px;top:110px;width:40px;height:40px;background:#222}
</style><div class="scene on" id="old"></div><div class="scene" id="target"><div class="item" id="inherited"></div><div class="item" id="new"></div><div class="gone"></div><div id="ticker"></div></div>
<script>window.go=(b)=>{document.querySelectorAll('.scene').forEach(x=>x.classList.remove('on'));document.querySelector(b===0?'#old':'#target').classList.add('on');if(b>=1)document.querySelector('#inherited').classList.add('on');if(b>=2){document.querySelector('#new').classList.add('on');let n=0;setInterval(()=>{n++;document.querySelector('#ticker').style.transform=`translateX(${n%40}px)`;document.querySelector('#ticker').style.background=n>35?'#ffff00':'#222'},100)}};</script>''',encoding='utf-8')
contract=dict(version=2,scene_selector='.scene',active_selector='.scene.on',removed_selector='.gone',inherited_selector='.scene.on .item.on',settle_ms=1000,motion_frames=50,phase_frames=0,handle_frames=0,width=320,height=180)
ff=r'E:\Videos\DaJoshiResolve\tools\ffmpeg.exe'
reports=[]
for name,frames,phase in [('full',50,0),('split-left',30,0),('split-right',20,30)]:
 print('Rendering fixture '+name,flush=True)
 reports.append(render(str(deck),2,1,frames,'10',str(out/(name+'.mp4')),str(out),ff,dict(contract,phase_frames=phase)))
frames=[Path(r['frame_directory']) for r in reports]
checks=[]
first=Image.open(frames[0]/'000000.png').convert('RGB')
assert first.getpixel((5,5))==(255,255,255);checks.append('Outgoing scene and gone overlay absent at first frame')
assert first.getpixel((40,40))==(0,255,0);checks.append('Prior entrance fully settled at first target frame')
assert first.getpixel((110,40))==(255,255,255);checks.append('New element entrance remains distinct from inherited state')
assert (frames[0]/'000030.png').read_bytes()==(frames[2]/'000000.png').read_bytes();checks.append('Split phase resumes exact matching rendered frame')
assert (frames[0]/'000049.png').read_bytes()==(frames[2]/'000019.png').read_bytes();checks.append('Split final state matches uninterrupted render')
assert (frames[0]/'000031.png').read_bytes()!=(frames[0]/'000040.png').read_bytes();checks.append('Required motion continues beyond three seconds')
for name,n in [('full',50),('split-left',30),('split-right',20)]:
 with av.open(str(out/(name+'.mp4'))) as container:
  stream=container.streams.video[0];assert stream.frames==n and stream.average_rate==10
checks.append('Encoded frame counts/rate match explicit durations')
try:render_contract(dict(scene_selector='.scene'),10,'30');raise AssertionError('Must reject incomplete scene contract')
except ValueError:checks.append('Incomplete isolation contract rejected')
(out/'verification.json').write_text(json.dumps(dict(checks=checks,reports=reports,fixture_only=True,native_playback_verified=False),indent=2))
print(json.dumps({'passed':checks}),flush=True)
