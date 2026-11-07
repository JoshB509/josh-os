from pathlib import Path
import json,subprocess,time,concurrent.futures,traceback
from playwright.sync_api import sync_playwright
import imageio_ffmpeg
ROOT=Path(__file__).resolve().parent
PLAN=json.loads((ROOT/'edit_plan.json').read_text())
SRC=next((ROOT.parent/'outputs/Promotion Rule Project/References').glob('*.html'))
FF=imageio_ffmpeg.get_ffmpeg_exe()
TICK="""({time,beat})=>{
 if(beat!==window.currentBeat){go(beat);window.currentBeat=beat;}
 document.getElementById('stage').getBoundingClientRect();
 for(const a of document.getAnimations()){
  if(!window.starts.has(a)){window.starts.set(a,time);a.pause();}
  a.currentTime=(time-window.starts.get(a))*1000;
 }
}"""
def render(a):
 out=Path(a['file']);log=ROOT/f"render_{a['number']:02d}.log"
 if out.exists() and out.with_suffix('.done').exists():return f"Already done {a['number']}"
 with sync_playwright() as p:
  browser=p.chromium.launch(channel='chrome',headless=True,args=['--hide-scrollbars','--disable-background-timer-throttling','--disable-renderer-backgrounding'])
  page=browser.new_page(viewport={'width':1920,'height':1080},device_scale_factor=2)
  page.goto(SRC.as_uri());page.evaluate('document.fonts.ready')
  page.add_style_tag(content='#hud,#prog,#pip{display:none!important}')
  page.evaluate('window.starts=new WeakMap();window.currentBeat=-1;')
  frames=round(a['duration']*25);beat=a['cues'][0]['beat'];ci=0
  with log.open('w') as lf:
   proc=subprocess.Popen([FF,'-hide_banner','-loglevel','warning','-f','image2pipe','-framerate','25','-vcodec','mjpeg','-i','-','-an','-c:v','h264_nvenc','-preset','p5','-tune','hq','-rc','vbr','-cq','16','-b:v','20M','-maxrate','60M','-bufsize','120M','-pix_fmt','yuv420p','-g','50','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-movflags','+faststart','-y',str(out)],stdin=subprocess.PIPE,stderr=lf)
   start=time.time()
   for n in range(frames):
    t=n/25
    while ci+1<len(a['cues']) and a['cues'][ci+1]['time']<=t+.0001:ci+=1
    beat=a['cues'][ci]['beat']
    page.evaluate(TICK,{'time':t,'beat':beat})
    img=page.screenshot(type='jpeg',quality=96)
    proc.stdin.write(img)
    if n==min(30,frames-1): (ROOT/f"animation_{a['number']:02d}_opening.jpg").write_bytes(img)
    if n==frames-1:(ROOT/f"animation_{a['number']:02d}_final.jpg").write_bytes(img)
    if n%250==0:print(f"Animation {a['number']:02d}: {n}/{frames}",flush=True)
   proc.stdin.close();code=proc.wait()
   if code:raise RuntimeError(log.read_text())
  browser.close()
 out.with_suffix('.done').write_text(str(frames))
 return f"DONE animation {a['number']:02d}: {frames} frames, {time.time()-start:.1f}s"
if __name__=='__main__':
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  fs=[pool.submit(render,a) for a in PLAN['animations']]
  for f in concurrent.futures.as_completed(fs):
   try:print(f.result(),flush=True)
   except Exception:traceback.print_exc();raise
