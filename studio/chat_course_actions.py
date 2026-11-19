"""Bounded course chat actions. No shell, arbitrary paths, approvals or delivery.

The caller must supply an authorization callback derived from the CURRENT owner
message, not chat history, retrieved text, or a model's confirmation fields.
It must authorize the complete action, including lesson/version and payload.
Unimplemented actions fail closed; the recording UI remains their safe route.
"""
import hashlib
import json
import re
from datetime import date

from .store import Conflict


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


SUPPORTED = {'cues', 'plan', 'use_latest', 'take_audio_check'}
GUARDED = {'check_slides', 'done', 'handover', 'import', 'take', 'associate_session'}


def current_request_authorizes(action, message):
    """Conservative fallback: direct commands only, exact IDs and payload.

    Pass only the authenticated owner's latest message, never a transcript or
    model summary. Titles/implicit references need a trusted caller resolver.
    Fingerprints are supplied by the fresh snapshot, not typed by the owner.
    """
    if not isinstance(message, str) or not isinstance(action, dict):
        return False
    if re.search(r"\?|\b(?:not|never|don't|do not|cancel|stop|maybe|if|could|would|should)\b", message, re.I):
        return False
    lid = re.escape(str(action.get('lesson_id', '')))
    prefix = r'(?:please\s+)?'
    if action.get('action') == 'cues':
        match = re.fullmatch(prefix + r'save cues (?:for )?' + lid + r':\s*([\s\S]+)',
                             message.strip(), flags=re.I)
        return bool(match and match.group(1) == action.get('text'))
    patterns = {
        'plan': prefix + r'(?:set|schedule) (?:the )?recording date (?:for )?' + lid + r' (?:to|on) ' + re.escape(str(action.get('date', ''))) + r'\.?',
        'use_latest': prefix + r'use (?:the )?latest (?:take|recording) (?:for )?' + lid + r'\.?',
        'take_audio_check': prefix + r'(?:recheck|check) audio (?:for )?' + lid + r' take ' + re.escape(str(action.get('take_id', ''))) + r'\.?',
    }
    pattern = patterns.get(action.get('action'))
    return bool(pattern and re.fullmatch(pattern, message.strip(), flags=re.I))
COURSE_ACTION_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'action': {'type': 'string', 'enum': sorted(SUPPORTED | GUARDED)},
        'lesson_id': {'type': 'string'},
        'revision': {'type': 'integer'},
        'source_hash': {'type': 'string'},
        'state_hash': {'type': 'string'},
        'text': {'type': 'string'},
        'date': {'type': 'string'},
        'take_id': {'type': 'string'},
        'take_sha256': {'type': 'string'},
    },
    'required': ['action', 'lesson_id', 'revision', 'source_hash', 'state_hash'],
}


def _recording_snapshot(recording, lid):
    """Read identity without state(), which schedules audio validation jobs."""
    recording.entry(lid)
    with recording.store.connect() as db:
        row = db.execute('SELECT body FROM lesson_state WHERE id=?', (lid,)).fetchone()
        takes = [json.loads(r['body']) for r in db.execute(
            'SELECT body FROM lesson_takes WHERE lesson_id=? ORDER BY id', (lid,))]
    return {'state': json.loads(row['body']) if row else {}, 'takes': takes}


def course_action_context(courses, lid):
    """Attach to the current lesson context before asking for a model proposal."""
    lesson = courses._get(lid)
    cues = courses.cues(lid)
    recording = (_recording_snapshot(courses.recording, lid)
                 if courses.recording is not None else None)
    return dict(lesson_id=lid, revision=lesson['revision'],
                source_hash=fingerprint(lesson.get('script', '')),
                state_hash=fingerprint({'cues': cues, 'recording': recording}))


def dispatch_course_action(courses, agent_id, action, *, authorize=None):
    """Execute one exact authorized local action or raise without mutating it.

    `authorize` is intentionally required out-of-band and never in the schema.
    Returning True asserts authorization of ALL values, not merely an action verb.
    The course lock protects revision/cues checks. Recording operations without
    backend compare-and-swap or explicit confirmation remain guarded.
    """
    if agent_id != 'saskia':
        raise ValueError('Only Saskia handles course chat actions.')
    if not isinstance(action, dict):
        raise ValueError('A course action object is required.')
    action = dict(action)
    if set(action) - set(COURSE_ACTION_SCHEMA['properties']):
        raise ValueError('Unknown course action fields; confirmations cannot come from the model.')
    if any(k not in action for k in COURSE_ACTION_SCHEMA['required']):
        raise ValueError('Exact lesson, revision and source/state hashes are required.')
    name = action['action']
    if not isinstance(name, str) or name not in SUPPORTED | GUARDED:
        raise ValueError('Unknown or forbidden course action.')
    if name in GUARDED:
        raise ValueError('Use the lesson recording/course page for this action: exact source, session or owner confirmation gates are not yet available in chat.')
    lid = action['lesson_id']
    if not isinstance(lid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', lid):
        raise ValueError('An exact lesson ID is required, not a path or title.')
    if type(action['revision']) is not int:
        raise ValueError('An integer saved revision is required.')
    for key in ('source_hash', 'state_hash'):
        if not isinstance(action[key], str) or not re.fullmatch('[0-9a-f]{64}', action[key]):
            raise ValueError('A current source/state fingerprint is required.')
    fields = {'cues': {'text'}, 'plan': {'date'}, 'use_latest': set(),
              'take_audio_check': {'take_id', 'take_sha256'}}[name]
    if set(action) != set(COURSE_ACTION_SCHEMA['required']) | fields:
        raise ValueError('Supply exactly the fields for this course action.')
    if name == 'cues' and (not isinstance(action['text'], str) or not action['text'].strip()
                           or len(action['text']) > 300_000):
        raise ValueError('Provide a nonempty cued script; deleting cues stays on the course page.')
    if name == 'plan':
        value = action['date']
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('Provide the exact recording date YYYY-MM-DD.')
        date.fromisoformat(value)
    if not callable(authorize) or authorize(dict(action)) is not True:
        raise ValueError('This exact course action needs a current explicit owner request.')
    with courses.lock:
        current = course_action_context(courses, lid)
        if any(action[k] != current[k] for k in current):
            raise Conflict('Lesson or recording changed. Refresh and request the exact current version.')
        if name == 'cues':
            result = courses.save_cues(lid, action['text'])
        else:
            rec = courses.recording
            if rec is None:
                raise ValueError('Lesson recording is unavailable.')
            if name == 'plan':
                result = rec.plan(lid, action['date'])
            elif name == 'use_latest':
                # Explicitly opts back into automatic selection; native backend
                # clears recorded_completion instead of falsely approving a take.
                result = rec.use_latest(lid)
            else:
                takes = _recording_snapshot(rec, lid)['takes']
                take = next((t for t in takes if t['id'] == action['take_id']), None)
                if not take or take.get('sha256') != action['take_sha256']:
                    raise Conflict('The exact saved take is unavailable or changed.')
                result = rec.take_action(lid, dict(action='audio_check', take_id=take['id']))
    return {'status': 'completed', 'action': name, 'lesson_id': lid, 'result': result}
