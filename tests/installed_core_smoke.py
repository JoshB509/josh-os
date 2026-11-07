"""Read-only installed UI smoke; no screenshots or production writes."""
import json,sqlite3
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(r'E:\Videos\DaJoshiResolve');out=root/'Evidence'/'CoreEditing-20261016';out.mkdir(exist_ok=True)
runtime=json.loads((root/'Data/runtime.json').read_text());errors=[];external=[];checks=[]
def snapshot():
 with sqlite3.connect('file:'+str(root/'Data/studio.sqlite')+'?mode=ro',uri=True) as c:return dict(c.execute('SELECT id,body FROM projects'))
before=snapshot();pid='dcb7b821-5ab7-559a-a2c6-c9be26cc9aa5'
with sync_playwright() as pw:
 browser=pw.chromium.launch(channel='chrome',headless=True,args=['--disable-background-networking','--disable-sync','--no-first-run'])
 ctx=browser.new_context(viewport={'width':1400,'height':1000});base=runtime['url'].split('?')[0]
 ctx.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base) else (external.append(r.request.url),r.abort()))
 page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto(runtime['url'])
 page.locator('[data-open="'+pid+'"]').click();page.locator('[data-tab=script]').click();page.locator('#script-document').wait_for();assert page.locator('#script-document').input_value()==json.loads(before[pid])['script'];checks.append('Installed script content exact')
 page.locator('[data-tab=edit]').click();page.locator('[data-milestone=clean]').click();page.get_by_role('button',name='Run silence-only cut',exact=True).wait_for()
 assert page.locator('[data-milestone]').count()==7;checks.append('Seven-stage workspace and staged cutter controls load')
 assert page.get_by_role('button',name='Run silence-only cut',exact=True).is_disabled();assert page.get_by_role('button',name='Create clean-cut candidate',exact=True).is_disabled();assert page.get_by_role('button',name='Import manual timing map',exact=True).is_disabled();checks.append('Unapproved production project cannot launch or import a clean cut')
 page.locator('[data-milestone=assembly]').click();assert page.get_by_text('Final visual assembly is unavailable in this increment.',exact=True).count()==1;checks.append('Unconnected visual assembly is explicitly unavailable')
 assert not errors,errors;assert not external,external;checks.append('No JavaScript errors or external requests');browser.close()
assert snapshot()==before;checks.append('All 11 installed project bodies unchanged')
result=dict(checks=checks,errors=errors,external_requests=external,screenshots_created=False,production_writes=False)
(out/'installed-browser.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
