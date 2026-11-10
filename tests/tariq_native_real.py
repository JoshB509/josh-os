"""The copied-project Premiere test in REAL Premiere, without restarting Josh OS.

    python tests/tariq_native_real.py "<keyed sequence name>" [reference] [--shadow]

Uses Josh Cut Pro's running helper on 47820 and the 0.8.8 panel in Premiere. Josh OS's own server is left alone:
this builds a throwaway store, registers the Executive Questions recording from the local caches, approves the
proposal, then runs native_test on a copy of the real project. The original project is only read, and its hash is
checked before and after. The receipt is saved to tests/fixtures/tariq_edit/native_flow_REAL_receipt.json.
"""
import json
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.edit_flow import EditFlow, native_test  # noqa: E402
from studio.edit_review import EditReview  # noqa: E402
from studio.store import Store  # noqa: E402

VIDEO = Path(r'E:\CareerOS\12_YouTube\answering-exec-questions')
CAM = VIDEO / 'Recorded' / 'Input' / '2026-11-05_answering-exec-questions_recording_v02.mp4'
MIC = VIDEO / 'Recorded' / 'Input' / '00111_Wireless_PRO-esv2-50p-bg-10p-music-10p.wav'
PRPROJ = VIDEO / 'video' / 'Exec questions.prproj'
BRIDGE = 'http://127.0.0.1:47820'
OUT = Path(r'E:\Videos\DaJoshiResolve\Data\edit_flow\native-real')


class Folders:
    def __init__(self, path):
        self.p = path

    def path(self, pid, c=None):
        return str(self.p)


def main():
    source = sys.argv[1]
    reference = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else None
    shadow = '--shadow' in sys.argv
    if not json.loads(urllib.request.urlopen(BRIDGE + '/bridge/status', timeout=3).read()).get('connected'):
        sys.exit('The Josh Cut Pro panel is not linked to the helper. Open it in Premiere first.')
    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='run-', dir=OUT))
    video = work / 'video-folder'
    video.mkdir()
    for f in ('2026-11_answering-exec-questions_slides_v02.html', '2026-11_answering-exec-questions_shot-list_v02.md'):
        shutil.copy2(VIDEO / f, video / f)
    store = Store(work / 'data')
    p = store.create('Native test, Executive Questions')
    folders = Folders(video)
    flow = EditFlow(store, ROOT, None, folders, EditReview(store, folders))
    cache = ROOT / 'Data' / 'edit_flow' / 'cache'
    flow._cache = lambda: cache
    flow.bridge = BRIDGE
    src = flow.register(p['id'], CAM, MIC)
    flow.analyse(p['id'], src['id'], editorial=False)
    while (flow.progress(p['id']) or {}).get('state') in (None, 'running'):
        time.sleep(0.5)
    prop = flow.latest(p['id'], 'edit_proposal')
    flow.approve_script(p['id'], prop['id'], prop['digest'])
    started = native_test(flow, p['id'], PRPROJ, source, reference=reference, add_shadow=shadow, render_limit=3)
    print('test folder:', started['folder'], flush=True)
    folder = Path(started['folder'])
    rec, last = folder / 'receipt.json', None
    while not rec.exists():
        try:
            msg = json.loads((folder / 'progress.json').read_text(encoding='utf-8')).get('message')
        except (OSError, ValueError):
            msg = None
        if msg and msg != last:
            print('..', msg, flush=True)
            last = msg
        time.sleep(1)
    receipt = json.loads(rec.read_text(encoding='utf-8'))
    receipt['scope'] = 'REAL Premiere 25.6 with Josh Cut Pro 0.8.8, on a copy of Exec questions.prproj.'
    out = ROOT / 'tests' / 'fixtures' / 'tariq_edit' / 'native_flow_REAL_receipt.json'
    out.write_text(json.dumps(receipt, indent=1, default=str)[:400000], encoding='utf-8')
    for s in receipt['steps']:
        print(('ok    ' if s['ok'] else 'FAIL  ') + s['step'] + ('' if s['ok'] else '  ' + str(s.get('error') or s.get('result'))[:400]))
    print('stopped:', receipt.get('stopped'), '| original unchanged:', receipt['original_unchanged'], '| passed:', receipt['passed'])


if __name__ == '__main__':
    main()
