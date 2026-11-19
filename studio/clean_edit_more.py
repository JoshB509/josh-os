"""Previews and versioned transcripts for the clean edit (added onto studio.clean_edit.CleanEdit).

- preview: for one word, a clip of the source mic around it (stretches the clean cut leaves out shaded) and a clip of
  the clean cut around where it sits, both 16 kHz WAV from the mic, with waveform peaks for the page.
- clean_transcript: the approved cut's words on the clean timeline, with source ids, source times and provenance.
- final_transcript: the words of a sequence as it is in Premiere now, compared piece by piece with the approved cut.
  Josh's hand edits are reported and used, never undone. Versions bind words and provenance with a SHA-256 digest.
"""
import hashlib
import json
import wave
from pathlib import Path

import numpy as np

from . import clean_transcript as ct
from .clean_edit import CleanEdit, CleanEditError, now

T = ct.T


def _peaks(x, n=150):
    if len(x) == 0:
        return []
    k = max(1, len(x) // n)
    return [round(float(np.abs(x[i:i + k]).max()), 4) for i in range(0, min(len(x), k * n), k)]


def clean_to_source(keep, c0, c1):
    """Source tick ranges that make up clean time [c0, c1) (ticks), in order."""
    out, at = [], 0
    for a, b in keep:
        n = b - a
        lo, hi = max(c0, at), min(c1, at + n)
        if hi > lo:
            out.append((a + lo - at, a + hi - at))
        at += n
    return out


def source_to_clean(keep, t):
    """Clean time of source tick t. A removed tick maps to the join where it was taken out."""
    at = 0
    for a, b in keep:
        if t < a:
            return at
        if t < b:
            return at + t - a
        at += b - a
    return at


def preview(self, pid, word_id, context_s=2.0):
    st = self._load(pid)
    m = self.model(pid, st)
    pcm = self._pcm(st)
    w = next((x for x in m.words if x['id'] == word_id), None)
    if w is None:
        raise CleanEditError('Unknown word.')
    context_s = min(10.0, max(0.5, float(context_s)))
    keep, _ = m.keeps(pcm)
    ctx, tps = int(context_s * T), ct.TPS
    s0, s1 = max(0, w['start'] - ctx), w['end'] + ctx
    src = pcm[s0 // tps:s1 // tps]
    cuts = [((max(a, s0) - s0) / T, (min(b, s1) - s0) / T) for a, b in ct._subtract([[s0, s1]], keep)]
    c = source_to_clean(keep, (w['start'] + w['end']) // 2)
    parts = [pcm[a // tps:b // tps] for a, b in clean_to_source(keep, max(0, c - ctx), c + ctx)]
    clean = np.concatenate(parts) if parts else np.zeros(1, dtype='<f4')
    folder = Path(self.folder_of(pid)) / 'clean-previews'
    folder.mkdir(exist_ok=True)
    key = hashlib.sha1(json.dumps([word_id, context_s, st['edits'], st['evidence']]).encode()).hexdigest()[:16]
    names = {}
    for kind, x in (('source', src), ('clean', clean)):
        name = f'{key}-{kind}.wav'
        f = folder / name
        if not f.is_file():
            with wave.open(str(f), 'wb') as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(ct.SR)
                out.writeframes((np.clip(x, -1, 1) * 32767).astype('<i2').tobytes())
        names[kind] = name
    return dict(word=word_id, context_s=context_s, source=names['source'], clean=names['clean'], source_peaks=_peaks(src),
                clean_peaks=_peaks(clean), source_cuts=cuts, source_len=(s1 - s0) / T)


def preview_file(self, pid, name):
    if not (name.endswith('-source.wav') or name.endswith('-clean.wav')) or any(c in name for c in '/\\') or '..' in name:
        raise CleanEditError('Unknown preview.')
    f = Path(self.folder_of(pid)) / 'clean-previews' / name
    if not f.is_file():
        raise CleanEditError('That preview is gone. Make it again.')
    return f


def _transcript_digest(body):
    # Creation time is non-semantic; all words and provenance are bound together.
    return hashlib.sha256(json.dumps({k:v for k,v in body.items() if k not in ('digest','made')}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _write_version(self, pid, stem, body):
    folder = Path(self.folder_of(pid)) / 'transcripts'
    folder.mkdir(exist_ok=True)
    body = dict(body, project_id=pid, schema=2)
    digest = _transcript_digest(body)
    f = folder / f'{stem}-{digest}.json'
    if f.is_file():
        existing = json.loads(f.read_text(encoding='utf-8'))
        if existing.get('digest') != digest or _transcript_digest(existing) != digest:
            raise CleanEditError('Existing transcript content changed. It will not be overwritten.')
    else:
        f.write_text(json.dumps(dict(body, digest=digest), indent=1), encoding='utf-8')
    return f, digest


def _validated_final(self, pid, entry, st):
    from dajoshi import edit_plan as ep
    if not st.get('approved') or st['approved'].get('edits_digest') != ep.digest(st['edits']):
        raise CleanEditError('The clean cut approval no longer matches these edits.')
    path = Path(entry['file']).resolve()
    folder = (Path(self.folder_of(pid)) / 'transcripts').resolve()
    if path.parent != folder:
        raise CleanEditError('Final transcript is outside this project.')
    body = json.loads(path.read_text(encoding='utf-8'))
    if body.get('schema') != 2:
        raise CleanEditError('Make a new final transcript with verified provenance before approving or handing it over.')
    digest = _transcript_digest(body)
    if body.get('digest') != digest or entry.get('digest') != digest:
        raise CleanEditError('Final transcript content changed. Make and review a fresh version.')
    if body.get('project_id') != pid or body.get('kind') != 'final' or body.get('sequence') != entry.get('sequence'):
        raise CleanEditError('Final transcript does not match this project and sequence.')
    if body.get('approved') != st.get('approved') or body.get('evidence') != st.get('evidence') or body.get('source_revision') != self.revision(st)['digest']:
        raise CleanEditError('Final transcript sources or approved cut changed. Make a fresh version.')
    if body.get('source_project') != st.get('prproj') or body.get('source_mic') != st.get('mic'):
        raise CleanEditError('Final transcript source identity changed.')
    return body


def verified_clean_map(self, pid, st, sequence):
    """Read-only, tick-exact comparison before handing approved timings downstream."""
    from .premiere_transcript import media_key
    from dajoshi import edit_plan as ep
    if self.driver_factory is None:
        raise CleanEditError('Premiere readback is unavailable. The clean cut cannot be verified.')
    m, pcm = self.model(pid, st), self._pcm(st)
    pm, _ = m.plan_map(pcm)
    pm['frame_ticks'] = int(pm['frame_ticks'])
    project, info = self.driver_factory().premiere_ops([
        dict(op='project'), dict(op='info', seq=sequence)])
    if media_key(project.get('project')) != media_key(st['prproj']):
        raise CleanEditError('The open Premiere project does not match this clean cut.')
    if info.get('name') != sequence or int(info.get('frame_ticks', 0)) != int(st['frame']):
        raise CleanEditError('The Premiere sequence or frame rate changed. Review the cut again.')
    tracks = {}
    for kind, field in (('a', 'mic_in'), ('v', 'camera_in')):
        items = sorted([x for x in info['items'] if x['kind'] == kind and x['track'] == 0],
                       key=lambda x: int(x['start']))
        actual = [(int(x['start']), int(x['end']), int(x['inp'])) for x in items]
        expected = [(x['out_start'], x['out_end'], x[field]) for x in pm['pieces']]
        if actual != expected:
            raise CleanEditError('Premiere timing changed after the reviewed cut. Review those edits before creating transcript or animations.')
        if kind == 'a' and any(media_key(x.get('path')) != media_key(st['mic']) for x in items):
            raise CleanEditError('The Premiere microphone source changed. Review the cut again.')
        tracks[kind] = actual
    if any(x['kind'] == 'a' and x['track'] != 0 for x in info['items']):
        raise CleanEditError('Additional audio is present in the clean cut. Review it before creating transcript or animations.')
    identity = dict(project=media_key(st['prproj']), mic=media_key(st['mic']), sequence=sequence,
                    frame=int(st['frame']), tracks=tracks)
    return m, pcm, pm, ep.digest(identity)


def prepare_clean_transcript(self, pid, st):
    """Prepare a version without committing an approval or changing Premiere."""
    from dajoshi import edit_plan as ep
    if not st.get('approved') or st['approved'].get('edits_digest') != ep.digest(st['edits']):
        raise CleanEditError('The clean transcript needs the approval of these exact edits.')
    revision = self.revision(st)['digest']
    m, pcm, pm, timeline_digest = self.verified_clean_map(pid, st, st['approved']['sequence'])
    if st['approved'].get('timeline_digest') != timeline_digest or st['approved'].get('source_revision') != revision:
        raise CleanEditError('The approved cut or its sources changed. Review and approve the current cut again.')
    words = []
    for w in m.words:
        for piece in pm['pieces']:
            lo, hi = max(w['start'], piece['mic_in']), min(w['end'], piece['mic_out'])
            if hi > lo:
                offset = piece['out_start'] - piece['mic_in']
                words.append(dict(id=w['id'], text=w['text'], start=(lo + offset) / T, end=(hi + offset) / T,
                                  source_start=lo / T, source_end=hi / T,
                                  partial=lo > w['start'] or hi < w['end']))
    words.sort(key=lambda w: w['start'])
    body = dict(source_revision=revision, source_project=st['prproj'], source_mic=st['mic'], kind='clean',
                sequence=st['approved']['sequence'], approved=st['approved'], evidence=st['evidence'],
                edits=st['edits'], timeline_digest=timeline_digest, map=pm, made=now(), words=words)
    f, digest = self._write_version(pid, 'clean', body)
    if self.revision(st)['digest'] != revision:
        raise CleanEditError('Sources changed while making the transcript. Review them again.')
    return dict(kind='clean', file=str(f), digest=digest, sequence=body['sequence'], at=now()), len(words)


def clean_transcript(self, pid):
    with self.lock:
        st = self._load(pid)
        entry, count = self.prepare_clean_transcript(pid, st)
        if not any(t['digest'] == entry['digest'] for t in st.get('transcripts', [])):
            st.setdefault('transcripts', []).append(entry)
        st['clean_transcript'] = entry
        self._save(pid, st)
    return dict(file=entry['file'], digest=entry['digest'], words=count, sequence=entry['sequence'])


for _f in (verified_clean_map, prepare_clean_transcript):
    setattr(CleanEdit, _f.__name__, _f)


def final_transcript(self, pid, sequence):
    st = self._load(pid)
    if not st['approved']:
        raise CleanEditError('The final transcript follows the approved clean cut.')
    source_revision = self.revision(st)['digest']
    from . import autocut_pass as ap
    rb = ap.readback(self.driver_factory(), sequence)
    a1 = sorted([i for i in rb['info']['items'] if i['kind'] == 'a' and i['track'] == 0], key=lambda i: int(i['start']))
    pieces = [dict(seq_start=int(x['start']), mic_in=int(x['inp']), mic_out=int(x['inp']) + int(x['end']) - int(x['start'])) for x in a1]
    m = self.model(pid, st)
    pcm = self._pcm(st)
    want, _ = m.keeps(pcm)
    got = [[p['mic_in'], p['mic_out']] for p in pieces]
    changes = dict(same=got == want, added=[[a / T, b / T] for a, b in ct._subtract(got, want)],
                   taken_out=[[a / T, b / T] for a, b in ct._subtract(want, got)], gaps=rb['gaps'], overlaps=rb['overlaps'], problems=rb['problems'])
    words = []
    for w in m.words:
        for p in pieces:
            lo, hi = max(w['start'], p['mic_in']), min(w['end'], p['mic_out'])
            if hi > lo:
                off = p['seq_start'] - p['mic_in']
                words.append(dict(id=w['id'], text=w['text'], start=(lo + off) / T, end=(hi + off) / T,
                                  partial=lo > w['start'] or hi < w['end'], source_start=w['start'] / T))
    words.sort(key=lambda x: x['start'])
    approved_ids = {r['id'] for r in m.states(pcm) if r['state'] != 'removed'}
    changed = sorted({x['id'] for x in words} ^ approved_ids)
    body = dict(source_revision=source_revision, source_project=st['prproj'], source_mic=st['mic'], kind='final', sequence=sequence, approved=st['approved'], compared_with_approved=changes, words_changed_by_hand=changed,
                evidence=st['evidence'], made=now(), words=words)
    f, digest = self._write_version(pid, 'final', body)
    with self.lock:
        st = self._load(pid)
        if body['source_revision'] != self.revision(st)['digest'] or body['approved'] != st.get('approved') or body['source_project'] != st.get('prproj') or body['source_mic'] != st.get('mic'):
            raise CleanEditError('Sources changed while making the transcript. Make it again.')
        st.setdefault('transcripts', []).append(dict(kind='final', file=str(f), digest=digest, sequence=sequence, at=now(), same=changes['same']))
        self._save(pid, st)
    return dict(file=str(f), digest=digest, words=len(words), sequence=sequence, same_as_approved=changes['same'], changes=changes,
                words_changed_by_hand=changed)


def latest_final(self, pid):
    """For Sam's chapters and upload: the newest final transcript, with where it came from."""
    st = self._load(pid)
    finals = [t for t in st.get('transcripts', []) if t['kind'] == 'final']
    if not finals:
        raise CleanEditError('There is no final transcript yet. Make one from the finished sequence.')
    return finals[-1]


for _f in (preview, preview_file, _write_version, clean_transcript, final_transcript, latest_final):
    setattr(CleanEdit, _f.__name__, _f)


def animation_inputs(self, pid):
    """What the animation planner needs, from the approved clean cut and Premiere's own words: words in mic samples,
    the stretches of mic left out (samples), and the approved cut's map pieces (ticks). Refused unless the cut is
    approved and the edits haven't changed since."""
    from dajoshi import edit_plan as ep
    st = self._load(pid)
    if not st['approved']:
        raise CleanEditError('Animations wait for the approved clean cut.')
    if st['approved'].get('edits_digest') != ep.digest(st['edits']):
        raise CleanEditError('The words changed after the clean cut was approved. Approve it again.')
    m, pcm, pm, timeline_digest = self.verified_clean_map(pid, st, st['approved']['sequence'])
    if st['approved'].get('timeline_digest') != timeline_digest or st['approved'].get('source_revision') != self.revision(st)['digest']:
        raise CleanEditError('The approved cut or its sources changed. Review and approve the current cut again.')
    entry = st.get('clean_transcript')
    if not entry:
        raise CleanEditError('The approved clean transcript is missing. Make it before planning animations.')
    path = Path(entry['file']).resolve()
    if path.parent != (Path(self.folder_of(pid)) / 'transcripts').resolve():
        raise CleanEditError('The clean transcript is outside this project.')
    body = json.loads(path.read_text(encoding='utf-8'))
    if body.get('digest') != entry['digest'] or _transcript_digest(body) != entry['digest'] or body.get('approved') != st['approved'] or body.get('map') != pm or body.get('source_revision') != self.revision(st)['digest']:
        raise CleanEditError('The approved clean transcript changed and is stale. Make and review it again.')
    keep = [[p['mic_in'], p['mic_out']] for p in pm['pieces']]
    tps = ct.TPS
    end = max([w['end'] for w in m.words] + [m.raw[1]])
    removals = [[a // tps, -(-b // tps)] for a, b in ct._subtract([[0, end]], keep)]
    words = [dict(id=w['id'], text=w['text'], start=w['start'] // tps, end=-(-w['end'] // tps)) for w in m.words]
    return dict(sequence=st['approved']['sequence'], words=words, removals=removals, map=dict(pm, frame_ticks=int(pm['frame_ticks'])),
                provenance=dict(evidence=st['evidence'], approved=st['approved'], words='premiere-native',
                              clean_transcript=entry['digest'], timeline_digest=timeline_digest))


setattr(CleanEdit, 'animation_inputs', animation_inputs)


RELEASE_STATES = ('scheduled', 'published')


def approve_final(self, pid, digest, by, from_josh):
    """Josh approves one exact final transcript version for chapters and upload."""
    if not from_josh:
        raise CleanEditError('Only Josh can approve the final transcript, from the video page.')
    with self.lock:
        st = self._load(pid)
        fin = next((t for t in reversed(st.get('transcripts', [])) if t['kind'] == 'final' and t['digest'] == str(digest)), None)
        if fin is None:
            raise CleanEditError('There is no final transcript with that version.')
        newest = [t for t in st['transcripts'] if t['kind'] == 'final'][-1]
        if newest['digest'] != fin['digest']:
            raise CleanEditError('A newer final transcript exists. Approve the newest one.')
        _validated_final(self, pid, fin, st)
        st['final_approved'] = dict(digest=fin['digest'], file=fin['file'], sequence=fin['sequence'], by=str(by)[:40], at=now())
        self._save(pid, st)
    return st['final_approved']


def sam_handoff(self, pid):
    """For Sam's chapters, description and upload: the approved final transcript with where every part came from.
    Refused until Josh approves a final transcript, and again if a newer one has been made since."""
    with self.lock:
        st = self._load(pid)
        fa = st.get('final_approved')
        if not fa:
            raise CleanEditError('Josh hasn\'t approved a final transcript yet, so Sam waits.')
        newest = [t for t in st.get('transcripts', []) if t['kind'] == 'final'][-1]
        if newest['digest'] != fa['digest']:
            raise CleanEditError('A newer final transcript was made after Josh approved one. Josh approves the newest first.')
        body = _validated_final(self, pid, fa, st)
        if body.get('digest') != fa['digest']:
            raise CleanEditError('The approved final transcript file has changed on disk, so it is not handed over.')
        text = ' '.join(w['text'] for w in body['words'])
        return dict(sequence=fa['sequence'], digest=fa['digest'], file=fa['file'], words=body['words'], text=text,
                    provenance=dict(autocut_evidence=st['evidence'], clean_cut=st['approved'], final=fa,
                                    hand_edits=body.get('compared_with_approved'), words_changed_by_hand=body.get('words_changed_by_hand')),
                    release=st.get('release'))


def record_release(self, pid, state, video_id, by):
    """Records that the video was scheduled or published (done elsewhere, through the publishing flow). Nothing here
    talks to YouTube. Only with an approved final transcript, so chapters always match what went out."""
    if state not in RELEASE_STATES:
        raise CleanEditError('A release is scheduled or published.')
    if not str(video_id or '').strip():
        raise CleanEditError('Say which YouTube video it is.')
    with self.lock:
        st = self._load(pid)
        if not st.get('final_approved'):
            raise CleanEditError('There is no approved final transcript, so the release can\'t be recorded against one.')
        st['release'] = dict(state=state, video_id=str(video_id).strip()[:40], by=str(by)[:40], at=now(), final=st['final_approved']['digest'])
        st.setdefault('release_history', []).append(st['release'])
        self._save(pid, st)
    return st['release']


for _f in (approve_final, sam_handoff, record_release):
    setattr(CleanEdit, _f.__name__, _f)


_AUDIO_HASH = {}


def _sha_file(path):
    """sha256 of a file, cached by path, size and modified time (the mic audio is large and never edited in place)."""
    f = Path(path)
    st = f.stat()
    key = (str(f), st.st_size, st.st_mtime_ns)
    if key not in _AUDIO_HASH:
        h = hashlib.sha256()
        with open(f, 'rb') as fh:
            for chunk in iter(lambda: fh.read(1 << 22), b''):
                h.update(chunk)
        _AUDIO_HASH[key] = h.hexdigest()
    return _AUDIO_HASH[key]


def revision(self, st):
    """What a cleanup pass was worked out from: the AutoCut evidence (Premiere's words, both read-backs, the raw piece),
    the mic audio, the frame and Josh's edits. Any change gives a new digest, and proposals from an older one can't be
    accepted."""
    ev = Path(st['evidence'])
    files = ['premiere-native-words-mic.json', 'after-silences-readback.json', 'after-repeat-readback.json']
    raw = ev / 'autocut-pass.json'
    files += [raw.name] if raw.is_file() else sorted(x.name for x in ev.glob('autocut-raw-*-build.json'))
    body = dict(evidence={n: _sha_file(ev / n) for n in files}, audio=_sha_file(st['pcm']), frame=int(st['frame']),
                edits=hashlib.sha256(json.dumps(st['edits'], sort_keys=True).encode()).hexdigest())
    body['digest'] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return body


def cleanup(self, pid, reuse=False):
    """Tariq's second cleanup pass on this video's clean transcript as it stands now (AutoCut plus Josh's edits).
    Proposes only. Bound to the revision it was worked out from: if anything changes while it runs, nothing is saved,
    and each proposal's key includes that revision, so no decision carries over to changed audio or timing."""
    from . import cleanup_review as cr
    st = self._load(pid)
    rev = self.revision(st)
    existing = st.get('cleanup')
    if reuse and existing and (existing.get('revision') or {}).get('digest') == rev['digest']:
        return existing  # Keep Josh's decisions and the existing review pack on resume.
    full = cr.run_pass(st['evidence'], st['pcm'], st['frame'], st['edits'], say=lambda m: None)
    props = []
    for p in full['proposals']:
        p['key'] = hashlib.sha256(json.dumps([rev['digest'], p.get('kind'), p.get('cut_ids'), p.get('use_ids'), p.get('join'),
                                              p.get('status')], sort_keys=True).encode()).hexdigest()[:20]
        p['decision'] = None
        props.append(p)
    with self.lock:
        st = self._load(pid)
        if self.revision(st)['digest'] != rev['digest']:
            raise CleanEditError('The edit changed while the cleanup pass ran, so its proposals were not saved. Run it again.')
        st['cleanup'] = dict(at=full['at'], folder=full['folder'], revision=rev, proposals=props, nonspeech=full['nonspeech'],
                             listened=False)
        st.setdefault('cleanup_passes', []).append(dict(at=full['at'], folder=full['folder'], revision=rev['digest'],
                                                        proposals=len(props), buildable=full['buildable']))
        self._save(pid, st)
    return st['cleanup']


def decide(self, pid, key, decision, by, from_josh, revision_digest=None):
    """Josh's decision on one cleanup proposal: accept (its words are removed through the lossless edit), dismiss, or
    manual (he does it himself in Premiere). Only Josh decides, once per proposal, and only on the current revision:
    a proposal from before any change to the evidence, audio or edits is refused, and so is a second decision on it."""
    if not from_josh:
        raise CleanEditError('Only Josh decides on a cleanup proposal.')
    if decision not in ('accept', 'dismiss', 'manual'):
        raise CleanEditError('A decision is accept, dismiss or manual.')
    with self.lock:
        st = self._load(pid)
        c = st.get('cleanup') or {}
        now_rev = self.revision(st)['digest']
        if (c.get('revision') or {}).get('digest') != now_rev or (revision_digest and revision_digest != now_rev):
            raise CleanEditError('The edit has changed since this cleanup pass, so its proposals are out of date. Run it again.')
        p = next((x for x in c.get('proposals') or [] if x.get('key') == key), None)
        if p is None:
            raise CleanEditError('That proposal is not in the latest cleanup pass.')
        if key in (st.get('cleanup_decisions') or {}):
            raise CleanEditError('That proposal already has your decision.')
        if decision == 'accept':
            if p.get('status') not in ('ok', 'alt') or not p.get('use_ids'):
                raise CleanEditError('That proposal is blocked, so it can only be dismissed or done by hand in Premiere.')
            if st['approved']:
                raise CleanEditError('This clean cut is approved. Start a new pass to change it.')
            edits = st['edits'] + [dict(op='remove', words=[str(w) for w in p['use_ids']], by=str(by)[:40], at=now(), cleanup=key)]
            self.model(pid, st, edits).plan_map(self._pcm(st))     # refuses anything the lossless rules can't build
            st['edits'] = edits
        rec = dict(decision=decision, by=str(by)[:40], at=now(), text=p.get('use_text') or p.get('cut_text'), revision=now_rev)
        st.setdefault('cleanup_decisions', {})[key] = rec
        p['decision'] = rec
        self._save(pid, st)
    return self.view(pid)


def edit_span(self, pid, start_s, end_s, by):
    """Remove a sound that has no word in it (a breath, cough or throat clear Josh heard), by its times. The lossless
    rules refuse it if any word overlaps, and put its edges on the frame grid in word-free mic."""
    a, b = int(round(float(start_s) * ct.T)), int(round(float(end_s) * ct.T))
    with self.lock:
        st = self._load(pid)
        if st['approved']:
            raise CleanEditError('This clean cut is approved. Start a new pass to change it.')
        edits = st['edits'] + [dict(op='remove_span', span=[a, b], words=[], by=str(by)[:40], at=now())]
        m = self.model(pid, st, edits)
        try:
            m.plan_map(self._pcm(st))
        except ValueError as e:
            raise CleanEditError(str(e)) from None
        st['edits'] = edits
        self._save(pid, st)
    return self.view(pid)


for _f in (revision, cleanup, decide, edit_span):
    setattr(CleanEdit, _f.__name__, _f)
