"""CareerOS aggregate-only daily activity. No learner enumeration or delivery.

Window completeness is separate from metric tracking completeness. Cache only validated
raw aggregate tool results, never the model's final text; retain last-good data on failure.
"""
import json
import math
import os
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
DUBAI = timezone(timedelta(hours=4), 'Asia/Dubai')
TOOL = 'mcp__careeros__get_learning_activity_summary'
METRICS = ('active_learners', 'minutes_learned', 'new_questions', 'new_reviews')


def stamp(value):
    t = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('Source timestamp requires an offset')
    return t.astimezone(UTC)


def request_for(at):
    today = at.astimezone(DUBAI).date()
    return dict(start=(today-timedelta(days=1)).isoformat()+'T00:00', end=today.isoformat()+'T00:00', time_zone='Asia/Dubai')


def validate(raw, requested, collected_at):
    """Validate the actual endpoint contract, keeping its status/null/definitions intact."""
    collected_at = stamp(collected_at)
    if set(requested) != {'start', 'end', 'time_zone'} or requested['time_zone'] != 'Asia/Dubai':
        raise ValueError('Wrong requested timezone or arguments')
    start_local = datetime.fromisoformat(requested['start'])
    end_local = datetime.fromisoformat(requested['end'])
    if start_local.tzinfo is not None or end_local.tzinfo is not None:
        raise ValueError('Requested boundaries must be local Dubai times without offsets')
    start = start_local.replace(tzinfo=DUBAI)
    end = end_local.replace(tzinfo=DUBAI)
    if start.time().isoformat() != '00:00:00' or end-start != timedelta(days=1) or end > collected_at:
        raise ValueError('Expected one completed Dubai calendar day')
    summary = raw.get('summary') if isinstance(raw, dict) else None
    if not isinstance(summary, dict):
        raise ValueError('CareerOS did not return an aggregate summary')
    window = summary.get('window', {})
    if not isinstance(window, dict):
        raise ValueError('CareerOS window is missing')
    if (window.get('time_zone') != 'Asia/Dubai' or window.get('bounds') != 'start-inclusive, end-exclusive'
            or stamp(window.get('start')) != start or stamp(window.get('end')) != end
            or datetime.fromisoformat(window.get('start_local', '')) != start.replace(tzinfo=None)
            or datetime.fromisoformat(window.get('end_local', '')) != end.replace(tzinfo=None)):
        raise ValueError('CareerOS returned the wrong period, bounds or timezone')
    generated = stamp(summary.get('generated_at'))
    through = stamp(summary.get('data_through'))
    if generated > collected_at+timedelta(minutes=5) or generated < collected_at-timedelta(hours=1) or generated < end:
        raise ValueError('CareerOS generation timestamp is future, stale or before the window ended')
    if through != end or summary.get('window_complete') is not True:
        raise ValueError('CareerOS has not covered the complete requested window')
    metrics = summary.get('metrics')
    if not isinstance(metrics, dict):
        raise ValueError('CareerOS metrics are missing')
    kept = {}
    for name in METRICS:
        m = metrics.get(name)
        if not isinstance(m, dict) or m.get('status') not in (('available', 'partial', 'unavailable', 'tracked') if name == 'active_learners' else ('available', 'partial', 'unavailable')) or 'value' not in m:
            raise ValueError('Invalid or missing metric status/value: '+name)
        v = m['value']
        if m['status'] == 'unavailable' and v is not None:
            raise ValueError('Unavailable metric must remain null: '+name)
        if m['status'] in ('available', 'tracked') and v is None:
            raise ValueError('Available metric has no value: '+name)
        if v is not None and (type(v) not in (int, float) or not math.isfinite(v) or v < 0
                              or (name != 'minutes_learned' and type(v) is not int)):
            raise ValueError('Invalid aggregate number: '+name)
        if not isinstance(m.get('definition'), str) or not m['definition']:
            raise ValueError('Metric definition is missing: '+name)
        if any(m.get(k) is not None and not isinstance(m[k], str) for k in ('caveat','reason','tracking_needed')):
            raise ValueError('Invalid metric explanation: '+name)
        if m.get('tracked_since') is not None and stamp(m['tracked_since']) > generated:
            raise ValueError('Tracking start is in the future: '+name)
        kept[name] = {k:m[k] for k in ('value','status','tracked_since','definition','caveat','reason','tracking_needed') if k in m}
        if name == 'active_learners' and 'breakdown' in m:
            breakdown = m['breakdown']
            if not isinstance(breakdown, dict) or set(breakdown) != {'from_recorded_events','from_latest_progress_only'}:
                raise ValueError('Unknown active learner breakdown')
            if any(type(n) is not int or n < 0 for n in breakdown.values()) or (v is not None and sum(breakdown.values()) != v):
                raise ValueError('Active learner breakdown does not match total')
            kept[name]['breakdown'] = dict(breakdown)
        if name == 'new_questions':
            for kind in ('public','private'):
                if kind in m:
                    if type(m[kind]) is not int or m[kind] < 0:
                        raise ValueError('Invalid question breakdown')
                    kept[name][kind] = m[kind]
            if all(k in m for k in ('public','private')) and v is not None and m['public']+m['private'] != v:
                raise ValueError('Question breakdown does not match total')
    clean = {k:summary[k] for k in ('window','generated_at','data_through','window_complete','excluded_accounts') if k in summary}
    clean['window'] = {k:window[k] for k in ('start','end','start_local','end_local','time_zone','bounds')}
    clean['metrics'] = kept
    return dict(source='CareerOS', tool=TOOL, requested=dict(requested), collected_at=collected_at.isoformat(), summary=clean)


def from_stream(stdout, requested, collected_at):
    """Only the exact allowed tool's matching raw result can become a cache."""
    from .scorecard import Scorecard
    uses, results = {}, []
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        for block in (ev.get('message') or {}).get('content') or []:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use' and block.get('name') == TOOL:
                uses[block['id']] = block.get('input')
            elif block.get('type') == 'tool_result' and block.get('tool_use_id') in uses:
                if block.get('is_error'):
                    raise ValueError('CareerOS aggregate tool returned an error')
                body = block.get('content')
                if isinstance(body, list):
                    body = ''.join(x.get('text','') for x in body if isinstance(x,dict))
                results.append((uses[block['tool_use_id']], json.loads(Scorecard._persisted(str(body or '')))))
    if len(uses) != 1 or len(results) != 1:
        raise ValueError('The exact CareerOS aggregate tool was not called once with one result')
    args, raw = results[0]
    if args != requested:
        raise ValueError('CareerOS tool call arguments do not match the requested day')
    return validate(raw, requested, collected_at)


def fetch(root, at=None, run=subprocess.run, tries=2, sleep=time.sleep, clock=lambda: datetime.now(UTC)):
    from .agent import find_claude
    from .scorecard import server_status
    at = at or clock()
    args = request_for(at)
    claude = find_claude(root)
    if not claude:
        raise RuntimeError('Claude Code is unavailable; CareerOS aggregate was not read')
    prompt = (f'Call the read-only tool {TOOL} exactly once with arguments {json.dumps(args)}, then reply with the single word done. '
              'Do not enumerate learners or call any other tool.')
    cmd = [claude,'-p',prompt,'--output-format','stream-json','--verbose','--no-session-persistence','--tools','','--allowedTools',TOOL]
    for attempt in range(tries):
        result = run(cmd,capture_output=True,text=True,encoding='utf-8',timeout=240,cwd=str(root),
                     env=dict(os.environ,CLAUDE_CODE_MCP_STARTUP_WAIT_MS='30000'),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:
            raise RuntimeError('CareerOS aggregate reader exited unsuccessfully')
        try:
            return from_stream(result.stdout or '', args, clock().isoformat())
        except (ValueError, TypeError, KeyError) as exc:
            if server_status(result.stdout or '', 'careeros') == 'connected' or attempt+1 >= tries:
                raise ValueError('CareerOS aggregate rejected: '+str(exc)) from exc
            sleep(15)
    raise RuntimeError('CareerOS aggregate was not read')


class LearningActivity:
    def __init__(self, root, data_root, fetcher=None):
        self.root, self.path = Path(root), Path(data_root)/'business'
        self.fetcher = fetcher or (lambda at: fetch(self.root, at))
        self.lock = threading.Lock()

    def _read(self, name):
        try:
            return json.loads((self.path/name).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def _write(self, name, value):
        self.path.mkdir(parents=True,exist_ok=True)
        fd,tmp = tempfile.mkstemp(prefix=name+'.',dir=self.path)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                json.dump(value,stream,indent=2,allow_nan=False)
            os.replace(tmp,self.path/name)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)

    def facts(self, at):
        saved = self._read('learning-activity.json')
        attempt = self._read('learning-activity-attempt.json')
        empty = dict(source='CareerOS',available=False,state='unavailable',latest_attempt=attempt)
        if not saved:
            return dict(empty,reason='No validated CareerOS activity aggregate has been saved')
        try:
            got = validate({'summary':saved['summary']},saved['requested'],saved['collected_at'])
            if stamp(got['collected_at']) > at+timedelta(minutes=5):
                raise ValueError('Cached collection timestamp is in the future')
        except (ValueError,TypeError,KeyError) as exc:
            return dict(empty,state='invalid',reason=str(exc))
        stale = got['requested'] != request_for(at)
        return dict(got,available=True,state='stale' if stale else 'ok',stale=stale,latest_attempt=attempt)

    def collect(self, at=None):
        at = at or datetime.now(UTC)
        with self.lock:
            have = self.facts(at)
            if have.get('available') and not have.get('stale'):
                return have
            attempt = dict(at=at.isoformat(),requested=request_for(at),state='failed')
            try:
                got = self.fetcher(at)
                got = validate({'summary':got['summary']},request_for(at),got['collected_at'])
                self._write('learning-activity.json',got)
                attempt.update(state='ok',generated_at=got['summary']['generated_at'])
            except Exception as exc:
                attempt['reason'] = str(exc)[:300]
            self._write('learning-activity-attempt.json',attempt)
            return self.facts(at)
