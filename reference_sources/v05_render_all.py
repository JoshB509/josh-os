from pathlib import Path
import subprocess,json,time,base64,tempfile,urllib.request,websocket,sys,hashlib,math
sys.stdout.reconfigure(encoding='utf-8')
P=Path(__file__).parent;plan=json.loads((P/'v05_full_plan.json').read_text());D=Path(r'E:\CareerOS\10_Sales\VSL\VIDEO\08_V05_New_Edit\10_Full_Assembly');Q=D/'Animation_QA_v02';Q.mkdir(exist_ok=True)
src=Path(r'E:\CareerOS\10_Sales\VSL\2026-10_executive-communication-program_vsl_slides_v05.html');chrome=r'C:\Program Files\Google\Chrome\Application\chrome.exe';ff=r'E:\Videos\Reusable-Video-Kit\tools\ffmpeg.exe'
exec((P/'v05_cdp_class.txt').read_text())
clock='''<script>window.vt=0;window.jobs=new Map();window.jid=0;window.setTimeout=(fn,ms=0,...args)=>{let id=++jid;jobs.set(id,{at:vt+(+ms||0),fn:()=>fn(...args)});return id};window.clearTimeout=id=>jobs.delete(id);</script>'''
control='''<style>#hud,#prog,#pip,.camera{display:none!important}html,body{background:#fff!important;margin:0;overflow:hidden}</style><script>
window.animBirth=new WeakMap();window.freeze=()=>{void document.body.offsetWidth;document.getAnimations().forEach(a=>{if(!animBirth.has(a)){animBirth.set(a,vt);a.pause()}a.currentTime=Math.max(0,vt-animBirth.get(a))})};
window.jump=(t)=>{while(true){let next=[...jobs.entries()].filter(([k,j])=>j.at<=t).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;vt=next[1].at;freeze();jobs.delete(next[0]);next[1].fn();freeze()}vt=t;freeze()};window.cue=(n)=>{go(n);freeze()};go(INIT);scenes.forEach(s=>s.classList.remove('on'));freeze();</script>'''
def render(info):
 a=info['animation'];out=Path(info['path']);report=Q/f'Animation_{a:02}_REPORT.json'
 oldreport=D/'Animation_QA'/f'Animation_{a:02}_REPORT.json'
 if out.exists() and oldreport.exists():
  prior=json.loads(oldreport.read_text())
  if prior['asset']==str(out) and prior['frames']==info['frames'] and prior['cues']==info['cues']:print('REUSE UNCHANGED VERIFIED RENDER',a,flush=True);return
 if out.exists() and not report.exists():out.rename(out.with_name(out.stem+'_INTERRUPTED_PRESERVED'+out.suffix))
 if out.exists() and report.exists():print('VERIFIED PREVIOUS COMPLETION',a,flush=True);return
 assert not out.exists(),str(out)
 h=Q/f'Animation_{a:02}_RENDER_COPY.html';s=src.read_text(encoding='utf-8').replace('data-d="1500"','data-d="2000"');s=s.replace('<script>',clock+'<script>',1).replace('</body>',control.replace('INIT',str(info['cues'][0]['beat']-1))+'</body>');h.write_text(s,encoding='utf-8')
 profile=Path(tempfile.mkdtemp(prefix='VSL_Full_Headless_'));p=subprocess.Popen([chrome,'--headless','--no-first-run','--no-default-browser-check','--disable-background-networking','--hide-scrollbars','--force-device-scale-factor=1','--window-size=1920,1080','--remote-debugging-port=0','--user-data-dir='+str(profile),'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
 try:
  for _ in range(100):
   f=profile/'DevToolsActivePort'
   if f.exists():break
   time.sleep(.1)
  port=int(f.read_text().splitlines()[0]);tabs=json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json'));target=next(t for t in tabs if t.get('type')=='page' and t.get('url')=='about:blank');c=CDP(target['webSocketDebuggerUrl']);c.call('Page.enable');c.call('Network.enable');c.call('Network.setBlockedURLs',{'urls':['http://*','https://*']});c.call('Emulation.setDeviceMetricsOverride',{'width':1920,'height':1080,'deviceScaleFactor':1,'mobile':False});c.call('Page.navigate',{'url':h.as_uri()})
  for _ in range(100):
   if c.js('document.readyState==="complete" && typeof freeze==="function"'):break
   time.sleep(.1)
  c.js('Promise.all([...document.fonts].map(f=>f.load())).then(()=>document.fonts.ready).then(()=>true)',True);c.js('freeze()');fonts=c.js('[...document.fonts].map(x=>({family:x.family,status:x.status}))');assert all(x['status']=='loaded' for x in fonts)
  enc=subprocess.Popen([ff,'-v','error','-n','-f','image2pipe','-framerate','30','-i','-','-an','-c:v','libx264','-threads','2','-preset','fast','-crf','15','-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-movflags','+faststart',str(out)],stdin=subprocess.PIPE)
  N=info['frames'];byframe={x['frame']:x for x in info['cues']};samples=set([0,N-1]+[min(N-1,x['frame']+24) for x in info['cues']]);reports=[];started=time.time()
  for n in range(N):
   cmd=f'jump({n*1000/30});'
   if n in byframe:cmd+=f"cue({byframe[n]['beat']});"
   c.js(cmd);data=base64.b64decode(c.call('Page.captureScreenshot',{'format':'jpeg','quality':98,'captureBeyondViewport':False})['data']);enc.stdin.write(data)
   if n in samples:
    (Q/f'A{a:02}_f{n:05}.jpg').write_bytes(data);state=c.js('({beat:b,clock:vt,scenes:scenes.filter(s=>s.classList.contains("on")).map(s=>({from:s.dataset.from,to:s.dataset.to,text:s.innerText})),cycle:[...document.querySelectorAll(".cyc.on")].map(x=>x.innerText)})');reports.append({'frame':n,'state':state})
    assert not any(x.get('text','').strip()==str(a) for x in state['scenes']),('number card',a,n,state)
   if n%450==0:print('RENDER',a,n,'/',N,'elapsed',round(time.time()-started,1),flush=True)
  enc.stdin.close();assert enc.wait()==0
  report.write_text(json.dumps({'animation':a,'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'frames':N,'fps':30,'asset':str(out),'cues':info['cues'],'fonts':fonts,'samples':reports,'opening_review_duration_ms':2000,'source_html_changed':False,'elapsed_seconds':time.time()-started,'native_premiere_playback_verified':False},indent=2),encoding='utf-8');print('COMPLETE ANIMATION',a,flush=True)
 finally:
  try:c.call('Browser.close')
  except Exception:pass
  try:p.wait(timeout=15)
  except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=10)
parity=int(sys.argv[1]) if len(sys.argv)>1 else -1
for info in plan['animations']:
 if parity<0 or info['animation']%2==parity:render(info)
print('ALL REQUESTED ANIMATIONS COMPLETE',parity,flush=True)
