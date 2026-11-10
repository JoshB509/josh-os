"""Tariq's cut planner: source-anchored, reversible edit decisions for one recording.

Pure functions with no media jobs, model calls or Premiere access. Everything is measured on the
good microphone's own clock in sample indices (half-open intervals). The planner turns words and a
level profile into events (words, pauses, sounds, room-tone inserts), then turns events plus
decisions into a retained-source map that the native builder applies to a NEW sequence.

What this is not. The studies behind it (Executive Questions and Data Storytelling, 10 Nov 2026)
were measured development fixtures with no listening pass and no verified cough labels. Every
number in CONFIG is a declared development setting, marked calibrated=False, never a validated
threshold. Pauses are kept unless an editorial decision shortens them. Sounds are never called
coughs, only possible coughs or unclassified sounds, and they all go to review.
"""
import hashlib
import json
import math
import re
from fractions import Fraction

import numpy as np

SCHEMA_VERSION = 1
TICKS_PER_SECOND = 254016000000  # Premiere's tick rate
KINDS = ('speech', 'take_group', 'pause', 'cough', 'throat_clear', 'breath', 'other_sound', 'room_tone_insert', 'boundary_issue')
ACTIONS = ('keep', 'remove', 'shorten', 'restore')
CONFIG = dict(
    calibrated=False,
    note='Development settings from two measured fixtures. Not a validated policy; every decision stays visible and reversible.',
    bin_ms=10,                     # level profile resolution, as in both studies
    activity_dbfs=-50.0,           # level both studies measured at; declared, not calibrated for other mic chains
    word_guard_ms=20,              # never cut this close to a kept word
    pad_in_ms=100,                 # handle before an incoming word (report: first pilot candidate 100 ms)
    pad_out_ms=150,                # handle after an outgoing word (report: 130 to 150 ms on retake joins)
    head_keep_ms=100,              # before the first kept word
    tail_keep_ms=300,              # after the last kept word (reference tail kept about 280 to 360 ms)
    pause_min_ms=300,              # pauses this long appear in the transcript
    pause_review_ms=700,           # report's conservative review gate; NOT a cut rule
    sound_min_ms=80,
    sound_max_ms=1200,
    sound_isolation_ms=150,        # a sound nearer a word than this is speech-adjacent
    word_edge_max_ms=200,          # shorter speech-adjacent activity counts as the word's own edge, not an event
    faint_below_p95_db=25.0,       # quieter than speech p95 minus this reads as breath or faint sound
    sound_margin_ms=30,
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def ms(n, sr):
    return int(round(n * sr / 1000))


# ---------------------------------------------------------------- measurement

def level_profile(pcm, sr, bin_ms=CONFIG['bin_ms']):
    """10 ms RMS in dBFS. Bin i covers samples [i*hop, (i+1)*hop)."""
    hop = ms(bin_ms, sr)
    pcm = np.asarray(pcm, dtype=np.float64)
    n = len(pcm) // hop * hop
    rms = np.sqrt(np.mean(pcm[:n].reshape(-1, hop) ** 2, axis=1) + 1e-20) if n else np.zeros(0)
    return 20 * np.log10(np.maximum(rms, 1e-10)), hop


def runs(mask):
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist()))


def words_from_segments(segments, sr):
    """Flatten a transcript (list of segments with words, seconds) into stable word records."""
    out = []
    for s in segments if isinstance(segments, list) else segments.get('segments', []):
        for w in s.get('words', []):
            text = str(w.get('word', '')).strip()
            if not text:
                continue
            a, b = int(round(float(w['start']) * sr)), int(round(float(w['end']) * sr))
            if b <= a:
                b = a + 1
            out.append(dict(id=f'w{len(out) + 1:05d}', text=text, start=a, end=b, probability=w.get('probability')))
    for i in range(1, len(out)):  # ASR can overlap neighbours slightly; keep order and half-open intervals
        if out[i]['start'] < out[i - 1]['end']:
            mid = (out[i]['start'] + out[i - 1]['end']) // 2
            out[i - 1]['end'] = max(out[i - 1]['start'] + 1, mid)
            out[i]['start'] = max(out[i - 1]['end'], min(out[i]['start'], mid))
    return out


NONVERBAL_WORDS = {'ahem', 'hem', 'ehem'}


def norm(text):
    return re.sub(r"[^a-z0-9']+", '', text.lower())


# ---------------------------------------------------------------- discovery

def discover(words, pcm, sr, config=CONFIG):
    """Measure pauses, sounds and restart candidates. Returns events with default proposals only."""
    if not words:
        raise ValueError('No transcript words. Transcription must succeed before planning.')
    db, hop = level_profile(pcm, sr, config['bin_ms'])
    total = len(pcm)
    active = db >= config['activity_dbfs']
    speech_db = [db[w['start'] // hop:max(w['start'] // hop + 1, w['end'] // hop)] for w in words]
    speech_db = np.concatenate([x for x in speech_db if len(x)]) if words else db
    p95 = float(np.percentile(speech_db, 95)) if len(speech_db) else -20.0
    events = []

    def add(**e):
        e.setdefault('schema_version', SCHEMA_VERSION)
        e['event_id'] = f"{e['kind'][:2]}-{e['start']}-{e['end']}"
        events.append(e)
        return e

    guard = ms(config['word_guard_ms'], sr)
    word_edges = 0
    bounds = [(0, None, words[0])] + [(words[i]['end'], words[i], words[i + 1]) for i in range(len(words) - 1)] + [(words[-1]['end'], words[-1], None)]
    for gap_start, before, after in bounds:
        gap_end = after['start'] if after else total
        if before is None:
            gap_start = 0
        if gap_end - gap_start <= 0:
            continue
        lo, hi = gap_start // hop, max(gap_start // hop, gap_end // hop)
        seg = active[lo:hi]
        # sounds inside the gap, measured away from the word guards
        for a, b in runs(seg):
            s, e = (lo + a) * hop, (lo + b) * hop
            dur = (e - s) * 1000 / sr
            if dur < config['sound_min_ms']:
                continue
            inside = max(0, min(e, gap_end - guard) - max(s, gap_start + guard))
            if inside < 0.7 * (e - s):
                continue
            peak = float(db[lo + a:lo + b].max())
            near = min(abs(s - before['end']) if before else 10 ** 9, abs(after['start'] - e) if after else 10 ** 9) * 1000 / sr
            ev = dict(kind='other_sound', start=s, end=e, word_ids_before=[before['id']] if before else [],
                      word_ids_after=[after['id']] if after else [],
                      measurements=dict(duration_ms=round(dur), peak_dbfs=round(peak, 2), speech_p95_dbfs=round(p95, 2),
                                        nearest_word_ms=round(near)))
            if dur > config['sound_max_ms']:
                ev.update(label='[untranscribed sound or speech]', proposed_action='keep', review=True,
                          reason='Long activity with no transcript words. Missing words do not prove silence.',
                          uncertainty=dict(label='unverified', acoustic='possible_speech'))
            elif peak < p95 - config['faint_below_p95_db']:
                ev.update(kind='breath', label='[breath or faint sound]', proposed_action='keep', review=False,
                          reason='Faint sound between words. Breaths are kept by default.',
                          uncertainty=dict(label='unverified'))
            elif near < config['sound_isolation_ms'] and dur < config['word_edge_max_ms']:
                word_edges += 1  # activity touching a word is usually that word's own onset or tail (ASR times drift)
                continue
            elif near < config['sound_isolation_ms']:
                ev.update(label='[unclassified sound]', proposed_action='keep', review=False,
                          reason='Sound right beside a word. Kept, because removing it could clip speech.',
                          uncertainty=dict(label='unverified', editorial='speech_adjacent'))
            else:
                ev.update(label='[possible cough]', proposed_action='remove', review=True,
                          reason='Isolated sound between words. Not a verified cough, so it needs a listen.',
                          uncertainty=dict(label='unverified', editorial='needs_review'))
            add(**ev)
        if before is None or after is None:
            add(kind='pause', start=gap_start, end=gap_end, word_ids_before=[before['id']] if before else [],
                word_ids_after=[after['id']] if after else [], label='[start]' if before is None else '[end]',
                proposed_action='shorten', target_ms=config['head_keep_ms'] if before is None else config['tail_keep_ms'],
                review=False, outer=True, reason='Outer trim before the first word or after the last word.',
                measurements=dict(duration_ms=round((gap_end - gap_start) * 1000 / sr)))
            continue
        dur = (gap_end - gap_start) * 1000 / sr
        if dur >= config['pause_min_ms']:
            low = [(b - a) for a, b in runs(~seg)] or [0]
            add(kind='pause', start=gap_start, end=gap_end, word_ids_before=[before['id']], word_ids_after=[after['id']],
                label=f'[pause {dur / 1000:.1f}s]', proposed_action='keep', review=dur >= config['pause_review_ms'],
                reason=('Long pause. Kept unless Tariq or Josh decides it should be shorter.' if dur >= config['pause_review_ms']
                        else 'Ordinary pause, kept.'),
                measurements=dict(duration_ms=round(dur), longest_low_run_ms=max(low) * config['bin_ms']),
                uncertainty=dict(label='unverified', editorial='needs_review' if dur >= config['pause_review_ms'] else 'default'))
    # Whisper sometimes writes a throat clear as a word. It is a sound event, not speech.
    for w in words:
        if norm(w['text']) in NONVERBAL_WORDS:
            add(kind='throat_clear', start=w['start'], end=w['end'], word_ids=[w['id']], label='[possible throat clear]',
                proposed_action='remove', review=True, text=w['text'],
                reason=f"The transcript wrote {w['text'].strip()!r} here. Not a verified label, so it needs a listen.",
                uncertainty=dict(label='unverified', editorial='needs_review'))
    events += restart_candidates(words, sr)
    events.sort(key=lambda e: (e['start'], e['end'], e['kind']))
    return events, dict(speech_p95_dbfs=round(p95, 2), word_edge_sounds=word_edges, samples=total, sample_rate=sr, bins=len(db), config=dict(config))


def restart_candidates(words, sr, window_s=30, min_match=2):
    """Places where a later phrase starts with the same words as an earlier one: possible retakes.

    Candidates only. Repeated wording can be a list, emphasis or a separate sentence, so the default
    action is keep until the editorial pass or Josh decides."""
    out, seen = [], set()
    toks = [norm(w['text']) for w in words]
    for i in range(len(words)):
        for j in range(i + 1, len(words)):
            if words[j]['start'] - words[i]['start'] > window_s * sr:
                break
            k = 0
            while j + k < len(words) and i + k < j and toks[i + k] and toks[i + k] == toks[j + k]:
                k += 1
            if k >= min_match and (i, j) not in seen and not any(a <= i < b for a, b in seen):
                seen.add((i, j))
                out.append(dict(kind='take_group', start=words[i]['start'], end=words[j]['start'],
                                word_ids=[w['id'] for w in words[i:j]], retake_word_id=words[j]['id'],
                                label='[possible retake]', proposed_action='keep', review=True,
                                text=' '.join(w['text'] for w in words[i:j]),
                                reason=f'Words {words[j]["text"]!r}... restart {k} words from earlier. Repeats can be deliberate.',
                                uncertainty=dict(label='candidate', editorial='needs_review'),
                                event_id=f"ta-{words[i]['start']}-{words[j]['start']}", schema_version=SCHEMA_VERSION))
                break
    return out


# ---------------------------------------------------------------- decisions to removals

def apply_editorial(events, words, removals=(), pauses=(), sounds=(), checks=()):
    """Fold the editorial pass (Tariq) into the proposal. Inputs are already validated id references."""
    ids = {w['id']: i for i, w in enumerate(words)}
    out = [dict(e) for e in events]
    index = {e['event_id']: e for e in out}
    for r in removals:
        a, b = ids[r['start_word']], ids[r['end_word']]
        if b < a:
            a, b = b, a
        span = words[a:b + 1]
        out.append(dict(kind='take_group', start=span[0]['start'], end=span[-1]['end'], word_ids=[w['id'] for w in span],
                        label='[retake]' if r.get('kind') != 'other' else '[removed]', proposed_action='remove', review=bool(r.get('unsure')),
                        text=' '.join(w['text'] for w in span), reason=str(r.get('reason', ''))[:400], source='editorial',
                        uncertainty=dict(label='editorial', editorial='needs_review' if r.get('unsure') else 'default'),
                        event_id=f"tr-{span[0]['id']}-{span[-1]['id']}", schema_version=SCHEMA_VERSION))
    for c in checks:
        # Tariq would keep this but wants Josh to listen: a possible reworded retake, a stumble, anything odd
        a, b = ids[c['start_word']], ids[c['end_word']]
        if b < a:
            a, b = b, a
        span = words[a:b + 1]
        out.append(dict(kind='take_group', start=span[0]['start'], end=span[-1]['end'], word_ids=[w['id'] for w in span],
                        label='[check]', proposed_action='keep', review=True, text=' '.join(w['text'] for w in span),
                        reason=str(c.get('reason', ''))[:400], source='editorial', check=True,
                        uncertainty=dict(label='editorial', editorial='needs_review'),
                        event_id=f"tc-{span[0]['id']}-{span[-1]['id']}", schema_version=SCHEMA_VERSION))
    for p in pauses:
        e = index[p['event_id']]
        e['proposed_action'] = p['action']
        e['reason'] = str(p.get('reason', ''))[:400] or e['reason']
        if p['action'] == 'shorten':
            e['target_ms'] = int(p['target_ms'])
        e['source'] = 'editorial'
    for s in sounds:
        e = index[s['event_id']]
        e['proposed_action'] = s['action']
        e['reason'] = str(s.get('reason', ''))[:400] or e['reason']
        e['source'] = 'editorial'
    removed = [(e['start'], e['end']) for e in out if e.get('source') == 'editorial' and e['kind'] == 'take_group' and not e.get('check')]
    checked = [(e['start'], e['end']) for e in out if e.get('check')]
    for e in out:
        if e['kind'] == 'take_group' and e.get('source') != 'editorial':
            covered = any(a <= e['start'] and e['end'] <= b for a, b in removed)
            e['review'] = False
            if any(a < e['end'] and e['start'] < b for a, b in checked):
                e['reason'] = 'Tariq flagged this for you to hear. ' + e['reason']
                continue
            e['reason'] = ('Covered by a retake Tariq removed.' if covered else 'Tariq read this repeat and kept it.') + ' ' + e['reason']
    out.sort(key=lambda e: (e['start'], e['end'], e['kind']))
    return out


def effective(event, decisions):
    """The action in force: Josh's or Tariq's change wins over the default proposal."""
    d = decisions.get(event['event_id'])
    return (d or {}).get('action') or event['proposed_action']


def plan(events, words, decisions, total, sr, config=CONFIG, word_actions=None):
    """Turn events plus decisions into source removals (mic samples), keeping every kept word whole.

    decisions: {event_id: {action, target_ms?, start?, end?}} from Josh or Tariq.
    word_actions: {word_id: 'remove'|'keep'} for Josh's direct transcript selections.
    Returns (removals, issues) where removals are merged half-open [start, end) sample intervals."""
    word_actions = word_actions or {}
    removed_words = set(w['id'] for w in words if word_actions.get(w['id']) == 'remove')
    sound_words = set()  # words that are really sounds (a transcribed 'ahem'): cut on their own span, not neighbour to neighbour
    for e in events:
        if effective(e, decisions) != 'remove' or not e.get('word_ids'):
            continue
        if e['kind'] == 'take_group':
            removed_words |= {w for w in e['word_ids'] if word_actions.get(w) != 'keep'}
        elif e['kind'] in ('throat_clear', 'cough'):
            sound_words |= {w for w in e['word_ids'] if word_actions.get(w) != 'keep'}
    kept = [w for w in words if w['id'] not in removed_words and w['id'] not in sound_words]
    guard, pin, pout = ms(config['word_guard_ms'], sr), ms(config['pad_in_ms'], sr), ms(config['pad_out_ms'], sr)
    issues, cuts = [], []

    # removed word runs become cuts between the kept neighbours
    i = 0
    while i < len(words):
        if words[i]['id'] not in removed_words:
            i += 1
            continue
        j = i
        while j + 1 < len(words) and words[j + 1]['id'] in removed_words:
            j += 1
        prev = words[i - 1] if i > 0 else None
        nxt = words[j + 1] if j + 1 < len(words) else None
        lo = prev['end'] + min(pout, max(guard, words[i]['start'] - prev['end'])) if prev else 0
        hi = nxt['start'] - min(pin, max(guard, nxt['start'] - words[j]['end'])) if nxt else total
        lo = max(lo, prev['end'] + guard) if prev else lo
        hi = min(hi, nxt['start'] - guard) if nxt else hi
        if prev and words[i]['start'] - prev['end'] < guard * 2:
            issues.append(dict(kind='boundary_issue', severity='review', word_id=words[i]['id'],
                               reason='The cut starts right against the previous word. Listen to the join.'))
        if nxt and nxt['start'] - words[j]['end'] < guard * 2:
            issues.append(dict(kind='boundary_issue', severity='review', word_id=nxt['id'],
                               reason='The cut ends right against the next word. Listen to the join.'))
        if hi > lo:
            cuts.append([lo, hi, 'words'])
        i = j + 1

    for e in events:
        act = effective(e, decisions)
        d = decisions.get(e['event_id']) or {}
        if e['kind'] == 'pause' and act == 'shorten':
            if any(w in removed_words for w in e.get('word_ids_before', []) + e.get('word_ids_after', [])):
                continue  # the neighbouring take is gone, so the cut above already handles this gap
            target = ms(int(d.get('target_ms', e.get('target_ms', 0))), sr)
            if e.get('outer') and not e['word_ids_before']:
                cuts.append([0, max(0, e['end'] - target), 'head'])
            elif e.get('outer'):
                cuts.append([min(total, e['start'] + target), total, 'tail'])
            elif e['end'] - e['start'] > target:
                left = e['start'] + int(target * 0.6)
                cuts.append([left, left + (e['end'] - e['start'] - target), 'pause'])
        elif e['kind'] in ('other_sound', 'cough', 'throat_clear', 'breath') and act == 'remove' and (
                not e.get('word_ids') or any(w in sound_words for w in e['word_ids'])):
            m = ms(config['sound_margin_ms'], sr)
            lo, hi = int(d.get('start', e['start'] - m)), int(d.get('end', e['end'] + m))
            cuts.append([lo, hi, 'sound'])
        elif e['kind'] == 'take_group' and act == 'remove' and d.get('start') is not None:
            cuts.append([int(d['start']), int(d['end']), 'adjusted'])

    # protect every kept word: a cut that reaches a kept word is clipped and reported
    protected = [(w['start'] - guard, w['end'] + guard, w) for w in kept]
    safe = []
    for lo, hi, why in sorted(cuts):
        pieces = [[max(0, lo), min(total, hi)]]
        for a, b, w in protected:
            nxt = []
            for x, y in pieces:
                if b <= x or a >= y:
                    nxt.append([x, y])
                    continue
                issues.append(dict(kind='boundary_issue', severity='clipped', word_id=w['id'],
                                   reason=f'A {why} cut reached the kept word {w["text"]!r}, so it was trimmed back.'))
                if a > x:
                    nxt.append([x, a])
                if b < y:
                    nxt.append([b, y])
            pieces = nxt
        safe += [p for p in pieces if p[1] > p[0]]
    merged = []
    for a, b in sorted(safe):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged, issues


def keeps_from(removals, total):
    out, p = [], 0
    for a, b in removals:
        if a > p:
            out.append([p, a])
        p = max(p, b)
    if p < total:
        out.append([p, total])
    return out


# ---------------------------------------------------------------- output map

def output_map(keeps, sr, mic_minus_camera_s, frame_ticks, inserts=()):
    """Retained mic intervals and room-tone inserts as native pieces, rounded once to the frame grid.

    mic_minus_camera_s: verified offset (mic clock = camera clock + offset).
    frame_ticks: the sequence's own ticks per frame (read from Premiere, never a rounded 30 fps constant).
    inserts: [{after_sample, media_path, media_in_ticks, media_out_ticks, reason}] room tone, separate from cuts.
    Returns pieces with camera and mic in/out ticks and output ticks, plus the rounding report."""
    tps = TICKS_PER_SECOND
    if tps * 1 % sr:
        raise ValueError('Sample rate must divide the tick rate')
    per = tps // sr
    offset = int(round(Fraction(str(mic_minus_camera_s)) * tps))
    frame_ticks = int(frame_ticks)

    def snap(mic_tick):
        cam = mic_tick - offset
        return int(round(cam / frame_ticks)) * frame_ticks + offset

    pieces, out, displaced = [], 0, []
    inserts = sorted(inserts, key=lambda x: x['after_sample'])
    k = 0
    for a, b in keeps:
        while k < len(inserts) and inserts[k]['after_sample'] <= a:
            out = _insert(pieces, inserts[k], out, frame_ticks)
            k += 1
        x, y = snap(a * per), snap(b * per)
        displaced += [abs(x - a * per), abs(y - b * per)]
        if pieces and pieces[-1]['kind'] == 'source' and x <= pieces[-1]['mic_out']:
            # cuts under one frame merge back into one piece
            prev = pieces[-1]
            if y > prev['mic_out']:
                out += y - prev['mic_out']
                prev['mic_out'] = y
                prev['camera_out'] = y - offset
                prev['out_end'] = out
            continue
        if y - x < frame_ticks / 2 or y <= x:
            continue  # slivers under half a frame are dropped
        pieces.append(dict(kind='source', mic_in=x, mic_out=y, camera_in=x - offset, camera_out=y - offset,
                           out_start=out, out_end=out + (y - x)))
        out += y - x
    while k < len(inserts):
        out = _insert(pieces, inserts[k], out, frame_ticks)
        k += 1
    bad = [p for p in pieces if p['kind'] == 'source' and p['camera_in'] < 0]
    if bad:
        raise ValueError('A kept piece starts before the camera recording. The mic covers time the camera does not.')
    # Premiere starts a clip on a whole frame of the sequence, so the mic's in point would be floored. Round the
    # mic-to-camera offset once to the nearest whole frame instead: every piece then slips by the same amount,
    # at most half a frame, and Premiere places it exactly where the plan says.
    whole = int(round(offset / frame_ticks)) * frame_ticks
    for p in pieces:
        if p['kind'] == 'source':
            p['mic_in'], p['mic_out'] = p['camera_in'] + whole, p['camera_out'] + whole
    return dict(pieces=pieces, duration_ticks=out, frame_ticks=frame_ticks, ticks_per_second=tps,
                mic_minus_camera_ticks=offset, mic_slip_ticks=offset - whole, max_rounding_ticks=max(displaced or [0]),
                rounding='Each piece edge rounded once to the nearest camera frame. Cuts under one frame merge, pieces under half a frame drop.')


def _insert(pieces, ins, out, frame_ticks):
    d = int(ins['media_out_ticks']) - int(ins['media_in_ticks'])
    d = int(round(d / frame_ticks)) * frame_ticks
    if d <= 0:
        return out
    pieces.append(dict(kind='room_tone', media_path=ins['media_path'], media_in=int(ins['media_in_ticks']),
                       media_out=int(ins['media_in_ticks']) + d, out_start=out, out_end=out + d, reason=ins.get('reason', '')))
    return out + d


def map_word(pieces, sample, sr):
    """Where a source sample lands in the output, in ticks, or None if it was removed."""
    t = sample * (TICKS_PER_SECOND // sr)
    for p in pieces:
        if p['kind'] == 'source' and p['mic_in'] <= t < p['mic_out']:
            return p['out_start'] + t - p['mic_in']
    return None


# ---------------------------------------------------------------- transcript view

def transcript(words, events, decisions, removals, sr, word_actions=None):
    """Every word and every listed event in source order, with what happens to it."""
    word_actions = word_actions or {}

    def gone(a, b):  # a word counts as cut when most of it is inside a cut (the cut keeps clear of its kept neighbours)
        return sum(max(0, min(b, y) - max(a, x)) for x, y in removals) >= 0.5 * (b - a)

    def partly(a, b):
        return any(x < b and a < y for x, y in removals)

    items = []
    for w in words:
        items.append(dict(type='word', id=w['id'], text=w['text'], start=w['start'], end=w['end'],
                          state='removed' if gone(w['start'], w['end']) else 'kept',
                          by_josh=w['id'] in word_actions))
    for e in events:
        if e['kind'] == 'take_group':
            continue  # takes show as removed words; their cards carry the reason
        act = effective(e, decisions)
        cut = sum(max(0, min(y, e['end']) - max(x, e['start'])) for x, y in removals)
        items.append(dict(type='event', id=e['event_id'], kind=e['kind'], label=e['label'], start=e['start'], end=e['end'],
                          action=act, review=e.get('review', False), duration_ms=round((e['end'] - e['start']) * 1000 / sr),
                          removed_ms=round(cut * 1000 / sr),
                          state='removed' if gone(e['start'], e['end']) else 'shortened' if partly(e['start'], e['end']) else 'kept'))
    items.sort(key=lambda x: (x['start'], 0 if x['type'] == 'event' else 1))
    return items


def queue(events, decisions, issues):
    """The cards Josh should look at: everything flagged for review, uncertain first. Never hides the transcript."""
    order = {'take_group': 0, 'other_sound': 1, 'pause': 2, 'breath': 3}
    cards = [dict(event_id=e['event_id'], kind=e['kind'], label=e['label'], start=e['start'], end=e['end'],
                  action=effective(e, decisions), reason=e.get('reason', ''), text=e.get('text', ''),
                  decided=e['event_id'] in decisions, uncertainty=e.get('uncertainty', {}))
             for e in events if e.get('review') or (e['kind'] == 'take_group' and effective(e, decisions) == 'remove')]
    cards.sort(key=lambda c: (c['decided'], order.get(c['kind'], 9), c['start']))
    return cards


def hard_errors(sync, issues, missing):
    """Problems that block approval. Review-level issues do not block, they show on cards."""
    errors = []
    if not sync or sync.get('verified') is not True:
        errors.append('Camera and mic sync is not verified.')
    errors += [f'Missing source: {m}' for m in missing]
    return errors
