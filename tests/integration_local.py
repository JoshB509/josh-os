"""Opt-in bounded integration checks. Never invoked by unittest discovery."""
import hashlib,json,subprocess,sys,time
from pathlib import Path
import av
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worker import read,write,guard_recording
from dajoshi.animation import CLOCK
from dajoshi.media import transcribe,run

root=Path(__file__).resolve().parents[1];out=root/'Evidence';out.mkdir(exist_ok=True);settings=read(root/'settings.json')
guard_recording()

def deterministic_deck():
    from playwright.sync_api import sync_playwright
    deck=read(root/'examples/html-fixture.json')['deck'];results=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True,args=['--disable-background-networking','--disable-sync'])
        try:
            for i in range(2):
                context=browser.new_context(viewport={'width':1920,'height':1080},offline=True,service_workers='block');context.add_init_script(CLOCK)
                page=context.new_page();page.goto(Path(deck).as_uri());page.evaluate('Promise.all([...document.fonts].map(f=>f.load())).then(()=>document.fonts.ready).then(()=>true)');page.add_style_tag(content='#hud,#prog,#pip,.camera{display:none!important}')
                page.evaluate('go(1);__dj.freeze();__dj.jump(2500);go(2);__dj.freeze()');hashes=[]
                for t in [2500,2750,3500]:
                    page.evaluate('(t)=>__dj.jump(t)',t);png=page.screenshot(type='png');hashes.append(hashlib.sha256(png).hexdigest())
                results.append(hashes);context.close()
        finally:browser.close()
    assert results[0]==results[1],results
    assert len(set(results[0]))>1,'Expected actual motion, not identical static frames'
    write(out/'html_determinism.json',dict(passed=True,independent_contexts=2,frame_times_ms=[0,250,1000],hashes=results))
    print('Actual HTML deck: two independent renders have identical hashes; animation changes over time.',flush=True)

def short_asr():
    cfg=read(root/'examples/fixture.json');wav=out/'asr-12seconds.wav'
    run([settings['ffmpeg'],'-v','error','-nostdin','-y','-ss','26','-i',cfg['audio'],'-t','12','-ac','1','-ar','16000',str(wav)],out,'Make bounded ASR test')
    started=time.monotonic();segments=transcribe(wav,settings['model'],out)
    assert segments and any(s['words'] for s in segments)
    write(out/'local_asr.json',dict(passed=True,model=settings['model'],local_files_only=True,cpu_threads=2,input_seconds=12,elapsed_seconds=time.monotonic()-started,segments=segments))
    print('Cached medium.en: local 12-second transcription succeeded.',flush=True)

def cancellation_resume():
    job=root/'Runs'/'cancel-resume-proof';request=root/'examples/fixture.json'
    if job.exists():raise ValueError('Use a fresh integration folder')
    with (out/'cancel-resume.log').open('w') as log:
        p=subprocess.Popen([sys.executable,str(root/'worker.py'),'--request',str(request),'--job',str(job)],cwd=root,stdout=log,stderr=log)
        deadline=time.monotonic()+30
        while not (job/'status.json').exists() and p.poll() is None:
            if time.monotonic()>deadline:raise RuntimeError('Worker never reached a cancellable stage')
            time.sleep(.1)
        (job/'CANCEL').write_text('integration cancellation')
        code=p.wait(timeout=60);assert code==2,(code,read(job/'status.json'))
        cancelled=read(job/'status.json');assert cancelled['state']=='cancelled'
        done=subprocess.run([sys.executable,str(root/'worker.py'),'--job',str(job),'--resume'],cwd=root,stdout=log,stderr=log,timeout=180)
        assert done.returncode==0,read(job/'status.json')
    write(out/'cancel_resume.json',dict(passed=True,cancellation_exit_code=code,resume_exit_code=done.returncode,job=str(job),status=read(job/'status.json')))
    print('Cancellation and resume: real worker cancelled safely, then produced its preview/XML.',flush=True)

if __name__=='__main__':
    actions={'html':deterministic_deck,'asr':short_asr,'resume':cancellation_resume}
    actions[sys.argv[1]]()
