"""Premiere's own speech-to-text words, read from a saved .prproj (read-only, always on a copy).

The decoder is the one proven on Executive Questions (answering-exec-questions/video/Animation-Edit-v01-20261105/
reproducibility/inspect_exec_edit.py and inspect_more.py): Premiere stores each transcript as base64 FlatBuffers in a
TranscriptData node, with every word's start and duration in ticks of the source clip's media time.

- words(prproj) gives every transcript in the project with its words in media seconds, plus which media file it belongs to.
- sequence_words(prproj, sequence, media) maps one transcript's words through that sequence's clips of that media, so a
  word gets its time on the cut timeline and keeps its source time (source-to-clean mapping). Words whose middle falls in
  removed media are listed as removed, with their source time, so nothing disappears without a trace.
"""
import base64
import gzip
import shutil
import struct
import tempfile
import xml.etree.ElementTree as E
from pathlib import Path

T = 254016000000


class Project:
    def __init__(self, path):
        b = Path(path).read_bytes()
        self.root = E.fromstring(gzip.decompress(b) if b[:2] == b'\x1f\x8b' else b)
        self.ids = {x.get('ObjectID'): x for x in self.root if x.get('ObjectID')}
        self.uids = {x.get('ObjectUID'): x for x in self.root if x.get('ObjectUID')}

    def ref(self, x):
        if x is None:
            return None
        return self.ids.get(x.get('ObjectRef')) if x.get('ObjectRef') else self.uids.get(x.get('ObjectURef'))

    def sequences(self):
        return {s.findtext('Name'): s for s in self.root if s.tag == 'Sequence'}

    def tracks(self, seq):
        for r in seq.findall('TrackGroups/TrackGroup/Second'):
            g = self.ref(r)
            if g is None or g.tag not in ('VideoTrackGroup', 'AudioTrackGroup'):
                continue
            for tr in g.findall('TrackGroup/Tracks/Track'):
                track = self.ref(tr)
                items = []
                for cr in track.findall('ClipTrack/ClipItems/TrackItems/TrackItem'):
                    item = self.ref(cr)
                    it = item.find('ClipTrackItem') if item is not None else None
                    if it is None:
                        continue
                    s = self.ref(it.find('SubClip'))
                    c = self.ref(s.find('Clip'))
                    source = self.ref(c.find('Clip/Source'))
                    media = self.ref(source.find('MediaSource/Media')) if source is not None else None
                    start, end = int(it.findtext('TrackItem/Start')), int(it.findtext('TrackItem/End'))
                    inp = int(c.findtext('Clip/InPoint', '0'))
                    items.append(dict(start=start, end=end, inp=inp, out=int(c.findtext('Clip/OutPoint', str(inp + end - start))),
                                      media=media.findtext('FilePath') if media is not None else None))
                yield dict(type=g.tag, index=int(tr.get('Index')), items=sorted(items, key=lambda x: x['start']))


def _decode(text):
    d = base64.b64decode(text)

    def fields(pos):
        v = pos - struct.unpack_from('<i', d, pos)[0]
        return {i: pos + o for i, o in enumerate(struct.unpack_from('<' + 'H' * ((struct.unpack_from('<H', d, v)[0] - 4) // 2), d, v + 4)) if o}

    def target(v):
        return v + struct.unpack_from('<I', d, v)[0]

    def vector(v):
        v = target(v)
        return [target(v + 4 + i * 4) for i in range(struct.unpack_from('<I', d, v)[0])]

    def string(v):
        v = target(v)
        n = struct.unpack_from('<I', d, v)[0]
        return d[v + 4:v + 4 + n].decode()

    def tick(v):
        return struct.unpack_from('<q', d, v)[0] / T

    doc = fields(target(fields(struct.unpack_from('<I', d, 0)[0])[0]))
    out = []
    for si, seg in enumerate(vector(doc[0])):
        sf = fields(seg)
        for word in vector(sf[3]):
            wf = fields(word)
            start = tick(wf[0])
            out.append(dict(word=string(wf[2]), start=start, end=start + tick(wf[1]), segment=si))
    return out


def _copy(prproj):
    """Never read the live project in place: Premiere may be writing it."""
    tmp = Path(tempfile.mkdtemp(prefix='prtx-')) / 'copy.prproj'
    shutil.copy2(prproj, tmp)
    return tmp


def words(prproj):
    """[{media, object_id, words}] for every transcript in the project. media is the file the transcript belongs to,
    found from whichever item points at the transcript document, or None when that link isn't found."""
    p = Project(_copy(prproj))
    owner = {}
    for x in p.root.iter():
        r = x.get('ObjectRef')
        if r:
            owner.setdefault(r, []).append(x)
    parent = {c: q for q in p.root.iter() for c in q}
    out = []
    for doc in p.root:
        node = doc.find('.//TranscriptData')
        if node is None or not (node.text or '').strip():
            continue
        media = None
        # Native source transcripts are siblings of the audio clip in a MasterClip.
        # TranscriptClip's own DataMediaSource is synthetic, not the WAV file.
        transcript_clips = {x.get('ObjectID') for x in p.root if x.tag == 'TranscriptClip'
                            and any(r.get('ObjectRef') == doc.get('ObjectID') for r in x.iter())}
        paths = set()
        for master in p.root:
            refs = master.findall('Clips/Clip') if master.tag == 'MasterClip' else []
            if not any(r.get('ObjectRef') in transcript_clips for r in refs):
                continue
            for ref in refs:
                clip = p.ref(ref)
                if clip is None or clip.tag != 'AudioClip':
                    continue
                source = p.ref(clip.find('Clip/Source'))
                medium = p.ref(source.find('MediaSource/Media')) if source is not None else None
                path = medium.findtext('FilePath') if medium is not None else None
                if path:
                    paths.add(path)
        if len(paths) == 1:
            media = next(iter(paths))
        for ref in owner.get(doc.get('ObjectID'), []):
            top = ref
            while parent.get(top) is not None and parent.get(top) is not p.root:
                top = parent[top]
            fp = top.findtext('.//FilePath') if top is not None else None
            if not fp and top is not None and top.tag == 'Media':
                fp = top.findtext('FilePath')
            if fp and media is None and not paths:
                media = fp
                break
        out.append(dict(media=media, object_id=doc.get('ObjectID'), tag=doc.tag, words=_decode(node.text)))
    return out


def media_key(path):
    return str(path or '').replace('\\', '/').removeprefix('//?/').casefold()


def microphone_transcript(transcripts, mic):
    """Select by source identity, never by transcript count or a sequence's name."""
    matches = [t for t in transcripts if media_key(t.get('media')) == media_key(mic)]
    if len(matches) > 1:
        raise ValueError('Multiple native transcripts refer to the selected microphone. Review their identity before continuing.')
    return matches[0] if matches else None


def sequence_words(prproj, sequence, media_words, media_path):
    """Map source words onto one sequence through its clips of media_path (every audio track, the first that holds it).
    Returns (kept, removed): kept words carry seq_start/seq_end plus source start/end; removed words carry source times."""
    p = Project(_copy(prproj))
    seq = p.sequences().get(sequence)
    if seq is None:
        raise ValueError(f'There is no sequence called {sequence}')
    norm = lambda s: str(s or '').replace('\\\\?\\', '').lower()
    clips = []
    for t in p.tracks(seq):
        if t['type'] == 'AudioTrackGroup':
            mine = [c for c in t['items'] if norm(c['media']) == norm(media_path)]
            if mine:
                clips = mine
                break
    if not clips:
        raise ValueError(f'{sequence} has no audio clip of {media_path}')
    kept, removed = [], []
    for w in media_words:
        mid = (w['start'] + w['end']) / 2
        c = next((c for c in clips if c['inp'] / T <= mid < c['out'] / T), None)
        if c is None:
            removed.append(dict(w))
            continue
        off = c['start'] / T - c['inp'] / T
        kept.append(dict(w, seq_start=max(w['start'], c['inp'] / T) + off, seq_end=min(w['end'], c['out'] / T) + off,
                         clipped=w['start'] < c['inp'] / T or w['end'] > c['out'] / T,
                         clipped_ms=round(1000 * (max(0, c['inp'] / T - w['start']) + max(0, w['end'] - c['out'] / T)), 1),
                         piece_in=c['inp'] / T, piece_out=c['out'] / T))
    return kept, removed
