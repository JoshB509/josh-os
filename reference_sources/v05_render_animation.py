from pathlib import Path
import subprocess,json,time,base64,tempfile,urllib.request,websocket,sys,hashlib
sys.stdout.reconfigure(encoding='utf-8')
Q=Path(__file__).parent/'v05_animation';Q.mkdir(exist_ok=True)
src=Path(r'E:\CareerOS\10_Sales\VSL\2026-10_executive-communication-program_vsl_slides_v05.html')
chrome=r'C:\Program Files\Google\Chrome\Application\chrome.exe';ff=r'E:\Videos\Reusable-Video-Kit\tools\ffmpeg.exe'
clock='''<script>
window.vt=0;window.jobs=new Map();window.jid=0;
window.setTimeout=(fn,ms=0,...args)=>{let id=++jid;jobs.set(id,{at:vt+(+ms||0),fn:()=>fn(...args)});return id};
window.clearTimeout=id=>jobs.delete(id);
</script>'''
control='''<style>#hud,#prog,#pip,.camera{display:none!important}html,body{background:#fff!important;margin:0;overflow:hidden}</style>
<script>
window.animBirth=new WeakMap();
window.freeze=()=>{void document.body.offsetWidth;document.getAnimations().forEach(a=>{if(!animBirth.has(a)){animBirth.set(a,vt);a.pause()}a.currentTime=Math.max(0,vt-animBirth.get(a))})};
window.jump=(t)=>{while(true){let next=[...jobs.entries()].filter(([k,j])=>j.at<=t).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;vt=next[1].at;freeze();jobs.delete(next[0]);next[1].fn();freeze()}vt=t;freeze()};
window.cue=(n)=>{go(n);freeze()};
go(8);scenes.forEach(s=>s.classList.remove('on'));freeze();
</script>'''
h=Q/'V05_RENDER_COPY.html';s=src.read_text(encoding='utf-8');s=s.replace('<script>',clock+'<script>',1).replace('</body>',control+'</body>');h.write_text(s,encoding='utf-8')
profile=Path(tempfile.mkdtemp(prefix='VSL_V05_Headless_'))
p=subprocess.Popen([chrome,'--headless','--no-first-run','--no-default-browser-check','--disable-background-networking','--hide-scrollbars','--force-device-scale-factor=1','--window-size=1920,1080','--remote-debugging-port=0','--user-data-dir='+str(profile),'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
class CDP:
 def __init__(self,url):self.ws=websocket.create_connection(url,suppress_origin=True,timeout=30);self.id=0
 def call(self,method,params={}):
  self.id+=1;self.ws.send(json.dumps({'id':self.id,'method':method,'params':params}))
  while True:
   m=json.loads(self.ws.recv())
   if m.get('id')==self.id:
    if 'error'in m:raise RuntimeError(m)
    return m.get('result',{})
 def js(self,expr,awaitp=False):
  r=self.call('Runtime.evaluate',{'expression':expr,'returnByValue':True,'awaitPromise':awaitp})
  if 'exceptionDetails'in r:raise RuntimeError(r)
  return r.get('result',{}).get('value')
try:
 for _ in range(100):
  f=profile/'DevToolsActivePort'
  if f.exists():break
  time.sleep(.1)
 port=int(f.read_text().splitlines()[0]);tabs=json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json'))
 target=next(t for t in tabs if t.get('type')=='page' and t.get('url')=='about:blank')
 c=CDP(target['webSocketDebuggerUrl']);c.call('Page.enable');c.call('Network.enable');c.call('Network.setBlockedURLs',{'urls':['http://*','https://*']});c.call('Emulation.setDeviceMetricsOverride',{'width':1920,'height':1080,'deviceScaleFactor':1,'mobile':False});c.call('Page.navigate',{'url':h.as_uri()})
 for _ in range(100):
  if c.js('document.readyState==="complete" && typeof window.freeze==="function"'):break
  time.sleep(.1)
 print('PAGE',c.js('({url:location.href,title:document.title,body:document.body.innerText.slice(0,200),scripts:document.scripts.length,clock:typeof vt,freeze:typeof freeze})'),flush=True)
 c.js('Promise.all([...document.fonts].map(f=>f.load())).then(()=>document.fonts.ready).then(()=>true)',True)
 c.js('freeze()');fonts=c.js('[...document.fonts].map(x=>({family:x.family,status:x.status}))');assert all(x['status']=='loaded' for x in fonts)
 # Asset starts at the PiP cut (mic81.5467s), first element at the spoken "with"85.08s.
 cues=[(106,8),(243,9),(404,10),(464,11),(639,12)];N=689
 out=Q/'Animation02_23sec_1080p30_PROOF.mp4';assert not out.exists()
 enc=subprocess.Popen([ff,'-v','error','-n','-f','image2pipe','-framerate','30','-i','-','-an','-c:v','libx264','-threads','2','-preset','fast','-crf','14','-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-movflags','+faststart',str(out)],stdin=subprocess.PIPE)
 reports=[]
 for n in range(N):
  c.js(f'jump({n*1000/30})')
  for k,b in cues:
   if n==k:c.js(f'cue({b})')
  data=base64.b64decode(c.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})['data']);enc.stdin.write(data)
  if n in [0,105,106,111,130,242,255,403,420,470,650,688]:
   (Q/f'frame_{n:04d}.png').write_bytes(data);reports.append({'frame':n,'state':c.js('({beat:b,clock:vt,on:scenes.filter(s=>s.classList.contains("on")).map(s=>s.dataset.from),text:scenes.filter(s=>s.classList.contains("on")).map(s=>s.innerText),fonts:[...document.fonts].map(f=>f.status)})')})
  if n%90==0:print('ANIMATION FRAME',n,'/',N,flush=True)
 enc.stdin.close();assert enc.wait()==0
 (Q/'RENDER_REPORT.json').write_text(json.dumps({'source':str(src),'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'asset':str(out),'frames':N,'fps':30,'cues_frames':cues,'fonts':fonts,'samples':reports,'method':'Own headless Chrome process; local self-contained HTML; network URLs blocked; deterministic JavaScript timer queue and CSS animation clock; no OBS or shared browser UI'},indent=2),encoding='utf-8')
 print('RENDER COMPLETE',out,flush=True)
finally:
 try:c.call('Browser.close')
 except Exception:pass
 p.wait(timeout=15)
