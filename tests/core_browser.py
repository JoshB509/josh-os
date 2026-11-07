"""Fixture UI and renderer DOM regression; deliberately creates no screenshots."""
import json,sys,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from studio.server import Server
from tests.test_clean_workflow import CleanWorkflowTests
from dajoshi.animation import CLOCK,prepare_scene,render_contract
root=Path(__file__).resolve().parents[1];out=root/'Evidence'/'CoreChecks';out.mkdir(parents=True,exist_ok=True)
fixture=CleanWorkflowTests();fixture.setUp();a=fixture.cut();p=fixture.p
server=Server(root,fixture.store.root,demo=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
checks=[];errors=[];external=[]
def passed(x):checks.append(x);print('PASS '+x,flush=True)
try:
 with sync_playwright() as pw:
  browser=pw.chromium.launch(channel='chrome',headless=True,args=['--disable-background-networking','--disable-sync','--no-first-run'])
  ctx=browser.new_context(viewport={'width':1450,'height':1000},permissions=['clipboard-read','clipboard-write'])
  ctx.route('**/*',lambda r:r.continue_() if r.request.url.startswith('http://127.0.0.1:'+str(server.server_port)+'/') else (external.append(r.request.url),r.abort()))
  page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto(server.url)
  page.locator('[data-open="'+p['id']+'"]').click();page.locator('[data-tab=edit]').click();page.locator('[data-milestone=clean]').click();page.locator('.kept-transcript').wait_for()
  assert page.get_by_role('button',name='Render this exact candidate',exact=True).is_enabled()
  assert page.get_by_text('No completed preview is bound to this exact candidate yet.',exact=False).count()==1
  passed('Exact candidate render action available; no unrelated preview presented')
  page.get_by_text('Optional review audio fades',exact=True).click();assert page.locator('#review-fade-ms').input_value()=='0'
  assert page.get_by_text('XML does not export the fades.',exact=False).count()>0;passed('Fades default off and XML limitation is visible')
  page.get_by_text('Remove rejected ranges',exact=True).click();page.locator('#rejected-ranges').fill('0, 1.5, Incomplete start')
  page.get_by_role('button',name='Apply removals to a new candidate',exact=True).click();page.get_by_text('Rejected ranges removed from the new map and XML. Review this new revision.',exact=True).wait_for()
  assert page.locator('.kept-transcript button').count()==5;new=server.workflow.artifacts(p['id'])[0];assert new['id']!=a['id'];passed('UI rejection actually creates five-word canonical candidate')
  page.get_by_text('Review & approve this revision',exact=True).click();page.get_by_role('button',name='Approve this exact revision',exact=True).click();page.get_by_text('Complete every review check before approval',exact=True).wait_for();passed('UI approval cannot bypass attestations')
  page.get_by_text('Start or import a candidate',exact=True).click();assert page.get_by_role('button',name='Import manual timing map',exact=True).is_enabled();passed('Manual map import is exposed behind approved Pass 1')
  page.locator('[data-milestone=visuals]').click();page.get_by_role('button',name='Download visual handoff',exact=True).click();page.get_by_text('Approve the clean cut before visual planning',exact=True).wait_for();passed('Downstream visual handoff stays gated')
  page.reload();page.locator('[data-open="'+p['id']+'"]').click();page.locator('[data-tab=edit]').click();page.locator('[data-milestone=clean]').click();page.locator('.kept-transcript').wait_for();assert page.locator('.kept-transcript button').count()==5;passed('Candidate survives reload')
  assert not errors,errors;assert not external,external;passed('No JavaScript errors or external requests')
  # DOM-only renderer test for mixed finite entrance + infinite movement.
  dom=browser.new_context(offline=True);dom.add_init_script(CLOCK);pg=dom.new_page()
  pg.goto('data:text/html,<style>.scene{display:none}.scene.on{display:block}.item{width:30px;height:30px;animation:enter .5s both,spin 2s infinite linear}@keyframes enter{from{opacity:0}to{opacity:1}}@keyframes spin{to{transform:rotate(360deg)}}</style><div class="scene on"><div class="item"></div></div><script>window.go=b=>{}</script>')
  prepare_scene(pg,2,1,render_contract(dict(scene_selector='.scene',active_selector='.scene.on',inherited_selector='.item',settle_ms=1000),20,'25'))
  durations=pg.evaluate("getComputedStyle(document.querySelector('.item')).animationDuration")
  assert durations=='0s, 2s',durations;passed('Inherited finite entrance settles while infinite CSS animation duration remains intact')
  browser.close()
finally:
 server.shutdown();server.server_close();thread.join();fixture.tearDown()
 (out/'browser.json').write_text(json.dumps(dict(checks=checks,errors=errors,external_requests=external,screenshots_created=False),indent=2))
