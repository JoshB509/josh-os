"""Route an explicitly requested edit through the existing AutoCut pass, never the legacy planner."""
import json
from pathlib import Path

from .store import Conflict


def run(flow, pid, src, premiere, authorized_by, pass_factory=None):
    from .autocut_pass import AutoCutPass
    from .edit_flow import write_atomic
    from dajoshi.media import sync

    if not str(authorized_by or '').strip():
        raise Conflict('AutoCut needs the explicit go-ahead for this recording.')
    if premiere.get('state') not in ('built', 'exists') or not Path(premiere.get('project', '')).is_file():
        raise Conflict('Finish the Premiere setup before starting AutoCut.')
    clean = getattr(flow, 'clean_edit', None)
    if clean is None:
        raise Conflict('The clean-cut review service is unavailable. No AutoCut pass was started.')
    active_path = flow.folder(pid) / 'active-edit-run.json'
    active = json.loads(active_path.read_text(encoding='utf-8')) if active_path.is_file() else {}
    ev = flow.folder(pid) / 'autocut' / (active.get('id') or src['digest'][:24])
    ev.mkdir(parents=True, exist_ok=True)
    journal = ev / 'setup-job.json'
    job = json.loads(journal.read_text(encoding='utf-8')) if journal.exists() else {}
    job.setdefault('state', 'preparing')
    write_atomic(journal, job)
    files = src['payload']['files']

    def check_sources():
        if (flow.folder(pid) / 'AUTOCUT_CANCEL').exists():
            raise Conflict('AutoCut cancelled between stages. Completed work was preserved.')
        if (flow.latest(pid, 'edit_sources') or {}).get('id') != src['id']:
            raise Conflict('The chosen recording changed. AutoCut stopped before the next stage.')
        reason = flow.sources_ok(src)
        if reason:
            raise Conflict(reason)

    check_sources()
    flow._setup_step(pid, 'sync', 'running', 'Checking camera and good microphone sync; no local transcription')
    hashes = {key: flow._hash(ident) for key, ident in files.items()}
    mic = flow._decode(files['mic']['path'], hashes['mic'], ev)
    camera = flow._decode(files['camera']['path'], hashes['camera'], ev)
    sync_report = sync(camera, mic)
    if not sync_report.get('verified'):
        raise Conflict('Camera and microphone sync is not verified. AutoCut has not cut anything.')
    pcm = flow._cache() / f"pcm16k-{hashes['mic'][:24]}.f32"
    if not pcm.is_file():
        raise Conflict('The verified microphone PCM is missing. AutoCut has not started.')
    flow._setup_step(pid, 'sync', 'done', 'Camera and good microphone sync verified')
    # Inspect the exact open project before a pass can create or cut a sequence.
    from .autocut_driver import Driver
    driver = getattr(flow, 'autocut_driver_factory', Driver)()
    project, master = driver.premiere_ops([dict(op='project'), dict(op='info', seq='GREEN_MASTER')])
    normal = lambda value: str(value).replace('\\\\?\\', '').replace('\\', '/').casefold()
    if normal(project.get('project', '')) != normal(premiere['project']):
        raise Conflict('Premiere has another project open. No AutoCut sequence was created.')
    frame = int(master['frame_ticks'])
    config = dict(project_id=pid, sources_id=src['id'], sources_digest=src['digest'], hashes=hashes,
                  project=str(premiere['project']), master='GREEN_MASTER', frame_ticks=frame,
                  mic_samples=len(mic), sync_offset_s=sync_report['offset_seconds'])
    if job.get('config') and job['config'] != config:
        raise Conflict('The saved AutoCut pass no longer matches this recording. Existing work was retained.')
    job.update(config=config, authorized_by=str(authorized_by))
    write_atomic(journal, job)
    pass_ = (pass_factory or AutoCutPass)(ev, premiere['project'], files['mic']['path'], str(pcm),
                                        sync_report['offset_seconds'], len(mic), frame, project_id=pid,
                                        driver=driver, say=lambda message: None)
    pending = job.get('pending_stage')
    if pending and not pass_.stage(pending):
        raise Conflict(f'The previous AutoCut {pending} attempt has an uncertain outcome. Inspect its evidence before retrying; it was not repeated.')
    job.pop('pending_stage', None)
    write_atomic(journal, job)
    if not pass_.stage('repeat'):
        driver.ensure_open()

    def stage(key, label, call, destructive=False, skip_completed=True):
        check_sources()
        opened = driver.premiere_ops([dict(op='project')])[0]
        if normal(opened.get('project', '')) != normal(premiere['project']):
            raise Conflict('Premiere has another project open. AutoCut stopped before the next stage.')
        ui_key = 'transcribe' if key == 'native' else 'cut' if key == 'review' else key
        if skip_completed and pass_.stage(key):
            flow._setup_step(pid, ui_key, 'done', label + ' already completed')
            return pass_.stage(key)
        flow._setup_step(pid, ui_key, 'running', label)
        job.update(state='running', stage=key, message=label)
        if destructive:
            job['pending_stage'] = key
        write_atomic(journal, job)
        try:
            result = call()
        except Exception as exc:
            job.update(state=getattr(exc, 'setup_state', 'failed'), stage=key, message=str(exc))
            write_atomic(journal, job)
            flow._setup_step(pid, ui_key, job['state'], str(exc))
            raise
        job.pop('pending_stage', None)
        write_atomic(journal, job)
        flow._setup_step(pid, ui_key, 'done', label)
        return result

    stage('raw', 'Creating a fresh AutoCut copy of GREEN_MASTER', pass_.raw, True)
    stage('silences', 'AutoCut Silences on the copy, good microphone A1 only', lambda: pass_.silences(authorized_by), True)
    stage('repeat', 'AutoCut Repeat on the copy, good microphone A1 only', lambda: pass_.repeat(authorized_by), True)
    stage('native', 'Preparing Premiere native microphone transcript', lambda: pass_.native(
        progress=lambda message: flow._setup_step(pid, 'transcribe', 'running', message), cancelled=check_sources))
    stage('review', 'Preparing the AutoCut clean-cut review', pass_.review)
    check_sources()
    sequence = pass_.stage('raw')['sequence']
    # Retrying a completed pass must preserve Josh's edits and approval.
    clean_file = flow.folder(pid) / 'clean-edit.json'
    existing = json.loads(clean_file.read_text(encoding='utf-8')) if clean_file.exists() else None
    if not existing or normal(existing.get('evidence', '')) != normal(ev):
        clean.register(pid, ev, premiere['project'], files['mic']['path'], str(pcm), sequence, frame)
    # Use the registered edit service so proposals bind to Josh's current edits and appear in Clean Cut.
    # Do not mark AutoCutPass.cleanup here: changing its evidence file would invalidate that revision.
    stage('cleanup', 'Checking restarts, fillers and non-speech sounds for review',
          lambda: clean.cleanup(pid, reuse=True), skip_completed=False)
    check_sources()
    job.update(state='review', sequence=sequence, message='Clean cut and cleanup proposals are ready for review')
    write_atomic(journal, job)
    return dict(sequence=sequence, evidence=str(ev), state='review')
