"""The editable clean transcript: Premiere's own words of the good mic against the exact cut AutoCut made, lossless.

Built from three exact sources, never from screenshots or copied text:
- Premiere's native transcript of the mic (studio/premiere_transcript.py): every word with its source time. Words get
  fixed ids in source order (n0001, n0002...) that never change, whatever is cut or restored.
- The uncut copy's single map piece (ticks): where the copy starts and ends in the mic, and the mic-to-camera offset.
- Premiere's read-back of the sequence after each AutoCut pass (A1 in points and lengths, in ticks): what was kept.

Every removed stretch of the mic is a removal with its provenance (outside the camera take, Silences, Repeat, or Josh)
and its exact tick edges. Each word is kept, removed or partial (an edge of a kept piece falls inside it), and records
which removal took it.

Josh's edits are remove and restore, by word id. They never touch AutoCut's pieces in place. A new keep list is worked
out from AutoCut's exact pieces plus the edits, and a fresh sequence is built from GREEN_MASTER and the original mic
(plan()), so a restored word comes back with its real sound and picture. Where an edit makes a new edge, the edge goes at
the quietest 10 ms of the mic between the neighbouring words, is put on the frame grid against the untouched edge of its
piece, and is listed for a listen. Edges AutoCut made, and Josh's earlier edges, are kept to the tick.
"""
import json
import re
from pathlib import Path

import numpy as np

T = 254016000000
SR = 16000
TPS = T // SR            # ticks per 16 kHz sample
BIN = 160                # 10 ms


def _ticks(seconds):
    return int(round(seconds * T))


def _subtract(keep, cut):
    """keep minus cut, both lists of [a, b) in ticks."""
    out = []
    for a, b in keep:
        segs = [(a, b)]
        for c, d in cut:
            nxt = []
            for x, y in segs:
                if d <= x or c >= y:
                    nxt.append((x, y))
                else:
                    if c > x:
                        nxt.append((x, c))
                    if d < y:
                        nxt.append((d, y))
            segs = nxt
        out += segs
    return [list(s) for s in out if s[1] > s[0]]


def _intersect(a_list, b_list):
    out = []
    for a, b in a_list:
        for c, d in b_list:
            x, y = max(a, c), min(b, d)
            if y > x:
                out.append([x, y])
    return sorted(out)


def keeps_from_readback(info):
    """A1 pieces of a read-back as [mic_in, mic_out) ticks, plus their sequence starts, in timeline order."""
    a = sorted([i for i in info['items'] if i['kind'] == 'a' and i['track'] == 0], key=lambda i: int(i['start']))
    return [[int(x['inp']), int(x['inp']) + int(x['end']) - int(x['start'])] for x in a]


class CleanTranscript:
    def __init__(self, native_words, raw_piece, after_silences_info, after_repeat_info, frame_ticks, edits=(), require_pcm=False):
        # Production models (Josh OS's Script tab, the cleanup pass, every Premiere build) pass require_pcm=True: then a
        # cut fails closed without the mic audio or with an ambiguous boundary, instead of falling back to timestamps.
        # Only unit-test helpers leave it off.
        self.require_pcm = bool(require_pcm)
        self.frame = int(frame_ticks)
        self.raw = [int(raw_piece['mic_in']), int(raw_piece['mic_out'])]
        self.cam_offset = int(raw_piece['mic_in']) - int(raw_piece['camera_in'])
        self.k_sil = keeps_from_readback(after_silences_info)
        self.k_auto = keeps_from_readback(after_repeat_info)
        for k in (self.k_sil, self.k_auto):
            if any(b <= a for a, b in k) or any(q[0] < p[1] for p, q in zip(k, k[1:])):
                raise ValueError('A read-back has pieces out of order or overlapping in the mic, so it is not a simple cut.')
        self.words = []
        for i, w in enumerate(x for x in native_words if x['word'].strip()):
            self.words.append(dict(id=f'n{i + 1:04d}', text=w['word'], start=_ticks(w['start']), end=_ticks(w['end'])))
        self.edits = list(edits)
        # removals with provenance, from AutoCut's exact edges
        outside = _subtract([[0, 1 << 62]], [self.raw])
        sil = _subtract([self.raw], self.k_sil)
        rep = _intersect(self.k_sil, _subtract(self.k_sil, self.k_auto))
        self.removals = sorted([dict(a=a, b=b, by='outside_camera') for a, b in outside if b > a] +
                               [dict(a=a, b=b, by='silences') for a, b in sil] + [dict(a=a, b=b, by='repeat') for a, b in rep],
                               key=lambda r: r['a'])

    # ------------------------------------------------------------------ the keep list with Josh's edits
    def keeps(self, pcm=None):
        """AutoCut's keeps with every edit applied in order. Returns (keeps, new_edges) in ticks."""
        if self.require_pcm and pcm is None and self.edits:
            raise ValueError('The mic audio is not available, so no edit can be placed. Nothing was cut.')
        keep = [list(k) for k in self.k_auto]
        new_edges = []
        self.extended = []
        byid = {w['id']: w for w in self.words}
        for e in self.edits:
            if e['op'] == 'remove_span':
                # A sound with no word in it (a breath, a cough, a throat clear), removed by its own times. Refused if any
                # word Premiere wrote down overlaps it; its edges then go onto the frame grid in word-free mic (_on_grid).
                a, b = int(e['span'][0]), int(e['span'][1])
                if b <= a or a < self.raw[0] or b > self.raw[1]:
                    raise ValueError('That stretch is empty or outside the recording.')
                if self._words_in(a, b):
                    raise ValueError(f'A word overlaps {a / T:.2f} to {b / T:.2f} s, so the stretch was not removed. Remove the word itself.')
                # A gap in the transcript is not proof there is no speech, so the mic itself is checked.
                if pcm is None:
                    raise ValueError('Removing a sound by its times needs the mic audio to check it, so nothing was removed.')
                ref = self._speech_ref(pcm)
                voiced = self._voiced_frames(a, b, pcm, ref)
                if voiced:
                    raise ValueError(f'The mic at {voiced[0] / T:.2f} s inside that stretch sounds like voiced speech, so it was not removed. '
                                     'Josh decides by ear and trims it by hand if he wants.')
                a = self._quiet_edge(a, pcm, ref)
                b = self._quiet_edge(b, pcm, ref)
                if a is None or b is None or b <= a:
                    raise ValueError('There is no quiet, word-free point near one edge of that stretch, so it was not removed.')
                keep = _subtract(keep, [[a, b]])
                new_edges += [dict(at=a, edit=e, side='start'), dict(at=b, edit=e, side='end')]
                continue
            ws = sorted((byid[i] for i in e['words']), key=lambda w: w['start'])
            if not ws or any(i not in byid for i in e['words']):
                raise ValueError(f'Unknown word id in edit {e}')
            idx = [self.words.index(w) for w in ws]
            if idx != list(range(idx[0], idx[0] + len(idx))):
                raise ValueError('An edit must be one run of neighbouring words.')
            prev = self.words[idx[0] - 1] if idx[0] > 0 else None
            nxt = self.words[idx[-1] + 1] if idx[-1] + 1 < len(self.words) else None
            lo_win = (prev['end'] if prev else self.raw[0], ws[0]['start'])
            hi_win = (ws[-1]['end'], nxt['start'] if nxt else self.raw[1])
            if e['op'] == 'remove':
                a, b = self._quiet(lo_win, pcm), self._quiet(hi_win, pcm)
                keep = _subtract(keep, [[a, b]])
            elif e['op'] == 'restore':
                a, b = self._quiet(lo_win, pcm), self._quiet(hi_win, pcm)
                # the restored stretch may not run past the camera take
                a, b = max(a, self.raw[0]), min(b, self.raw[1])
                keep = self._merge(keep + [[a, b]])
            else:
                raise ValueError(f"Unknown edit {e['op']}")
            new_edges += [dict(at=a, edit=e, side='start'), dict(at=b, edit=e, side='end')]
        keep = self._grid(keep, pcm)
        if self.require_pcm and pcm is not None and self.edits:
            self._check_final(keep, pcm)
        # only edges that aren't AutoCut's (or the raw copy's) are new
        old = {x for k in self.k_auto for x in k} | set(self.raw)
        new = []
        for i, (a, b) in enumerate(keep):
            for at, side in ((a, 'start'), (b, 'end')):
                if at not in old:
                    src = min(new_edges, key=lambda ed: abs(ed['at'] - at)) if new_edges else {}
                    gap = self._gap_around(at)
                    # under 20 ms of word-free mic around the edge: the words touch, so the cut needs a listen
                    tight = gap is None or gap[1] - gap[0] < 2 * BIN * TPS
                    new.append(dict(at_final=at, side=side, piece=i, edit=src.get('edit'), quiet_point=src.get('at'), tight=tight,
                                    gap_ms=None if gap is None else round((gap[1] - gap[0]) / T * 1000, 1)))
        return keep, new

    @staticmethod
    def _merge(ks):
        ks = sorted(ks)
        out = []
        for a, b in ks:
            if out and a <= out[-1][1]:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return out

    def _quiet(self, window, pcm):
        """The quietest 10 ms point of the mic between two words (ticks). This is only where sound is lowest, never proof
        that no sound of a word is lost; that needs listening. In production (require_pcm) there is no fallback: no mic,
        no gap between the words, or a gap under 10 ms fails closed. Unit-test helpers without audio get the middle."""
        a, b = window
        if b <= a or pcm is None or (b - a) // TPS < BIN:
            if self.require_pcm:
                why = 'the mic audio is missing' if pcm is None else 'the words there touch, with no silence between them'
                raise ValueError(f'The edge near {a / T:.2f} s is ambiguous ({why}), so nothing was cut there.')
            return a if b <= a else (a + b) // 2
        s0, s1 = a // TPS, b // TPS
        seg = pcm[s0:s1]
        n = len(seg) // BIN
        db = 20 * np.log10(np.sqrt((seg[:n * BIN].astype(np.float64).reshape(n, BIN) ** 2).mean(axis=1)) + 1e-9)
        k = int(np.argmin(db))
        return (s0 + k * BIN + BIN // 2) * TPS

    def _grid(self, keep, pcm=None):
        """Make every piece a whole number of frames without losing any sound.

        Why rounding is needed at all: Premiere pieces are whole video frames (8,467,191,533 ticks), but AutoCut's edges in
        the mic sit at any 16 kHz sample. A piece between two of AutoCut's edges is already whole (Premiere made it), but
        an edit's new edge, or a restore that joins two pieces across a gap that wasn't whole frames, leaves a remainder.

        The rule is conservative: a piece is only ever made longer, by under one frame, into mic that holds no word (no
        word starts or ends inside the added stretch, judged on Premiere's own word times). A new edge extends first;
        a piece whose two edges are both AutoCut's extends at its end, else at its start. AutoCut's other edges never
        move. If neither side has a word-free stretch, the edit is refused with the reason, never cut short.

        A whole-frame length is not enough on its own (found 13 Nov 2026): a piece whose two edges are both new could be
        whole frames long yet sit off the frame grid, and Premiere then snapped its audio in-point by 27 ms while the
        video stayed, so picture and mic drifted. AutoCut's edges all sit on the grid, so every new edge is first put on
        the grid too (_on_grid), then the length is checked as before."""
        keep = self._on_grid(keep, pcm)
        out = []
        for i, (a, b) in enumerate(keep):
            r = (b - a) % self.frame
            if r == 0:
                out.append([a, b])
                continue
            add = self.frame - r
            lo_lim = out[-1][1] if out else self.raw[0]
            hi_lim = keep[i + 1][0] if i + 1 < len(keep) else self.raw[1]
            old = {x for k in self.k_auto for x in k} | set(self.raw)
            # A new edge is placed straight onto its piece's own frame grid, at the quietest grid point inside the
            # word-free gap it sits in (between the end of one word and the start of the next).
            moved = None
            if b not in old:
                moved = self._grid_point(b, a, +1, pcm, lo_lim, hi_lim)
                if moved is not None:
                    out.append([a, moved])
                    self.extended.append(dict(piece=i, side='end', ticks=moved - b, at=b, placed=True))
                    continue
            if a not in old:
                moved = self._grid_point(a, b, -1, pcm, lo_lim, hi_lim)
                if moved is not None:
                    out.append([moved, b])
                    self.extended.append(dict(piece=i, side='start', ticks=a - moved, at=a, placed=True))
                    continue
            tries = []
            if b not in old:
                tries.append('end')
            if a not in old:
                tries.append('start')
            for side in ('end', 'start'):
                if side not in tries:
                    tries.append(side)
            done = False
            for side in tries:
                x, y = (b, b + add) if side == 'end' else (a - add, a)
                if side == 'end' and y > hi_lim or side == 'start' and x < lo_lim:
                    continue
                if self._words_in(x, y):
                    continue
                out.append([a, y] if side == 'end' else [x, b])
                self.extended.append(dict(piece=i, side=side, ticks=add, at=b if side == 'end' else a))
                done = True
                break
            if not done:
                raise ValueError(f'The piece ending at {b / T:.3f} s needs {add / T * 1000:.1f} ms more to be whole frames, and both sides '
                                 'have a word right there. Tariq stops rather than cut a word or pull a removed one back in.')
        return self._merge_whole(out)

    def _on_grid(self, keep, pcm=None):
        """Put every edge that is off the frame grid onto it: the grid point just before or just after, whichever stays
        in word-free mic and inside the neighbouring pieces, the quieter if the mic is given. AutoCut's edges are already
        on the grid and never move. Refuses, with the reason, when neither grid point is word-free."""
        old = {x for k in self.k_auto for x in k} | set(self.raw)
        out = []
        for i, (a, b) in enumerate(keep):
            lo_lim = out[-1][1] if out else self.raw[0]
            hi_lim = keep[i + 1][0] if i + 1 < len(keep) else self.raw[1]
            na = a if a in old else self._snap(a, lo_lim, b, pcm)
            nb = b if b in old else self._snap(b, a, hi_lim, pcm)
            if na is None or nb is None:
                t = a if na is None else b
                raise ValueError(f'The edge at {t / T:.3f} s is off the frame grid and both grid points next to it are inside a word. '
                                 'Tariq stops rather than cut a word or let picture and sound drift.')
            if nb <= na:
                raise ValueError(f'The piece at {a / T:.3f} s is shorter than a frame once its edges are on the frame grid.')
            if (na, nb) != (a, b):
                self.extended.append(dict(piece=i, side='grid', at=(a, b), now=(na, nb)))
            out.append([na, nb])
        return out

    def _snap(self, t, lo_lim, hi_lim, pcm=None):
        r = t % self.frame
        if r == 0:
            return t
        cands = [c for c in (t - r, t - r + self.frame) if lo_lim <= c <= hi_lim and not self._words_in(min(c, t), max(c, t))]
        if not cands:
            return None
        if pcm is None:
            return min(cands, key=lambda c: abs(c - t))
        return min(cands, key=lambda c: (round(self._level(c, pcm), 1), abs(c - t)))

    # Waveform checks for removing a sound by its times --------------------------------------------------------------
    SPEECH_FRAME = 320           # 20 ms at 16 kHz
    VOICED = 0.6                 # autocorrelation peak for a 70 to 400 Hz pitch that reads as voiced
    VOICED_LEVEL_DB = 18         # voiced and within this many dB of speech level counts as possible speech
    QUIET_EDGE_DB = 30           # an edge must sit at least this far below speech level
    EDGE_SEARCH = 60             # ms either side of an edge to look for a quieter point

    def _speech_ref(self, pcm):
        """Speech level: the median level of the mic inside Premiere's words."""
        if getattr(self, '_ref', None) is not None:
            return self._ref
        lv = []
        for w in self.words[::7]:
            seg = pcm[w['start'] // TPS:w['end'] // TPS].astype(np.float64)
            if len(seg):
                lv.append(20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-9))
        self._ref = float(np.median(lv)) if lv else -30.0
        return self._ref

    def _voiced_frames(self, a, b, pcm, ref):
        """Start times (ticks) of 20 ms frames in [a, b) that are voiced and near speech level."""
        out, f = [], self.SPEECH_FRAME
        lo_lag, hi_lag = int(16000 / 400), int(16000 / 70)
        for s0 in range(a // TPS, b // TPS - f + 1, f):
            seg = pcm[s0:s0 + 2 * f].astype(np.float64)
            if len(seg) < 2 * f:
                break
            db = 20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-9)
            if db < ref - self.VOICED_LEVEL_DB:
                continue
            x = seg - seg.mean()
            ac = np.correlate(x, x, 'full')[len(x) - 1:]
            if ac[0] > 0 and ac[lo_lag:hi_lag].max() / ac[0] > self.VOICED:
                out.append(s0 * TPS)
        return out

    def _quiet_edge(self, t, pcm, ref):
        """The quietest 10 ms point within EDGE_SEARCH ms of t, inside the same word-free gap as t (so the edge can never
        move across a word, however short), if it is at least QUIET_EDGE_DB below speech level. None otherwise."""
        step = BIN * TPS
        gap = self._gap_around(t)
        if gap is None:
            return None
        cands = [t + k * step for k in range(-self.EDGE_SEARCH // 10, self.EDGE_SEARCH // 10 + 1)]
        cands = [c for c in cands if gap[0] + step // 2 <= c <= gap[1] - step // 2 and self.raw[0] <= c <= self.raw[1]]
        if not cands:
            return None
        best = min(cands, key=lambda c: (round(self._level(c, pcm), 1), abs(c - t)))
        return best if self._level(best, pcm) <= ref - self.QUIET_EDGE_DB else None

    QUIET_CUT_DB = 25           # every new edge in production must sit at least this far below speech level

    def _check_final(self, keep, pcm):
        """The final check, after every edge was placed and snapped to the grid (production only). Whatever this pass newly
        removes, beyond AutoCut, may hold only the words an edit chose to remove, and a stretch removed by its times may
        hold no voiced sound at all. Every new edge must be quiet. Anything else fails closed."""
        ref = self._speech_ref(pcm)
        chosen = {i for e in self.edits if e['op'] == 'remove' for i in e['words']}
        spans = [e['span'] for e in self.edits if e['op'] == 'remove_span']
        newly = _subtract(_intersect(self.k_auto, [self.raw]), keep)
        for a, b in newly:
            others = [w for w in self._words_in(a, b) if w['id'] not in chosen]
            if others:
                raise ValueError(f'The cut from {a / T:.2f} to {b / T:.2f} s would also take "{others[0]["text"]}", which no edit chose. '
                                 'Nothing was cut.')
            if any(x < b and y > a for x, y in spans):
                voiced = self._voiced_frames(a, b, pcm, ref)
                if voiced:
                    raise ValueError(f'After its edges were placed, the stretch from {a / T:.2f} to {b / T:.2f} s holds voiced sound at '
                                     f'{voiced[0] / T:.2f} s, so it was not removed.')
        old = {x for k in self.k_auto for x in k} | set(self.raw)
        for a, b in keep:
            for t in (a, b):
                if t not in old and self._level(t, pcm) > ref - self.QUIET_CUT_DB:
                    raise ValueError(f'The new edge at {t / T:.2f} s is not quiet ({self._level(t, pcm):.0f} dB, speech is about '
                                     f'{ref:.0f} dB), so the boundary is ambiguous and nothing was cut.')

    def _gap_around(self, t):
        """The word-free stretch of mic around t: from the end of the word before to the start of the word after.
        None when t is inside a word."""
        if self._words_in(t, t + 1):
            return None
        lo = max([w['end'] for w in self.words if w['end'] <= t] or [self.raw[0]])
        hi = min([w['start'] for w in self.words if w['start'] >= t] or [self.raw[1]])
        return lo, hi

    def _grid_point(self, edge, fixed, direction, pcm, lo_lim, hi_lim):
        """A replacement for a new edge that makes its piece whole frames: a point fixed + n frames (direction +1 for an
        end, -1 for a start) inside the same word-free gap and inside the neighbours' limits, the quietest if the mic is
        given, else the nearest. None if the gap holds no such point."""
        gap = self._gap_around(edge)
        if gap is None:
            return None
        lo, hi = max(gap[0], lo_lim), min(gap[1], hi_lim)
        if direction > 0:
            n0, n1 = -((fixed - lo) // self.frame), (hi - fixed) // self.frame
            cands = [fixed + n * self.frame for n in range(max(1, n0), n1 + 1)]
        else:
            n0, n1 = -((hi - fixed) // self.frame), (fixed - lo) // self.frame
            cands = [fixed - n * self.frame for n in range(max(1, n0), n1 + 1)]
        cands = [c for c in cands if lo <= c <= hi]
        # only the grid points either side of the edge, so no edge moves by a frame or more
        cands = [c for c in cands if abs(c - edge) < self.frame]
        if not cands:
            return None
        if pcm is None:
            return min(cands, key=lambda c: abs(c - edge))
        return min(cands, key=lambda c: (round(self._level(c, pcm), 1), abs(c - edge)))

    @staticmethod
    def _level(t, pcm):
        i = t // TPS
        seg = pcm[max(0, i - BIN // 2):i + BIN // 2].astype(np.float64)
        return float(20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-9)) if len(seg) else 0.0

    def _words_in(self, x, y):
        """Words that start or end inside [x, y), or span it."""
        return [w for w in self.words if w['start'] < y and w['end'] > x]

    def _merge_whole(self, ks):
        """Touching pieces become one only when the result stays whole frames. Pieces never overlap."""
        ks = sorted(k for k in ks if k[1] > k[0])
        out = []
        for a, b in ks:
            if out and a < out[-1][1]:
                raise ValueError('Two pieces overlap in the mic after an edit.')
            if out and a == out[-1][1] and (b - out[-1][0]) % self.frame == 0:
                out[-1][1] = b
            else:
                out.append([a, b])
        return out

    # ------------------------------------------------------------------ word states
    def states(self, pcm=None):
        keep, _ = self.keeps(pcm)
        joshes = []                               # what Josh's edits removed, beyond AutoCut
        for a, b in _subtract([self.raw], keep):
            joshes.append([a, b])
        rows = []
        seq = 0
        starts = []
        for a, b in keep:
            starts.append(seq)
            seq += b - a
        for w in self.words:
            inside = sum(max(0, min(b, w['end']) - max(a, w['start'])) for a, b in keep)
            dur = max(1, w['end'] - w['start'])
            state = 'kept' if inside >= dur else 'removed' if inside == 0 else 'partial'
            mid = (w['start'] + w['end']) // 2
            by = None
            if state != 'kept':
                # whoever removed the biggest part of the word that is now left out
                lost = _subtract([[w['start'], w['end']]], keep)
                share = {}
                for a, b in lost:
                    for x, y in _intersect([[a, b]], self.k_auto):
                        share['josh'] = share.get('josh', 0) + y - x
                    for r in self.removals:
                        for x, y in _intersect([[a, b]], [[r['a'], r['b']]]):
                            share[r['by']] = share.get(r['by'], 0) + y - x
                by = max(share, key=share.get) if share else None
            clean = None
            for (a, b), s in zip(keep, starts):
                if a <= mid < b:
                    clean = (s + mid - a) / T
            rows.append(dict(w, state=state, kept_fraction=round(inside / dur, 3), removed_by=by,
                             restored=state != 'removed' and not any(a <= mid < b for a, b in self.k_auto), clean_at=clean))
        return rows

    # ------------------------------------------------------------------ Premiere plan for a fresh sequence
    def plan_map(self, pcm=None):
        keep, new = self.keeps(pcm)
        pieces, out = [], 0
        for a, b in keep:
            pieces.append(dict(kind='source', mic_in=a, mic_out=b, camera_in=a - self.cam_offset, camera_out=b - self.cam_offset,
                               out_start=out, out_end=out + (b - a)))
            out += b - a
        if any(p['camera_in'] < 0 for p in pieces):
            raise ValueError('A kept piece starts before the camera recording.')
        return dict(pieces=pieces, frame_ticks=str(self.frame), duration_ticks=out), new

    def to_json(self):
        return dict(words=self.words, raw=self.raw, cam_offset=self.cam_offset, k_sil=self.k_sil, k_auto=self.k_auto,
                    removals=self.removals, edits=self.edits, frame=self.frame)
