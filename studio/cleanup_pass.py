"""Tariq's second editorial cleanup pass, after AutoCut's Silences then Repeat (added 13 Nov 2026).

AutoCut's base is kept as it is. This pass reads what AutoCut left, in Premiere's own word timestamps (the native
transcript, studio/premiere_transcript.py), and proposes small extra cuts for Josh to review. It never cuts by itself.
Each proposal names the exact native word ids to remove (n0001...), so it goes through the lossless clean transcript
(studio/clean_transcript.py and clean_edit.py), which places every edge on the frame grid inside the silence between
words, never inside a word, and builds a fresh copy sequence. Josh's own and published sequences are never touched.

What it looks for, on the kept words in clean order:
- Restarts: the same one to five words said twice in a row ("they'd have, they'd have", "the reason, the reason",
  "And And"). The first take is proposed for removal and the second kept, since the second leads into the phrase Josh
  meant. A pause between the takes, or a first take that ends on a comma, makes it more certain.
- Filler clusters: two or more fillers in a row at the start of a phrase ("Okay. Okay.", "okay yeah yeah okay"). All but
  the last are proposed, so the phrase still starts the way Josh restarted it.
- Apology restarts: "sorry" (once or twice) mid-flow, with the abandoned words just before it back to the last sentence
  end ("And then sorry sorry. So it's ..."). Proposed with the apology, kept phrase after it.
- Not proposed, because it's deliberate: emphasis idioms where a word comes back around a link word ("again and again",
  "over and over", "day after day", "step by step"), and a doubled intensifier said without a pause ("very very").
Cough and throat-clear candidates come from the audio: nonspeech_screen over all the kept audio (inside words too) and
Premiere's tokens with no text. They are only candidates for Josh to listen to. Level and spectrum can't tell a cough from
a hissed consonant or a sharp breath for sure, so a short list never means there was no cough.
"""
import re
from pathlib import Path

import numpy as np

FILLERS = {'okay', 'ok', 'yeah', 'yep', 'um', 'uh', 'erm', 'er', 'ah', 'alright'}   # never 'so', 'right' or 'yes', which usually carry meaning
APOLOGY = {'sorry'}
LINKS = {'and', 'after', 'by', 'to', 'upon', 'over', 'or'}
INTENSIFIERS = {'very', 'really', 'so', 'much', 'many', 'no', 'never', 'more', 'far', 'way'}
SENTENCE_END = re.compile(r'[.?!]["\')\]]*$')
PAUSE_S = 0.2          # a pause this long between two takes makes a restart more certain
PHRASE_GAP_S = 0.4     # a gap this long starts a new phrase
FRAGMENT_MAX = 6       # an abandoned fragment before "sorry" longer than this is left for Josh to judge
FRAGMENT_GAP_S = 1.0   # the abandoned start can sit across a hesitation this long before "sorry"
MAX_GAP_S = 1.2        # two takes, or two fillers, further apart than this in the clean cut are not one restart or cluster
BETWEEN_FILLERS = 2    # a restart may have up to this many fillers between its takes ("I think uh I think")
# A single "sorry" is the content, not an apology for a fumble, when it reads like these: "sorry for ...", "oh, sorry",
# "sounding sorry". Josh often acts out how people talk, so these are left alone.
SORRY_MEANT_AFTER = {'for', 'to', 'about', 'that', 'if', 'but', 'i', 'you'}
SORRY_MEANT_BEFORE = {'oh', 'like', 'as', 'say', 'said', 'saying', 'feel', 'felt', 'so', 'very', 'really', 'sounding', 'sound',
                      'sounds', "i'm", 'im', 'am', 'was', 'be', 'being', 'were', 'are', 'is', 'just'}


def norm(w):
    return re.sub(r"[^\w']+", '', str(w or '').lower().replace('’', "'"))


def kept_words(review_words):
    """The words AutoCut kept, in clean order, with their native ids (n0001 is the first native word)."""
    out = []
    for i, w in enumerate(review_words):
        if w.get('state') == 'kept':
            out.append(dict(w, id=w.get('id') or f'n{i + 1:04d}', i=i))
    return sorted(out, key=lambda w: (w.get('seq_start', 0), w['start']))


def _gap(a, b):
    """The pause between two kept words as Josh hears it in the clean cut (after AutoCut), not in the raw recording.
    Falls back to the source times only when a word has no clean time."""
    if a.get('seq_start') is not None and b.get('seq_start') is not None:
        return round(b['seq_start'] - (a['seq_start'] + (a['end'] - a['start'])), 3)
    return round(b['start'] - a['end'], 3)


def _phrase_start(kw, i):
    return i == 0 or bool(SENTENCE_END.search(kw[i - 1]['word'])) or _gap(kw[i - 1], kw[i]) >= PHRASE_GAP_S


def find(review_words):
    """Proposals for the cleanup pass. Each is dict(kind, cut_ids, cut_text, keep_text, at, clean_at, confidence, why)."""
    kw = kept_words(review_words)
    n = len(kw)
    used, protected, out = set(), set(), []
    words = [norm(w['word']) for w in kw]

    def propose(kind, cut, keep_from, confidence, why, alt=()):
        idx = list(cut)
        used.update(idx)
        keep = kw[keep_from:keep_from + 6]
        out.append(dict(kind=kind, cut_ids=[kw[j]['id'] for j in idx], cut_text=' '.join(kw[j]['word'] for j in idx),
                        alt_cut_ids=[kw[j]['id'] for j in alt], alt_cut_text=' '.join(kw[j]['word'] for j in alt),
                        keep_text=' '.join(w['word'] for w in keep), at=kw[idx[0]]['start'], clean_at=kw[idx[0]].get('seq_start'),
                        confidence=confidence, why=why,
                        context=' '.join(w['word'] for w in kw[max(0, idx[0] - 6):min(n, keep_from + 6)])))

    # Emphasis idioms are protected first: X link X ("again and again", "step by step").
    for i in range(n - 2):
        if words[i] and words[i] == words[i + 2] and words[i + 1] in LINKS and words[i] not in FILLERS:
            protected.update((i, i + 1, i + 2))

    # Filler clusters at the start of a phrase: all but the last filler go.
    i = 0
    while i < n:
        if words[i] in FILLERS and words[i] not in APOLOGY and _phrase_start(kw, i):
            j = i
            while j + 1 < n and words[j + 1] in FILLERS and words[j + 1] not in APOLOGY and _gap(kw[j], kw[j + 1]) <= MAX_GAP_S:
                j += 1
            if j > i and not any(k in protected for k in range(i, j + 1)):
                gaps = [_gap(kw[k], kw[k + 1]) for k in range(i, j)]
                propose('filler', range(i, j), j, 'high' if max(gaps) >= PAUSE_S else 'medium',
                        f'{j - i + 1} fillers in a row at the start of a phrase; the last one is kept')
                i = j + 1
                continue
        i += 1

    # Apology restarts: the abandoned words back to the last sentence end, then "sorry" once or twice.
    for i in range(n):
        if words[i] not in APOLOGY or i in used:
            continue
        j = i
        while j + 1 < n and words[j + 1] in APOLOGY:
            j += 1
        if j + 1 >= n:
            continue                                         # "sorry" at the very end is probably meant
        doubled = j > i
        if not doubled and (words[j + 1] in SORRY_MEANT_AFTER or (i > 0 and words[i - 1] in SORRY_MEANT_BEFORE)):
            continue                                         # "sorry for wasting...", "oh, sorry": the word is the content
        if not doubled and not SENTENCE_END.search(kw[j]['word']) and _gap(kw[j], kw[j + 1]) < PAUSE_S:
            continue                                         # a single "sorry" flowing straight on is part of the sentence
        s = i
        # Walk back over the abandoned start, across the hesitation before "sorry", to the last sentence end.
        while s > 0 and not SENTENCE_END.search(kw[s - 1]['word']) and _gap(kw[s - 1], kw[s]) < FRAGMENT_GAP_S and s - 1 not in used:
            s -= 1
        frag = i - s
        if frag > FRAGMENT_MAX:
            propose('apology', range(i, j + 1), j + 1, 'low', f'"sorry" mid-flow; the {frag} words before it may be an abandoned start, so Josh decides')
        else:
            propose('apology', range(s, j + 1), j + 1, 'medium' if frag else 'high',
                    'an abandoned start, then "sorry"; the phrase after it is kept' if frag else '"sorry" mid-flow; the phrase after it is kept')

    # Restarts: the same 1 to 5 words said twice in a row. The first take goes.
    i = 0
    while i < n:
        hit = None
        for m in range(5, 0, -1):
            a = list(range(i, i + m))
            for f in range(0, BETWEEN_FILLERS + 1):
                mid = list(range(i + m, i + m + f))
                b = list(range(i + m + f, i + 2 * m + f))
                if b[-1] >= n or any(k in used or k in protected for k in a + mid + b):
                    continue
                if f and not all(words[k] in FILLERS for k in mid):
                    continue
                if [words[k] for k in a] != [words[k] for k in b] or not all(words[k] for k in a):
                    continue
                if all(words[k] in FILLERS for k in a) and f:
                    continue                                 # filler, filler, filler is a cluster, handled above
                steps = [_gap(kw[x], kw[x + 1]) for x in range(a[-1], b[0])]
                if max(steps) > MAX_GAP_S:
                    continue                                 # too far apart in the clean cut to be one restart
                pause = sum(steps) + sum(kw[k]['end'] - kw[k]['start'] for k in mid)
                if m == 1 and words[i] in INTENSIFIERS and pause < PAUSE_S and not f:
                    continue                                 # "very very": emphasis said in one breath
                hit = (m, a, mid, b, pause)
                break
            if hit:
                break
        if hit:
            m, a, mid, b, pause = hit
            comma = kw[a[-1]]['word'].rstrip().endswith((',', '.', '-', '—'))
            kind = 'filler' if all(words[k] in FILLERS for k in a) else 'restart'
            propose(kind, a + mid, b[0], 'high' if (pause >= PAUSE_S or comma or mid) else 'medium',
                    f'said twice in a row ({pause:.2f}s between in the clean cut' + (', with a filler between' if mid else '')
                    + '); the second take is kept', alt=b)
            i = b[-1] + 1
        else:
            i += 1
    return sorted(out, key=lambda c: c['at'])


def nonspeech_screen(pcm, kept_spans, words, rate=16000, frame_s=0.02):
    """Screen ALL the kept audio, speech included, for stretches that look like a cough or a throat clear rather than a
    voiced word. It is a screen, not a classifier: it only says which moments to look at and listen to.

    Per 20 ms frame it measures loudness, voicing (autocorrelation peak for a 70 to 400 Hz pitch), spectral flatness and
    centroid. A cough-like stretch is loud, mostly unvoiced, broadband and starts sharply. A throat-clear-like stretch is
    loud, unvoiced or rough, and low (centroid under 1.2 kHz). Hiss sounds (s, sh, f) are excluded by their high centroid,
    and short unvoiced consonants by a minimum length. Each candidate says which native word, if any, it overlaps, since
    a cough can sit inside a word's timestamps. Returns candidates sorted by time."""
    x = np.asarray(pcm, dtype=np.float32)
    hop = int(rate * frame_s)
    nfr = len(x) // hop
    inside = np.zeros(nfr, bool)
    for a, b in kept_spans:
        inside[max(0, int(a / frame_s)):min(nfr, int(b / frame_s))] = True
    idx = np.nonzero(inside)[0]
    db = np.full(nfr, -120.0)
    voiced = np.zeros(nfr)
    flat = np.zeros(nfr)
    cent = np.zeros(nfr)
    win = np.hanning(hop * 2)
    freqs = np.fft.rfftfreq(hop * 2, 1 / rate)
    lo_lag, hi_lag = int(rate / 400), int(rate / 70)
    for k in idx:
        seg = x[k * hop:k * hop + hop * 2]
        if len(seg) < hop * 2:
            continue
        seg = seg.astype(np.float64)
        e = np.sqrt((seg ** 2).mean())
        db[k] = 20 * np.log10(e + 1e-9)
        s = seg - seg.mean()
        ac = np.correlate(s, s, 'full')[len(s) - 1:]
        voiced[k] = ac[lo_lag:hi_lag].max() / (ac[0] + 1e-12) if ac[0] > 0 else 0
        spec = np.abs(np.fft.rfft(s * win)) + 1e-9
        flat[k] = np.exp(np.mean(np.log(spec))) / np.mean(spec)
        cent[k] = (freqs * spec).sum() / spec.sum()
    speech = np.median(db[idx][db[idx] > np.percentile(db[idx], 60)]) if len(idx) else -30
    loud = inside & (db > speech - 10)
    unvoiced = voiced < 0.35
    cough_f = loud & unvoiced & (flat > 0.12) & (cent > 700) & (cent < 4500)
    throat_f = loud & (voiced < 0.5) & (cent < 1200)
    spoken = [(w['start'], w['end'], w['word']) for w in words]
    out = []
    for kind, mask, min_s in (('cough-like', cough_f, 0.08), ('throat-clear-like', throat_f, 0.15)):
        k = 0
        while k < nfr:
            if mask[k]:
                e = k
                while e + 1 < nfr and (mask[e + 1] or (e + 2 < nfr and mask[e + 2])):
                    e += 1
                at, end = k * frame_s, (e + 1) * frame_s
                if end - at >= min_s:
                    onset = float(db[k] - db[max(0, k - 2)])
                    over = [w for a, b, w in spoken if a < end and b > at]
                    out.append(dict(kind=kind, at=round(at, 2), end=round(end, 2), peak_db=round(float(db[k:e + 1].max()), 1),
                                    rel_speech_db=round(float(db[k:e + 1].max() - speech), 1), voicing=round(float(voiced[k:e + 1].mean()), 2),
                                    flatness=round(float(flat[k:e + 1].mean()), 3), centroid_hz=round(float(cent[k:e + 1].mean())),
                                    onset_db=round(onset, 1), overlaps_words=over[:4]))
                k = e + 1
            else:
                k += 1
    return sorted(out, key=lambda c: c['at'])


def cough_candidates(pcm, kept_spans, words, rate=16000, floor_db=None):
    """Stretches of sound between words, inside the kept audio, with no word on them. Josh hears each before anything is
    cut: breaths, coughs, throat clears and lip noise all look alike in level alone. Returns dict(at, end, peak_db,
    flatness) sorted loudest first. pcm is float32 mono at rate; kept_spans and words are in source seconds."""
    import numpy as np
    x = np.asarray(pcm, dtype=np.float32)
    hop = int(rate * 0.01)
    frames = x[:len(x) // hop * hop].reshape(-1, hop)
    db = 20 * np.log10(np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1)) + 1e-9)
    floor = floor_db if floor_db is not None else float(np.percentile(db, 10)) + 12
    spoken = np.zeros(len(db), bool)
    for w in words:
        spoken[max(0, int(w['start'] / 0.01) - 3):int(w['end'] / 0.01) + 3] = True
    inside = np.zeros(len(db), bool)
    for a, b in kept_spans:
        inside[int(a / 0.01):int(b / 0.01)] = True
    loud = (db > floor) & ~spoken & inside
    out, k = [], 0
    while k < len(loud):
        if loud[k]:
            e = k
            while e + 1 < len(loud) and loud[e + 1]:
                e += 1
            if e - k + 1 >= 8:                                # at least 80 ms
                seg = x[k * hop:(e + 1) * hop]
                spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) + 1e-9
                flat = float(np.exp(np.mean(np.log(spec))) / np.mean(spec))
                out.append(dict(at=round(k * 0.01, 2), end=round((e + 1) * 0.01, 2), peak_db=round(float(db[k:e + 1].max()), 1),
                                flatness=round(flat, 3)))
            k = e + 1
        else:
            k += 1
    return sorted(out, key=lambda c: -c['peak_db'])


# YAMNet (Google's AudioSet classifier, as shipped for MediaPipe), checked by hash before use. See
# models/yamnet-mediapipe/PROVENANCE.md. It scores sound classes in 0.975 s windows; its scores are candidates for Josh to
# listen to, never a decision, and it can't place an edge: edges still come from the waveform and the word times.
YAMNET_DIR = Path(__file__).resolve().parents[1] / 'models' / 'yamnet-mediapipe'
YAMNET_SHA256 = '4d8b4a53282dc83ef04e3e7dbc4fbc98082e34e44ed798e16c3a0cdd4c584faf'
YAMNET_CLASSES = ('Speech', 'Cough', 'Throat clearing', 'Breathing', 'Laughter', 'Sniff', 'Gasp')
YAMNET_WIN, YAMNET_HOP = 15600, 4000          # 0.975 s windows every 0.25 s


def yamnet_windows(span_start, span_end):
    """Sample ranges for one kept span: every window starts and ends inside the span, the last one ends exactly on the
    span's last sample, and a span shorter than a window gets one window of just its own samples (padded with zeros
    later, never with the audio around it, which may have been cut)."""
    s, e = int(span_start), int(span_end)
    if e - s <= YAMNET_WIN:
        return [(s, e)]
    out = [(k, k + YAMNET_WIN) for k in range(s, e - YAMNET_WIN + 1, YAMNET_HOP)]
    if out[-1][1] < e:
        out.append((e - YAMNET_WIN, e))                    # covers the span's last samples
    return out


def yamnet_scores(pcm, kept_spans, rate=16000, model=None, classify=None):
    """Scores for the classes above over the kept audio, speech included. Each window holds only samples from inside
    one kept span; a short span is zero-padded to the window length. Windows never cross a join, so a sound spread
    across a join is scored in two parts. Returns dict(windows=[dict(at, end, <class>: score)], kept_samples,
    covered_samples): covered equals kept when every kept sample was in at least one window. classify(samples) can
    stand in for the model in tests. Refuses a model file whose SHA-256 isn't the recorded one."""
    x = np.asarray(pcm, dtype=np.float32)
    if classify is None:
        import hashlib
        from mediapipe.tasks.python import audio as mp_audio
        from mediapipe.tasks.python.components.containers import audio_data as mp_audio_data
        from mediapipe.tasks.python.core import base_options as mp_base
        path = Path(model or YAMNET_DIR / 'yamnet.tflite')
        if hashlib.sha256(path.read_bytes()).hexdigest() != YAMNET_SHA256:
            raise ValueError('The YAMNet model file is not the one recorded in PROVENANCE.md, so it was not used.')
        opts = mp_audio.AudioClassifierOptions(base_options=mp_base.BaseOptions(model_asset_path=str(path)), max_results=-1,
                                               running_mode=mp_audio.RunningMode.AUDIO_CLIPS)
        clf = mp_audio.AudioClassifier.create_from_options(opts)

        def classify(seg):
            res = clf.classify(mp_audio_data.AudioData.create_from_array(seg, rate))[0]
            return {c.category_name: c.score for c in res.classifications[0].categories}
    else:
        clf = None
    out, kept, covered = [], 0, 0
    try:
        for a, b in kept_spans:
            s, e = int(round(a * rate)), int(round(b * rate))
            e = min(e, len(x))
            if e <= s:
                continue
            kept += e - s
            mask = np.zeros(e - s, bool)
            for w0, w1 in yamnet_windows(s, e):
                seg = x[w0:w1].astype(np.float32)
                mask[w0 - s:w1 - s] = True
                if len(seg) < YAMNET_WIN:
                    seg = np.concatenate([seg, np.zeros(YAMNET_WIN - len(seg), np.float32)])
                cats = classify(seg)
                out.append(dict(at=round(w0 / rate, 3), end=round(w1 / rate, 3), padded=w1 - w0 < YAMNET_WIN,
                                **{c: round(float(cats.get(c, 0.0)), 4) for c in YAMNET_CLASSES}))
            covered += int(mask.sum())
    finally:
        if clf is not None:
            clf.close()
    return dict(windows=out, kept_samples=kept, covered_samples=covered)


def yamnet_candidates(scores, words, threshold=0.1):
    """Stretches where Cough, Throat clearing, Laughter, Gasp or Sniff score at least `threshold` in some window, merged.
    Each says its best scores, the speech score there and the words it overlaps. Candidates only. The threshold is an
    uncalibrated model score, not a probability, and no known cough from Josh's own recordings has been used to set it."""
    scores = scores['windows'] if isinstance(scores, dict) else scores
    keys = ('Cough', 'Throat clearing', 'Laughter', 'Gasp', 'Sniff')
    hits = [w for w in scores if max(w.get(k, 0) for k in keys) >= threshold]
    out = []
    for w in sorted(hits, key=lambda w: w['at']):
        if out and w['at'] <= out[-1]['end']:
            o = out[-1]
            o['end'] = max(o['end'], w['end'])
            for k in keys + ('Speech', 'Breathing'):
                o[k] = max(o.get(k, 0), w.get(k, 0))
            o['windows'] += 1
        else:
            out.append(dict(at=w['at'], end=w['end'], windows=1, **{k: w.get(k, 0) for k in keys + ('Speech', 'Breathing')}))
    for o in out:
        o['top'] = max(keys, key=lambda k: o[k])
        o['overlaps_words'] = [x['word'] for x in words if x['start'] < o['end'] and x['end'] > o['at'] and x['word'].strip()][:6]
    return out
