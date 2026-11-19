"""Bind deck authoring and resulting files to the live approved clean transcript.

Authoring provenance is not storyboard approval or a claim of editorial quality.
No Premiere mutations, media processing, user-note edits or automatic approval.
"""
import json
from pathlib import Path

from .clean_edit import CleanEditError
from .clean_edit_more import _transcript_digest
from .transcript_worker import digest, file_digest


def capture(flow, pid):
    folder = Path(flow.folder(pid))
    if not (folder / 'clean-edit.json').is_file():
        return None  # Existing legacy projects retain their own approval route.
    flow.clean_gate(pid)
    clean = getattr(flow, 'clean_edit', None)
    if clean is None:
        raise CleanEditError('Approved clean transcript service is unavailable; deck authoring did not start.')
    ci = clean.animation_inputs(pid)  # Current host, exact A1/V1 cut, approval and transcript hash guards.
    st = clean._load(pid)
    entry = st.get('clean_transcript') or {}
    if (entry.get('digest') != ci['provenance']['clean_transcript'] or
            st.get('approved') != ci['provenance']['approved']):
        raise CleanEditError('The approval changed while preparing the deck brief.')
    path = Path(entry['file']).resolve()
    if path.parent != (folder / 'transcripts').resolve():
        raise CleanEditError('The clean transcript is outside this project.')
    body = json.loads(path.read_text(encoding='utf-8'))
    if (body.get('digest') != entry['digest'] or _transcript_digest(body) != entry['digest'] or
            body.get('approved') != st['approved'] or body.get('map') != ci['map']):
        raise CleanEditError('The approved transcript changed while preparing the deck brief.')
    words = [{k: w[k] for k in ('id', 'text', 'start', 'end', 'partial') if k in w} for w in body['words']]
    if not words:
        raise CleanEditError('The approved clean transcript has no retained words.')
    out = dict(version=1, project_id=pid, sequence=ci['sequence'], provenance=ci['provenance'],
               words=words, notes=str((flow.anim_notes(pid) or {}).get('text') or ''),
               timing='Seconds on the approved cut, not raw microphone time.')
    out['digest'] = digest(out)
    return out


def brief(source):
    if source is None:
        return ''
    return ('AUTHORITATIVE APPROVED CLEAN TRANSCRIPT\n'
            'Use only these retained words as the spoken script for the deck and shot list. '
            'The teleprompter, original written script and raw transcript are not authoring sources for this job. '
            'Quote exact retained phrases for every press. start/end are seconds on the approved cut. '
            'partial marks a word clipped at an existing join; do not repair the spoken content or timeline. '
            'Josh\'s notes below guide visuals, not edits to the approved speech. '
            'Create slides and a shot list for review; do not approve, place, export or publish them.\n' +
            json.dumps(source, ensure_ascii=False))


def files(review, pid):
    root = Path(review.project_folder(pid)).resolve()
    paths = review.deck_files(pid)
    if len(paths) != 2 or not all(paths):
        raise CleanEditError('Both deck and shot list are required before authoring can be verified.')
    out = []
    for p in paths:
        p = Path(p).resolve()
        if p.parent != root:
            raise CleanEditError('The deck output is outside this project.')
        out.append(dict(path=str(p), sha256=file_digest(p)))
    return out


def record(flow, review, pid, source):
    if source is None:
        return None
    if capture(flow, pid) != source:
        raise CleanEditError('The approved transcript, timeline or notes changed during deck authoring. Output is retained but unverified.')
    value = dict(version=1, state='source-bound', source=source, outputs=files(review, pid))
    from .edit_flow import write_atomic
    write_atomic(Path(flow.folder(pid)) / 'deck-authoring.json', value)
    return value


def require(flow, review, pid):
    current = capture(flow, pid)
    if current is None:
        return None
    path = Path(flow.folder(pid)) / 'deck-authoring.json'
    if not path.is_file():
        raise CleanEditError('This deck has no verified approved-transcript authoring handoff. Preserve it and run a verified correction before placement.')
    saved = json.loads(path.read_text(encoding='utf-8'))
    if saved.get('state') != 'source-bound' or saved.get('source') != current or saved.get('outputs') != files(review, pid):
        raise CleanEditError('The deck, shot list, notes or approved cut changed after authoring verification. Placement is blocked.')
    return saved


def record_correction(flow, review, pid, audit):
    """Explicit migration of preserved visuals with audited timing, never retroactive original authorship.

    This does not grant storyboard approval. It binds only the exact corrected files,
    current clean transcript and notes after live timing validation.
    """
    source = capture(flow, pid)
    if source is None or audit.get('provenance') != source['provenance']:
        raise CleanEditError('Correction belongs to a different approved cut.')
    outputs = files(review, pid)
    if audit.get('kind') != 'bounded-anchor-correction' or audit.get('outputs') != outputs:
        raise CleanEditError('Correction files changed or were not audited.')
    originals = audit.get('originals') or []
    root = Path(review.project_folder(pid)).resolve()
    if len(originals) != 2:
        raise CleanEditError('Correction must preserve both original files.')
    for entry in originals:
        path = Path(entry['path']).resolve()
        if path.parent != root or file_digest(path) != entry['sha256']:
            raise CleanEditError('Original deck changed after the correction audit.')
    if originals[0]['sha256'] != outputs[0]['sha256']:
        raise CleanEditError('Timing correction changed the visual content.')
    from . import edit_animations as ea
    from .edit_review import parse_scenes, parse_shot_list
    by_id = {w['id']: w for w in source['words']}
    for change in audit.get('changes') or []:
        words = [by_id.get(wid) for wid in change.get('word_ids') or []]
        if not words or any(w is None or w.get('partial') for w in words):
            raise CleanEditError('Correction anchor contains a missing or clipped word.')
        if ([ea.norm(w['text']) for w in words] != ea.tokens(change['new']) or
                words[0]['start'] != change['start'] or words[-1]['end'] != change['end']):
            raise CleanEditError('Correction anchor no longer matches the approved transcript.')
    ci = flow.clean_edit.animation_inputs(pid)
    slides, shotfile = review.deck_files(pid)
    md = shotfile.read_text(encoding='utf-8')
    scenes = {s['n']: s for s in parse_scenes(slides.read_text(encoding='utf-8'))}
    parts = parse_shot_list(md)
    shots, problems = ea.plan_shots(ci['words'], ci['removals'], ci['map']['pieces'], parts, scenes, ci['map']['frame_ticks'])
    issues = ea.deck_gate(md, parts, scenes, shots, ci['words'], ci['removals'], ci['map']['pieces'], ci['map']['frame_ticks'])
    if not shots or problems or issues or shots != audit.get('shots'):
        raise CleanEditError('Corrected storyboard timing did not pass the current-cut audit.')
    if capture(flow, pid) != source or files(review, pid) != outputs:
        raise CleanEditError('Source or output changed during correction validation.')
    value = dict(version=1, state='source-bound', source=source, outputs=outputs,
                 provenance_kind='verified-timing-correction',
                 original_authoring_source=audit.get('original_authoring_source'),
                 original_outputs=originals, correction_audit_digest=digest(audit),
                 note='Preserved visuals; corrected timing verified against approved transcript. No storyboard approval granted.')
    from .edit_flow import write_atomic
    write_atomic(Path(flow.folder(pid)) / 'deck-authoring.json', value)
    return value
