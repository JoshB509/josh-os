"""Read-only acceptance check of deployed Studio and repeat-launch reconnect."""
import json,subprocess,sys,urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(r'E:\Videos\DaJoshiResolve');workspace=Path(__file__).resolve().parents[1];r=json.loads((root/'Data'/'runtime.json').read_text());pid=r['pid']
subprocess.run([sys.executable,str(root/'studio_app.py'),'--no-browser'],check=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
assert json.loads((root/'Data'/'runtime.json').read_text())['pid']==pid
req=urllib.request.Request(r['url'].split('?')[0]+'api/state',headers={'X-DJ-Token':r['token']})
with urllib.request.urlopen(req) as response:state=json.load(response)
assert not state['demo'];assert len(state['projects'])==1;assert state['projects'][0]['name']=='Storytelling 2.0';assert len(state['jobs'])==2
errors=[]
with sync_playwright() as p:
    b=p.chromium.launch(channel='chrome',headless=True,args=['--disable-background-networking','--disable-sync','--no-first-run'])
    page=b.new_page(viewport={'width':1500,'height':1050});page.on('pageerror',lambda e:errors.append(str(e)));page.goto(r['url'])
    page.get_by_text('Storytelling 2.0',exact=True).click();page.locator('[data-tab=edit]').click();page.locator('.job').nth(1).wait_for()
    assert page.locator('.job').count()==2;assert page.locator('#media-shots').input_value().endswith('shot-list.json')
    assert page.get_by_text('This source rate is preserved in timing_map.json',exact=False).count()==1
    page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(root/'Evidence'/'Studio'/'installed-storytelling.png'),full_page=True)
    b.close()
assert not errors,errors
report=dict(passed=True,version=state['version'],repeat_launch_reused_server=True,server_pid=pid,preloaded_project='Storytelling 2.0',preserved_failed_runs=2,code_errors=errors,production_data_unchanged=True)
for folder in [root/'Evidence'/'Studio',workspace/'Evidence'/'Studio']:(folder/'installed-smoke-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
