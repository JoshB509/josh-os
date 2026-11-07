"""Local-only HTML go(beat) renderer with a deterministic timer/CSS clock."""
from pathlib import Path
import hashlib,json,subprocess,uuid
from .media import check,run
from .timing import rate

CLOCK=r'''(() => {
 let now=0,id=0;const jobs=new Map(),birth=new WeakMap();
 const add=(fn,delay,args,repeat=0)=>{if(typeof fn!=='function')throw Error('String timers unsupported');const k=++id;jobs.set(k,{at:now+Math.max(1,+delay||0),fn:()=>fn(...args),repeat});return k};
 window.setTimeout=(f,d,...a)=>add(f,d,a);window.setInterval=(f,d,...a)=>add(f,d,a,Math.max(1,+d||0));
 window.clearTimeout=window.clearInterval=k=>jobs.delete(k);
 window.requestAnimationFrame=f=>add(()=>f(now),1000/60,[]);window.cancelAnimationFrame=k=>jobs.delete(k);
 const NativeDate=Date;window.Date=class extends NativeDate{constructor(...a){super(...(a.length?a:[1700000000000+now]))}static now(){return 1700000000000+now}};
 Object.defineProperty(performance,'now',{value:()=>now});let seed=1234567;Math.random=()=>{seed=(seed*16807)%2147483647;return(seed-1)/2147483646};
 function freeze(){void document.documentElement.offsetWidth;for(const a of document.getAnimations()){if(!birth.has(a)){birth.set(a,now);a.pause()}a.currentTime=Math.max(0,now-birth.get(a))}}
 window.__dj={freeze,jump(t){if(t<now)throw Error('Clock cannot reverse');let guard=0;while(true){const next=[...jobs].filter(([k,v])=>v.at<=t).sort((a,b)=>a[1].at-b[1].at||a[0]-b[0])[0];if(!next)break;if(++guard>10000)throw Error('Timer runaway');const[k,v]=next;now=v.at;freeze();if(v.repeat)v.at+=v.repeat;else jobs.delete(k);v.fn();freeze()}now=t;freeze()},time:()=>now};
})();'''

def inspect_deck(path):
    text=Path(path).read_text(encoding='utf-8-sig')
    if 'go(' not in text:raise ValueError('Deck must expose window.go(integer beat)')
    return dict(path=str(Path(path).resolve()),sha256=hashlib.sha256(text.encode()).hexdigest(),contract='window.go(integer); local assets/fonts; deterministic timers and CSS animations')

def render_contract(value,frames,fps):
    value=dict(value or {})
    allowed={'version','scene_selector','active_selector','removed_selector','inherited_selector',
             'settle_ms','motion_frames','phase_frames','handle_frames','width','height'}
    if set(value)-allowed:raise ValueError('Unknown renderer contract field')
    if value.get('version',2)!=2:raise ValueError('Unsupported renderer contract version')
    c=dict(version=2,scene_selector=None,active_selector=None,removed_selector=None,
           inherited_selector=None,settle_ms=4000,motion_frames=frames,phase_frames=0,
           handle_frames=round(30*float(rate(fps))),width=1920,height=1080)
    c.update(value)
    if type(frames) is not int or frames<=0:raise ValueError('Positive integer render frame count required')
    for key in ('motion_frames','phase_frames','handle_frames','settle_ms','width','height'):
        if type(c[key]) is not int or c[key]<0:raise ValueError('Invalid renderer '+key)
    if c['motion_frames']<1 or c['settle_ms']>60000:raise ValueError('Invalid motion/settle duration')
    if not 16<=c['width']<=7680 or not 16<=c['height']<=4320 or c['width']%2 or c['height']%2:raise ValueError('Use supported even viewport dimensions')
    if bool(c['scene_selector'])!=bool(c['active_selector']):raise ValueError('Scene and active selectors must be declared together')
    for key in ('scene_selector','active_selector','removed_selector','inherited_selector'):
        if c[key] is not None and (not isinstance(c[key],str) or not c[key].strip() or len(c[key])>300):raise ValueError('Invalid CSS selector contract')
    return c


def prepare_scene(page,beat,previous,contract):
    """The v06 pattern, parameterized: settle prior state, isolate target scene.

    Inherited selectors explicitly identify finite entrances to settle. Infinite
    animations retain their clock; they are not indiscriminately disabled.
    """
    c=contract
    page.add_style_tag(content='#hud,#prog,#pip,.camera{display:none!important}')
    page.evaluate('''(c)=>{
      if(c.scene_selector){for(const e of document.querySelectorAll(c.scene_selector))e.style.setProperty('transition','none','important');}
      window.__djIsolate=()=>{
        if(c.scene_selector){const active=[...document.querySelectorAll(c.active_selector)];
          if(active.length!==1)throw Error('Exactly one active target scene is required');
          for(const e of document.querySelectorAll(c.scene_selector)){
            if(active.includes(e))e.style.removeProperty('display');else e.style.setProperty('display','none','important');
          }
        }
        if(c.removed_selector)for(const e of document.querySelectorAll(c.removed_selector))e.style.setProperty('display','none','important');
      };
    }''',c)
    if previous is not None:
        page.evaluate('(b)=>{go(b);__dj.freeze()}',previous)
        page.evaluate('(t)=>__dj.jump(t)',c['settle_ms'])
        if c['inherited_selector']:
            page.evaluate('''(selector)=>{for(const e of document.querySelectorAll(selector)){
              const s=getComputedStyle(e);
              if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0||!e.getClientRects().length)continue;
              e.style.setProperty('transition','none','important');
              const finite=e.getAnimations().filter(a=>a.effect?.target===e && a.effect.getTiming().iterations!==Infinity);
              if(finite.length){
                const list=k=>s[k].split(',').map(v=>v.trim()),names=list('animationName'),counts=list('animationIterationCount');
                for(const [property,key,value] of [['animation-duration','animationDuration','0s'],['animation-delay','animationDelay','0s'],['animation-fill-mode','animationFillMode','forwards']]){
                  const values=list(key);e.style.setProperty(property,names.map((_,i)=>counts[i%counts.length]==='infinite'?values[i%values.length]:value).join(','),'important');
                }
              }
            }}''',c['inherited_selector'])
    base=page.evaluate('__dj.time()')
    page.evaluate('(b)=>{go(b);__dj.freeze();__djIsolate();__dj.freeze()}',beat)
    return base


def render(deck,beat,previous,frames,fps,out,job,ffmpeg,contract=None):
    from playwright.sync_api import sync_playwright
    out=Path(out);fps_text=str(rate(fps));fps=float(rate(fps));c=render_contract(contract,frames,fps_text);check(job);blocked=[];failed=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True,args=['--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run'])
        try:
            context=browser.new_context(viewport={'width':c['width'],'height':c['height']},device_scale_factor=1,service_workers='block',offline=True)
            def route(r):
                if r.request.url.startswith(('http:','https:','ws:','wss:')):blocked.append(r.request.url);r.abort()
                else:r.continue_()
            context.route('**/*',route);context.add_init_script(CLOCK)
            page=context.new_page();page.on('pageerror',lambda e:failed.append(str(e)))
            page.on('requestfailed',lambda r:failed.append(r.url))
            page.goto(Path(deck).resolve().as_uri(),wait_until='load');page.evaluate('Promise.all([...document.fonts].map(f=>f.load())).then(()=>document.fonts.ready).then(()=>true)')
            fonts=page.evaluate('[...document.fonts].map(f=>({family:f.family,status:f.status}))')
            if blocked or failed or any(f['status']!='loaded' for f in fonts):raise ValueError('HTML has failed/external assets, fonts or script errors: '+str(dict(blocked=blocked,errors=failed,fonts=fonts)))
            if not page.evaluate('typeof window.go==="function"'):raise ValueError('HTML does not expose window.go(beat)')
            base=prepare_scene(page,beat,previous,c)
            raw=out.with_name(out.stem+'.frames-'+uuid.uuid4().hex[:8]);raw.mkdir()
            # Motion is explicit and uncapped. A split resumes from its declared
            # phase; any remaining duration is an intentional held final frame.
            motion_frames=min(frames,max(1,c['motion_frames']-c['phase_frames']))
            hashes=[]
            for n in range(motion_frames):
                check(job);phase=min(c['phase_frames']+n,c['motion_frames']-1)
                page.evaluate('(t)=>{__dj.jump(t);__djIsolate();__dj.freeze()}',base+phase*1000/fps)
                png=page.screenshot(type='png');(raw/('%06d.png'%n)).write_bytes(png)
                if n in [0,motion_frames-1]:hashes.append(hashlib.sha256(png).hexdigest())
            if failed:raise ValueError('HTML runtime failure: '+str(failed))
            # Freeze the last rendered state, including cycling cards. No timer runs during holds.
            tail=(frames-motion_frames+c['handle_frames'])/fps
            partial=out.with_suffix('.partial.mp4')
            run([ffmpeg,'-v','error','-nostdin','-y','-framerate',fps_text,'-i',str(raw/'%06d.png'),'-vf','tpad=stop_mode=clone:stop_duration='+str(tail),'-frames:v',str(frames+c['handle_frames']),'-an','-c:v','libx264','-threads','2','-preset','fast','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(partial)],job,'Encode beat '+str(beat))
            partial.replace(out)
            report=dict(beat=beat,previous=previous,fonts=fonts,blocked_requests=blocked,errors=failed,frame_hashes=hashes,frame_directory=str(raw),motion_frames=motion_frames,required_motion_frames=c['motion_frames'],phase_frames=c['phase_frames'],duration_frames=frames,hold_handle_seconds=c['handle_frames']/fps,contract=c,source_sha256=hashlib.sha256(Path(deck).read_bytes()).hexdigest(),scene_isolation=bool(c['scene_selector']),clock='virtual timers/RAF/Date/performance + paused CSS animations',native_visual_verified=False)
            out.with_suffix('.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            return report
        finally:browser.close()

if __name__=='__main__':
    import sys
    request=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    render(**request)
