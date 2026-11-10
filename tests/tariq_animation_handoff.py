"""Tariq's animation hand-off on the real Executive Questions deck and recording, without Premiere.

    python tests/tariq_animation_handoff.py [render_count]

Uses the local transcript cache, cuts the editor's reference retakes, finds every press of the v02 deck in the
transcript, maps it through the cut, renders the first PiP press and the first full screen press for real
(ProRes with alpha and H.264), and checks the files. Writes tests/fixtures/tariq_edit/animation_handoff_receipt.json.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dajoshi import edit_plan as ep  # noqa: E402
from dajoshi.media import probe  # noqa: E402
from studio import edit_animations as ea  # noqa: E402
from studio.edit_review import parse_scenes, parse_shot_list  # noqa: E402

VIDEO = Path(r'E:\CareerOS\12_YouTube\answering-exec-questions')
EV = Path(r'C:\Users\Anigma PC\Desktop\Tariq-Editing-Handover-2026-11-10\evidence\executive-questions')
CACHE = ROOT / 'Data' / 'edit_flow' / 'cache'
F = 8467191533
SR = 16000


def main(render_count=2):
    pcm = np.fromfile(CACHE / 'pcm16k-f227708cc96ca9bf08e08103.f32', dtype='<f4')
    segs = json.loads((CACHE / 'asr-f227708cc96ca9bf08e08103-53d23d5160fd.json').read_text(encoding='utf-8'))
    words = ep.words_from_segments(segs, SR)
    events, _ = ep.discover(words, pcm, SR)
    sel = {}
    for c in json.loads((EV / 'every_cut.json').read_text(encoding='utf-8')):
        for w in words:
            if c['source_out_s'] <= (w['start'] + w['end']) / 2 / SR < c['source_in_s']:
                sel[w['id']] = 'remove'
    rem, _ = ep.plan(events, words, {}, len(pcm), SR, word_actions=sel)
    m = ep.output_map(ep.keeps_from(rem, len(pcm)), SR, 3.702875, F)
    slides = VIDEO / '2026-11_answering-exec-questions_slides_v02.html'
    shots_md = VIDEO / '2026-11_answering-exec-questions_shot-list_v02.md'
    scenes = {s['n']: s for s in parse_scenes(slides.read_text(encoding='utf-8'))}
    parts = parse_shot_list(shots_md.read_text(encoding='utf-8'))
    shots, problems = ea.plan_shots(words, rem, m['pieces'], parts, scenes, F, SR)
    anims = [p for p in parts if p['kind'] == 'animation']
    want = sum(len(p.get('presses') or []) for p in anims)
    ordered = all(a['end_f'] <= b['start_f'] for a, b in zip(shots, shots[1:]))
    receipt = dict(deck=str(slides), shot_list=str(shots_md), animations=len({p['n'] for p in anims}), presses_in_shot_list=want,
                   presses_placed=len(shots), problems=problems, in_order_no_overlap=ordered,
                   types={t: sum(s['type'] == t for s in shots) for t in ('pip', 'half', 'full')},
                   cut_seconds=round(m['duration_ticks'] / ep.TICKS_PER_SECOND, 2),
                   last_shot_end_s=round(shots[-1]['end_f'] * F / ep.TICKS_PER_SECOND, 2) if shots else None)
    picks = [next((s for s in shots if s['type'] == 'pip'), None), next((s for s in shots if s['type'] == 'full'), None)][:int(render_count)]
    picks = [dict(p) for p in picks if p]
    out = Path(tempfile.mkdtemp(prefix='tariq-renders-'))
    job = out / 'job'
    job.mkdir()
    try:
        for p in picks:  # keep the test renders short
            p['end_f'] = p['start_f'] + min(p['end_f'] - p['start_f'], 90)
        ea.render_shots(picks, slides, out, F, 1, str(ROOT / 'tools' / 'ffmpeg.exe'), job)
        checks = []
        for p in picks:
            h = probe(p['asset'])
            checks.append(dict(animation=p['animation'], press=p['press'], type=p['type'], file=Path(p['asset']).name, alpha=p['alpha'],
                               seconds=round(h['duration'], 3), size=[h['width'], h['height']], fps=h['fps']))
        for s in shots:
            s.setdefault('asset', 'not rendered in this test')
        result = ea.layer_result(shots, F, r'E:\camera.mp4', (3840, 2160), (3840, 2160))
        receipt.update(renders=checks, layers=dict(animation=sum(l['kind'] == 'animation' for l in result['layers']),
                                                   camera_copy=sum(l['kind'] == 'camera_copy' for l in result['layers']),
                                                   fps=[result['fps_num'], result['fps_den']]))
    finally:
        shutil.rmtree(out, ignore_errors=True)
    (ROOT / 'tests' / 'fixtures' / 'tariq_edit' / 'animation_handoff_receipt.json').write_text(json.dumps(receipt, indent=1), encoding='utf-8')
    print(json.dumps(receipt, indent=1))


if __name__ == '__main__':
    main(*sys.argv[1:])
