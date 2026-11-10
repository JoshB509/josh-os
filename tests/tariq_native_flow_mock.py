"""MOCK dress rehearsal of the copied-project Premiere test, through the real pieces around Premiere.

    python tests/tariq_native_flow_mock.py

Josh OS (EditFlow and native_test) talks to a real Josh Cut Pro helper on a spare port, whose bridge is polled by
the real panel code running in Chrome against the Premiere stub (tests/ppro-stub.js plus tq-stub-ext.js). The
recording is Executive Questions (local caches), the deck is a temporary copy of its slides and shot list, and the
project file copied is the handover's reference copy. Everything Premiere does here is the stub's model, so this
proves the plumbing and the order of steps, NOT nesting, keys, export or reopening in Premiere. The stub writes no
video, so the export file check is expected to fail honestly.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.edit_flow import EditFlow, native_test  # noqa: E402
from studio.edit_review import EditReview  # noqa: E402
from studio.store import Store  # noqa: E402

CUT = Path(r'E:\Videos\DaJoshiCut')
VIDEO = Path(r'E:\CareerOS\12_YouTube\answering-exec-questions')
CAM = VIDEO / 'Recorded' / 'Input' / '2026-11-05_answering-exec-questions_recording_v02.mp4'
MIC = VIDEO / 'Recorded' / 'Input' / '00111_Wireless_PRO-esv2-50p-bg-10p-music-10p.wav'
PRPROJ = Path(r'C:\Users\Anigma PC\Desktop\Tariq-Editing-Handover-2026-11-10\reference-projects\Exec questions.prproj')
PORT = 47822
LAUNCH = '''
import os, sys, types, runpy
w = types.ModuleType('worker'); w.guard_recording = lambda *a, **k: None; sys.modules['worker'] = w
sys.path.insert(0, r'E:\\Videos\\DaJoshiCut\\helper')
sys.argv = ['dajoshi_cut.py', '--serve', '--no-window']
runpy.run_path(r'E:\\Videos\\DaJoshiCut\\helper\\dajoshi_cut.py', run_name='__main__')
'''


class Folders:
    def __init__(self, path):
        self.p = path

    def path(self, pid, c=None):
        return str(self.p)


def main():
    tmp = Path(tempfile.mkdtemp(prefix='tariq-native-mock-'))
    video = tmp / 'video-folder'
    video.mkdir()
    for f in ('2026-11_answering-exec-questions_slides_v02.html', '2026-11_answering-exec-questions_shot-list_v02.md'):
        shutil.copy2(VIDEO / f, video / f)
    (tmp / 'projects').mkdir()
    (tmp / 'styles').mkdir()
    (tmp / 'launch.py').write_text(LAUNCH, encoding='utf-8')
    env = dict(os.environ, DAJOSHI_CUT_PORT=str(PORT), DAJOSHI_CUT_PROJECTS=str(tmp / 'projects'), DAJOSHI_CUT_STYLES=str(tmp / 'styles'),
               DAJOSHI_CLAUDE_URL='http://127.0.0.1:9', KMP_DUPLICATE_LIB_OK='TRUE')
    helper = subprocess.Popen([sys.executable, str(tmp / 'launch.py')], env=env, stdout=open(tmp / 'helper.log', 'wb'), stderr=subprocess.STDOUT)
    receipt = None
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{PORT}/bridge/status', timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        with sync_playwright() as pw:
            b = pw.chromium.launch(channel='chrome')
            page = b.new_page()
            page.goto(f'http://127.0.0.1:{PORT}/bridge/status')  # same origin as the helper, as the panel tests do
            page.set_content('<div id="activity"></div><div id="link"></div><div id="linkText"></div><div id="seq"></div><button id="open"></button>')
            page.add_script_tag(content=f"window.JOSH_HELPER='http://127.0.0.1:{PORT}';window.STUB_VIDEO={json.dumps(str(CAM))};"
                                        f"window.STUB_MIC={json.dumps(str(MIC))};window.STUB_SOURCE_ONLY=true;window.STUB_LENGTH=600;"
                                        + ("window.STUB_SOURCE_CUT=true;" if '--cut-source' in sys.argv else '')
                                        + "window.STUB_TIMEBASE='8467191533';")
            page.add_script_tag(path=str(CUT / 'tests' / 'ppro-stub.js'))
            page.add_script_tag(path=str(CUT / 'tests' / 'tq-stub-ext.js'))
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.add_script_tag(path=str(CUT / 'panel' / 'index.js'))

            store = Store(tmp / 'data')
            p = store.create('Mock native test')
            folders = Folders(video)
            flow = EditFlow(store, ROOT, None, folders, EditReview(store, folders))
            cache = ROOT / 'Data' / 'edit_flow' / 'cache'
            flow._cache = lambda: cache
            flow.bridge = f'http://127.0.0.1:{PORT}'
            src = flow.register(p['id'], CAM, MIC)
            flow.analyse(p['id'], src['id'], editorial=False)
            while (flow.progress(p['id']) or {}).get('state') in (None, 'running'):
                page.wait_for_timeout(500)
            prop = flow.latest(p['id'], 'edit_proposal')
            flow.approve_script(p['id'], prop['id'], prop['digest'])
            for _ in range(40):  # wait for the panel to link up with the helper
                if json.loads(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/bridge/status').read()).get('connected'):
                    break
                page.wait_for_timeout(500)
            started = native_test(flow, p['id'], PRPROJ, 'Keyed edit', reference='data', add_shadow=True, render_limit=2)
            rec = Path(started['folder']) / 'receipt.json'
            t0 = time.time()
            while not rec.exists() and time.time() - t0 < 1800:
                page.wait_for_timeout(1000)
            receipt = json.loads(rec.read_text(encoding='utf-8'))
            receipt['panel_errors'] = errors
            receipt['ops_tail'] = page.evaluate('window.OPS.slice(-12)')
            b.close()
    finally:
        helper.kill()
    out = ROOT / 'tests' / 'fixtures' / 'tariq_edit' / ('native_flow_MOCK_cut_source_receipt.json' if '--cut-source' in sys.argv else 'native_flow_MOCK_receipt.json')
    receipt['scope'] = 'MOCK: Josh OS + real helper bridge + real panel code against the Premiere stub. Not native proof.'
    out.write_text(json.dumps(receipt, indent=1, default=str)[:400000], encoding='utf-8')
    for s in receipt['steps']:
        print(('ok    ' if s['ok'] else 'FAIL  ') + s['step'] + ('' if s['ok'] else '  ' + str(s.get('error') or s.get('result'))[:200]))
    print('stopped:', receipt.get('stopped'), '| original unchanged:', receipt['original_unchanged'], '| panel errors:', receipt['panel_errors'])
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
