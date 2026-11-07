import json,sys,threading,traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from studio.server import Server
from playwright.sync_api import sync_playwright
from test_clean_workflow import CleanWorkflowTests

root=Path(__file__).resolve().parents[1];out=root/'Evidence'/'EditingIncrement';out.mkdir(parents=True,exist_ok=True)
fixture=CleanWorkflowTests();fixture.setUp();artifact=fixture.cut()
project=fixture.store.get(fixture.p['id']);project=fixture.store.save(project['id'],dict(name='Clean-cut workflow · TEST FIXTURE',script='# Hook\nMake the decision clear.\n\n# Signpost\nThree things matter.\n\n# Section 1 — Motivation\nHelp the viewer see why this matters.\n\n# CTA\nTry it in your next update.'),project['revision'])
server=Server(root,fixture.store.root,demo=True);t=threading.Thread(target=server.serve_forever,daemon=True);t.start();checks=[];errors=[];external=[]
def passed(name):checks.append(name);print('PASS '+name,flush=True)
try:
 with sync_playwright() as pw:
  browser=pw.chromium.launch(channel='chrome',headless=True,args=['--disable-background-networking','--disable-sync','--no-first-run'])
  context=browser.new_context(viewport={'width':1500,'height':1100},permissions=['clipboard-read','clipboard-write'])
  context.route('**/*',lambda r:r.continue_() if r.request.url.startswith('http://127.0.0.1:'+str(server.server_port)+'/') else (external.append(r.request.url),r.abort()))
  page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto(server.url)
  page.locator('[data-open="'+project['id']+'"]').click();page.locator('[data-tab=script]').click()
  page.locator('#script-approval').filter(has_text='Draft').wait_for()
  assert page.locator('#script-document').count()==1
  assert page.locator('.writing-companion').count()==0
  assert page.get_by_text('Previous versions',exact=True).locator('..').get_attribute('open') is None
  passed('Single script document; previous versions collapsed; no companion sidebar')
  text=page.locator('#script-document').input_value()+'\nA final thought.'
  page.locator('#script-document').fill(text);page.locator('#save-project').click();page.get_by_text('Saved on this PC',exact=True).wait_for()
  assert server.store.get(project['id'])['script']==text
  assert len(server.writing.workspace(project['id'])['versions'])>=2
  passed('Script save preserves exact text and prior versions')
  page.screenshot(path=str(out/'script-simple.png'),full_page=True)
  page.locator('[data-tab=edit]').click();page.locator('[data-milestone=source] small').filter(has_text='ready').wait_for()
  assert page.locator('[data-milestone]').count()==7;assert page.locator('#media-shots').count()==0
  passed('Seven clickable milestones; Sources shows only relevant fields')
  page.screenshot(path=str(out/'editing-sources.png'),full_page=True)
  page.locator('[data-milestone=clean]').click();page.locator('.kept-transcript').wait_for()
  assert page.get_by_text('Audio crossfades are not implemented',exact=False).count()>0
  page.screenshot(path=str(out/'clean-cut-review.png'),full_page=True)
  page.get_by_text('Remove rejected ranges',exact=True).click()
  page.locator('#rejected-ranges').fill('0, 1.5, Incomplete rephrased attempt; keep the complete sentence')
  page.get_by_role('button',name='Apply removals to a new candidate',exact=True).click()
  page.get_by_text('Rejected ranges removed from the new map and XML. Review this new revision.',exact=True).wait_for()
  latest=server.workflow.artifacts(project['id'])[0]
  assert latest['id']!=artifact['id'];assert latest['payload']['duration_frames']==255
  assert len(latest['payload']['words'])==5;assert server.workflow.get(project['id'],artifact['id'])==artifact
  passed('UI range removal creates new XML/map and preserves previous candidate')
  page.get_by_text('Review & approve this revision',exact=True).click()
  page.get_by_role('button',name='Approve this exact revision',exact=True).click()
  page.get_by_text('Complete every review check before approval',exact=True).wait_for()
  assert not server.workflow.approved(server.store.get(project['id']),latest)
  passed('UI/API cannot approve without all explicit review attestations')
  page.locator('[data-milestone=visuals]').click()
  page.get_by_role('button',name='Download visual handoff',exact=True).click()
  page.get_by_text('Approve the clean cut before visual planning',exact=True).wait_for()
  passed('Visual handoff blocked until exact clean cut approved')
  page.locator('[data-milestone=assembly]').click()
  assert page.get_by_text('Final visual assembly is unavailable in this increment.',exact=True).count()==1
  assert page.locator('[data-action=edit]').count()==0
  passed('Unconnected assembly is explicitly unavailable; no legacy recut launch button')
  page.locator('[data-flow=context]').click();page.get_by_text('Video context copied. Paste it into your existing chat; nothing was sent.',exact=True).wait_for()
  copied=json.loads(page.evaluate('navigator.clipboard.readText()'));assert copied['project_id']==project['id'];assert copied['pending_action'] is None
  passed('Copy context contains exact project; no simulated conversation or broad approval')
  page.reload();page.locator('[data-open="'+project['id']+'"]').click();page.locator('[data-tab=edit]').click();page.locator('[data-milestone=clean]').click();page.locator('.kept-transcript').wait_for()
  assert page.locator('.kept-transcript button').count()==5
  passed('Clean-cut candidate survives reload')
  assert not errors,errors;assert not external,external;passed('No browser errors or external resource requests')
  browser.close()
except Exception:
 errors.append(traceback.format_exc());raise
finally:
 server.shutdown();server.server_close();t.join();fixture.tearDown()
 (out/'browser-results.json').write_text(json.dumps(dict(checks=checks,errors=errors,external_requests=external,fixture_only=True),indent=2))
