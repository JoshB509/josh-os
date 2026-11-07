"""Headless acceptance test in an isolated Studio database; never user browser."""
import sys,json,threading,uuid,shutil
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from studio.server import Server
from playwright.sync_api import sync_playwright,expect

root=Path(__file__).resolve().parents[1];out=root/'Evidence'/'Keying';out.mkdir(parents=True,exist_ok=True)
server=Server(root,out/('browser-data-'+uuid.uuid4().hex[:8]));t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
p=server.store.create('CorridorKey integration test');checks=[];errors=[]
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True,args=['--disable-background-networking','--disable-sync','--no-first-run'])
        context=browser.new_context(viewport={'width':1480,'height':1050});context.route('**/*',lambda r:r.continue_() if r.request.url.startswith('http://127.0.0.1:'+str(server.server_port)+'/') else r.abort())
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto(server.url)
        page.locator('[data-open="'+p['id']+'"]').click();page.locator('[data-tab=edit]').click()
        page.locator('[data-keying=enabled]').check();page.locator('[data-keying=start]').fill('25');page.locator('[data-keying=end]').fill('30');page.locator('[data-keying=scale]').fill('1.15');page.locator('[data-keying=crop_bottom]').fill('0.12')
        page.locator('[data-media=video]').fill(r'E:\Videos\Promotion Policy\C0323.MP4')
        canvas=page.locator('#key-mask');canvas.scroll_into_view_if_needed();box=canvas.bounding_box();page.mouse.move(box['x']+.31*box['width'],box['y']+.02*box['height']);page.mouse.down();page.mouse.move(box['x']+.28*box['width'],box['y']+.04*box['height']);page.mouse.up()
        page.locator('#save-project').click();expect(page.get_by_text('Saved on this PC',exact=True)).to_be_visible()
        saved=server.store.get(p['id']);assert saved['keying']['scale']==1.15 and abs(saved['keying']['polygon'][0][0]-.28)<.01;checks.append('Saved controls and dragged mask point persist')
        page.reload();page.locator('[data-open="'+p['id']+'"]').click();page.locator('[data-tab=edit]').click();expect(page.locator('[data-keying=scale]')).to_have_value('1.15');checks.append('Reload restores saved framing')
        with patch.object(server.jobs,'_spawn'):
            page.locator('[data-key-preview]').click();expect(page.get_by_text('Source key preview started',exact=True)).to_be_visible()
        job=server.store.jobs(p['id'])[0];cfg=json.loads(Path(job['request']).read_text());assert cfg['keying']['preview'] and cfg['keying']['start']==25;assert server.store.get(p['id'])['stage']=='Idea';checks.append('Preview action saves revision and routes a bounded preview-only job')
        page.screenshot(path=str(out/'keying-controls.png'),full_page=True)
        assert not errors,errors;checks.append('No JavaScript errors');browser.close()
finally:
    server.shutdown();server.server_close();t.join();(out/'browser-results.json').write_text(json.dumps(dict(checks=checks,errors=errors),indent=2))
print(json.dumps(dict(checks=checks,errors=errors)))
