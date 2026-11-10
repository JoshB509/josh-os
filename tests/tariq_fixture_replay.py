"""Replay Tariq's planner on the Executive Questions study recording and save a receipt.

    python tests/tariq_fixture_replay.py [handover folder]

Reads the study evidence (read-only) and the original mic WAV, decodes it locally at 16 kHz,
plans with no editorial pass (the defaults) and again with the editor's reference removals given
as word selections, then compares both with the reference. Writes
tests/fixtures/tariq_edit/exec_replay_receipt.json. Geometric agreement only: no listening, and
the nonverbal candidates are not verified coughs.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dajoshi import edit_plan as ep  # noqa: E402

HANDOVER = Path(sys.argv[1] if len(sys.argv) > 1 else r'C:\Users\Anigma PC\Desktop\Tariq-Editing-Handover-2026-11-10')
EV = HANDOVER / 'evidence' / 'executive-questions'
MIC = Path(r'E:\CareerOS\12_YouTube\answering-exec-questions\Recorded\Input\00111_Wireless_PRO-esv2-50p-bg-10p-music-10p.wav')
FFMPEG = ROOT / 'tools' / 'ffmpeg.exe'
OUT = ROOT / 'tests' / 'fixtures' / 'tariq_edit'
SR = 16000


def overlap(a, b, x, y):
    return max(0.0, min(b, y) - max(a, x))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / 'exec_mic_16k.f32'
    if not cache.exists():
        raw = subprocess.run([str(FFMPEG), '-v', 'error', '-nostdin', '-i', str(MIC), '-vn', '-ac', '1', '-ar', str(SR), '-f', 'f32le', '-'],
                             capture_output=True, check=True).stdout
        cache.write_bytes(raw)
    pcm = np.fromfile(cache, dtype='<f4')
    words = ep.words_from_segments(json.loads((EV / 'asr_original.json').read_text(encoding='utf-8')), SR)
    events, meta = ep.discover(words, pcm, SR)
    total = len(pcm)
    ref_cuts = json.loads((EV / 'every_cut.json').read_text(encoding='utf-8'))
    head = json.loads((EV / 'head_tail_trims.json').read_text(encoding='utf-8'))
    pauses = json.loads((EV / 'retained_pauses.json').read_text(encoding='utf-8'))
    cands = json.loads((EV / 'nonverbal_candidates.json').read_text(encoding='utf-8'))
    ref_removed = [(c['source_out_s'], c['source_in_s']) for c in ref_cuts]
    outer = [(0.0, head[0]['source_s']), (head[1]['source_s'], 1e9)]  # editor's head and tail trims

    def report(removals, label):
        rem = [(a / SR, b / SR) for a, b in removals]
        shortened = [p for p in pauses if any(overlap(p['source_start_s'], p['source_end_s'], a, b) > 0.005 for a, b in rem)]
        # time removed that the editor kept (inside the editor's kept span, excluding head/tail)
        ref_internal = ref_removed + outer
        removed_kept = 0.0
        for a, b in rem:
            inside = b - a - sum(overlap(a, b, x, y) for x, y in ref_internal)
            removed_kept += max(0.0, inside)
        caught = [dict(id=c['id'], reference=c['reference_decision'],
                       removed_here=any(overlap(c['start_s'], c['end_s'], a, b) >= 0.5 * (c['end_s'] - c['start_s']) for a, b in rem))
                  for c in cands]
        cover = [round(sum(overlap(x, y, a, b) for a, b in rem) / (y - x), 3) for x, y in ref_removed]
        return dict(label=label, removals=len(rem), removed_s=round(sum(b - a for a, b in rem), 3),
                    retained_pauses_touched=len(shortened), retained_pauses_total=len(pauses),
                    removed_where_editor_kept_s=round(removed_kept, 3),
                    reference_cut_coverage=cover,
                    nonverbal=caught)

    defaults, issues_d = ep.plan(events, words, {}, total, SR)
    # The editor's choices as word selections: every word whose middle lies in a reference removal.
    sel = {}
    for x, y in ref_removed:
        for w in words:
            mid = (w['start'] + w['end']) / 2 / SR
            if x <= mid < y:
                sel[w['id']] = 'remove'
    guided, issues_g = ep.plan(events, words, {}, total, SR, word_actions=sel)
    kinds = {}
    for e in events:
        k = (e['kind'], e['label'].split(' ')[0] if e['kind'] == 'pause' else e['label'], e['proposed_action'])
        kinds[str(k)] = kinds.get(str(k), 0) + 1
    receipt = dict(
        fixture='Executive Questions, enhanced Wireless PRO mic, faster-whisper medium.en (study asr_original.json, not corrected)',
        mic_sha256=hashlib.sha256(MIC.read_bytes()).hexdigest(),
        planner_sha256=hashlib.sha256((ROOT / 'dajoshi' / 'edit_plan.py').read_bytes()).hexdigest(),
        config=ep.CONFIG, analysis=dict(sample_rate=SR, decode='ffmpeg mono 16 kHz f32', speech_p95_dbfs=meta['speech_p95_dbfs']),
        events=kinds, review_cards=len(ep.queue(events, {}, issues_d)),
        defaults=report(defaults, 'No editorial pass: outer trims and possible coughs only'),
        defaults_issues=len(issues_d),
        reference_words=report(guided, 'Editor reference removals given as word selections'),
        reference_words_issues=[i for i in issues_g if i['severity'] == 'clipped'][:20],
        head_tail_reference=head if isinstance(head, dict) else None,
        limits='Geometric comparison only. No listening pass. Nonverbal candidates are unverified. ASR is uncorrected.')
    (OUT / 'exec_replay_receipt.json').write_text(json.dumps(receipt, indent=1), encoding='utf-8')
    print(json.dumps({k: receipt[k] for k in ('events', 'review_cards', 'defaults_issues')}, indent=1))
    for k in ('defaults', 'reference_words'):
        r = dict(receipt[k])
        r['nonverbal'] = [f"{c['id']}:{c['reference'][0]}{'X' if c['removed_here'] else '.'}" for c in r['nonverbal']]
        print(k, json.dumps(r))
    print('clipped issues with reference words:', len(receipt['reference_words_issues']))


if __name__ == '__main__':
    main()
