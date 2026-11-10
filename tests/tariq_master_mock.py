"""MOCK: Tariq reads Josh's look and builds GREEN_MASTER from the raw files, through the real helper bridge and the
real panel code, against the Premiere stub (tests/ppro-stub.js plus tq-stub-ext.js).

    python tests/tariq_master_mock.py

The stub's reference sequence is laid out like Data Storytelling (background V1, shadow V2, presenter V3). This proves
the order of the steps and the settings copied, NOT what Premiere does with them.
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
from studio import edit_master as em  # noqa: E402
from studio.edit_flow import EditFlow  # noqa: E402
from studio.store import Store  # noqa: E402

CUT = Path(r'E:\Videos\DaJoshiCut')
VIDEO = Path(r'E:\CareerOS\12_YouTube\answering-exec-questions')
CAM = VIDEO / 'Recorded' / 'Input' / '2026-11-05_answering-exec-questions_recording_v02.mp4'
MIC = VIDEO / 'Recorded' / 'Input' / '00111_Wireless_PRO-esv2-50p-bg-10p-music-10p.wav'
PORT = 47823
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
    tmp = Path(tempfile.mkdtemp(prefix='tariq-master-mock-'))
    (tmp / 'projects').mkdir()
    (tmp / 'styles').mkdir()
    (tmp / 'video').mkdir()
    ref = tmp / 'Reference.prproj'
    ref.write_bytes(b'stub project')
    (tmp / 'launch.py').write_text(LAUNCH, encoding='utf-8')
    env = dict(os.environ, DAJOSHI_CUT_PORT=str(PORT), DAJOSHI_CUT_PROJECTS=str(tmp / 'projects'), DAJOSHI_CUT_STYLES=str(tmp / 'styles'),
               DAJOSHI_CLAUDE_URL='http://127.0.0.1:9', KMP_DUPLICATE_LIB_OK='TRUE')
    helper = subprocess.Popen([sys.executable, str(tmp / 'launch.py')], env=env, stdout=open(tmp / 'helper.log', 'wb'), stderr=subprocess.STDOUT)
    out = {}
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
            page.goto(f'http://127.0.0.1:{PORT}/bridge/status')
            page.set_content('<div id="activity"></div><div id="link"></div><div id="linkText"></div><div id="seq"></div><button id="open"></button>')
            page.add_script_tag(content=f"window.JOSH_HELPER='http://127.0.0.1:{PORT}';window.STUB_VIDEO={json.dumps(str(CAM))};"
                                        f"window.STUB_MIC={json.dumps(str(MIC))};window.STUB_LENGTH=600;window.STUB_LOOK_NAME='Reference look';"
                                        "window.STUB_TIMEBASE='8467191533';window.STUB_FRAME=[3840,2160];")
            page.add_script_tag(path=str(CUT / 'tests' / 'ppro-stub.js'))
            page.add_script_tag(path=str(CUT / 'tests' / 'tq-stub-ext.js'))
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.add_script_tag(path=str(CUT / 'panel' / 'index.js'))
            store = Store(tmp / 'data')
            p = store.create('Mock master build')
            flow = EditFlow(store, ROOT, None, Folders(tmp / 'video'))
            flow.bridge = f'http://127.0.0.1:{PORT}'
            for _ in range(40):
                if json.loads(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/bridge/status').read()).get('connected'):
                    break
                page.wait_for_timeout(500)
            look = em.read_look(flow, reference=ref, sequence='Reference look')
            out['look'] = dict(presenter=[e['match'] for e in look['presenter']], shadow=[e['match'] for e in look['shadow'] or []],
                               background=look['background_path'])
            out['build'] = em.build_master(flow, p['id'], CAM, MIC, look=look)
            out['again'] = em.build_master(flow, p['id'], CAM, MIC, look=look)['state']
            m = page.evaluate("(() => { const s = window.SEQS.find((q) => q.name === 'GREEN_MASTER'); return s.items.map((i) => ({ kind: i.kind, track: i.track, "
                              "start: i._start, end: i._end, path: i.clip.path || i.clip.name, fx: (i._chain ? i._chain.comps : []).map((c) => c.match + ' ' + "
                              "c.params.map((q) => q.displayName + '=' + JSON.stringify(q.value)).join(',')) })); })()")
            out['master'] = m
            out['panel_errors'] = errors
            b.close()
    finally:
        helper.kill()
    out['scope'] = 'MOCK: real helper bridge and panel code against the Premiere stub. Not native proof.'
    (ROOT / 'tests' / 'fixtures' / 'tariq_edit' / 'master_build_MOCK_receipt.json').write_text(json.dumps(out, indent=1, default=str), encoding='utf-8')
    print(json.dumps(out, indent=1, default=str)[:5000])
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
