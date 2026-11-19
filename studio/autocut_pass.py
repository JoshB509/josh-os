"""Tariq's clean pass with Josh's installed AutoCut, the same way every time.

Stages, each recorded in <evidence>/autocut-pass.json so a run picks up where it stopped and never repeats a cut:
  1 raw       A fresh AUTOCUT_RAW_rNN: GREEN_MASTER's keyed nest (key, shadow and look come with the clone) plus the
              good mic on A1, in sync, uncut over the whole camera take. Built by the panel's tq_build_cut, which refuses
              an existing name. GREEN_MASTER and every other sequence are never touched.
  2 silences  AutoCut Silences, whole timeline, A1 only, Josh's defaults (studio/autocut_driver.py). Read back after.
  3 repeat    AutoCut Repeat, whole timeline, A1 only, English, default mode. Read back after.
  4 native    CareerOS requests Premiere's native source-microphone transcription through Josh Cut Pro,
              polls its job, and verifies the microphone's transcript in the saved project before review.
  5 review    review.html and review.json from Premiere's words (studio/autocut_review.py).
  6 approved  Josh's review, recorded with approve(). Nothing downstream (animations, assembly) may start before it.

Stages 2 and 3 cut, so they need cut_authorized_by: who allowed AutoCut to cut this recording's copy, recorded as given.
Every AutoCut cut is also checked three ways: AutoCut's finished dialog, one new /batch-cut-and-delete in its host log,
and Premiere's read-back (video and mic still paired with one offset, no gaps or overlaps).
"""
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from . import autocut_driver as ad
from . import autocut_review
from . import premiere_transcript as pt

T = 254016000000
SR = 16000


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class PassError(RuntimeError):
    pass


class NativeTranscriptBlocked(PassError):
    """A resumable handoff blocker, not a failed or repeated AutoCut pass."""
    setup_state = 'blocked'



def readback(driver, sequence):
    """Premiere's read-back of a cut sequence: A1 pieces, gaps, overlaps, and whether V1 and A1 still move together."""
    project, info = driver.premiere_ops([dict(op='project'), dict(op='info', seq=sequence)])
    v = sorted([i for i in info['items'] if i['kind'] == 'v' and i['track'] == 0], key=lambda i: int(i['start']))
    a = sorted([i for i in info['items'] if i['kind'] == 'a' and i['track'] == 0], key=lambda i: int(i['start']))
    pieces = [dict(seq_start=int(x['start']) / T, seq_end=int(x['end']) / T, mic_in=int(x['inp']) / T,
                   mic_out=(int(x['inp']) + int(x['end']) - int(x['start'])) / T) for x in a]
    gaps = [(p['seq_end'], q['seq_start']) for p, q in zip(pieces, pieces[1:]) if q['seq_start'] - p['seq_end'] > 1e-6]
    over = [(p['seq_end'], q['seq_start']) for p, q in zip(pieces, pieces[1:]) if q['seq_start'] - p['seq_end'] < -1e-6]
    pair = [(int(x['start']) == int(y['start']) and int(x['end']) == int(y['end']), int(x['inp']) - int(y['inp'])) for x, y in zip(a, v)]
    sync = dict(count_match=len(a) == len(v), edges_match=all(p[0] for p in pair), offsets=sorted({p[1] for p in pair}))
    problems = []
    if not pieces:
        problems.append('nothing on A1')
    if gaps or over:
        problems.append(f'{len(gaps)} gaps and {len(over)} overlaps')
    if not (sync['count_match'] and sync['edges_match'] and len(sync['offsets']) == 1):
        problems.append(f'video and mic no longer move together: {sync}')
    backups = [s for s in project['sequences'] if s.startswith('AutoCut-Backup') and sequence in s]
    return dict(sequence=sequence, at=now(), pieces=pieces, gaps=gaps, overlaps=over, sync=sync, problems=problems,
                length_s=pieces[-1]['seq_end'] if pieces else 0, autocut_backups=backups, info=info)


class AutoCutPass:
    def __init__(self, evidence, prproj, mic_path, pcm_path, sync_offset_s, mic_samples, frame_ticks, master='GREEN_MASTER',
                 project_id='', driver=None, say=print):
        self.ev = Path(evidence)
        self.ev.mkdir(parents=True, exist_ok=True)
        self.prproj, self.mic, self.pcm = str(prproj), str(mic_path), str(pcm_path)
        self.offset, self.samples, self.frame = float(sync_offset_s), int(mic_samples), int(frame_ticks)
        self.master, self.project_id = master, project_id
        self.driver = driver or ad.Driver(log=say)
        self.say = say
        self.state_file = self.ev / 'autocut-pass.json'
        self.state = json.loads(self.state_file.read_text()) if self.state_file.is_file() else dict(stages={}, created=now())

    def _save(self):
        tmp = self.state_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.state, indent=1, default=str))
        tmp.replace(self.state_file)

    def _done(self, stage, record):
        self.state['stages'][stage] = dict(record, done=now())
        self._save()
        self.say(f'{stage}: done')

    def stage(self, name):
        return self.state['stages'].get(name)

    # ------------------------------------------------------------------------ 1 raw
    def raw(self):
        if self.stage('raw'):
            return self.stage('raw')
        from dajoshi import edit_plan as ep
        project, master = self.driver.premiere_ops([dict(op='project'), dict(op='info', seq=self.master)])
        if str(master['frame_ticks']) != str(self.frame):
            raise PassError(f"{self.master} runs at {master['frame_ticks']} ticks a frame, not {self.frame}.")
        if any(i['kind'] == 'a' for i in master['items']):
            raise PassError(f'{self.master} has audio on it, which would bring camera sound into the copy.')
        taken = {int(m.group(1)) for s in project['sequences'] for m in [re.fullmatch(r'AUTOCUT_RAW_r(\d{2})', s)] if m}
        name = f'AUTOCUT_RAW_r{(max(taken) + 1) if taken else 1:02d}'
        cam_len = max(int(i['end']) for i in master['items']) / T
        a = max(0, int(self.offset * SR)) + SR // 10                      # 0.1 s inside both files at each end
        b = min(self.samples, int((self.offset + cam_len) * SR)) - SR // 10
        if b <= a:
            raise PassError("Camera and microphone have no usable overlapping recording.")
        m = ep.output_map([[a, b]], SR, self.offset, self.frame)
        plan = dict(project_id=self.project_id, proposal_id='autocut-raw', proposal_digest='autocut-raw', revision=0, camera_path='',
                    mic_path=self.mic, master=self.master, sequence_name=name, map=m, map_digest=ep.digest(m))
        self.say(f'Building {name} from {self.master} and the good mic')
        import urllib.request
        req = urllib.request.Request(self.driver.bridge + '/bridge/call', data=json.dumps(dict(cmd='tq_build_cut', args=dict(plan=plan, request_id=name))).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
        out = json.loads(urllib.request.urlopen(req, timeout=1800).read())
        res = out.get('result') or {}
        if res.get('sequence') != name or res.get('gaps') or res.get('overlaps'):
            raise PassError(f'Premiere did not build {name} cleanly: {json.dumps(out)[:400]}')
        self.driver.premiere_ops([dict(op='save')])
        self._done('raw', dict(sequence=name, plan=plan, result=res))
        return self.stage('raw')

    # ------------------------------------------------------------------------ 2 and 3
    def _cut_stage(self, stage, cut_authorized_by, run):
        if self.stage(stage):
            return self.stage(stage)
        if not str(cut_authorized_by or '').strip():
            raise PassError(f'{stage} cuts the copy, so say who allowed it (cut_authorized_by).')
        seq = self.stage('raw')['sequence']
        if stage == 'repeat' and not self.stage('silences'):
            raise PassError('Josh\'s order is Silences first, then Repeat.')
        rec = run(seq)
        rb = readback(self.driver, seq)
        if rb['problems']:
            self._done(stage + '_failed', dict(autocut=rec, readback=rb))
            raise PassError(f'After {stage}, {seq} has problems: {"; ".join(rb["problems"])}. Restore AutoCut\'s backup '
                            f'({rb["autocut_backups"][-1] if rb["autocut_backups"] else "none found"}) before going on.')
        self.driver.premiere_ops([dict(op='save')])
        (self.ev / f'after-{stage}-readback.json').write_text(json.dumps(rb, indent=1, default=str))
        self._done(stage, dict(autocut=rec, readback={k: v for k, v in rb.items() if k != 'info'}, cut_authorized_by=str(cut_authorized_by)))
        return self.stage(stage)

    def silences(self, cut_authorized_by):
        return self._cut_stage('silences', cut_authorized_by, lambda seq: self.driver.silences(seq, cut=True))

    def repeat(self, cut_authorized_by):
        return self._cut_stage('repeat', cut_authorized_by, lambda seq: self.driver.repeat(seq, generate=True, cut=True))

    # ------------------------------------------------------------------------ 4 and 5
    def native(self, progress=None, cancelled=None, timeout=900):
        import uuid
        from . import transcript_worker as tw, native_background as nb
        progress = progress or (lambda message: None)
        cancelled = cancelled or (lambda: None)
        cancelled()
        # Save and decode only when the exact project is still open.
        project = self.driver.premiere_ops([dict(op='project')])[0]
        if pt.media_key(project.get('project')) != pt.media_key(self.prproj):
            raise NativeTranscriptBlocked('The active Premiere project changed. Native transcription was not requested.')
        save_result = self.driver.premiere_ops([dict(op='save')])
        selected = pt.microphone_transcript(pt.words(self.prproj), self.mic)
        handoff = tw.RequestStore(self.ev / 'native-ui-request.json')
        if handoff.exists():
            try:
                handoff.check(self._native_ui_identity(), readback(self.driver, self.stage('raw')['sequence'])['info'])
                if selected:
                    handoff.verify(self._native_ui_identity(), readback(self.driver, self.stage('raw')['sequence'])['info'], selected,
                                   saved=any(r.get('saved') is True for r in save_result))
                elif handoff.read()['state'] not in ('pending', 'observing') or not nb.capabilities().get('enabled'):
                    raise tw.WorkerBlocked('The existing source-transcription worker request is retained. Verify its outcome before any new dispatch; no saved microphone transcript is present yet.')
            except tw.WorkerBlocked as exc:
                raise NativeTranscriptBlocked(str(exc)) from exc
        if not selected:
            progress('Checking native microphone transcription support in Josh Cut Pro')
            try:
                capabilities = (dict(supported=False, reason='Existing native observation request retained.')
                                if handoff.exists() else self.driver.native_transcript(dict(action='capabilities')))
                if not capabilities.get('supported'):
                    try:
                        rb = readback(self.driver, self.stage('raw')['sequence'])
                        if rb['problems']:
                            raise tw.WorkerBlocked('Current cut readback is not valid: ' + '; '.join(rb['problems']))
                        request = handoff.prepare(self._native_ui_identity(), rb['info'])
                    except tw.WorkerBlocked as exc:
                        raise NativeTranscriptBlocked(str(exc)) from exc
                    automatic = nb.capabilities()
                    if automatic.get('enabled'):
                        progress('Premiere automatic transcription is enabled; waiting for the selected microphone native words.')
                        try:
                            selected = nb.wait(handoff, self._native_ui_identity(), rb['info'], self.driver,
                                lambda: readback(self.driver, self.stage('raw')['sequence'])['info'],
                                lambda: pt.words(self.prproj), progress, cancelled, timeout=timeout)
                        except tw.WorkerBlocked as exc:
                            raise NativeTranscriptBlocked(str(exc)) from exc
                    else:
                        message = ((capabilities.get('reason') or 'Native transcription API unavailable.') + ' ' +
                            automatic.get('reason', '') + ' A source-transcription request is saved (' +
                            request['request_id'] + ', ' + request['state'] + '). No supported desktop worker is connected; no UI action was dispatched. ' +
                            'Resume verifies the saved microphone transcript and unchanged cut before cleanup.')
                        progress(message)
                        raise NativeTranscriptBlocked(message)
                else:
                    request = self.state.get('native_request')
                    action = 'status'
                    if not request:
                        request = dict(request_id=str(uuid.uuid4()), expected_project=self.prproj, mic_path=self.mic)
                        self.state['native_request'] = request
                        self._save()  # Persist before dispatch; an uncertain request must never be resubmitted.
                        action = 'start'
                    deadline = time.monotonic() + timeout
                    while True:
                        cancelled()
                        result = self.driver.native_transcript(dict(request, action=action))
                        action = 'status'
                        if result.get('state') == 'done':
                            if (pt.media_key(result.get('project')) != pt.media_key(self.prproj)
                                    or pt.media_key(result.get('mic')) != pt.media_key(self.mic) or result.get('saved') is not True):
                                raise NativeTranscriptBlocked('Native transcription returned an unverified project or microphone identity.')
                            break
                        if result.get('state') != 'running':
                            raise NativeTranscriptBlocked(result.get('message') or 'The native transcript request is missing or blocked. Its outcome must be checked before retrying.')
                        progress('Premiere is transcribing the selected microphone. AutoCut Silences and Repeat are complete.')
                        if time.monotonic() >= deadline:
                            raise NativeTranscriptBlocked('Premiere has not finished native transcription. The request is retained; resume checks it without repeating cuts or submitting another request.')
                        time.sleep(2)
            except ad.AutoCutError as exc:
                raise NativeTranscriptBlocked(str(exc)) from exc
            cancelled()
            selected = pt.microphone_transcript(pt.words(self.prproj), self.mic)
        if not selected:
            raise NativeTranscriptBlocked('Premiere did not save a native transcript bound to the selected microphone. Both AutoCut passes are preserved.')
        words = [w for w in selected['words'] if w['word'].strip()]
        if not words:
            raise NativeTranscriptBlocked('The selected microphone transcript contains no words. Clean Cut review cannot be built yet.')
        (self.ev / 'premiere-native-words-mic.json').write_text(json.dumps([selected], indent=1))
        self._done('native', dict(media=self.mic, words=len(words), first=words[:3], last=words[-3:]))
        return self.stage('native')

    def _native_ui_identity(self):
        from . import transcript_worker as tw
        return dict(project_id=self.project_id, run=str(self.ev.resolve()),
                    project=pt.media_key(self.prproj), mic=pt.media_key(self.mic),
                    mic_sha256=tw.file_digest(self.mic), sequence=self.stage('raw')['sequence'])

    def review(self):
        for need in ('silences', 'repeat', 'native'):
            if not self.stage(need):
                raise PassError(f'The review needs the {need} stage first.')
        after_sil = json.loads((self.ev / 'after-silences-readback.json').read_text())['pieces']
        clean = json.loads((self.ev / 'after-repeat-readback.json').read_text())['length_s']
        summary = autocut_review.build(self.prproj, self.stage('raw')['sequence'], self.mic, self.pcm, self.ev,
                                       self.stage('raw')['plan']['map']['pieces'][0], after_sil, clean, say=self.say)
        self._done('review', dict(summary=summary, page=str(self.ev / 'review.html'), waiting_for='Josh'))
        return self.stage('review')

    def cleanup(self):
        """Tariq's second pass after Silences then Repeat: restarts, filler clusters, abandoned starts and non-speech
        candidates, with a review pack. It proposes only and never cuts; Josh decides each on the Script tab."""
        from . import cleanup_review
        if not self.stage('review'):
            raise PassError('The cleanup pass runs after the AutoCut review.')
        full = cleanup_review.run_pass(self.ev, self.pcm, self.frame, say=self.say)
        self._done('cleanup', dict(folder=full['folder'], proposals=full['proposals'] and len(full['proposals']),
                                   buildable=full['buildable'], nonspeech=full['nonspeech'], listened=False, waiting_for='Josh'))
        return self.stage('cleanup')

    def run(self, cut_authorized_by):
        """Every stage in order, stopping at the first that can't go on. Ends waiting for Josh's review."""
        self.raw()
        self.silences(cut_authorized_by)
        self.repeat(cut_authorized_by)
        self.native()
        review = self.review()
        try:
            self.cleanup()                          # proposals for Josh; a failure here never undoes AutoCut's review
        except Exception as e:  # noqa: BLE001
            self.say(f'The cleanup pass could not run: {str(e)[:200]}')
        return review

    # ------------------------------------------------------------------------ 6
    def approve(self, by, note=''):
        if not self.stage('review'):
            raise PassError('There is no review for Josh to approve yet.')
        self._done('approved', dict(by=str(by), note=str(note)[:2000], sequence=self.stage('raw')['sequence']))
        return self.stage('approved')

    def approved_sequence(self):
        """For animations and assembly: the clean sequence, only once Josh's approval is recorded."""
        if not self.stage('approved'):
            raise PassError('Josh hasn\'t approved this AutoCut pass yet, so nothing downstream may use it.')
        return self.stage('approved')['sequence']
