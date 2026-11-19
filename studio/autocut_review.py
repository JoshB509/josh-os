"""Tariq's check of an AutoCut pass, from Premiere's own words (read-only). Reusable for any recording.

Every native word of the good mic is mapped through the cut sequence's A1 clips (studio/premiere_transcript.py), so each
word keeps its source time and gets its clean-timeline time. Then removed words are grouped into runs and tagged with
the pass that took them (Silences or Repeat); repeats still kept are listed as leftovers; words whose edge falls outside
a piece are checked against the mic level, and only those losing sound (under 40 dB below the word) are flagged; removed
runs not followed by a restart are flagged as possible content. Level only says where sound is, never which word it is.
Writes review.json and review.html into the evidence folder. Nothing in Premiere is changed."""
import html
import json
import re
from pathlib import Path

import numpy as np

from . import premiere_transcript as pt


def build(prproj, sequence, mic, pcm, evidence, raw_piece, after_silences, clean_length_s, say=print):
    """raw_piece: the one map piece of the uncut copy (ticks). after_silences: read-back pieces after Silences (seconds).
    pcm: the mic decoded to 16 kHz float32. Returns the summary."""
    ev = Path(evidence)
    tx = pt.words(prproj)
    selected = pt.microphone_transcript(tx, mic)
    if not selected:
        raise ValueError('The saved project has no native transcript bound to the selected microphone.')
    src = [w for w in selected['words'] if w['word'].strip()]          # empty entries are Premiere's pause markers
    kept, removed = pt.sequence_words(prproj, sequence, src, mic)
    after_sil = after_silences
    raw = raw_piece
    T = pt.T
    raw_in, raw_out = raw['mic_in'] / T, raw['mic_out'] / T
    in_pieces = lambda t, ps: any(p['mic_in'] <= t < p['mic_out'] for p in ps)
    norm = lambda s: re.sub(r"[^a-z0-9']+", '', s.lower())

    rows = []
    kept_ids = {id(w) for w in kept}
    k_by_start = {round(w['start'], 4): w for w in kept}
    for w in src:
        mid = (w['start'] + w['end']) / 2
        k = k_by_start.get(round(w['start'], 4))
        if not (raw_in <= mid < raw_out):
            state = 'outside'           # before the camera starts or after it ends: trimmed by the raw build, not AutoCut
        elif k is not None:
            state = 'kept'
        elif not in_pieces(mid, after_sil):
            state = 'silences'
        else:
            state = 'repeat'
        rows.append(dict(w, state=state, seq_start=k and k['seq_start'], clipped=bool(k and k['clipped'])))

    # removed runs, with the words right after them, to judge whether each is a retake
    runs, cur = [], None
    for i, r in enumerate(rows):
        if r['state'] in ('silences', 'repeat'):
            if cur and cur['state'] == r['state'] and cur['end_i'] == i - 1:
                cur['end_i'] = i
            else:
                cur = dict(state=r['state'], start_i=i, end_i=i)
                runs.append(cur)
    for run in runs:
        ws = rows[run['start_i']:run['end_i'] + 1]
        after = [r for r in rows[run['end_i'] + 1:run['end_i'] + 12] if r['state'] == 'kept'][:8]
        before = [r for r in rows[max(0, run['start_i'] - 8):run['start_i']] if r['state'] == 'kept'][-6:]
        first = norm(ws[0]['word'])
        restart = first and first in [norm(a['word']) for a in after[:4]]
        run.update(text=' '.join(w['word'] for w in ws), at=ws[0]['start'], end=ws[-1]['end'], words=len(ws),
                   before=' '.join(b['word'] for b in before), after=' '.join(a['word'] for a in after),
                   looks_like_retake=bool(restart))

    # repeats still kept: n words immediately said again (n = 1..5), on the kept words in clean order
    kw = [r for r in rows if r['state'] == 'kept']
    left = []
    i = 0
    while i < len(kw):
        hit = None
        for n in range(5, 0, -1):
            a, b = kw[i:i + n], kw[i + n:i + 2 * n]
            if len(b) == n and [norm(x['word']) for x in a] == [norm(x['word']) for x in b] and all(norm(x['word']) for x in a):
                if n == 1 and norm(a[0]['word']) in ('sorry', 'okay', 'yes', 'no', 'very', 'so', 'that', 'had'):
                    continue     # natural doubles ("Sorry, sorry", "Okay. Okay.") are listed separately below, not as leftovers
                hit = n
                break
        if hit:
            left.append(dict(text=' '.join(x['word'] for x in kw[i:i + 2 * hit]), source_at=kw[i]['start'], clean_at=kw[i]['seq_start'],
                             context=' '.join(x['word'] for x in kw[max(0, i - 6):i + 2 * hit + 6])))
            i += 2 * hit
        else:
            i += 1
    doubles = [dict(text=f"{a['word']} {b['word']}", source_at=a['start'], clean_at=a['seq_start']) for a, b in zip(kw, kw[1:])
               if norm(a['word']) == norm(b['word']) and norm(a['word']) in ('sorry', 'okay', 'yes', 'no', 'very', 'so', 'that', 'had')]
    # A word counts as cut through only if the part outside the piece holds sound. Premiere's word times start a little
    # early, so most "clipped" starts are silence. Level only says where sound is, never which phoneme it is.
    _pcm = np.fromfile(pcm, dtype='<f4')
    _db = lambda a, b: float(20 * np.log10(np.sqrt(np.mean(_pcm[int(a * 16000):max(int(a * 16000) + 1, int(b * 16000))].astype(float) ** 2)) + 1e-9))
    clipped, silent_edge = [], 0
    for w in kept:
        if not w['clipped']:
            continue
        lost = (w['start'], w['piece_in']) if w['start'] < w['piece_in'] else (w['piece_out'], w['end'])
        below = _db(w['start'], w['end']) - _db(*lost)
        if below >= 40:
            silent_edge += 1      # the part left out is silence, 40 dB or more under the word
            continue
        clipped.append(dict(word=w['word'], source_at=w['start'], clean_at=w['seq_start'], ms=w['clipped_ms'], below_word_db=round(below, 1)))
    for r in rows:
        r['clipped'] = any(abs(r['start'] - c['source_at']) < 1e-6 for c in clipped)
    content_cuts = [r for r in runs if r['state'] == 'repeat' and not r['looks_like_retake']]

    summary = dict(sequence=sequence, native_words=len(src), kept=sum(r['state'] == 'kept' for r in rows),
                   removed_by_silences=sum(r['state'] == 'silences' for r in rows), removed_by_repeat=sum(r['state'] == 'repeat' for r in rows),
                   outside_camera=sum(r['state'] == 'outside' for r in rows), removed_runs=len(runs), leftover_repeats=len(left),
                   natural_doubles=len(doubles), words_cut_at_a_join=len(clipped), word_edges_trimmed_in_silence_only=silent_edge, repeat_cuts_not_followed_by_a_restart=len(content_cuts),
                   clean_length_s=clean_length_s)
    (ev / 'review.json').write_text(json.dumps(dict(summary=summary, runs=runs, leftovers=left, doubles=doubles, clipped=clipped,
                                                    words=rows), indent=1), encoding='utf-8')
    say(json.dumps(summary, indent=1))
    for r in runs:
        if r['state'] == 'silences':
            say(f"  SILENCES took words at {r['at']:.2f}: {r['text']!r}")
    for r in content_cuts:
        say(f"  REPEAT cut not followed by a restart at {r['at']:.2f}: {r['text']!r} | next: {r['after']!r}")
    for l in left:
        say(f"  LEFTOVER at source {l['source_at']:.2f} (clean {l['clean_at']:.2f}): {l['text']!r} | {l['context']!r}")
    for c in clipped:
        say(f"  CUT THROUGH a word at source {c['source_at']:.2f}: {c['word']!r}")


    # ---- review page: the whole transcript, removed words struck through in red with their pass, leftovers marked
    def mmss(t):
        return f'{int(t // 60)}:{t % 60:04.1f}' if t is not None else ''


    left_starts = {round(l['source_at'], 3) for l in left}
    clip_starts = {round(c['source_at'], 3) for c in clipped}
    parts, prev = [], None
    for r in rows:
        if r['state'] == 'outside':
            continue
        w = html.escape(r['word'])
        if r['state'] != prev and r['state'] in ('silences', 'repeat'):
            parts.append(f'<span class="tag">{"Silences" if r["state"] == "silences" else "Repeat"} at {mmss(r["start"])}</span>')
        cls = {'kept': 'k', 'silences': 'rs', 'repeat': 'rr'}[r['state']]
        if round(r['start'], 3) in left_starts:
            cls += ' left'
        if round(r['start'], 3) in clip_starts:
            cls += ' clip'
        parts.append(f'<span class="{cls}" title="source {r["start"]:.2f}s{"" if r["seq_start"] is None else f" | clean {r[chr(115)+chr(101)+chr(113)+chr(95)+chr(115)+chr(116)+chr(97)+chr(114)+chr(116)]:.2f}s"}">{w}</span>')
        prev = r['state']


    def li(items, fmt):
        return ''.join(f'<li>{fmt(x)}</li>' for x in items) or '<li>None found.</li>'


    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>AutoCut review: 4 Signs</title><style>
    :root{{--ink:#0c0d10;--ink2:#16181d;--ink3:#22252c;--text:#e9e6df;--muted:#9a978f;--gold:#f5c21b;--red:#e2584c;--green:#43b37a}}
    body{{margin:0;background:var(--ink);color:var(--text);font:15px/1.65 Manrope,system-ui,sans-serif}}
    main{{max-width:860px;margin:0 auto;padding:28px 16px 80px}}
    h1{{font:700 26px/1.2 Outfit,system-ui,sans-serif;margin:0 0 6px}} h2{{font:600 18px Outfit,system-ui,sans-serif;margin:32px 0 8px;color:var(--gold)}}
    p.lead{{color:var(--muted);margin:0 0 18px}}
    .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
    .stat{{background:var(--ink2);border-radius:8px;padding:10px 12px;box-shadow:0 1px 2px #0008,0 4px 12px #0004}}
    .stat b{{display:block;font:700 20px Outfit,system-ui,sans-serif}} .stat span{{color:var(--muted);font-size:13px}}
    ul{{padding-left:20px}} li{{margin:4px 0}}
    .tx{{background:var(--ink2);border-radius:8px;padding:16px;box-shadow:0 1px 2px #0008,0 4px 12px #0004}}
    .rs,.rr{{color:var(--red);text-decoration:line-through}} .rs{{opacity:.75}}
    .left{{background:#f5c21b33;outline:1px solid var(--gold);border-radius:3px}} .clip{{border-bottom:2px dotted var(--gold)}}
    .tag{{font-size:11px;color:var(--ink);background:var(--red);border-radius:3px;padding:0 4px;margin:0 3px;vertical-align:2px}}
    .key span{{margin-right:14px}}
    </style></head><body><main>
    <h1>AutoCut pass on {html.escape(sequence)}</h1>
    <p class="lead">AutoCut Silences then AutoCut Repeat, both on their defaults, audio track 1, on a copy of GREEN_MASTER with the good mic. This is a review cut, nothing more. The words are Premiere's own transcript of the mic.</p>
    <div class="stats">
    <div class="stat"><b>{mmss(summary['clean_length_s'])}</b><span>clean length</span></div>
    <div class="stat"><b>{summary['removed_by_silences']}</b><span>words taken by Silences</span></div>
    <div class="stat"><b>{summary['removed_by_repeat']}</b><span>words taken by Repeat</span></div>
    <div class="stat"><b>{summary['leftover_repeats']}</b><span>repeats still in</span></div>
    <div class="stat"><b>{summary['words_cut_at_a_join']}</b><span>words cut through at a join</span></div>
    </div>
    <h2>Repeats still in the cut</h2>
    <ul>{li(left, lambda l: f"<b>{html.escape(l['text'])}</b> at {mmss(l['clean_at'])} in the cut. {html.escape(l['context'])}")}</ul>
    <h2>Words Silences took out</h2>
    <p class="lead">Silences should only take out pauses, so any word here needs a listen.</p>
    <ul>{li([r for r in runs if r['state'] == 'silences'], lambda r: f"<b>{html.escape(r['text'])}</b> at {mmss(r['at'])} in the recording. Before it: {html.escape(r['before'])}")}</ul>
    <h2>Repeat cuts with no restart after them</h2>
    <p class="lead">Repeat took these out, but the next words don't start the same way, so it may have been a rephrase or real content.</p>
    <ul>{li(content_cuts, lambda r: f"<b>{html.escape(r['text'])}</b> at {mmss(r['at'])}. Then: {html.escape(r['after'])}")}</ul>
    <h2>Words that may be cut through at a join</h2>
    <p class="lead">Each of these loses a faint bit of sound at its edge. That could be the start of the word or a breath, so it needs a listen. Another {silent_edge} word edges fall in silence and are fine.</p>
    <ul>{li(clipped, lambda c: f"<b>{html.escape(c['word'])}</b> at {mmss(c['clean_at'])} in the cut, {c['ms']:.0f} ms left out, {c['below_word_db']:.0f} dB under the word")}</ul>
    <h2>Doubles kept on purpose</h2>
    <ul>{li(doubles, lambda d: f"{html.escape(d['text'])} at {mmss(d['clean_at'])} in the cut")}</ul>
    <h2>Full transcript</h2>
    <p class="key"><span class="k">Kept</span><span class="rr">Taken by Repeat</span><span class="rs">Taken by Silences</span><span class="left">Repeat still in</span><span class="clip">Cut through</span></p>
    <div class="tx">{' '.join(parts)}</div>
    </main></body></html>"""
    (ev / 'review.html').write_text(page, encoding='utf-8')
    say(f"wrote {ev / 'review.html'}")
    return summary
