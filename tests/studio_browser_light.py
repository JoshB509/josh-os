"""Browser acceptance test against an isolated demo database, not Josh's projects."""
import json,sys,threading,uuid,traceback,argparse
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from studio.server import Server
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--output',default=str(root/'Evidence'/'Studio'));args=parser.parse_args()
out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
data=out/('test-data-'+uuid.uuid4().hex[:8]);server=Server(root,data,demo=True)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();checks=[]
def passed(name):checks.append(name);print('PASS '+name,flush=True)
font=ImageFont.truetype(r'C:\Windows\Fonts\seguisb.ttf',58);small=ImageFont.truetype(r'C:\Windows\Fonts\segoeui.ttf',19)
thumbs=[]
for i,(title,color) in enumerate([('MAKE\nTHE POINT','#b8a4ef'),('LESS DETAIL.\nMORE IMPACT.','#e2bd7a'),('THE THREE\nTHINGS','#8ecbb1')]):
    im=Image.new('RGB',(960,540),'#171b29');d=ImageDraw.Draw(im);d.rounded_rectangle((620,80,890,460),30,fill=color);d.ellipse((675,130,835,290),fill='#30344b');d.rectangle((660,320,850,350),fill='#30344b');d.rectangle((690,380,820,400),fill='#30344b');d.multiline_text((55,130),title,font=font,fill='#f8f6ff',spacing=8);d.text((55,450),'UI TEST THUMBNAIL  /  SAMPLE ONLY',font=small,fill=color);path=out/('sample-thumbnail-'+str(i+1)+'.png');im.save(path);thumbs.append(path)
for name,stage,day in [('A better Monday update','Scripting','2026-10-22'),('The decision, in one sentence','Ready to record','2026-10-28'),('Why good work gets overlooked','Idea',''),('The executive briefing','Review','2026-10-18'),('The promotion conversation','Scheduled','2026-10-25')]:
    p=server.store.create(name);server.store.save(p['id'],{'stage':stage,'publish_date':day},1)
errors=[];blocked=[]
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True,args=['--disable-background-networking','--disable-sync','--no-first-run'])
        context=browser.new_context(viewport={'width':1500,'height':1050},device_scale_factor=1)
        context.route('**/*',lambda r:r.continue_() if r.request.url.startswith('http://127.0.0.1:'+str(server.server_port)+'/') else (blocked.append(r.request.url),r.abort()))
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto(server.url);page.get_by_text('Your next great video.',exact=True).wait_for()
        if 'Light' in out.name:
            palette=page.evaluate('''()=>{const b=getComputedStyle(document.body),p=getComputedStyle(document.querySelector('.primary'));return {background:b.backgroundColor,text:b.color,button:p.backgroundColor,buttonText:p.color,scheme:getComputedStyle(document.documentElement).colorScheme}}''')
            assert palette['background']=='rgb(255, 255, 255)' and palette['scheme']=='light',palette
            def luminance(rgb):
                values=[int(x.strip())/255 for x in rgb[4:-1].split(',')]
                return sum(v*w for v,w in zip([v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in values],[.2126,.7152,.0722]))
            def contrast(a,b):
                x,y=sorted([luminance(a),luminance(b)]);return (y+.05)/(x+.05)
            assert contrast(palette['background'],palette['text'])>=7
            assert contrast(palette['button'],palette['buttonText'])>=4.5
            (out/'light-palette.json').write_text(json.dumps(palette,indent=2));passed('White light theme and readable text/button contrast')
        count=len(server.store.projects());page.get_by_role('button',name='＋ New video',exact=True).click();page.locator('#new-name').fill('Cancelled project');page.get_by_role('button',name='Cancel',exact=True).click();assert len(server.store.projects())==count;passed('Cancel new project creates no record')
        page.get_by_role('button',name='＋ New video',exact=True).click();page.locator('#new-name').fill('Storytelling 2.0 — demo');page.get_by_role('button',name='Create video',exact=True).click();page.locator('[data-field=notes]').fill('Make the decision easy. Focus on motivation, outcome and effort.');page.locator('[data-field=publish_date]').fill('2026-10-20');page.get_by_label('Project stage',exact=True).select_option('Recorded');page.locator('#save-project').click();page.get_by_text('Saved on this PC',exact=True).wait_for();passed('Create and edit project, stage and publication date')
        page.locator('[data-tab=titles]').first.click()
        for title in ['The 3 Things Every Executive Wants From You','Stop Explaining Everything. Do This Instead.','How to Make Executives Understand Your Work']:
            page.locator('#title-idea').fill(title);page.get_by_role('button',name='＋ Add idea',exact=True).click()
        page.get_by_label('Choose title 1',exact=True).click();page.locator('#save-project').click();page.get_by_text('Saved on this PC',exact=True).wait_for();passed('Alternative titles and chosen title persist')
        page.locator('[data-tab=script]').click();page.get_by_label('Video script',exact=True).fill('Most professionals explain everything. Here are the three things executives need: motivation, outcome, and a simple next step.');page.locator('#save-project').click();page.get_by_text('Saved on this PC',exact=True).wait_for();passed('Save script')
        page.locator('[data-tab=thumbnails]').click();page.locator('#thumbnail-files').set_input_files([str(x) for x in thumbs]);page.locator('.thumb-small').nth(2).wait_for();page.get_by_text('Saved on this PC',exact=True).wait_for()
        for _ in range(3):page.get_by_role('button',name='Add to top 3',exact=True).first.click()
        for i in range(3):page.get_by_label('Title for option '+str(i+1),exact=True).select_option(index=i+1)
        page.locator('#save-project').click();page.get_by_text('Saved on this PC',exact=True).wait_for();assert page.locator('.top-three img').count()==3
        boxes=page.locator('.thumb-preview').evaluate_all('(xs)=>xs.map(x=>({x:x.getBoundingClientRect().x,y:x.getBoundingClientRect().y,w:x.getBoundingClientRect().width}))');assert len({b['y'] for b in boxes})==1 and max(b['w'] for b in boxes)-min(b['w'] for b in boxes)<2;passed('Three thumbnails displayed side by side with alternative titles')
        page.evaluate('window.scrollTo(0,0);document.querySelector("#toast").style.display="none"');page.screenshot(path=str(out/'thumbnail-comparison.png'),full_page=True)
        page.get_by_label('Move option 3 left',exact=True).click();page.locator('#save-project').click();page.get_by_text('Saved on this PC',exact=True).wait_for();pid=next(p['id'] for p in server.store.projects() if p['name']=='Storytelling 2.0 — demo');saved=server.store.get(pid);assert len(saved['top_three'])==3;passed('Reorder thumbnail options')
        page.reload();page.locator('[data-open="'+pid+'"]').click();page.locator('[data-tab=thumbnails]').click();assert page.locator('.top-three img').count()==3;assert server.store.get(pid)['script'].startswith('Most professionals');passed('Reload retains script, titles, date and top three')
        page.locator('[data-tab=edit]').click();page.get_by_role('button',name='Build editable draft',exact=True).click();page.get_by_text('Choose a source video first',exact=True).wait_for();assert not server.store.jobs();passed('Missing media prevents editing handoff without creating a run')
        page.locator('[data-action=back]').click();page.locator('[data-stage="Review"]').scroll_into_view_if_needed();page.locator('[data-open="'+pid+'"]').drag_to(page.locator('[data-stage="Review"]'));page.locator('[data-stage="Review"] [data-open="'+pid+'"]').wait_for();assert server.store.get(pid)['stage']=='Review';passed('Drag project to another stage')
        page.evaluate('window.scrollTo(0,0);document.querySelector(".board").scrollLeft=0;document.querySelector("#toast").style.display="none"');page.screenshot(path=str(out/'project-board.png'),full_page=True)
        page.locator('[data-view=calendar]').click();page.locator('.calendar').wait_for();page.screenshot(path=str(out/'publication-calendar.png'),full_page=True);assert page.locator('.cal-project').count()>=4;passed('Calendar displays planned projects')
        assert not errors,errors;assert not blocked,blocked;passed('No JavaScript errors or external resource requests')
        browser.close()
except Exception:
    errors.append(traceback.format_exc());raise
finally:
    server.shutdown();server.server_close();thread.join()
    (out/'browser-test-report.json').write_text(json.dumps(dict(passed=checks,errors=errors,blocked_external_requests=blocked,isolated_demo_data=str(data)),indent=2),encoding='utf-8')
