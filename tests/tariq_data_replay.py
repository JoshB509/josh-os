"""Reproduce the Data Storytelling acoustic replay (handover doc 10) and save a receipt, then run
Tariq's planner defaults on the same audio for comparison.

    python tests/tariq_data_replay.py

Read-only on every input. Writes tests/fixtures/tariq_edit/data_replay_receipt.json.
Geometric agreement only. No listening, candidates are not verified coughs, and the words are the
study's Premiere transcript words, not a new local transcription.
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dajoshi import edit_plan as ep  # noqa: E402

CUT = Path(r'E:\Videos\DaJoshiCut\helper')
INPUTS = Path(r'C:\Users\Anigma PC\Documents\Codex\2026-11-10\storytelling-cut-analysis')
EV = Path(r'C:\Users\Anigma PC\Desktop\Tariq-Editing-Handover-2026-11-10\evidence\data-storytelling')
OUT = ROOT / 'tests' / 'fixtures' / 'tariq_edit'
SETTINGS = dict(max_pause=0.45, pad_before=0.08, pad_after=0.10, cut_sounds=True)  # as recorded in doc 10


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def overlap(a, b, x, y):
    return max(0.0, min(b, y) - max(a, x))


def removed_from(keeps, total):
    out, p = [], 0.0
    for a, b in keeps:
        if a > p:
            out.append((p, a))
        p = max(p, b)
    if p < total:
        out.append((p, total))
    return out


def score(removed, pauses, cands):
    touched = []
    for p in pauses:
        cut = sum(overlap(p['source_start_s'], p['source_end_s'], a, b) for a, b in removed)
        if cut > 0.005:
            touched.append(dict(start=p['source_start_s'], duration=p['duration_s'], left=round(p['duration_s'] - cut, 3), cut=round(cut, 3)))
    ev = [dict(id=c['id'], reference=c['reference_decision'],
               removed_here=sum(overlap(c['start_s'], c['end_s'], a, b) for a, b in removed) >= 0.5 * (c['end_s'] - c['start_s']))
          for c in cands]
    kept = [e for e in ev if e['reference'] == 'kept']
    gone = [e for e in ev if e['reference'] == 'removed']
    return dict(retained_pauses_shortened=len(touched), retained_pauses_total=len(pauses),
                seconds_lost_from_retained_pauses=round(sum(t['cut'] for t in touched), 3),
                worst=sorted(touched, key=lambda t: -t['cut'])[:5],
                kept_candidates_survived=f"{sum(not e['removed_here'] for e in kept)}/{len(kept)}",
                removed_candidates_caught=f"{sum(e['removed_here'] for e in gone)}/{len(gone)}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(CUT))
    import cleancut
    x = np.load(INPUTS / 'source_mono.npy').astype(np.float64)
    sr = 48000
    total = len(x) / sr
    pauses = json.loads((INPUTS / 'retained_pauses.json').read_text(encoding='utf-8'))
    cands = json.loads((INPUTS / 'nonverbal_candidates.json').read_text(encoding='utf-8'))
    db = cleancut.db_profile(x, sr)
    floor, speech, auto = cleancut.levels(db)
    keeps, dropped = cleancut.acoustic_keeps(db, auto, speech, SETTINGS)
    current = score(removed_from(keeps, total), pauses, cands)

    # Tariq's planner defaults, using the study's Premiere words (16 kHz like production).
    words_src = json.loads((EV / 'premiere_source_words.json').read_text(encoding='utf-8'))
    x16 = x[: len(x) // 3 * 3].reshape(-1, 3).mean(axis=1)
    seg = [dict(words=[dict(start=w.get('start', w.get('start_s')), end=w.get('end', w.get('end_s')),
                            word=w.get('word', w.get('text', ''))) for w in (words_src if isinstance(words_src, list) else words_src['words'])])]
    words = ep.words_from_segments(seg, 16000)
    events, meta = ep.discover(words, x16, 16000)
    rem, issues = ep.plan(events, words, {}, len(x16), 16000)
    planner = score([(a / 16000, b / 16000) for a, b in rem], pauses, cands)
    planner['review_cards'] = len(ep.queue(events, {}, issues))
    planner['removed_s'] = round(sum(b - a for a, b in rem) / 16000, 3)

    receipt = dict(
        purpose='Reproduction of the read-only Data replay in handover doc 10, now with a saved receipt.',
        inputs={p.name: sha(p) for p in [INPUTS / 'source_mono.npy', INPUTS / 'retained_pauses.json',
                                         INPUTS / 'nonverbal_candidates.json', INPUTS / 'final_clips.json', CUT / 'cleancut.py']},
        current_tool=dict(call='db_profile(x, 48000); floor, speech, auto = levels(db); acoustic_keeps(db, auto, speech, settings)',
                          settings=SETTINGS, floor_dbfs=round(floor, 2), speech_dbfs=round(speech, 2), auto_threshold_dbfs=round(auto, 2),
                          sounds_dropped=dropped, **current),
        doc10_reported=dict(retained_pauses_shortened='9/210', seconds_lost=3.55, kept_survived='7/7', removed_caught='3/6'),
        tariq_planner_defaults=dict(words='premiere_source_words.json (study evidence, not new ASR)', audio='source_mono.npy averaged to 16 kHz',
                                    planner_sha256=sha(ROOT / 'dajoshi' / 'edit_plan.py'), **planner),
        limits='48 kHz cached PCM and 10 ms bins for the current tool, as in doc 10. No listening, no Claude pass, no native edit. '
               'Candidates are not verified coughs. Room-tone inserts in the reference are not cuts.')
    (OUT / 'data_replay_receipt.json').write_text(json.dumps(receipt, indent=1), encoding='utf-8')
    print(json.dumps(dict(current=receipt['current_tool'], planner=receipt['tariq_planner_defaults']), indent=1)[:3000])


if __name__ == '__main__':
    main()
