"""The whole "Tariq, go ahead with the edit" route in REAL Premiere, on the Executive Questions recording.

    python tests/tariq_go_ahead_real.py

Josh Cut Pro's helper on 47820 and panel 0.9.0 in Premiere. Josh OS's own server is left alone: this uses a
throwaway store and a scratch video folder, so Tariq's project lands there, not in Josh's video folder. The steps:
go ahead (look, Tariq's project, GREEN_MASTER from the raw files, proposal from the local caches), Josh's script
approval (which builds the cut), Josh's storyboard approval (which compiles and exports). The animations are
limited to six presses to keep the export short, and Tariq's model pass is skipped. Receipt:
tests/fixtures/tariq_edit/go_ahead_REAL_receipt.json.
"""
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio import edit_master as em  # noqa: E402
from studio.edit_flow import EditFlow  # noqa: E402
from studio.edit_review import EditReview  # noqa: E402
from studio.store import Store  # noqa: E402

VIDEO = Path(r'E:\CareerOS\12_YouTube\answering-exec-questions')
CAM = VIDEO / 'Recorded' / 'Input' / '2026-11-05_answering-exec-questions_recording_v02.mp4'
MIC = VIDEO / 'Recorded' / 'Input' / '00111_Wireless_PRO-esv2-50p-bg-10p-music-10p.wav'
WORK = ROOT / 'Data' / 'edit_flow' / 'go-ahead-real' / time.strftime('%Y%m%d-%H%M%S')


class Folders:
    def __init__(self, path):
        self.p = path

    def path(self, pid, c=None):
        return str(self.p)


def wait_for(fn, what, limit=7200):
    t0 = time.time()
    while time.time() - t0 < limit:
        r = fn()
        if r:
            return r
        time.sleep(3)
    raise TimeoutError(what)


def main():
    video = WORK / 'Executive Questions'
    video.mkdir(parents=True)
    for f in ('2026-11_answering-exec-questions_slides_v02.html', '2026-11_answering-exec-questions_shot-list_v02.md'):
        shutil.copy2(VIDEO / f, video / f)
    store = Store(WORK / 'data')
    p = store.create('Executive Questions')
    pid = p['id']
    folders = Folders(video)
    er = EditReview(store, folders)
    flow = EditFlow(store, ROOT, None, folders, er)
    flow._cache = lambda: ROOT / 'Data' / 'edit_flow' / 'cache'
    told = []
    flow._tell = lambda _pid, text, via='': (told.append(text), print('TARIQ:', text, flush=True))
    real_layers = flow.animation_layers
    flow.animation_layers = lambda *a, **k: real_layers(*a, **dict(k, render_limit=6))
    receipt = dict(started=time.strftime('%Y-%m-%d %H:%M:%S'), work=str(WORK), told=told)
    flow.register(pid, CAM, MIC)

    # 1. Josh says go ahead
    flow.go_ahead(pid)
    wait_for(lambda: not flow.jobs[('go', pid)].is_alive(), 'go ahead')
    receipt['master'] = (flow.latest(pid, 'edit_master') or {}).get('payload')
    prop = flow.latest(pid, 'edit_proposal')
    v = flow.view(pid, prop['id'])
    receipt['proposal'] = dict(summary=v['summary'], cards=len(v['cards']))

    # 2. Josh approves the script; Tariq builds the cut
    flow.approve_script(pid, prop['id'], prop['digest'], auto=False)
    flow._after_script(pid, prop)
    cut = flow.latest(pid, 'edit_cut')
    receipt['cut'] = dict(state=cut['payload'].get('state'), sequence=cut['payload']['plan']['sequence_name'],
                          problems=cut['payload'].get('problems'), error=cut['payload'].get('error'))
    if cut['payload'].get('state') != 'verified':
        return finish(receipt)

    # 3. Josh approves the storyboard; Tariq compiles and exports
    review = er.review(pid)
    vis = flow.visuals(pid, cut['id'], storyboard_digest=f"{review.get('slides')}|{review.get('rendered')}", approve=True, auto=True)
    fin = wait_for(lambda: (lambda f: f if f and f['payload'].get('state') != 'running' else None)(flow.latest(pid, 'edit_final')), 'final')
    receipt['final'] = {k: fin['payload'].get(k) for k in ('state', 'output', 'problems', 'animations', 'error')}
    finish(receipt)


def finish(receipt):
    receipt['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
    receipt['scope'] = 'REAL Premiere 25.6.6 with Josh Cut Pro 0.9.0. Proposal from cached transcript, no model pass. Six animation presses.'
    out = ROOT / 'tests' / 'fixtures' / 'tariq_edit' / 'go_ahead_REAL_receipt.json'
    out.write_text(json.dumps(receipt, indent=1, default=str)[:400000], encoding='utf-8')
    print(json.dumps({k: receipt.get(k) for k in ('master', 'proposal', 'cut', 'final')}, indent=1, default=str)[:6000])


if __name__ == '__main__':
    main()
