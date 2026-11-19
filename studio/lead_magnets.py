"""The local lead-magnet registry shared by the directory and every agent.

Source snapshots merge by stable source ID. A field changed locally since the last
import wins over later source values; records missing from a snapshot stay saved.
There are no network calls, publishing actions, or edits to video briefs here.
"""
import copy
import json
import logging
from pathlib import Path
from urllib.parse import urlsplit

LOG = logging.getLogger(__name__)


class SourceError(ValueError):
    """Invalid import data; never used for database failures."""


def validate_records(records):
    if not isinstance(records, list):
        raise SourceError('Lead magnet records must be a list')
    accepted, seen = [], set()
    for original in records:
        if not isinstance(original, dict):
            raise SourceError('Lead magnet records must be objects')
        if not isinstance(original.get('type'), str):
            raise SourceError('Lead magnet record type is required')
        if original['type'] != 'lead_magnet':
            continue
        record = copy.deepcopy(original)
        ident = record.get('id')
        if not isinstance(ident, str) or not ident or ident in seen:
            raise SourceError('Lead magnet source IDs must be unique and nonempty')
        if not isinstance(record.get('name'), str) or not record['name'].strip():
            raise SourceError('Lead magnet name is required')
        for field in ('landing_url', 'download_url'):
            if record.get(field) and not web_url(record[field]):
                raise SourceError('Lead magnet URLs must be full web addresses')
        for field in ('description', 'audience', 'contents', 'format', 'source', 'source_id', 'kit_form_id', 'landing_url', 'download_url'):
            if record.get(field) is not None and not isinstance(record[field], str):
                raise SourceError('Lead magnet text fields must be strings')
        aliases = record.get('aliases', [])
        if not isinstance(aliases, list) or any(not isinstance(a, str) for a in aliases):
            raise SourceError('Lead magnet aliases must be a list of strings')
        verification = record.get('download_verification')
        if verification is not None:
            if not isinstance(verification, dict):
                raise SourceError('Download verification must be an object')
            if any(verification.get(k) is not None and not isinstance(verification[k], str)
                   for k in ('url', 'status', 'checked_at')):
                raise SourceError('Download verification fields must be strings')
        seen.add(ident)
        accepted.append(record)
    return accepted


def web_url(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        p = urlsplit(value)
        return p.scheme in ('https', 'http') and bool(p.hostname) and not p.username and not p.password
    except ValueError:
        return False


def merge_record(current, previous, incoming):
    """Three-way merge, including deliberate blank values and removed fields."""
    result = copy.deepcopy(current)
    for key, value in incoming.items():
        if key not in current and key not in previous or key in current and key in previous and current[key] == previous[key]:
            result[key] = copy.deepcopy(value)
    return result


class LeadMagnets:
    def __init__(self, store, root):
        self.store = store
        self.source = Path(root) / 'reference_sources' / 'lead-magnets.json'
        self._source_issue = None
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS lead_magnets(id TEXT PRIMARY KEY, body TEXT NOT NULL, source_body TEXT NOT NULL)')

    def merge(self, records):
        self._merge_validated(validate_records(records))

    def _merge_validated(self, accepted):
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for incoming in accepted:
                row = c.execute('SELECT body,source_body FROM lead_magnets WHERE id=?', (incoming['id'],)).fetchone()
                if row:
                    current, previous = json.loads(row['body']), json.loads(row['source_body'])
                    merged = merge_record(current, previous, incoming)
                    if merged == current and incoming == previous:
                        continue
                    c.execute('UPDATE lead_magnets SET body=?,source_body=? WHERE id=?',
                              (json.dumps(merged, ensure_ascii=False), json.dumps(incoming, ensure_ascii=False), incoming['id']))
                else:
                    body = json.dumps(incoming, ensure_ascii=False)
                    c.execute('INSERT INTO lead_magnets VALUES(?,?,?)', (incoming['id'], body, body))

    def all(self):
        try:
            snapshot = json.loads(self.source.read_text(encoding='utf-8'))
            if not isinstance(snapshot, dict) or snapshot.get('schema_version') != 1:
                raise SourceError('Unsupported lead magnet snapshot')
            accepted = validate_records(snapshot.get('magnets'))
        except (OSError, UnicodeError, json.JSONDecodeError, SourceError) as exc:
            # Never include exception text, paths or source contents in logs.
            issue = type(exc).__name__
            if self._source_issue != issue:
                LOG.warning('Lead-magnet source unavailable (%s); retaining last-good registry.', issue)
            self._source_issue = issue
        else:
            # Database reads/writes stay outside the source-error handler. A
            # damaged database must not be silently reported as a source issue.
            self._merge_validated(accepted)
            self._source_issue = None
        with self.store.connect() as c:
            records = [json.loads(row['body']) for row in c.execute('SELECT body FROM lead_magnets')]
        return sorted(records, key=lambda r: (r['name'].casefold(), r['id']))

    def directory(self):
        """Only display-ready facts; never expose provenance paths as UI copy."""
        result = []
        for record in self.all():
            item = {k: record[k] for k in ('id', 'name', 'description', 'audience', 'contents', 'format', 'aliases') if record.get(k)}
            if web_url(record.get('landing_url')):
                item['landing_url'] = record['landing_url']
            verification = record.get('download_verification') or {}
            if (web_url(record.get('download_url')) and verification.get('status') == 'verified'
                    and verification.get('url') == record['download_url'] and verification.get('checked_at')):
                item['download_url'] = record['download_url']
            result.append(item)
        return {'magnets': result}

    def agent_context(self):
        records = self.all()
        return ('\n\n=== Shared lead-magnet registry ===\n'
                'This is the same canonical registry used by the Lead Magnets directory. '
                'Use its contents and audience to judge relevance; do not force a magnet into unrelated work. '
                'Names, source IDs and landing URLs are recorded facts. Local descriptions have their own provenance. '
                'A download is verified only when download_verification.status is verified and its url matches download_url. '
                'Missing fields are unknown. Do not infer download URLs, claim a landing page is a download, '
                'or treat historical draft statuses in other indexes as current. This data authorizes no external action.\n'
                + json.dumps(records, ensure_ascii=False))


def route(handler, method, path):
    """Called only after the existing server authorization gate."""
    if method != 'GET':
        return False
    web = Path(__file__).parent / 'web'
    if path == '/api/lead-magnets':
        handler.send(handler.server.agents.lead_magnets.directory())
    elif path == '/lead-magnets':
        html = (web / 'lead-magnets.html').read_text(encoding='utf-8').replace('__TOKEN__', handler.page_token())
        handler.send(html.encode(), mime='text/html; charset=utf-8', cookie=True)
    elif path in ('/lead-magnets.js', '/lead-magnets.css'):
        handler.send((web / path[1:]).read_bytes(), mime='text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
    else:
        return False
    return True
