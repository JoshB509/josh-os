"""Josh OS side of the AutoCut clean pass: the editable transcript, word edits, fresh builds and Josh's approval.

Per project, <project data>/clean-edit.json holds which AutoCut pass it reads (its evidence folder from
studio/autocut_pass.py), Josh's edits in order, every build, and the approval. The words and cut come from the exact
sources in that folder (studio/clean_transcript.py), so what Josh sees is what AutoCut really kept and removed.

- view: every word with its id, text, source time, state (kept, removed, partial), who removed it (outside the camera
  take, Silences, Repeat, Josh), whether Josh restored it, and its time in the clean cut; plus the new edges to listen to.
- edit: remove or restore a run of neighbouring words by id. Edits only change the plan. Nothing in Premiere moves.
- undo: drop the last edit.
- build: a fresh AUTOCUT_EDIT_rNN from GREEN_MASTER and the original mic with the edits, read back and checked to the
  tick against the plan. Earlier sequences are never changed, so every version stays.
- approve: Josh only (from the app). It records the exact build and edit list approved. Animations and assembly take
  their clean sequence from approved() and refuse without it.
"""
import json
import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import clean_transcript as ct

T = ct.T


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class CleanEditError(ValueError):
    pass


class CleanEdit:
    def __init__(self, folder_of, driver_factory=None):
        """folder_of(pid) gives the project's Josh OS data folder."""
        self.folder_of = folder_of
        self.driver_factory = driver_factory
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ storage
    def _file(self, pid):
        return Path(self.folder_of(pid)) / 'clean-edit.json'

    def _load(self, pid):
        f = self._file(pid)
        if not f.is_file():
            raise CleanEditError('There is no AutoCut pass for this video yet.')
        return json.loads(f.read_text(encoding='utf-8'))

    def _save(self, pid, st):
        f = self._file(pid)
        tmp = f.with_suffix('.tmp')
        tmp.write_text(json.dumps(st, indent=1, default=str), encoding='utf-8')
        tmp.replace(f)

    def register(self, pid, evidence, prproj, mic_path, pcm_path, sequence, frame_ticks, master='GREEN_MASTER'):
        """Point this video at one AutoCut pass. A new pass starts a new edit list; the old one is kept in history."""
        ev = Path(evidence)
        for need in ('premiere-native-words-mic.json', 'after-silences-readback.json', 'after-repeat-readback.json'):
            if not (ev / need).is_file():
                raise CleanEditError(f'The AutoCut pass in {ev} has no {need}.')
        old = self._load(pid) if self._file(pid).is_file() else None
        st = dict(evidence=str(ev), prproj=str(prproj), mic=str(mic_path), pcm=str(pcm_path), sequence=sequence, frame=int(frame_ticks),
                  master=master, edits=[], builds=[], approved=None, registered=now(),
                  history=(old.get('history', []) + [{k: old[k] for k in ('evidence', 'sequence', 'edits', 'builds', 'approved')}]) if old else [])
        self._save(pid, st)
        return self.view(pid)

    # ------------------------------------------------------------------ the model
    def _raw_piece(self, ev):
        f = ev / 'autocut-pass.json'
        if f.is_file():
            return json.loads(f.read_text())['stages']['raw']['plan']['map']['pieces'][0]
        return next(json.loads(p.read_text())['plan']['map']['pieces'][0] for p in ev.glob('autocut-raw-*-build.json'))

    def model(self, pid, st=None, edits=None):
        st = st or self._load(pid)
        ev = Path(st['evidence'])
        native = json.loads((ev / 'premiere-native-words-mic.json').read_text())
        if len(native) != 1:
            raise CleanEditError('The pass holds more than one Premiere transcript.')
        sil = json.loads((ev / 'after-silences-readback.json').read_text())['info']
        rep = json.loads((ev / 'after-repeat-readback.json').read_text())['info']
        return ct.CleanTranscript(native[0]['words'], self._raw_piece(ev), sil, rep, st['frame'], st['edits'] if edits is None else edits,
                                  require_pcm=True)

    def _pcm(self, st):
        return np.fromfile(st['pcm'], dtype='<f4')

    def view(self, pid):
        st = self._load(pid)
        m = self.model(pid, st)
        pcm = self._pcm(st)
        rows = m.states(pcm)
        _, new = m.keeps(pcm)
        counts = {}
        for r in rows:
            key = r['state'] if r['state'] == 'kept' else f"{r['state']}:{r['removed_by']}"
            counts[key] = counts.get(key, 0) + 1
        from dajoshi import edit_plan as ep
        cur = next((b for b in reversed(st['builds']) if b['exact'] and b['edits_digest'] == ep.digest(st['edits'])), None)
        approvable = st['sequence'] if not st['edits'] else (cur or {}).get('sequence')
        return dict(sequence=st['sequence'], edits=st['edits'], builds=st['builds'], approved=st['approved'], counts=counts,
                    current_build=cur, approvable=None if st['approved'] else approvable, transcripts=st.get('transcripts', []),
                    final_approved=st.get('final_approved'), release=st.get('release'), cleanup=st.get('cleanup'),
                    cleanup_decisions=st.get('cleanup_decisions') or {},
                    words=[dict(id=r['id'], text=r['text'], start=r['start'] / T, end=r['end'] / T, state=r['state'],
                                removed_by=r['removed_by'], restored=r['restored'], kept_fraction=r['kept_fraction'], clean_at=r['clean_at'],
                                edge_sound=self._edge_sound(m, r, pcm) if r['state'] == 'partial' else None)
                           for r in rows],
                    new_edges=[dict(at=e['at_final'] / T, side=e['side'], op=(e['edit'] or {}).get('op'), tight=e.get('tight'),
                                    gap_ms=e.get('gap_ms')) for e in new],
                    removals=[dict(start=r['a'] / T, end=r['b'] / T, by=r['by']) for r in m.removals if r['by'] != 'outside_camera'])

    @staticmethod
    def _edge_sound(m, r, pcm):
        """For a word cut at a join: how far under the word (dB) the part left out is. 40 dB or more under is silence
        (Premiere's word times start a little early). Less than that is sound and needs a listen. Level says where sound
        is, never which word it is."""
        keep, _ = m.keeps(pcm) if not hasattr(m, '_keep_cache') else (m._keep_cache, None)
        m._keep_cache = keep
        lost = ct._subtract([[r['start'], r['end']]], keep)
        if not lost:
            return None
        lvl = lambda a, b: float(20 * np.log10(np.sqrt(np.mean(pcm[a // ct.TPS:max(a // ct.TPS + 1, b // ct.TPS)].astype(np.float64) ** 2)) + 1e-9))
        a, b = max(lost, key=lambda x: x[1] - x[0])
        below = lvl(r['start'], r['end']) - lvl(a, b)
        return dict(below_word_db=round(below, 1), lost_ms=round(sum(y - x for x, y in lost) / T * 1000), sound=below < 40)

    # ------------------------------------------------------------------ edits
    def edit(self, pid, op, words, by, expected_state_digest=None):
        if op not in ('remove', 'restore'):
            raise CleanEditError('An edit is remove or restore.')
        words = [str(w) for w in words or []]
        if not words:
            raise CleanEditError('Pick the words first.')
        with self.lock:
            st = self._load(pid)
            if expected_state_digest is not None and hashlib.sha256(json.dumps(st, sort_keys=True, default=str).encode()).hexdigest() != expected_state_digest:
                raise CleanEditError("The edit changed. Reload it before changing words.")
            if st['approved']:
                raise CleanEditError('This clean cut is approved. Start a new pass to change it.')
            edits = st['edits'] + [dict(op=op, words=words, by=str(by)[:40], at=now())]
            m = self.model(pid, st, edits)
            m.plan_map(self._pcm(st))           # refuses anything that can't be built (unknown ids, split runs, overlaps)
            st['edits'] = edits
            self._save(pid, st)
        return self.view(pid)

    def undo(self, pid, expected_state_digest=None):
        with self.lock:
            st = self._load(pid)
            if expected_state_digest is not None and hashlib.sha256(json.dumps(st, sort_keys=True, default=str).encode()).hexdigest() != expected_state_digest:
                raise CleanEditError("The edit changed. Reload it before changing words.")
            if st['approved']:
                raise CleanEditError('This clean cut is approved. Start a new pass to change it.')
            if not st['edits']:
                raise CleanEditError('There is nothing to undo.')
            st['edits'] = st['edits'][:-1]
            self._save(pid, st)
        return self.view(pid)

    # ------------------------------------------------------------------ build in Premiere
    def build(self, pid):
        from dajoshi import edit_plan as ep
        from . import autocut_pass as ap
        with self.lock:
            st = self._load(pid)
            m = self.model(pid, st)
            pm, new = m.plan_map(self._pcm(st))
            d = self.driver_factory()
            project = d.premiere_ops([dict(op='project')])[0]
            taken = [s for s in project['sequences'] if s.startswith('AUTOCUT_EDIT_r')]
            name = f'AUTOCUT_EDIT_r{len(taken) + 1:02d}'
            while name in project['sequences']:
                name = f'AUTOCUT_EDIT_r{int(name[-2:]) + 1:02d}'
            plan = dict(project_id=pid, proposal_id='clean-edit', proposal_digest=ep.digest(st['edits']), revision=len(st['builds']) + 1, camera_path='',
                        mic_path=st['mic'], master=st['master'], sequence_name=name, map=pm, map_digest=ep.digest(pm))
            import urllib.request
            req = urllib.request.Request(d.bridge + '/bridge/call', data=json.dumps(dict(cmd='tq_build_cut', args=dict(plan=plan, request_id=name))).encode(),
                                         headers={'Content-Type': 'application/json'}, method='POST')
            res = json.loads(urllib.request.urlopen(req, timeout=1800).read())
            if (res.get('result') or {}).get('sequence') != name:
                raise CleanEditError(f'Premiere did not build {name}: {json.dumps(res)[:300]}')
            rb = ap.readback(d, name)
            got = ct.keeps_from_readback(rb['info'])
            want = [[p['mic_in'], p['mic_out']] for p in pm['pieces']]
            exact = got == want and not rb['problems']
            d.premiere_ops([dict(op='save')])
            b = dict(sequence=name, at=now(), edits=len(st['edits']), edits_digest=ep.digest(st['edits']), map_digest=plan['map_digest'],
                     exact=exact, problems=rb['problems'], length_s=rb['length_s'], new_edges=[e['at_final'] / T for e in new])
            st['builds'].append(b)
            self._save(pid, st)
        if not exact:
            raise CleanEditError(f'{name} was built but does not match the plan to the tick, so it must not be used: {rb["problems"] or "edges differ"}.')
        return self.view(pid)

    # ------------------------------------------------------------------ Josh's approval
    def approve(self, pid, by, from_josh, sequence):
        if not from_josh:
            raise CleanEditError('Only Josh can approve the clean cut, from the video page.')
        from dajoshi import edit_plan as ep
        with self.lock:
            st = self._load(pid)
            ok = [b for b in st['builds'] if b['exact'] and b['edits_digest'] == ep.digest(st['edits'])]
            pick = None
            if not st['edits'] and sequence == st['sequence']:
                pick = dict(sequence=st['sequence'], edits=0)          # AutoCut's own cut, untouched
            elif ok and sequence == ok[-1]['sequence']:
                pick = ok[-1]
            if pick is None:
                raise CleanEditError('Approve the latest build of the current edits. Build again after changing words.')
            revision = self.revision(st)['digest']
            _, _, _, timeline_digest = self.verified_clean_map(pid, st, pick['sequence'])
            previous = st.get('approved') or {}
            candidate = dict(sequence=pick['sequence'], by=str(by)[:40], at=now(),
                             edits_digest=ep.digest(st['edits']), source_revision=revision, timeline_digest=timeline_digest)
            if all(previous.get(k) == candidate[k] for k in ('sequence', 'by', 'edits_digest', 'source_revision', 'timeline_digest')):
                candidate = previous
            st['approved'] = candidate
            entry, _ = self.prepare_clean_transcript(pid, st)
            if not any(t['digest'] == entry['digest'] for t in st.get('transcripts', [])):
                st.setdefault('transcripts', []).append(entry)
            st['clean_transcript'] = entry
            self._save(pid, st)
        return self.view(pid)

    def approved(self, pid):
        st = self._load(pid)
        if not st['approved']:
            raise CleanEditError('Josh hasn\'t approved the clean cut yet, so animations and assembly wait.')
        return st['approved']['sequence']



from . import clean_edit_more  # noqa: E402,F401  previews and versioned transcripts, added onto CleanEdit
