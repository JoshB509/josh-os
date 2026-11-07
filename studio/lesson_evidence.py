"""Read-only, binding-scoped OBS evidence across restarts. Never control OBS."""
import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path


def recording_key(path):
    p = Path(path).resolve()
    return str(p.with_suffix('') if p.suffix.lower() in {'.mkv', '.mp4'} else p).casefold()


def scan_logs(bound, folder, input_folder, expected_scene):
    def invalid(reason):
        return dict(valid=False, completed={}, started=[], incomplete=[], ambiguous=True,
                    reason=reason, log_history=[])

    folder, input_folder = Path(folder).resolve(), Path(input_folder).resolve()
    cursor = bound.get('obs_log_cursor')
    end = bound.get('closed_log_cursor')
    if not cursor:
        return invalid('The prepared session has no OBS log anchor.')
    texts = {}
    anchors = [cursor] + bound.get('obs_log_history', []) + ([end] if end else [])
    try:
        for anchor in anchors:
            p = Path(anchor['path']).resolve()
            if p.parent != folder:
                return invalid('An OBS evidence path is outside the configured log folder.')
            if p not in texts:
                texts[p] = p.read_text(encoding='utf-8-sig', errors='replace')
            offset = anchor['offset']
            if not isinstance(offset, int) or offset < 0 or len(texts[p]) < offset:
                return invalid('An OBS evidence log was truncated.')
            if hashlib.sha256(texts[p][:offset].encode()).hexdigest() != anchor['prefix_sha256']:
                return invalid('Previously saved OBS evidence changed.')
        first = Path(cursor['path']).resolve()
        last = Path(end['path']).resolve() if end else None
        if last and (last.name < first.name or (last == first and end['offset'] < cursor['offset'])):
            return invalid('The session end precedes its preparation anchor.')
        logs = sorted((p.resolve() for p in folder.glob('*.txt')
                       if p.name >= first.name and (not last or p.name <= last.name)), key=lambda p: p.name)
        prepared = int(bound['prepared_ns']) / 1e9
        excluded = {recording_key(p) for p in bound.get('existing_paths', [])}
        starts, completed, history = {}, {}, []
        ambiguous, reasons, order = False, [], 0
        def unsure(reason):
            nonlocal ambiguous
            ambiguous = True
            if reason not in reasons:
                reasons.append(reason)

        for p in logs:
            text = texts.get(p)
            if text is None:
                text = p.read_text(encoding='utf-8-sig', errors='replace')
            begin = cursor['offset'] if p == first else 0
            limit = end['offset'] if p == last else len(text)
            text = text[:limit]
            day = datetime.strptime(p.stem, '%Y-%m-%d %H-%M-%S').date()
            previous, position, checkpoint = None, 0, begin
            # Preparation verified the first log's setup. A restarted OBS must
            # establish the expected scene again before any recording is adopted.
            setup_known = p == first
            for line in text.splitlines(keepends=True):
                line_start = position
                position += len(line)
                clock_match = re.match(r'(\d{2}:\d{2}:\d{2}\.\d{3}):', line)
                stamp = None
                if clock_match:
                    clock = datetime.strptime(clock_match[1], '%H:%M:%S.%f').time()
                    if previous and previous.hour == 23 and clock.hour == 0:
                        day += timedelta(days=1)
                    previous = clock
                    stamp = datetime.combine(day, clock).timestamp()
                if line_start < begin:
                    continue
                # A partially flushed event is not evidence; retry after it ends.
                relevant = re.search(r'Writing file|Output of file|switched to|Changing (?:profile|scene collection)', line, re.I)
                if relevant:
                    checkpoint = position
                    if not line.endswith('\n'):
                        unsure('OBS has not finished writing an evidence event.')
                if 'Command Line Arguments:' in line:
                    for flag, expected in [('profile', 'Lessons'), ('collection', 'Lessons - Facecam and Slides')]:
                        value = re.search(r'--'+flag+r'\s+(.+?)(?=\s+--|\r?\n|$)', line)
                        if value and value[1].strip().strip('"') != expected:
                            unsure('Restarted OBS was launched with a different lesson setup.')
                scene = re.search(r"(?:User switched to|Switched to) scene '([^']+)'", line, re.I)
                if scene:
                    if scene[1] != expected_scene or any(k not in completed for k in starts):
                        unsure('OBS scene changed or was uncertain during the lesson session.')
                    else:
                        setup_known = True
                elif re.search(r'User switched to|Switched to (?:profile|scene)|Changing (?:profile|scene collection)', line, re.I):
                    unsure('OBS profile or scene collection changed during the lesson session.')
                start = re.search(r"\[ffmpeg muxer: '([^']+)'\] Writing file '([^']+)'", line)
                stop = re.search(r"\[ffmpeg muxer: '([^']+)'\] Output of file '([^']+)' stopped", line)
                if start:
                    path, key = Path(start[2]).resolve(), recording_key(start[2])
                    if not setup_known:
                        unsure('Restarted OBS has no verified lesson scene before recording.')
                    if path.parent != input_folder or start[1] not in {'adv_file_output', 'simple_file_output'}:
                        unsure('OBS recorded outside the configured lesson output.')
                        continue
                    if stamp is None or stamp < prepared or key in excluded:
                        unsure('A recording predates or conflicts with the prepared lesson boundary.')
                        continue
                    if key in starts:
                        unsure('OBS reused a recording filename; association is ambiguous.')
                        continue
                    starts[key] = dict(log_path=str(p), recording_path=str(path), start=line.rstrip(),
                                       start_order=order, started_at=stamp, binding_id=bound['id'])
                    order += 1
                if stop:
                    key = recording_key(stop[2])
                    if (key not in starts or starts[key]['log_path'] != str(p) or key in completed
                            or stamp is None or stamp < starts[key]['started_at']):
                        unsure('A recording stop has no unique start in the same OBS process.')
                    else:
                        completed[key] = dict(starts[key], stop=line.rstrip())
            if not setup_known:
                unsure('Restarted OBS has not established the expected lesson scene.')
            # Freeze closed logs completely; freeze the current log through the
            # last evidence event, ignoring harmless ongoing websocket polling.
            if p != logs[-1] or end:
                checkpoint = len(text)
            if checkpoint:
                history.append(dict(path=str(p), offset=checkpoint,
                                    prefix_sha256=hashlib.sha256(text[:checkpoint].encode()).hexdigest()))
        incomplete = [k for k in starts if k not in completed]
        return dict(valid=True, completed={} if ambiguous else completed, started=list(starts),
                    incomplete=incomplete, ambiguous=ambiguous, reason=' '.join(reasons), log_history=history)
    except (OSError, ValueError, KeyError, TypeError):
        return invalid('An OBS evidence log is missing, unreadable or malformed. Recordings remain untouched.')
