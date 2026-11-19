"""Sam's YouTube chapters and description for one video, made from the final export, never the raw script.

Timing comes from the edit's own word-timed transcript (faster-whisper on the good mic, kept with the proposal), placed
on the final export through that export's cut map: a word at mic time t inside a kept piece sits at
out_start + (t - mic_in) in the export. A word that falls in a cut, or straddles one, is dropped. The result is bound to
the export's SHA-256 and duration, the cut and its map digest, the proposal and the transcript, and is rejected if the
export's length doesn't match its cut. Raw script or source-recording times are never used.

Sam picks chapter starts by word id, with the words he heard as evidence. He never types a timestamp. Python then applies
YouTube's rules (support.google.com/youtube/answer/9884579): start time and title only, the first at 00:00, at least three,
ascending, each at least 10 seconds long, the last one measured to the end of the video, all after rounding down to the
whole second shown. And Josh's rules:
- 00:00 is "Introduction".
- List items are numbered (Sign 1, Rule 1, Step 1, Mistake 1) and never give away the answer.
- Other chapters are truthful and make people curious, like "The Promotion Rule".
- No chapter just for a CTA, an ad, a download or the ending: it folds into the chapter before.
A boundary that would make a chapter too short is merged into the one before and flagged, never moved away from the words.
Too few useful chapters is flagged, never padded.

The description goes in this order: the video's own lead magnet link (Linklr) or a sales link first, then a second sales
link when the first is the lead magnet, then a brief description, then the chapters. Links come only from what's
recorded on the release. None are made here.

The prep is saved on the release with a digest of everything it was made from (export, cut, transcript, links), so a new
export, cut, transcript or link makes it stale. Josh edits, previews and approves it. Nothing goes to YouTube from here.
"""
import hashlib
import json
import math
import re
from pathlib import Path

from .store import now

TICKS = 254016000000
MIC_RATE = 16000
MIN_GAP_S = 10
LIST_ITEM = re.compile(r'^(sign|rule|step|mistake|tip|reason|habit|myth|lesson|question|way|thing)\s+(\d+)\b(.*)$', re.I)
NOT_A_CHAPTER = re.compile(r'\b(cta|call to action|download|free (guide|handbook|download)|handbook|lead magnet|sponsor|ad\b|advert|'
                           r'subscribe|outro|ending|end screen|wrap[ -]?up|thanks for watching|see you next|watch (this )?next)\b', re.I)


def digest(v):
    return hashlib.sha256(json.dumps(v, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def stamp(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'


# Transcript on the export ------------------------------------------------------------------------------------------
def place_words(words, cut_map, rate=MIC_RATE):
    """[(word, export seconds start, end)] for the words kept in the cut, plus how many were dropped and why."""
    pieces = [p for p in cut_map.get('pieces') or [] if p.get('kind') == 'source']
    out, cut_out, split, repeats = [], 0, 0, 0
    for w in words:
        a, b = w['start'] / rate * TICKS, w['end'] / rate * TICKS
        hits = [p for p in pieces if p['mic_in'] <= a < p['mic_out']]
        if not hits:
            cut_out += 1
            continue
        kept = 0
        for n, hit in enumerate(hits):
            if b > hit['mic_out'] + TICKS * 0.05:
                split += 1
                continue
            s = (hit['out_start'] + (a - hit['mic_in'])) / TICKS
            e = (hit['out_start'] + (min(b, hit['mic_out']) - hit['mic_in'])) / TICKS
            # The same recorded word used twice in the cut gets a distinct id for each place it appears.
            out.append(dict(id=w['id'] if n == 0 else f"{w['id']}#{n + 1}", source_id=w['id'], text=w['text'], start=round(s, 3), end=round(e, 3),
                            mic_start=round(w['start'] / rate, 3)))
            kept += 1
        repeats += max(0, kept - 1)
    out.sort(key=lambda x: x['start'])
    return out, dict(cut_out=cut_out, straddling=split, repeated=repeats)


def norm(word):
    return re.sub(r"[^a-z0-9']+", '', str(word).lower().replace('\u2019', "'"))


def resolve_anchor(phrase, placed, near_id=None, window=40):
    """The id of the first word of phrase, found as consecutive words in the final transcript. With near_id, only within
    window words of it. Returns (word, why): word is None unless there is exactly one match."""
    want = [norm(x) for x in str(phrase or '').split() if norm(x)]
    if len(want) < 2:
        return None, 'The anchor needs at least two words.'
    idx = {w['id']: i for i, w in enumerate(placed)}
    lo, hi = 0, len(placed)
    if near_id in idx:
        lo, hi = max(0, idx[near_id] - window), min(len(placed), idx[near_id] + window)
    got = [norm(w['text']) for w in placed]
    hits = [i for i in range(lo, hi - len(want) + 1) if got[i:i + len(want)] == want]
    if len(hits) != 1:
        return None, ('Those words are not in the final transcript there.' if not hits else 'Those words appear more than once there.')
    return placed[hits[0]], ''


NUMBER_WORDS = {1: ('one', '1', 'first'), 2: ('two', '2', 'second'), 3: ('three', '3', 'third'), 4: ('four', '4', 'fourth'),
                5: ('five', '5', 'fifth'), 6: ('six', '6', 'sixth'), 7: ('seven', '7', 'seventh'), 8: ('eight', '8', 'eighth'),
                9: ('nine', '9', 'ninth'), 10: ('ten', '10', 'tenth')}


def sentence_start(placed, i, back=45, clause=False):
    """Index of the first word of the sentence (or, with clause, the clause) that word i is in."""
    stop = r'[.?!,;:]$' if clause else r'[.?!]$'
    j = i
    while j > 0 and i - j < back and not re.search(stop, placed[j - 1]['text']):
        j -= 1
    return j


def signpost(title, placed, near, window_s=45.0):
    """For a numbered chapter ("Mistake 3"), the start of the sentence that says it: first one that says its number
    ("mistake number three"), else the nearest one that names the item ("this mistake"). Returns (index, why) or (None, '')."""
    m = LIST_ITEM.match(title or '')
    if not m:
        return None, ''
    kind, n = norm(m.group(1)), int(m.group(2))
    nums = NUMBER_WORDS.get(n, (str(n),))
    t0 = placed[near]['start']
    toks = [norm(w['text']) for w in placed]
    lo = next((i for i, w in enumerate(placed) if w['start'] >= t0 - window_s), 0)
    hi = next((i for i, w in enumerate(placed) if w['start'] > t0 + window_s), len(placed))
    numbered = []
    for i in range(lo, hi):
        seg = toks[i:i + 3]
        if seg and seg[0] == kind and ((len(seg) > 1 and seg[1] in nums) or (len(seg) > 2 and seg[1] == 'number' and seg[2] in nums)):
            numbered.append(i)
        if seg and seg[0] in nums[2:] and len(seg) > 1 and seg[1] == kind:      # "the third mistake"
            numbered.append(i)
    if numbered:
        i = min(numbered, key=lambda k: abs(placed[k]['start'] - t0))
        return sentence_start(placed, i, back=12, clause=True), f'says "{kind} {nums[0]}" there'
    named = [i for i in range(lo, hi) if toks[i] == kind and i > 0 and toks[i - 1] in ('this', 'next', 'that', 'last', 'final')]
    if named:
        i = min(named, key=lambda k: abs(placed[k]['start'] - t0))
        return sentence_start(placed, i), f'no spoken number, so the sentence that names "{toks[i - 1]} {kind}"'
    return None, ''


def lines_for_sam(placed, every=12):
    """Short lines of speech with the id of the word that starts each line, for Sam to pick chapter starts from."""
    lines, cur = [], []
    for w in placed:
        cur.append(w)
        if len(cur) >= every or re.search(r'[.?!]$', w['text']):
            lines.append(f"[{cur[0]['id']}] {' '.join(x['text'] for x in cur)}")
            cur = []
    if cur:
        lines.append(f"[{cur[0]['id']}] {' '.join(x['text'] for x in cur)}")
    return lines


# Rules ---------------------------------------------------------------------------------------------------------------
def build_chapters(picks, placed, duration):
    """Apply the rules to Sam's picks. picks: [{word_id, title, evidence}] in any order (Introduction may be left out).
    Returns dict(chapters, flags, ok). Every chapter keeps the word and time it is anchored to."""
    by_id = {w['id']: w for w in placed}
    pos = {w['id']: i for i, w in enumerate(placed)}
    flags, cands = [], []
    for pk in picks or []:
        title = re.sub(r'\s+', ' ', str(pk.get('title') or '')).strip()
        if not title:
            continue
        if pk.get('anchor'):
            w, why = resolve_anchor(pk['anchor'], placed, pk.get('word_id'))
        else:
            w, why = by_id.get(str(pk.get('word_id') or '')), 'That word is not in the final export.'
        if w is None:
            flags.append(f'"{title}" was left out. {why}')
            continue
        i = pos[w['id']]
        heard = ' '.join(x['text'] for x in placed[i:i + 12])
        c = dict(title=title, word_id=w['id'], word=w['text'], start=w['start'], evidence=heard, anchor=str(pk.get('anchor') or '')[:200],
                 audio=w.get('audio'), signpost=pk.get('signpost'), sam_anchor=pk.get('sam_anchor'))
        if w.get('unproven'):
            c['review'] = True
            flags.append(f'"{title}" could not be matched to the export\'s sound, so its time is unproven. Check it before using it.')
        cands.append(c)
    cands.sort(key=lambda c: c['start'])
    chapters = [dict(title='Introduction', word_id=placed[0]['id'] if placed else None, word=placed[0]['text'] if placed else '',
                     start=0.0, shown=0, evidence=' '.join(x['text'] for x in placed[:12]))]
    for c in cands:
        if c['start'] < 1 and c['title'].lower() == 'introduction':
            continue
        m = LIST_ITEM.match(c['title'])
        if m and m.group(3).strip(' :-–·'):
            flags.append(f'"{c["title"]}" gives away the answer, so it is shown as "{m.group(1).title()} {m.group(2)}". Check it.')
            c['title'] = f'{m.group(1).title()} {m.group(2)}'
            c['review'] = True
        if NOT_A_CHAPTER.search(c['title']):
            flags.append(f'"{c["title"]}" at {stamp(c["start"])} is a CTA, download or ending, so it folds into the chapter before.')
            continue
        c['shown'] = int(math.floor(c['start']))
        prev = chapters[-1]
        if c['shown'] - prev['shown'] < MIN_GAP_S:
            flags.append(f'"{c["title"]}" would start {c["shown"] - prev["shown"]}s after "{prev["title"]}", under YouTube\'s 10 seconds, '
                         'so it was merged into it. Look at where that chapter really starts.')
            prev['review'] = True
            continue
        chapters.append(c)
    # A numbered-list video gets the Introduction plus one chapter per item. Anything else Sam suggested stays out of the
    # draft and goes to Josh as an optional extra, so a chapter that might give the answer away is never added on its own.
    optional = []
    if sum(1 for c in chapters if LIST_ITEM.match(c['title'])) >= 3:
        keep = [c for c in chapters if c['title'] == 'Introduction' or LIST_ITEM.match(c['title'])]
        optional = [c for c in chapters if c not in keep]
        for c in optional:
            flags.append(f'Optional extra chapter "{c["title"]}" at {stamp(c["shown"])} is left out of the draft. Add it only if it is a '
                         'useful place to jump to and does not give the answer away.')
        chapters = keep
    end = int(math.floor(duration))
    while len(chapters) > 1 and end - chapters[-1]['shown'] < MIN_GAP_S:
        last = chapters.pop()
        flags.append(f'"{last["title"]}" would be the last {end - last["shown"]}s, under 10 seconds, so it was merged into the chapter before.')
        chapters[-1]['review'] = True
    ok = len(chapters) >= 3
    if not ok:
        flags.append(f'Only {len(chapters)} useful chapter{"" if len(chapters) == 1 else "s"}. YouTube needs at least 3, and Josh OS '
                     "won't pad it, so this video may not need chapters.")
    return dict(chapters=chapters, flags=flags, ok=ok, optional=optional)


def chapter_lines(chapters):
    return [f"{stamp(c['shown'])} {c['title']}" for c in chapters]


def description(links, brief, chapters):
    """Josh's order: primary link, second sales link only when the primary is the lead magnet, brief, chapters.
    links: [{role: lead_magnet|sales, url, label}]. Missing links are shown as placeholders and flagged."""
    lm = [l for l in links or [] if l.get('role') == 'lead_magnet' and str(l.get('url', '')).startswith('https://')]
    sales = [l for l in links or [] if l.get('role') == 'sales' and str(l.get('url', '')).startswith('https://')]
    parts, flags = [], []
    if lm:
        parts.append(f"{lm[0].get('label') or 'Free download'}: {lm[0]['url']}")
        if sales:
            parts.append(f"{sales[0].get('label') or 'Work with me'}: {sales[0]['url']}")
    elif sales:
        parts.append(f"{sales[0].get('label') or 'Work with me'}: {sales[0]['url']}")
    else:
        parts.append('[Linklr link for this video goes here]')
        flags.append("No tracked link is recorded for this video yet, so the first line is a placeholder. Make it in Linklr first.")
    parts.append('')
    parts.append(str(brief or '').strip() or '[Brief description]')
    if chapters:
        parts.append('')
        parts += chapter_lines(chapters)
    return '\n'.join(parts), flags


# The prep for one video ----------------------------------------------------------------------------------------------
class Chapters:
    def __init__(self, store, flow, release, agents=None, audio_check=True, hasher=None):
        self.store, self.flow, self.release, self.agents = store, flow, release, agents
        self.audio_check = audio_check
        self.hasher = hasher

    def export_sha(self, path, st):
        """The export's real SHA-256, cached against its size and modified time so it isn't read every view."""
        cache = Path(self.store.root) / 'chapters-hash-cache.json'
        try:
            c = json.loads(cache.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            c = {}
        key = f'{path}|{st.st_size}|{st.st_mtime_ns}'
        if key not in c:
            if self.hasher:
                c[key] = self.hasher(path)
            else:
                h = hashlib.sha256()
                with open(path, 'rb') as f:
                    for blk in iter(lambda: f.read(1 << 24), b''):
                        h.update(blk)
                c[key] = h.hexdigest()
            cache.write_text(json.dumps({key: c[key]}), encoding='utf-8')
        return c[key]

    def locate(self, export, mic, cut_map, word, rate=MIC_RATE, search=4.0, seconds=5.0, min_corr=0.9):
        """Where this word's sound really is in the export: its mic audio is matched against the export around the time
        the map gives, and the best match must be strong. Returns dict(start, corr, offset) or None when unproven."""
        import numpy as np
        src = word.get('source_id') or word['id']
        mic_s = word.get('mic_start')
        if mic_s is None:
            return None
        ff = str(Path(__file__).resolve().parents[1] / 'tools' / 'ffmpeg.exe')
        import subprocess

        def pcm(path, start, dur):
            pr = subprocess.run([ff, '-v', 'error', '-nostdin', '-ss', f'{max(0.0, start):.3f}', '-t', f'{dur:.3f}', '-i', str(path), '-vn',
                                 '-ac', '1', '-ar', '8000', '-f', 'f32le', '-'], capture_output=True, timeout=120,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return np.frombuffer(pr.stdout, dtype='<f4')

        def env(x):
            n = len(x) // 80 * 80
            return np.sqrt(np.mean(x[:n].reshape(-1, 80) ** 2, axis=1) + 1e-12) if n else np.zeros(1)
        m = env(pcm(mic, mic_s - 0.5, seconds))                       # the word and what follows it
        lo = max(0.0, word['start'] - search)
        e = env(pcm(export, lo, 2 * search + seconds))
        if len(m) < 100 or len(e) < len(m):
            return None
        m = m - m.mean()
        best, at = -1.0, 0
        for k in range(0, len(e) - len(m) + 1):
            y = e[k:k + len(m)] - e[k:k + len(m)].mean()
            c = float(np.dot(m, y) / ((np.linalg.norm(m) * np.linalg.norm(y)) or 1.0))
            if c > best:
                best, at = c, k
        if best < min_corr:
            return None
        start = lo + at * 0.01 + 0.5
        return dict(start=round(start, 2), corr=round(best, 3), offset=round(start - word['start'], 2))

    def audio_place(self, b, placed, picks):
        """Each picked chapter word, moved to where its sound really is in the export (strong match only). A word whose
        sound can't be found is marked unproven, and its chapter is flagged for review."""
        by = {w['id']: i for i, w in enumerate(placed)}
        out = list(placed)
        for pk in picks:
            w, _ = resolve_anchor(pk.get('anchor'), placed, pk.get('word_id')) if pk.get('anchor') else (None, '')
            if not w:
                continue
            got = self.locate(b['export']['path'], b['mic_path'], None, w)
            i = by[w['id']]
            out[i] = dict(w, start=got['start'], audio=got) if got else dict(w, audio=None, unproven=True)
        return out

    def check_audio(self, export, plan, placed, points=6, seconds=4.0):
        """Spot-check that the export's sound really is the mic where the cut map says: at several spoken moments, the
        export audio and the mic audio the map points to must line up closely."""
        import subprocess
        import numpy as np
        ff = str(Path(__file__).resolve().parents[1] / 'tools' / 'ffmpeg.exe')
        mic = plan.get('mic_path')
        if not mic or not Path(mic).is_file():
            return dict(checked=0, matched=0, skipped=True, reason='The mic file is not here, so the sound was not compared.')

        def pcm(path, start):
            p = subprocess.run([ff, '-v', 'error', '-nostdin', '-ss', f'{max(0.0, start):.3f}', '-t', f'{seconds:.3f}', '-i', str(path),
                                '-vn', '-ac', '1', '-ar', '8000', '-f', 'f32le', '-'], capture_output=True, timeout=120,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return np.frombuffer(p.stdout, dtype='<f4')

        def env(x):
            n = len(x) // 80 * 80
            return np.sqrt(np.mean(x[:n].reshape(-1, 80) ** 2, axis=1) + 1e-12) if n else np.zeros(1)
        pieces = [q for q in plan['map']['pieces'] if q.get('kind') == 'source']
        step = max(1, len(placed) // (points + 1))
        results = []
        for i in range(step, len(placed), step):
            if len(results) >= points:
                break
            w = placed[i]
            t = w['start'] * TICKS
            q = next((q for q in pieces if q['out_start'] <= t < q['out_end'] - seconds * TICKS), None)
            if q is None:
                continue
            mic_s = (q['mic_in'] + (t - q['out_start'])) / TICKS
            a, m = env(pcm(export, w['start'])), env(pcm(mic, mic_s))
            n = min(len(a), len(m))
            if n < 100:
                continue
            a, m = a[:n] - a[:n].mean(), m[:n] - m[:n].mean()
            best, lag = -1.0, 0
            for k in range(-30, 31):   # within 0.3 s
                x, y = (a[k:], m[:n - k]) if k >= 0 else (a[:n + k], m[-k:])
                d = float(np.linalg.norm(x) * np.linalg.norm(y)) or 1.0
                c = float(np.dot(x, y) / d)
                if c > best:
                    best, lag = c, k
            results.append(dict(at=round(w['start'], 2), corr=round(best, 3), lag_ms=lag * 10, ok=best >= 0.6 and abs(lag) <= 5))
        return dict(checked=len(results), matched=sum(r['ok'] for r in results), points=results)

    def basis(self, pid, audio=True):
        """What a prep is made from, bound to the exact export. Raises ValueError when timings can't be trusted."""
        ce = getattr(self.flow, 'clean_edit', None)
        if ce is not None and (self.flow.folder(pid) / 'clean-edit.json').is_file():
            return self._clean_basis(pid, ce)
        p = self.store.get(pid)
        r = p.get('release') or {}
        appr = r.get('export_approval') or {}
        fin = next((f for f in self.flow._all(pid, 'edit_final') if f['payload'].get('state') == 'verified'), None)
        if not fin:
            raise ValueError('There is no checked final export yet.')
        vis = self.flow.get(pid, fin['parent'])
        cut = self.flow.get(pid, vis['parent'])
        prop = self.flow.get(pid, cut['parent'])
        out = Path(fin['payload']['output'])
        approved = bool(appr.get('path')) and Path(appr['path']) == out
        if not out.is_file():
            raise ValueError(f'{out.name} is missing.')
        st = out.stat()
        if approved and (st.st_size != appr.get('size') or str(st.st_mtime_ns) != str(appr.get('mtime_ns'))):
            raise ValueError('The approved export changed after it was approved, so chapters were not made.')
        from .frameio_review import FrameioReview
        info = FrameioReview._probe(out)
        sha = self.export_sha(out, st)
        if approved and appr.get('sha256') and appr['sha256'] != sha:
            raise ValueError('The approved export no longer has the approved contents, so chapters were not made.')
        cmap = cut['payload']['plan']['map']
        cut_len = cmap['duration_ticks'] / TICKS
        if abs(info['duration'] - cut_len) > 0.5:
            raise ValueError(f"The export is {info['duration']:.1f}s but its cut is {cut_len:.1f}s, so the transcript can't be placed on it.")
        words = prop['payload'].get('words') or []
        placed, dropped = place_words(words, cmap)
        if not placed or placed[-1]['end'] > info['duration'] + 0.2:
            raise ValueError("The transcript doesn't fit inside the export, so chapters were not made.")
        if cut['payload'].get('state') != 'verified':
            raise ValueError("The export's cut was never checked against Premiere, so its map can't be trusted for chapters.")
        check = self.check_audio(out, cut['payload']['plan'], placed) if (self.audio_check and audio) else dict(checked=0, matched=0, skipped=True)
        # The stored map is only a starting point: where the export's sound doesn't match it, each chapter start is placed
        # from the export's own audio instead (see locate), and never taken from the map alone.
        b_mic = cut['payload']['plan'].get('mic_path')
        links = [l for l in (r.get('links') or []) if isinstance(l, dict)]
        b = dict(export=dict(path=str(out), name=out.name, size=st.st_size, mtime_ns=str(st.st_mtime_ns), sha256=sha,
                             duration=info['duration'], fps=float(info['fps'])),
                 export_approved=approved, final_id=fin['id'], cut_id=cut['id'], map_digest=digest(cmap), audio_check=check, mic_path=b_mic,
                 proposal_id=prop['id'], proposal_revision=prop['payload'].get('revision'), asr=prop['payload'].get('asr', {}).get('model'),
                 transcript_digest=digest([(w['id'], w['start'], w['end'], w['text']) for w in words]),
                 links_digest=digest(links), dropped=dropped, words=len(placed))
        b['digest'] = digest({k: v for k, v in b.items() if k not in ('dropped', 'words', 'audio_check', 'mic_path')})
        return b, placed, links

    def _clean_basis(self, pid, ce):
        """On the AutoCut clean cut, chapters come from the final transcript Josh approved (studio/clean_edit_more.py):
        Premiere's own words read off the finished sequence, already on its timeline, hand edits included. Still bound
        to the exact export Josh approved, and a newer final transcript or export makes the prep stale."""
        try:
            h = ce.sam_handoff(pid)
        except Exception as e:  # noqa: BLE001 - the reason is the one to show
            raise ValueError(str(e)) from None
        r = (self.store.get(pid).get('release') or {})
        appr = r.get('export_approval') or {}
        if not appr.get('path'):
            raise ValueError('Josh has to approve the final export first, so the chapters know which file they belong to.')
        out = Path(appr['path'])
        if not out.is_file():
            raise ValueError(f'{out.name} is missing.')
        st = out.stat()
        if st.st_size != appr.get('size') or str(st.st_mtime_ns) != str(appr.get('mtime_ns')):
            raise ValueError('The approved export changed after it was approved, so chapters were not made.')
        from .frameio_review import FrameioReview
        info = FrameioReview._probe(out)
        sha = self.export_sha(out, st)
        if appr.get('sha256') and appr['sha256'] != sha:
            raise ValueError('The approved export no longer has the approved contents, so chapters were not made.')
        seen, placed = {}, []
        for w in h['words']:
            n = seen[w['id']] = seen.get(w['id'], 0) + 1
            placed.append(dict(id=w['id'] if n == 1 else f"{w['id']}#{n}", source_id=w['id'], text=w['text'], start=round(w['start'], 3),
                               end=round(w['end'], 3), mic_start=round(w.get('source_start', 0), 3)))
        placed.sort(key=lambda x: x['start'])
        if not placed or placed[-1]['end'] > info['duration'] + 0.2:
            raise ValueError("The final transcript doesn't fit inside the approved export, so chapters were not made. "
                             f"Check the export is of {h['sequence']}.")
        links = [l for l in (r.get('links') or []) if isinstance(l, dict)]
        b = dict(export=dict(path=str(out), name=out.name, size=st.st_size, mtime_ns=str(st.st_mtime_ns), sha256=sha,
                             duration=info['duration'], fps=float(info['fps'])),
                 export_approved=True, source='clean-final-transcript', sequence=h['sequence'], final_digest=h['digest'],
                 final_file=h['file'], provenance=h['provenance'], asr='premiere-native',
                 transcript_digest=digest([(w['id'], w['start'], w['end'], w['text']) for w in placed]),
                 links_digest=digest(links), dropped={}, words=len(placed), audio_check=dict(skipped=True,
                 why='The words were read off the finished sequence itself, so there is no older map to check them against.'))
        b['digest'] = digest({k: v for k, v in b.items() if k not in ('dropped', 'words', 'audio_check', 'provenance')})
        return b, placed, links

    def prepare(self, pid, run=None, preview=False, *, expected_revision=None):
        """Sam picks the chapter starts and writes the brief, then Josh OS checks every rule. Without Josh's approval of
        the final export this is only a labelled preview."""
        from .store import Conflict
        p = self.store.get(pid)
        if expected_revision is not None and (type(expected_revision) is not int or p['revision'] != expected_revision):
            raise Conflict('The video changed. Read it again before preparing chapters.')
        b, placed, links = self.basis(pid)
        if not b['export_approved'] and not preview:
            raise ValueError('Josh has to approve the final export first. A preview can still be made.')
        title = next((t['text'] for t in p.get('titles') or [] if t['id'] == p.get('chosen_title')), p['name'])
        schema = {'type': 'object', 'required': ['chapters', 'brief'], 'properties': {
            'chapters': {'type': 'array', 'items': {'type': 'object', 'required': ['word_id', 'anchor', 'title'], 'properties': {
                'word_id': {'type': 'string', 'description': 'The id in brackets of the line the chapter starts in.'},
                'anchor': {'type': 'string', 'description': 'The first three to eight words of the chapter, copied exactly from that line '
                                                            'or the next one. The chapter starts at the first of these words.'},
                'title': {'type': 'string'}}}},
            'brief': {'type': 'string', 'description': 'Two or three tight sentences for the description, in Josh\'s voice, saying only '
                                                       'what the video covers. No broad claims the video does not back up.'}}}
        prompt = '\n'.join([
            f'Make YouTube chapters for "{title}" from the final edit below. Each line starts with the id of its first word.',
            'Pick where each useful section starts: a spoken signpost or a clear new section. Useful navigation, not every paragraph. '
            'Give the line id and the exact first words of the chapter, copied from the transcript. It may start in the middle of a line.',
            'Do not include the Introduction, it is added at 00:00. Do not give chapters to a CTA, a download or handbook mention, an ad or the ending.',
            'For a numbered list, title each item only "Sign 1", "Rule 1", "Step 1" (match the word the video uses) and never say what it is.',
            'When the video is a numbered list, prefer just the Introduction plus one chapter per item. Add another chapter only when it '
            'is a genuinely useful place to jump to, and never one whose title gives away a mistake, habit or answer.',
            'Other titles are truthful and make people curious without revealing the answer, like "The Promotion Rule" or '
            '"What Executives Want to See".',
            'Never write a timestamp. Also write a brief two or three sentence description.',
            'Josh\'s writing rules apply: no em dashes, no colons splitting sentences, never the words quiet, land, honest.', '',
            *lines_for_sam(placed)])
        team = self.agents.team.all()
        sam = next(a for a in team['agents'] if a['id'] == 'sam')
        _, ans = (run or self.agents.run)(self.agents.desk_system_prompt(sam, []), prompt, schema, (), full=True, tools='Read', timeout=300)
        ans = ans if isinstance(ans, dict) else {}
        picks = ans.get('chapters') or []
        # Numbered chapters start at the sentence that signposts them, not wherever Sam put the bridge before it.
        for pk in picks:
            w, _ = resolve_anchor(pk.get('anchor'), placed, pk.get('word_id')) if pk.get('anchor') else (None, '')
            if not w:
                continue
            i0 = next(i for i, x in enumerate(placed) if x['id'] == w['id'])
            j, why = signpost(pk.get('title'), placed, i0)
            if j is not None and placed[j]['id'] != w['id']:
                pk['sam_anchor'], pk['word_id'] = pk.get('anchor'), placed[j]['id']
                pk['anchor'] = ' '.join(x['text'] for x in placed[j:j + 4])
                pk['signpost'] = why
            elif j is not None:
                pk['signpost'] = why
        if self.audio_check and b.get('mic_path') and Path(b['mic_path']).is_file():
            placed = self.audio_place(b, placed, picks)
        built = build_chapters(picks, placed, b['export']['duration'])
        text, dflags = description(links, ans.get('brief'), built['chapters'] if built['ok'] else [])
        prep = dict(at=now(), by='sam', preview=not b['export_approved'], basis=b, chapters=built['chapters'], ok=built['ok'],
                    optional=built.get('optional') or [],
                    flags=built['flags'] + dflags, brief=str(ans.get('brief') or '')[:1500], description=text, approved=None)
        latest_basis, _, _ = self.basis(pid, audio=False)
        if latest_basis['digest'] != b['digest']:
            raise Conflict('The export, transcript or links changed while chapters were being prepared. Nothing was replaced.')
        self.release._save_release(pid, dict(chapters=prep), expected_revision=p['revision'])
        return prep

    def state(self, pid):
        """The saved prep, and whether it still matches the export, cut, transcript and links."""
        r = self.store.get(pid).get('release') or {}
        prep = r.get('chapters')
        if not prep:
            return dict(state='none')
        if prep.get('invalidated'):
            return dict(state='stale', reason=prep['invalidated'].get('reason') or 'These chapters were made for an earlier export.', prep=prep)
        try:
            b, _, _ = self.basis(pid, audio=False)   # a view only compares digests; the sound is checked when chapters are made
        except ValueError as e:
            return dict(state='stale', reason=str(e), prep=prep)
        if b['digest'] != prep['basis']['digest']:
            return dict(state='stale', reason='The export, cut, transcript or links changed since these chapters were made.', prep=prep)
        if prep.get('preview') and b['export_approved']:
            return dict(state='stale', reason='Made as a preview before the export was approved. Make them again from the approved export.', prep=prep)
        return dict(state='preview' if prep.get('preview') else ('approved' if prep.get('approved') else 'review'), prep=prep)
