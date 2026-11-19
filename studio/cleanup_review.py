"""The review pack for Tariq's cleanup pass (studio/cleanup_pass.py). Reads only. Builds nothing in Premiere.

For each proposal it works out whether the lossless clean transcript (studio/clean_transcript.py) can make the cut
without touching a word, trying the first take and then the second; measures the pause the join leaves against the
two reference-video waveform study; and writes before and after audio of the moment, a waveform and spectrogram image,
and the clean-pass transcript as it would read with every buildable proposal applied.

The study (Tariq-Editing-Handover-2026-11-10: Executive Questions and Data Storytelling, edited by a professional
editor, 10 ms RMS on the same Wireless PRO mic) found editors keep about 80 to 90 ms before the next sound and about 95
to 150 ms after the last, keep pauses of about 370 to 400 ms (90% at 560 ms or less), and treat 700 ms only as a pause
to look at, never as a cut rule. So a join's pause is compared with those numbers only: under LEAD_MS, within the
range, or over REVIEW_PAUSE_MS, all "unreviewed". A pause within the range doesn't mean the join sounds right or that no
sound was clipped. Only listening can say that, and nothing here has been listened to.
"""
import json
from pathlib import Path

import numpy as np

from . import clean_transcript as ct

T = 254016000000
RATE = 16000
LEAD_MS = 80
REVIEW_PAUSE_MS = 700
STUDY = 'C:/Users/Anigma PC/Desktop/Tariq-Editing-Handover-2026-11-10 (04-Cut-Decision-Engine.md, evidence/)'


def _join(m, keeps, cut):
    """The kept words either side of a removal and the pause between them in the clean cut, in ms."""
    W = m.words
    kept = lambda t: any(a <= t < b for a, b in keeps)  # noqa: E731
    first = next(w for w in W if w['id'] == cut[0])
    last = next(w for w in W if w['id'] == cut[-1])
    pw = [w for w in W if w['end'] <= first['start'] and kept((w['start'] + w['end']) // 2)]
    nw = [w for w in W if w['start'] >= last['end'] and kept((w['start'] + w['end']) // 2)]
    if not pw or not nw:
        return None
    pause = sum(max(0, min(b, nw[0]['start']) - max(a, pw[-1]['end'])) for a, b in keeps) / T * 1000
    return dict(before=pw[-1]['text'], after=nw[0]['text'], pause_ms=round(pause))


def assess(make, props, pcm):
    """Try each proposal on its own: the first take, then the second. Returns the proposals with status (ok, alt or
    blocked), the ids that would be cut, the join and how it compares with the study."""
    out = []
    for p in props:
        tried = [('first', p['cut_ids'], p['cut_text'])] + ([('second', p['alt_cut_ids'], p['alt_cut_text'])] if p.get('alt_cut_ids') else [])
        res = dict(p, status='blocked', why_blocked='')
        for which, ids, text in tried:
            m = make([dict(op='remove', words=ids, by='tariq-cleanup')])
            try:
                keeps, _ = m.keeps(pcm)
            except ValueError as e:
                res['why_blocked'] = (res['why_blocked'] + ' ' if res['why_blocked'] else '') + f'{which} take: {e}'
                continue
            j = _join(m, keeps, ids)
            # Only a timing comparison with the reference edits. It says nothing about clipped sounds, which needs listening.
            flag = ('below_reference_lead_unreviewed' if j and j['pause_ms'] < LEAD_MS else
                    'above_review_gate_unreviewed' if j and j['pause_ms'] > REVIEW_PAUSE_MS else 'within_reference_range_unreviewed')
            res.update(status='ok' if which == 'first' else 'alt', use_ids=ids, use_text=text, join=j, join_flag=flag)
            break
        out.append(res)
    return out


def _kept_audio(pcm, keeps, t0, t1):
    parts = []
    for a, b in keeps:
        x, y = max(a / T, t0), min(b / T, t1)
        if y > x:
            parts.append(pcm[int(x * RATE):int(y * RATE)])
    return np.concatenate(parts) if parts else np.zeros(1, np.float32)


def pack(make, props, pcm, out_dir, review_words):
    """Write the review pack. Returns its summary."""
    from scipy.io import wavfile
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    out = Path(out_dir)
    (out / 'clips').mkdir(parents=True, exist_ok=True)
    rows = assess(make, props, pcm)
    base, _ = make([]).keeps(pcm)
    good = [r for r in rows if r['status'] in ('ok', 'alt')]
    after_model = make([dict(op='remove', words=r['use_ids'], by='tariq-cleanup') for r in good])
    after, _ = after_model.keeps(pcm)
    for n, r in enumerate(rows, 1):
        t0, t1 = r['at'] - 3, r['at'] + 4
        b = _kept_audio(pcm, base, t0, t1)
        a = _kept_audio(pcm, after, t0, t1) if r['status'] != 'blocked' else b
        stem = f"{n:02d}-{int(r['at'] // 60)}m{r['at'] % 60:05.2f}s-{r['kind']}"
        wavfile.write(out / 'clips' / f'{stem}-before.wav', RATE, (b * 32767).clip(-32768, 32767).astype(np.int16))
        wavfile.write(out / 'clips' / f'{stem}-after.wav', RATE, (a * 32767).clip(-32768, 32767).astype(np.int16))
        fig, ax = plt.subplots(4, 1, figsize=(10, 7), constrained_layout=True)
        for k, (x, label) in enumerate(((b, 'AutoCut base'), (a, 'after cleanup'))):
            ax[2 * k].plot(np.arange(len(x)) / RATE, x, linewidth=0.4, color='#0c0d10')
            what = (f"with \"{r['cut_text']}\"" if k == 0 else
                    f"\"{r['use_text']}\" removed" if r.get('use_text') else 'blocked, unchanged')
            ax[2 * k].set_title(f'{label}, {what}', fontsize=9, loc='left')
            ax[2 * k + 1].specgram(x + 1e-9, Fs=RATE, NFFT=512, noverlap=384, cmap='magma')
        fig.suptitle(f"{r['kind']} at {r['at']:.2f}s · {r['status']} · join {(r.get('join') or {}).get('pause_ms', '-')} ms ({r.get('join_flag', '-')})", fontsize=10)
        fig.savefig(out / 'clips' / f'{stem}.png', dpi=110)
        plt.close(fig)
        r['files'] = dict(before=f'clips/{stem}-before.wav', after=f'clips/{stem}-after.wav', image=f'clips/{stem}.png')
    # The fresh clean-pass transcript, in clean order, for animation timing only after Josh approves the pass.
    states = after_model.states(pcm)
    # Words an edge clips (mostly AutoCut's own edges) are still heard in part, so they are written with a ~ in front.
    kept = sorted([s for s in states if s['state'] in ('kept', 'partial') and s['clean_at'] is not None], key=lambda s: s['clean_at'])
    lines, line, start = [], [], None
    for s in kept:
        start = s['clean_at'] if start is None else start
        line.append(s['text'] if s['state'] == 'kept' else '~' + s['text'])
        if s['text'].rstrip().endswith(('.', '?', '!')) or len(line) >= 18:
            lines.append(f"[{int(start // 60):02d}:{start % 60:05.2f}] {' '.join(line)}")
            line, start = [], None
    if line:
        lines.append(f"[{int(start // 60):02d}:{start % 60:05.2f}] {' '.join(line)}")
    (out / 'transcript-after-cleanup.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    summary = dict(proposals=len(rows), buildable=len(good), blocked=sum(r['status'] == 'blocked' for r in rows),
                   base_seconds=round(sum(b - a for a, b in base) / T, 2), after_seconds=round(sum(b - a for a, b in after) / T, 2),
                   study=STUDY, lead_ms=LEAD_MS, review_pause_ms=REVIEW_PAUSE_MS, words_kept=len(kept))
    (out / 'cleanup-proposals.json').write_text(json.dumps(dict(summary=summary, proposals=rows), indent=1, ensure_ascii=False), encoding='utf-8')
    return summary, rows


def for_project(evidence, clean_state, review_words=None):
    """make(edits) and pcm for a registered clean edit (Data/edit_flow/<pid>/clean-edit.json)."""
    ev = Path(evidence)
    native = json.loads((ev / 'premiere-native-words-mic.json').read_text())
    raw = next(json.loads(p.read_text())['plan']['map']['pieces'][0] for p in ev.glob('autocut-raw-*-build.json'))
    sil = json.loads((ev / 'after-silences-readback.json').read_text())['info']
    rep = json.loads((ev / 'after-repeat-readback.json').read_text())['info']
    pcm = np.fromfile(clean_state['pcm'], dtype='<f4')
    make = lambda edits: ct.CleanTranscript(native[0]['words'], raw, sil, rep, clean_state['frame'], list(clean_state.get('edits') or []) + edits,  # noqa: E731
                                            require_pcm=True)
    return make, pcm



def _raw_piece(ev):
    f = ev / 'autocut-pass.json'
    if f.is_file():
        return json.loads(f.read_text())['stages']['raw']['plan']['map']['pieces'][0]
    return next(json.loads(p.read_text())['plan']['map']['pieces'][0] for p in ev.glob('autocut-raw-*-build.json'))


def run_pass(evidence, pcm_path, frame_ticks, edits=(), out_dir=None, say=print):
    """Tariq's cleanup pass for one AutoCut pass, on the clean transcript with `edits` already applied. Writes the review
    pack to out_dir (default: <evidence>/cleanup-pass-<UTC date>) and cleanup-summary.json there. Proposes only."""
    from datetime import datetime, timezone
    from . import cleanup_pass as cp
    ev = Path(evidence)
    native = json.loads((ev / 'premiere-native-words-mic.json').read_text())
    raw = _raw_piece(ev)
    sil = json.loads((ev / 'after-silences-readback.json').read_text())['info']
    rep = json.loads((ev / 'after-repeat-readback.json').read_text())['info']
    pcm = np.fromfile(pcm_path, dtype='<f4')
    make = lambda more: ct.CleanTranscript(native[0]['words'], raw, sil, rep, frame_ticks, list(edits) + list(more),  # noqa: E731
                                           require_pcm=True)
    m = make([])
    words = [dict(id=r['id'], word=r['text'], start=r['start'] / T, end=r['end'] / T, seq_start=None if r['clean_at'] is None else r['clean_at'] - (r['end'] - r['start']) / T / 2,   # clean_at is the word's middle
                  state='kept' if r['state'] == 'kept' or (r['state'] == 'partial' and r['clean_at'] is not None) else 'removed')
             for r in m.states(pcm)]   # a word an edge clips is still heard while its middle is kept
    out = Path(out_dir) if out_dir else ev / f"cleanup-pass-{datetime.now(timezone.utc):%Y-%m-%d}"
    summary, rows = pack(make, cp.find(words), pcm, out, words)
    keeps, _ = m.keeps(pcm)
    spans = [(a / T, b / T) for a, b in keeps]
    screen = cp.nonspeech_screen(pcm, spans, native[0]['words'])
    try:
        scores = cp.yamnet_scores(pcm, spans)
        yam = dict(available=True, model_sha256=cp.YAMNET_SHA256, windows=len(scores['windows']),
                   kept_samples=scores['kept_samples'], covered_samples=scores['covered_samples'],
                   every_kept_sample_scored=scores['kept_samples'] == scores['covered_samples'],
                   threshold_note='0.1 is an uncalibrated model score, not a probability.',
                   candidates=cp.yamnet_candidates(scores, native[0]['words']))
        (out / 'yamnet-windows.json').write_text(json.dumps(scores), encoding='utf-8')
    except Exception as e:  # noqa: BLE001 - the model is extra; its absence is said, never hidden
        yam = dict(available=False, reason=str(e)[:200], candidates=[])
    nonspeech = dict(note=('Candidates to listen to, never a verdict. The screen reads level, voicing, flatness, centroid and '
                           'onset over all the kept audio, inside words too. YAMNet scores 0.975 s windows over the same audio. '
                           'A short list never means there was no cough.'),
                     screened_seconds=round(sum(b - a for a, b in spans), 1), screen=screen, yamnet=yam)
    (out / 'nonspeech-candidates.json').write_text(json.dumps(nonspeech, indent=1), encoding='utf-8')
    full = dict(summary, folder=str(out), at=datetime.now(timezone.utc).isoformat(), proposals=rows,
                nonspeech=dict(screen=len(screen), yamnet=len(yam['candidates']), yamnet_available=yam['available'],
                               yamnet_candidates=yam['candidates']),
                listened=False, note='Nothing here has been listened to. Every cut waits for Josh.')
    (out / 'cleanup-summary.json').write_text(json.dumps(full, indent=1, ensure_ascii=False, default=str), encoding='utf-8')
    say(f"Cleanup pass: {summary['proposals']} proposals ({summary['buildable']} buildable), {len(screen)} screen and "
        f"{len(yam['candidates'])} YAMNet non-speech candidates, in {out}")
    return full
