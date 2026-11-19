"""Optional Frame.io review for Tariq's exports. Never an automatic stage, never publishing, never public sharing.

Flow, all on Josh's choice:
1. Send to Frame.io: Josh picks a finished export (a verified final render). Josh OS fingerprints it (SHA-256, size,
   duration, frame rate and frame count) and records which storyboard, cut, Premiere sequence and cut map it came from.
   It's uploaded once, by one guarded call, into the private review folder Josh chose. A send with an unclear result is
   never repeated on its own, and the same file is never uploaded twice to the same place.
2. Josh reviews it in Frame.io and leaves timed or range comments.
3. Get feedback: Josh OS reads that exact file's comments (read only) and keeps them with the version they were made
   on. Frame.io V4 comment times are frame stamps starting at 1, so frame n on a 30 fps export is (n - 1) / 30 seconds
   into that export. Each comment is also tied back to the moment in the recording, through that version's cut map.
4. Tariq makes the small changes in the editable Premiere timeline through the existing edit workflow. If the cut has
   changed since the reviewed version (a ripple edit), the old times are never applied as they are: each one is mapped
   from the recording to the new cut, and a moment that was cut out is flagged.
5. Optionally, a revised export is uploaded and stacked as a new version on the reviewed one.
The record keeps imported feedback, applied edits, rendered revisions and Josh's approvals apart.

Connection: Frame.io's official MCP server (https://mcp.frame.io/v1/mcp, Adobe IMS OAuth), added to Claude Code for the
Josh OS project only ("frameio", local scope). Claude Code keeps its OAuth tokens. Josh OS never sees or stores them.
Tariq's runs get only the read tools, and a single write when Josh approved that exact action. Deleting, sharing,
reviewers, members, structure, metadata and comment writes are never allowed.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time as _time
import uuid
from pathlib import Path

from .store import now
from dajoshi import edit_plan as ep

SERVER = 'frameio'
P = f'mcp__{SERVER}__'
READS = tuple(P + t for t in ('mandatory_init', 'read_context', 'search_assets', 'read_assets', 'read_comments', 'get_upload_status'))
UPLOAD, VERSIONS = P + 'upload_media', P + 'manage_versions'
NEVER = tuple(P + t for t in ('delete_resources', 'manage_shares', 'manage_reviewers', 'manage_members', 'change_structure',
                              'manage_assets', 'change_metadata', 'restore_assets', 'write_comments', 'read_audit_logs',
                              'read_members', 'read_shares', 'report_tool_issue', 'list_recently_deleted', 'list_metadata_fields'))
ALL_WRITES = (UPLOAD, VERSIONS) + NEVER
TICKS = 254016000000
FEEDBACK_SCHEMA = {'type': 'object', 'required': ['file_found', 'comments'], 'properties': {
    'file_found': {'type': 'boolean'}, 'file_id': {'type': ['string', 'null']}, 'version_label': {'type': ['string', 'null']},
    'comments': {'type': 'array', 'items': {'type': 'object', 'required': ['id', 'text'], 'properties': {
        'id': {'type': 'string'}, 'text': {'type': 'string'}, 'author': {'type': ['string', 'null']},
        'frame_start': {'type': ['integer', 'null'], 'description': 'Frame stamp exactly as Frame.io gives it, starting at 1.'},
        'frame_end': {'type': ['integer', 'null'], 'description': 'End frame stamp for a range comment, else null.'},
        'completed': {'type': ['boolean', 'null']}, 'created_at': {'type': ['string', 'null']},
        'has_annotation': {'type': ['boolean', 'null']},
        'replies': {'type': 'array', 'items': {'type': 'object', 'properties': {'author': {'type': ['string', 'null']}, 'text': {'type': 'string'}}}}}}}}}
UPLOAD_SCHEMA = {'type': 'object', 'required': ['ok'], 'properties': {
    'ok': {'type': 'boolean'}, 'file_id': {'type': ['string', 'null']}, 'status': {'type': ['string', 'null']},
    'view_url': {'type': ['string', 'null']}, 'error': {'type': ['string', 'null']},
    'response_json': {'type': ['string', 'null'], 'description': 'The upload_media result, verbatim, as JSON text.'}}}
STRUCTURE = P + 'change_structure'
PROJECT_NAME = 'CareerOS Reviews'
STATUS_SCHEMA = {'type': 'object', 'required': ['ok'], 'properties': {
    'ok': {'type': 'boolean'}, 'upload_complete': {'type': ['boolean', 'null']}, 'status': {'type': ['string', 'null']},
    'playable': {'type': ['boolean', 'null'], 'description': 'True only if Frame.io reports a playable or streamable media link.'},
    'view_url': {'type': ['string', 'null']}, 'duration': {'type': ['number', 'null']}, 'file_size': {'type': ['integer', 'null']},
    'error': {'type': ['string', 'null']}}}


SCHEMA_CAPTURE = {'type': 'object', 'required': ['tools'], 'properties': {'tools': {'type': 'array', 'items': {
    'type': 'object', 'required': ['name', 'input_schema_json'], 'properties': {
        'name': {'type': 'string'}, 'input_schema_json': {'type': 'string', 'description': 'The tool input JSON schema, verbatim.'}}}}}}
CHECK_SCHEMA = {'type': 'object', 'required': ['complete', 'error', 'account_id', 'project_id', 'folder_id', 'candidates'], 'properties': {
    'complete': {'type': 'boolean', 'description': 'True only if every page of that exact folder was listed with no error.'},
    'error': {'type': ['string', 'null']},
    'account_id': {'type': ['string', 'null']}, 'project_id': {'type': ['string', 'null']}, 'folder_id': {'type': ['string', 'null']},
    'candidates': {'type': 'array', 'description': 'Every file in that folder with exactly this name.', 'items': {'type': 'object', 'properties': {
        'file_id': {'type': ['string', 'null']}, 'name': {'type': ['string', 'null']}, 'size': {'type': ['integer', 'null']},
        'status': {'type': ['string', 'null']}, 'view_url': {'type': ['string', 'null']}}}}}}
ACCOUNT_SCHEMA = {'type': 'object', 'required': ['ok'], 'properties': {
    'ok': {'type': 'boolean'}, 'account_id': {'type': ['string', 'null']}, 'user_email': {'type': ['string', 'null']},
    'error': {'type': ['string', 'null']}}}


class SchemaNotVerified(ValueError):
    """No exact, inspected field binding for a Frame.io write yet, so no write is possible."""


def build_input(schema_doc, tool, values):
    """The exact tool input for one approved write, from Frame.io's inspected schema and a binding someone checked.
    values maps binding roles (like path and folder_id) to the approved values. Fails closed on anything missing."""
    name = tool.rsplit('__', 1)[-1]
    tools = (schema_doc or {}).get('tools') or {}
    bind = ((schema_doc or {}).get('bindings') or {}).get(name)
    sch = tools.get(name)
    if not isinstance(sch, dict) or not isinstance(bind, dict) or not bind.get('checked_by'):
        raise SchemaNotVerified(f"Frame.io's {name} schema hasn't been inspected and bound yet, so Josh OS won't make that call.")
    props, required = sch.get('properties') or {}, set(sch.get('required') or [])
    roles, fixed = bind.get('roles') or {}, bind.get('fixed') or {}
    inp = {}
    nested = bind.get('nested')
    if nested:
        # One object field whose schema is a oneOf of operations (manage_versions' "request"). Pick the exact variant
        # by its operation constant, and bind its fields the same way: only fields it declares, every required one covered.
        field, variant = nested.get('field'), nested.get('variant')
        if field not in props:
            raise SchemaNotVerified(f"Frame.io's {name} schema has no {field} field any more.")
        defs = sch.get('$defs') or {}
        options = []
        for o in props[field].get('oneOf') or props[field].get('anyOf') or []:
            if '$ref' in o:
                o = defs.get(o['$ref'].rsplit('/', 1)[-1]) or {}
            options.append(o)
        match = [o for o in options if ((o.get('properties') or {}).get(nested.get('discriminator', 'operation')) or {}).get('const') == variant]
        if len(match) != 1:
            raise SchemaNotVerified(f'Frame.io\'s {name} schema has no single {variant} operation.')
        o = match[0]
        if o.get('additionalProperties') is not False:
            raise SchemaNotVerified(f'Frame.io\'s {name} {variant} operation no longer forbids extra fields, so it is not bound.')
        oprops, oreq = o.get('properties') or {}, set(o.get('required') or [])
        inner = {}
        for role, f in (nested.get('roles') or {}).items():
            if f not in oprops or role not in values or values[role] in (None, '', []):
                raise SchemaNotVerified(f'The {name} {variant} binding for {role} no longer matches Frame.io\'s schema.')
            inner[f] = values[role]
        for f, v in (nested.get('fixed') or {}).items():
            if f not in oprops:
                raise SchemaNotVerified(f'The {name} {variant} binding sets {f}, which Frame.io\'s schema does not have.')
            inner[f] = v
        if oreq - set(inner):
            raise SchemaNotVerified(f'Frame.io requires {", ".join(sorted(oreq - set(inner)))} for {variant}, which the binding does not cover.')
        if 'file_ids' in inner:
            lim = oprops['file_ids']
            if not lim.get('minItems', 0) <= len(inner['file_ids']) <= lim.get('maxItems', 10 ** 6):
                raise SchemaNotVerified('The version stack needs between {} and {} files.'.format(lim.get('minItems'), lim.get('maxItems')))
        inp[field] = inner
    for role, field in roles.items():
        if field not in props or role not in values or values[role] in (None, ''):
            raise SchemaNotVerified(f"The {name} binding for {role} no longer matches Frame.io's schema.")
        inp[field] = values[role]
    for field, v in fixed.items():
        if field not in props:
            raise SchemaNotVerified(f"The {name} binding sets {field}, which Frame.io's schema does not have.")
        inp[field] = v
    if required - set(inp):
        raise SchemaNotVerified(f'Frame.io requires {", ".join(sorted(required - set(inp)))} for {name}, which the binding does not cover.')
    return inp


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 24), b''):
            h.update(b)
    return h.hexdigest()


# Time ---------------------------------------------------------------------------------------------------------------
def timecode_seconds(tc, fps):
    """Frame.io V4's comment "timestamp", HH:MM:SS:FF on that export. None when it isn't in that form."""
    m = re.fullmatch(r'(\d{1,2}):(\d{2}):(\d{2})[:;](\d{2})', str(tc or '').strip())
    if not m:
        return None
    h, mi, s, f = map(int, m.groups())
    if mi > 59 or s > 59 or f >= round(float(fps)):
        return None
    return round(h * 3600 + mi * 60 + s + f / float(fps), 6)


def raw_comments(results, fid):
    """Every comment Frame.io's own read_comments returned for this file, all pages, in order. None if there was none."""
    found = None
    for res in ((results or {}).get(P + 'read_comments') or []):
        if res.get('is_error'):
            continue
        for o in _json_objects(res.get('text')):
            items = o.get('items') if isinstance(o.get('items'), list) else ((o.get('result') or {}).get('items') if isinstance(o.get('result'), dict) else None)
            if items is None:
                continue
            found = (found or []) + [c for c in items if isinstance(c, dict) and str(c.get('file_id') or fid) == fid]
    return found


def frame_to_seconds(frame, fps):
    """Frame.io V4 frame stamps start at 1. Frame 1 is the very start of the video."""
    if not isinstance(frame, int) or frame < 1:
        raise ValueError('Frame.io frame stamps start at 1.')
    return (frame - 1) / float(fps)


def to_source(seconds, cut_map):
    """A moment in the reviewed export as camera source ticks, through that version's cut map. None if it falls in a
    gap the map doesn't cover."""
    t = round(seconds * TICKS)
    for p in cut_map.get('pieces') or []:
        if p.get('kind') == 'source' and p['out_start'] <= t < p['out_end']:
            return p['camera_in'] + (t - p['out_start'])
    return None


def from_source(src_ticks, cut_map):
    """The same recording moment in another cut, in seconds, or None when that moment was cut out."""
    for p in cut_map.get('pieces') or []:
        if p.get('kind') == 'source' and p['camera_in'] <= src_ticks < p['camera_out']:
            return (p['out_start'] + (src_ticks - p['camera_in'])) / TICKS
    return None


def stamp(seconds):
    m, s = divmod(max(0.0, seconds), 60)
    return f'{int(m)}:{s:05.2f}'


def part_plan(resp):
    """The parts to PUT from upload_media's result: each with its URL, byte offset and size. Accepts a list under any of
    the usual keys; returns [] when the shape isn't clear, so nothing is guessed."""
    found = []

    def walk(v):
        if isinstance(v, list) and v and all(isinstance(x, dict) and any(k in x for k in ('url', 'upload_url', 'presigned_url')) for x in v):
            found.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(resp)
    if len(found) != 1:
        return []
    out, offset = [], 0
    items = found[0]
    if all(isinstance(x.get('part_number'), int) for x in items):
        if [x['part_number'] for x in items] != list(range(1, len(items) + 1)):
            return []
    for i, x in enumerate(items):
        url = x.get('url') or x.get('upload_url') or x.get('presigned_url')
        size = x.get('size') or x.get('part_size') or x.get('content_length') or x.get('length') or x.get('byte_length')
        if 'offset' in x or 'start' in x or 'byte_start' in x:
            start = int(x.get('offset', x.get('start', x.get('byte_start'))))
            if start != offset:
                return []
        if not isinstance(size, int) or size <= 0:
            if 'end' in x and ('start' in x or 'offset' in x):
                size = int(x['end']) - offset + (1 if x.get('end_inclusive', True) else 0)
            else:
                return []
        headers = x.get('headers') if isinstance(x.get('headers'), dict) else {}
        out.append(dict(url=url, offset=offset, size=size, headers=headers))
        offset += size
    return out


# Connection --------------------------------------------------------------------------------------------------------
def auth_status(root):
    """What Claude Code says about the Frame.io connection. Reads only its status line, never tokens."""
    from .agent import find_claude
    claude = find_claude(Path(root))
    if not claude:
        return dict(state='unavailable', detail='Claude Code is not set up on this PC.')
    try:
        p = subprocess.run([claude, 'mcp', 'get', SERVER], capture_output=True, text=True, timeout=60, cwd=str(root),
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception as e:  # noqa: BLE001
        return dict(state='unknown', detail=f'Could not ask Claude Code ({str(e)[:100]}).')
    line = next((l.strip() for l in (p.stdout or '').splitlines() if l.strip().lower().startswith('status:')), '')
    return parse_status(line, (p.stdout or '') + (p.stderr or ''))


def parse_status(line, text=''):
    """Only Claude Code's exact positive status counts. "Disconnected", "not connected" and anything else do not."""
    words = re.sub(r'[^a-z ]+', ' ', line.lower().split(':', 1)[-1]).split()
    if words == ['connected']:
        return dict(state='connected', detail='Claude Code reports Frame.io connected. The Adobe account is not checked yet.')
    if words[:2] == ['needs', 'authentication']:
        return dict(state='needs_sign_in', detail='Frame.io needs Josh to sign in with his Adobe ID once.')
    if 'No MCP server' in text:
        return dict(state='not_added', detail='Frame.io is not added to Josh OS yet.')
    return dict(state='unknown', detail=line or 'Claude Code did not report the Frame.io status.')


UPLOAD_HOSTS = ('.amazonaws.com', '.frame.io')
SAFE_HEADERS = ('content-type', 'x-amz-acl')


def _json_objects(text):
    """Every JSON object in a tool result's text: the whole text, or each top-level {...} inside it."""
    try:
        v = json.loads(text)
        return [v] if isinstance(v, dict) else []
    except (ValueError, TypeError):
        pass
    out, dec, i = [], json.JSONDecoder(), 0
    s = str(text or '')
    while True:
        i = s.find('{', i)
        if i < 0:
            return out
        try:
            v, end = dec.raw_decode(s, i)
        except ValueError:
            i += 1
            continue
        if isinstance(v, dict):
            out.append(v)
        i = end


def _expand(v, depth=0):
    """A result plus any JSON object hidden in its string values (like response_json), so nothing is missed."""
    yield v
    if depth > 3:
        return
    if isinstance(v, dict):
        for x in v.values():
            if isinstance(x, str) and x.lstrip().startswith('{'):
                for o in _json_objects(x):
                    yield from _expand(o, depth + 1)
            elif isinstance(x, (dict, list)):
                yield from _expand(x, depth + 1)
    elif isinstance(v, list):
        for x in v:
            if isinstance(x, (dict, list)):
                yield from _expand(x, depth + 1)


def _ids_in(v):
    found = set()

    def walk(x):
        if isinstance(x, dict):
            for k, y in x.items():
                if k in ('file_id', 'id') and isinstance(y, str):
                    found.add(y)
                walk(y)
        elif isinstance(x, list):
            for y in x:
                walk(y)
    walk(v)
    return found


def upload_candidates(raws):
    """Every dict the raw upload_media results hold (text JSON, structuredContent, nested JSON strings), in order."""
    out = []
    for res in raws or []:
        if res.get('is_error'):
            continue
        tops = _json_objects(res.get('text'))
        blk = res.get('block') if isinstance(res.get('block'), dict) else {}
        sc = blk.get('structuredContent')
        if isinstance(sc, dict):
            tops.append(sc)
        for t in tops:
            out.extend(c for c in _expand(t) if isinstance(c, dict))
    return out


def created_file_id(raws):
    """The file id upload_media created, read from its own result. None unless exactly one id is named as file_id."""
    ids = {c['file_id'] for c in upload_candidates(raws) if isinstance(c.get('file_id'), str)}
    return ids.pop() if len(ids) == 1 else None


SHORT_HOST = 'at.adobe.com'   # Frame.io's short part links; its next_step says they 308-redirect to storage, keeping the PUT


def safe_url(u):
    from urllib.parse import urlsplit
    try:
        p = urlsplit(u)
    except ValueError:
        return False
    host = (p.hostname or '').lower()
    if p.scheme != 'https' or p.port not in (None, 443) or p.username or p.password:
        return False
    if host == SHORT_HOST:
        return bool(re.fullmatch(r'/[A-Za-z0-9_-]{4,64}', p.path)) and not p.query
    return any(host.endswith(h) for h in UPLOAD_HOSTS) and bool(p.query)


def curl_put(url, data, headers, timeout=900):
    """PUT one part with Windows' own curl, following Frame.io's short-link redirect (curl -L keeps PUT on a 308), https
    only. The command line is built here from checked values, never taken from Frame.io's script. Returns the HTTP status
    only when the bytes ended at a storage host Josh OS expects. Nothing about the URL is logged."""
    from urllib.parse import urlsplit
    if not safe_url(url):
        return 0
    curl = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'curl.exe'
    cmd = [str(curl), '-sS', '-L', '--max-redirs', '3', '--proto', '=https', '--proto-redir', '=https', '-X', 'PUT', url,
           '--data-binary', '@-', '-o', os.devnull, '-w', '%{http_code} %{url_effective}', '--max-time', str(timeout)]
    for k, v in (headers or {}).items():
        if k.lower() in SAFE_HEADERS and '\n' not in str(v):
            cmd += ['-H', f'{k}: {v}']
    p = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout + 60,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        code, eff = p.stdout.decode('ascii', 'replace').strip().split(' ', 1)
        host = (urlsplit(eff).hostname or '').lower()
    except ValueError:
        return 0
    if not any(host.endswith(h) for h in UPLOAD_HOSTS):
        return 0
    return int(code) if code.isdigit() else 0


def script_parts(script, size, hints=None):
    """Read a returned upload script as text, never running it. Takes its presigned URLs in order and works out each
    part's offset and size from what the script or plan states (one part size, the last part taking the rest). Keeps
    only Content-Type and x-amz-acl headers. Returns [] when anything is unclear."""
    s, hints = str(script or ''), hints or {}
    urls = []
    for m in re.finditer(r'https://[^\s"\'<>`]+', s):
        u = m.group(0).rstrip('\\').replace('\\u0026', '&').replace('&amp;', '&')
        if safe_url(u) and u not in urls:
            urls.append(u)
    if not urls:
        return []
    headers = {}
    for m in re.finditer(r'''-H\s+["']([A-Za-z0-9-]+)\s*:\s*([^"'\n]+)["']''', s):
        if m.group(1).lower() in SAFE_HEADERS:
            headers[m.group(1)] = m.group(2).strip()
    for m in re.finditer(r'''["']([A-Za-z0-9-]+)["']\s*:\s*["']([^"'\n]+)["']''', s):
        if m.group(1).lower() in SAFE_HEADERS and m.group(1) not in headers:
            headers[m.group(1)] = m.group(2).strip()
    ct = next((str(hints[k]) for k in ('media_type', 'content_type', 'mime_type') if hints.get(k)), None)
    headers = {k: (ct if k.lower() == 'content-type' and '$' in v and ct else v) for k, v in headers.items()}
    if any('$' in v or '{' in v for v in headers.values()):
        return []
    n = len(urls)
    part = next((hints[k] for k in ('part_size', 'chunk_size', 'part_size_bytes', 'chunk_size_bytes')
                 if isinstance(hints.get(k), int) and hints[k] > 0), None)
    if part is None:
        found = {int(x) for x in re.findall(r'(?i)\b(?:part|chunk)_?size\w*["\']?\s*[=:]\s*(\d+)\b', s)}
        part = found.pop() if len(found) == 1 else None
    if part is None and n == 1:
        part = size
    if not part:   # A part size is never guessed: a wrong one would send the wrong bytes to each URL.
        return []
    last = size - part * (n - 1)
    if not 0 < last <= part:
        return []
    return [dict(url=u, offset=i * part, size=part if i < n - 1 else last, headers=dict(headers)) for i, u in enumerate(urls)]


def plan_from_raw(raws, file_id, size):
    """(parts, how) from the exact upload_media result. Tries an explicit part list first, then the upload script read as
    text. Every URL must be a signed https upload URL. Any doubt gives ([], why), so nothing is guessed."""
    cands = upload_candidates(raws)
    if not cands:
        return [], 'no JSON in the result'
    if file_id and not any(file_id in _ids_in(c) for c in cands):
        return [], 'the result names a different file'
    for c in cands:
        parts = part_plan(c)
        if parts and all(safe_url(p['url']) for p in parts) and sum(p['size'] for p in parts) == size:
            if isinstance(c.get('part_count'), int) and c['part_count'] != len(parts):
                continue
            if c.get('file_size') not in (None, size):
                continue
            shared = next((x['upload_headers'] for x in [c] + cands if isinstance(x.get('upload_headers'), dict)
                           and (x is c or not file_id or x.get('file_id') == file_id)), {})
            for p in parts:
                h = dict(shared, **(p.get('headers') or {}))
                p['headers'] = {k: str(v) for k, v in h.items() if k.lower() in SAFE_HEADERS}
            return parts, 'part list'
    hints = {}
    for c in cands:
        for k, v in c.items():
            if k in ('media_type', 'content_type', 'mime_type', 'part_size', 'chunk_size', 'part_size_bytes', 'chunk_size_bytes', 'part_count'):
                hints.setdefault(k, v)
    scripts = [v for c in cands for k, v in c.items() if isinstance(v, str) and 'script' in k.lower() and 'https://' in v]
    for s in dict.fromkeys(scripts):
        parts = script_parts(s, size, hints)
        if parts and (not isinstance(hints.get('part_count'), int) or hints['part_count'] == len(parts)):
            return parts, 'upload script read as text'
    return [], 'no part list or readable upload script'


def run_frameio(root, prompt, schema, write=None, timeout=900, show=(), reads=None, keep=None):
    """One Claude run with only the Frame.io read tools, plus at most one guarded write. show lists write tools whose
    schemas may be looked up (for capture_schema): they stay unallowed, and the guard denies any call to them."""
    from .agent import find_claude
    claude = find_claude(Path(root))
    if not claude:
        raise RuntimeError('Claude Code is not set up on this PC.')
    with tempfile.TemporaryDirectory() as tmp:
        pol = Path(tmp) / 'frameio.json'
        reads = list(READS) if reads is None else list(reads)
        pol.write_text(json.dumps(dict(reads=reads, write=write)), encoding='utf-8')
        hook = subprocess.list2cmdline([Path(sys.executable).as_posix(), Path(__file__).with_name('frameio_guard.py').resolve().as_posix(), pol.as_posix()])
        allowed = reads + ([write['tool']] if write else [])
        # stream-json so the exact tool results come from Claude Code itself, never retyped by the model (an upload's
        # presigned part plan is far too long and exact to copy by hand).
        cmd = [claude, '-p', '--output-format', 'stream-json', '--verbose', '--no-session-persistence', '--tools', 'ToolSearch',
               '--allowedTools', ','.join(['ToolSearch'] + allowed),
               '--disallowedTools', ','.join(t for t in ALL_WRITES if t not in allowed and t not in show),
               '--permission-mode', 'dontAsk', '--json-schema', json.dumps(schema),
               '--settings', json.dumps({'hooks': {'PreToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': hook}]}]}})]
        # A presigned part plan is long. Claude Code cuts MCP results over MAX_MCP_OUTPUT_TOKENS, so it is raised here.
        env = dict(os.environ, MAX_MCP_OUTPUT_TOKENS='400000')
        out = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding='utf-8', timeout=timeout, cwd=str(root),
                             env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if keep:
        # The raw tool results are written before anything is parsed or judged, so an error later can't lose them.
        keep_raw(keep, stream_results(out.stdout)[0])
    return parse_stream(out.stdout)


def keep_raw(path, raw):
    """Write raw tool results to a private file (never logged or printed), replacing it atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(raw), encoding='utf-8')
    os.replace(tmp, path)


def parse_stream(text):
    """The structured answer plus every raw tool result, by tool name, from Claude Code's stream-json output."""
    raw, final = stream_results(text)
    if final is None:
        raise RuntimeError('Claude Code gave no result.')
    if final.get('is_error'):
        raise RuntimeError(str(final.get('result') or 'Claude Code reported a problem')[:300])
    out = dict(final.get('structured_output') or {})
    out['_tool_results'] = raw
    return out


def stream_results(text):
    """(raw tool results by tool name, final result event or None). Never raises."""
    names, raw, final = {}, {}, None
    for line in (text or '').splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        for c in ((ev.get('message') or {}).get('content') or []) if isinstance(ev.get('message'), dict) else []:
            if not isinstance(c, dict):
                continue
            if c.get('type') == 'tool_use':
                names[c.get('id')] = c.get('name')
            elif c.get('type') == 'tool_result':
                body = c.get('content')
                parts = body if isinstance(body, list) else [body]
                texts = [x.get('text') if isinstance(x, dict) else x for x in parts]
                # The whole block is kept as it came (structuredContent and all), next to the joined text.
                raw.setdefault(names.get(c.get('tool_use_id'), '?'), []).append(dict(text='\n'.join(str(x) for x in texts if x is not None),
                                                                                       is_error=bool(c.get('is_error')), block=c))
        if ev.get('type') == 'result':
            final = ev
    return raw, final


# Reviews -----------------------------------------------------------------------------------------------------------
class FrameioReview:
    def __init__(self, store, root, flow=None, auth=None, runner=None, probe=None, *, recover_interrupted=True):
        # Only server startup owns interruption recovery. A one-shot client beside the live server opts out.
        self.store, self.root, self.flow = store, Path(root), flow
        self._auth_cache = (0.0, None)
        self.auth = auth or self._cached_auth
        self._keep = None
        self.put_fn = None   # tests swap in a fake PUT; the real one is urllib
        self.runner =runner or (lambda prompt, schema, write=None, show=(), reads=None: run_frameio(
            self.root, prompt, schema, write, show=show, reads=reads, keep=self._keep))
        self.probe = probe
        self.dir = Path(store.root) / 'frameio'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS frameio_reviews(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,key TEXT UNIQUE NOT NULL,body TEXT NOT NULL)')
            if recover_interrupted:
                for r in c.execute('SELECT id,body FROM frameio_reviews').fetchall():
                    b = json.loads(r['body'])
                    if b.get('state') == 'uploading':
                        b.update(state='uncertain', reason='Josh OS stopped during the upload, so it is not sent again on its own. Check Frame.io.')
                        c.execute('UPDATE frameio_reviews SET body=? WHERE id=?', (json.dumps(b), r['id']))

    def _cached_auth(self):
        """Usable only when Claude Code reports exactly Connected AND a read-only account check came back with the account
        Josh chose (when he has chosen one)."""
        at, val = self._auth_cache
        if val is None or _time.monotonic() - at > 60:
            val = auth_status(self.root)
            if val['state'] == 'connected':
                val = self.account_check(val)
            self._auth_cache = (_time.monotonic(), val)
        return val

    def account_check(self, status):
        try:
            r = self.runner('Read only. Use read_context to report the Frame.io account id and the signed-in user\'s email. '
                            'Change nothing.', ACCOUNT_SCHEMA, None)
        except Exception as e:  # noqa: BLE001
            return dict(state='unverified', detail=f'Frame.io answered, but the account could not be checked ({str(e)[:120]}).')
        if not r.get('ok') or not r.get('account_id'):
            return dict(state='unverified', detail='Frame.io answered, but did not identify the account.')
        want = (self.settings().get('destination') or {}).get('account_id')
        if want and str(r['account_id']) != str(want):
            return dict(state='wrong_account', detail='Frame.io is signed in to a different account than the one chosen for review.')
        return dict(state='connected', detail='Frame.io is connected and the account was checked.', account_id=str(r['account_id']))

    def send(self, pid, approval, path=None):
        """Josh chose Send to Frame.io: stage it now and upload in the background once nothing blocks it."""
        b = self.stage(pid, approval, path)
        if b['state'] in ('staged', 'checked_absent') and not self.blockers(b):
            threading.Thread(target=self.upload, args=(b['id'],), daemon=True, name='frameio-upload').start()
        elif b['state'] == 'staged':
            b['reason'] = self.blockers(b)
            self._save(b)
        return b

    def latest(self, pid):
        rows = self.all(pid)
        if not rows:
            raise ValueError('Nothing has been sent to Frame.io for this video yet.')
        return rows[0]

    # Settings: the private review destination Josh chose -------------------------------------------------------
    def settings(self):
        try:
            return json.loads((self.dir / 'settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def set_destination(self, dest, by='Josh'):
        """Josh's chosen private review folder. Ids only, plus names for display. Never a share link."""
        need = ('account_id', 'workspace_id', 'project_id', 'folder_id', 'project_name')
        if not isinstance(dest, dict) or any(not str(dest.get(k) or '').strip() for k in need):
            raise ValueError('Choose the Frame.io project and folder first.')
        # A project that isn't restricted is visible to everyone who is ever in its workspace. It is only usable as the
        # review folder when Josh approved that exact alternative (unrestricted_approval with his words).
        made = self.setup().get('project') or {}
        if str(dest.get('project_id')) == str(made.get('project_id')) and made.get('restricted') is not True:
            ok = dest.get('unrestricted_approval') or {}
            if not str(ok.get('said') or '').strip():
                raise ValueError(f"{made.get('name', 'That project')} isn't restricted, so anyone added to the workspace later could see it. "
                                 'Josh has to approve using it like that first.')
        s = self.settings()
        s['destination'] = {k: str(dest[k])[:200] for k in need + ('folder_name', 'workspace_name') if dest.get(k)}
        restricted = (self.setup().get('project') or {}).get('restricted') if str(dest.get('project_id')) == str((self.setup().get('project') or {}).get('project_id')) else None
        s['destination'].update(chosen_by=by, chosen_at=now(), restricted=restricted,
                                unrestricted_approval=dest.get('unrestricted_approval') if restricted is False else None)
        (self.dir / 'settings.json').write_text(json.dumps(s, indent=1), encoding='utf-8')
        return s['destination']

    def destinations(self):
        """Josh's Frame.io workspaces, projects and their top folders, read only, for him to choose from. Nothing is
        created, and Josh OS never picks one itself."""
        a = self.auth()
        if a['state'] != 'connected':
            raise ValueError(a['detail'])
        schema = {'type': 'object', 'required': ['account_id', 'workspaces'], 'properties': {
            'account_id': {'type': 'string'}, 'account_name': {'type': ['string', 'null']},
            'workspaces': {'type': 'array', 'items': {'type': 'object', 'properties': {
                'workspace_id': {'type': 'string'}, 'workspace_name': {'type': 'string'},
                'projects': {'type': 'array', 'items': {'type': 'object', 'properties': {
                    'project_id': {'type': 'string'}, 'project_name': {'type': 'string'}, 'root_folder_id': {'type': ['string', 'null']},
                    'folders': {'type': 'array', 'items': {'type': 'object', 'properties': {
                        'folder_id': {'type': 'string'}, 'name': {'type': 'string'}}}}}}}}}}}}
        r = self.runner('Read only. Call mandatory_init if needed, then read_context to list the account id and name, every workspace '
                        'and project with ids, each project root folder id, and the top-level folders in each project. Change nothing.',
                        schema, None)
        if a.get('account_id') and str(r.get('account_id')) != a['account_id']:
            raise ValueError('Frame.io listed a different account than the one signed in, so nothing is shown.')
        return r

    # Records ------------------------------------------------------------------------------------------------------
    def all(self, pid=None):
        with self.store.connect() as c:
            q = 'SELECT body FROM frameio_reviews' + (' WHERE project_id=?' if pid else '') + ' ORDER BY rowid DESC'
            return [json.loads(r['body']) for r in c.execute(q, (pid,) if pid else ())]

    def get(self, rid):
        r = next((x for x in self.all() if x['id'] == rid), None)
        if not r:
            raise KeyError('That Frame.io review is not in Josh OS.')
        return r

    def _save(self, b, c=None):
        b['updated_at'] = now()
        if c is not None:
            c.execute('UPDATE frameio_reviews SET body=? WHERE id=?', (json.dumps(b), b['id']))
            return b
        with self.store.connect() as c2:
            c2.execute('UPDATE frameio_reviews SET body=? WHERE id=?', (json.dumps(b), b['id']))
        return b

    def _log(self, b, what, **kw):
        b.setdefault('history', []).append(dict(at=now(), what=what, **kw))

    # 1. Send ------------------------------------------------------------------------------------------------------
    def _resolve_cut(self, pid, cut_id, *, map_digest=None, require_digest=True):
        """Resolve this project's exact cut, never substitute its latest clean cut for a historical ID.

        Clean cuts are synthetic, not workflow_artifacts. clean_cut() rechecks the current approval, source revision,
        transcript and Premiere map. Export-bound callers must also supply their persisted map digest. A different
        reprojection target has no export snapshot yet, but must still be the exact current approved clean ID.
        """
        if not self.flow or not cut_id:
            raise ValueError("Josh OS can't tell which cut this export came from, so it won't send it.")
        if str(cut_id).startswith('clean-'):
            if not re.fullmatch(r'clean-[0-9a-f]{16}', str(cut_id)):
                raise ValueError('The clean cut ID is invalid.')
            if require_digest and not map_digest:
                raise ValueError('This export has no recorded clean cut map digest. It cannot be resolved safely.')
            resolve = getattr(self.flow, 'clean_cut', None)
            cut = resolve(pid) if callable(resolve) else None
            if not cut or cut.get('id') != cut_id or cut.get('project_id', pid) != pid:
                raise ValueError('This export belongs to a different or stale clean cut. Choose a current verified export.')
            plan = (cut.get('payload') or {}).get('plan') or {}
            if (cut.get('kind') != 'edit_cut' or cut.get('payload', {}).get('state') != 'verified'
                    or not isinstance(plan.get('map'), dict) or not plan.get('map_digest')
                    or ep.digest(plan['map']) != plan['map_digest']
                    or (map_digest is not None and map_digest != plan['map_digest'])):
                raise ValueError('The approved clean cut map no longer matches this export.')
        else:
            cut = self.flow.get(pid, cut_id)
        if not cut or cut.get('kind') != 'edit_cut' or cut.get('project_id', pid) != pid:
            raise ValueError("Josh OS can't tell which cut this export came from, so it won't send it.")
        return cut

    def latest_final(self, pid):
        """The newest verified final render, with the storyboard, cut and sequence it came from."""
        fin = next((f for f in (self.flow._all(pid, 'edit_final') if self.flow else []) if f['payload'].get('state') == 'verified'), None)
        if not fin:
            raise ValueError('There is no checked final export for this video yet.')
        vis = self.flow.get(pid, fin['parent']) if fin.get('parent') else None
        if not vis or vis.get('kind') != 'edit_visuals' or vis.get('project_id', pid) != pid:
            raise ValueError("Josh OS can't tell which storyboard this export came from, so it won't send it.")
        if str(vis.get('parent') or '').startswith('clean-') and not self.flow.approved(vis):
            raise ValueError('The storyboard for this clean cut is no longer the current approved revision.')
        cut = self._resolve_cut(pid, vis.get('parent'), map_digest=vis.get('payload', {}).get('map_digest'))
        return fin, vis, cut

    def fingerprint(self, path):
        f = Path(path)
        if not f.is_file():
            raise ValueError(f'{f.name} is missing.')
        st = f.stat()
        info = (self.probe or self._probe)(f)
        return dict(path=str(f), name=f.name, size=st.st_size, mtime_ns=str(st.st_mtime_ns), sha256=sha256(f),
                    duration=info['duration'], fps=float(info['fps']), frames=info.get('frames'),
                    width=info.get('width'), height=info.get('height'))

    @staticmethod
    def _probe(f):
        import av
        from dajoshi.media import probe
        info = probe(f)
        with av.open(str(f)) as c:
            info['frames'] = c.streams.video[0].frames or round(info['duration'] * float(info['fps']))
        return info

    def stage(self, pid, approval, path=None):
        """Josh chose Send to Frame.io for this export. Records exactly what would go up. Nothing is uploaded here."""
        if not isinstance(approval, dict) or not str(approval.get('said') or '').strip():
            raise ValueError('Josh has to choose to send it to Frame.io first.')
        fin, vis, cut = self.latest_final(pid)
        out = Path(fin['payload']['output'])
        if path and Path(path) != out:
            raise ValueError(f'{Path(path).name} is not the newest checked export for this video ({out.name}).')
        fp = self.fingerprint(out)
        plan = cut['payload']['plan']
        src = dict(final_id=fin['id'], visuals_id=vis['id'] if vis else None, cut_id=cut['id'], sequence_name=plan.get('sequence_name'),
                   map_digest=plan.get('map_digest'), proposal_revision=plan.get('revision'))
        if abs(fp['duration'] - plan['map']['duration_ticks'] / TICKS) > 0.5:
            raise ValueError("The export's length doesn't match its cut, so its comments couldn't be mapped. It wasn't sent.")
        dest = (self.settings().get('destination') or {})
        key = f"{fp['sha256']}:{dest.get('folder_id') or 'no-destination'}"
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT body FROM frameio_reviews WHERE key=?', (key,)).fetchone()
            if row:
                return json.loads(row['body'])
            b = dict(id=str(uuid.uuid4()), project_id=pid, key=key, export=fp, source=src, destination=dest or None,
                     approval=dict(approval, at=approval.get('at') or now()), state='staged', frameio={}, feedback=[], applied=[],
                     revisions=[], created_at=now(), history=[])
            self._log(b, 'staged', sha256=fp['sha256'])
            c.execute('INSERT INTO frameio_reviews VALUES(?,?,?,?)', (b['id'], pid, key, json.dumps(b)))
        return b

    def schema_doc(self):
        try:
            return json.loads((self.dir / 'schema.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def capture_schema(self):
        """Read Frame.io's own tool schemas (tool search only, no Frame.io call) and save them for inspection. Bindings
        are added only after someone checks the fields, so this alone never enables a write."""
        a = self.auth()
        if a['state'] != 'connected':
            raise ValueError(a['detail'])
        names = ','.join((UPLOAD, VERSIONS, P + 'read_comments', P + 'get_upload_status', P + 'search_assets'))
        r = self.runner(f'Use ToolSearch with query "select:{names}". Call no other tool. For each tool found, return its name and '
                        'its input JSON schema exactly as given.', SCHEMA_CAPTURE, None, show=(UPLOAD, VERSIONS), reads=())
        doc = self.schema_doc()
        doc['tools'] = {}
        for x in r.get('tools') or []:
            try:
                doc['tools'][x['name'].rsplit('__', 1)[-1]] = json.loads(x['input_schema_json'])
            except (ValueError, KeyError, TypeError):
                continue
        doc['captured_at'] = now()
        old = doc.get('bindings') or {}
        # A binding stays only while every field it names is still in the schema.
        doc['bindings'] = {k: v for k, v in old.items() if all(
            f in ((doc['tools'].get(k) or {}).get('properties') or {})
            for f in list((v.get('roles') or {}).values()) + list((v.get('fixed') or {}).keys()))}
        (self.dir / 'schema.json').write_text(json.dumps(doc, indent=1), encoding='utf-8')
        return doc

    def blockers(self, b):
        a = self.auth()
        if a['state'] != 'connected':
            return a['detail']
        if not b.get('destination'):
            return 'Josh has not chosen the private Frame.io project and folder for review yet.'
        try:
            build_input(self.schema_doc(), UPLOAD, self.upload_values(b))
        except SchemaNotVerified as e:
            return str(e)
        return ''

    def upload_values(self, b):
        d, ex = b.get('destination') or {}, b['export']
        return dict(path=ex['path'], folder_id=d.get('folder_id'), account_id=d.get('account_id'), size=ex['size'])

    # One-time setup: Josh's private review project (approved by Josh on 12 Nov 2026) --------------------------------
    def setup(self):
        try:
            return json.loads((self.dir / 'setup.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def _save_setup(self, s):
        (self.dir / 'setup.json').write_text(json.dumps(s, indent=1), encoding='utf-8')

    def find_projects(self, account_id, workspace_id, name=PROJECT_NAME):
        schema = {'type': 'object', 'required': ['complete', 'matches'], 'properties': {'complete': {'type': 'boolean'}, 'matches': {
            'type': 'array', 'items': {'type': 'object', 'properties': {'project_id': {'type': 'string'}, 'name': {'type': 'string'},
                                                                        'root_folder_id': {'type': ['string', 'null']},
                                                                        'restricted': {'type': ['boolean', 'null']}}}}}}
        r = self.runner(f'Read only. With account id {account_id}, use read_context and search_assets (include_projects true) to list '
                        f'every project in workspace {workspace_id} named exactly "{name}", with its id, root folder id and whether it is '
                        'restricted. Set complete true only if both lookups finished without error. Change nothing.', schema, None)
        return r

    def create_review_project(self, account_id, workspace_id, approval, sole_member_evidence=None):
        """Create the one private review project Josh approved, once. Checks first that it doesn't exist, and after an
        unclear result looks again before anything is tried twice. Tariq never gets the structure tool."""
        if not isinstance(approval, dict) or not str(approval.get('said') or '').strip():
            raise ValueError('Josh has to approve creating the review project.')
        a = self.auth()
        if a['state'] != 'connected' or a.get('account_id') != account_id:
            raise ValueError(a['detail'] if a['state'] != 'connected' else 'That is not the signed-in Frame.io account.')
        s = self.setup()
        if s.get('project', {}).get('project_id'):
            return s['project']
        found = self.find_projects(account_id, workspace_id)
        if found.get('complete') is not True:
            raise ValueError('The check for an existing review project did not finish, so nothing was created.')
        same = [m for m in found.get('matches') or [] if m.get('name') == PROJECT_NAME]
        if len(same) > 1:
            raise ValueError(f'There are already {len(same)} projects called {PROJECT_NAME}. Josh needs to say which one.')
        if same:
            s['project'] = dict(same[0], account_id=account_id, workspace_id=workspace_id, created_by_josh_os=False, at=now())
            self._save_setup(s)
            return s['project']
        last = s.get('create_attempt') or {}
        if last and not last.get('refused_by_frameio'):
            raise ValueError('An earlier create had an unclear result and the project is still not listed. Check Frame.io first.')
        doc = self.schema_doc()
        req = {'operation': 'create_project', 'account_id': account_id, 'workspace_id': workspace_id, 'name': PROJECT_NAME, 'restricted': True}
        if last.get('refused_by_frameio') == 'restricted_not_on_plan':
            # Josh's plan can't make restricted projects. Only when the workspace and the account have no one but Josh
            # (checked read only, just now) is an ordinary project still private to him.
            ev = sole_member_evidence or {}
            if not (ev.get('complete') and len(ev.get('workspace_members') or []) == 1 and len(ev.get('account_members') or []) == 1
                    and all(m.get('is_signed_in_user') for m in ev['workspace_members'] + ev['account_members']) and not ev.get('pending_invites')):
                raise ValueError("The plan can't make restricted projects, and the workspace isn't shown to be Josh's alone, so nothing was created.")
            req.pop('restricted')
        inp = {'request': req, 'user_intent': 'Create the private CareerOS Reviews project Josh approved for video review'}
        sch = (doc.get('tools') or {}).get('change_structure') or {}
        if not sch:
            raise SchemaNotVerified("Frame.io's change_structure schema hasn't been captured, so nothing was created.")
        s['create_attempt'] = dict(at=now(), approval=approval, restricted=req.get('restricted', False),
                                   sole_member_evidence=sole_member_evidence if 'restricted' not in req else None)
        self._save_setup(s)
        schema = {'type': 'object', 'required': ['ok'], 'properties': {'ok': {'type': 'boolean'}, 'project_id': {'type': ['string', 'null']},
                                                                       'root_folder_id': {'type': ['string', 'null']}, 'error': {'type': ['string', 'null']}}}
        try:
            r = self.runner('Make exactly one change_structure call with exactly this input, no other fields and no changed values: '
                            + json.dumps(inp) + ' Then report ok, the new project id and its root folder id. Do not share, invite or '
                            'change anything else.', schema, dict(tool=STRUCTURE, input=inp))
        except Exception as e:  # noqa: BLE001
            r = dict(ok=False, error=str(e)[:200])
        found = self.find_projects(account_id, workspace_id)
        same = [m for m in found.get('matches') or [] if m.get('name') == PROJECT_NAME]
        if found.get('complete') is True and len(same) == 1:
            s['project'] = dict(same[0], account_id=account_id, workspace_id=workspace_id, created_by_josh_os=True, at=now(),
                                create_reported=dict(ok=r.get('ok'), project_id=r.get('project_id')))
            self._save_setup(s)
            return s['project']
        err = str(r.get('error') or '')
        if found.get('complete') is True and not same and 'does not allow restricted projects' in err and 'No project was created' in err:
            s['create_attempt'] = dict(s['create_attempt'], refused_by_frameio='restricted_not_on_plan', error=err[:300])
            self._save_setup(s)
        raise ValueError('Frame.io did not confirm the new project: ' + (err or 'it is not listed yet') + ' Nothing is tried again on its own.')

    def verify_private(self, project):
        """Read only: the project is restricted, has no shares and lists only who is a direct member."""
        schema = {'type': 'object', 'required': ['complete'], 'properties': {
            'complete': {'type': 'boolean'}, 'restricted': {'type': ['boolean', 'null']}, 'share_count': {'type': ['integer', 'null']},
            'members': {'type': 'array', 'items': {'type': 'object', 'properties': {'name': {'type': ['string', 'null']},
                                                                                     'email': {'type': ['string', 'null']},
                                                                                     'role': {'type': ['string', 'null']}}}},
            'error': {'type': ['string', 'null']}}}
        reads = list(READS) + [P + 'read_shares', P + 'read_members']
        r = self.runner(f'Read only. For Frame.io project id {project["project_id"]} in account {project["account_id"]}: report whether it is '
                        'restricted (read_context or read_assets), list its shares with read_shares (query action list) and count them, and '
                        'list its members with read_members (scope project). Set complete true only if all three finished without error. '
                        'Change nothing.', schema, None, reads=reads)
        return r

    def check_destination(self, rid):
        """After an unclear or failed upload, look in the exact review folder (read only) before anything is sent again.
        Only a complete, error-free listing of that exact folder can say the file is absent. Exactly one file with the same
        name and size is adopted. Anything else keeps the upload uncertain."""
        b = self.get(rid)
        d, ex = b.get('destination') or {}, b['export']
        a = self.auth()
        if a['state'] != 'connected':
            raise ValueError(a['detail'])
        if not d:
            raise ValueError('No review folder chosen.')
        try:
            r = self.runner(f'Read only. List every page of Frame.io folder id {d["folder_id"]} in project id {d["project_id"]} '
                            f'and report every file named exactly "{ex["name"]}", with its file id, size in bytes, status and view link. '
                            'Set complete to true only if you listed the whole folder without an error. Report the account, project '
                            'and folder ids you read. Do not change anything.', CHECK_SCHEMA, None)
        except Exception as e:  # noqa: BLE001 - nothing learned, so nothing changes
            b = self.get(rid)
            b['reason'] = f'The check could not finish ({str(e)[:150]}), so the upload stays unclear.'
            return self._save(b)
        b = self.get(rid)
        why = ''
        if r.get('error') or r.get('complete') is not True:
            why = 'The folder check was incomplete or reported an error, so the upload stays unclear.'
        elif str(r.get('folder_id') or '') != d['folder_id'] or str(r.get('project_id') or '') != d['project_id']:
            why = 'The check read a different project or folder, so the upload stays unclear.'
        elif d.get('account_id') and str(r.get('account_id') or '') != d['account_id']:
            why = 'The check read a different account, so the upload stays unclear.'
        if why:
            b['reason'] = why
            self._log(b, 'check inconclusive')
            return self._save(b)
        same = [c for c in r.get('candidates') or [] if c.get('name') == ex['name']]
        exact = [c for c in same if isinstance(c.get('size'), int) and c['size'] == ex['size'] and c.get('file_id')]
        if len(same) > 1:
            b['reason'] = f'{len(same)} files called {ex["name"]} are in the folder, so Josh OS will not guess which one is this export.'
            self._log(b, 'check ambiguous')
        elif len(exact) == 1:
            c = exact[0]
            b['frameio'] = dict(file_id=str(c['file_id']), view_url=str(c.get('view_url') or '')[:500], status=str(c.get('status') or ''))
            b['state'] = 'ready' if str(c.get('status') or '').lower() in ('ready', 'complete', 'completed', 'transcoded') else 'processing'
            b['reason'] = 'Found in the review folder after an unclear upload, so it was not sent again.'
            self._log(b, 'found after check', file_id=b['frameio']['file_id'])
        elif same:
            b['reason'] = 'A file with this name is there, but its size is missing or different, so the upload stays unclear.'
            self._log(b, 'check mismatch')
        else:
            b['state'] = 'checked_absent'
            b['reason'] = 'The whole folder was checked and it is not there, so it can be sent again when you choose.'
            self._log(b, 'checked, absent')
        return self._save(b)

    def upload(self, rid):
        """Upload the staged export once, by one guarded call, then check it processed."""
        with self.lock:
            b = self.get(rid)
            if b['state'] in ('ready', 'processing', 'uploading', 'putting'):
                return b
            if b['state'] in ('uncertain', 'failed'):
                raise ValueError("The last upload didn't finish clearly, so Josh OS checks the review folder first (Check Frame.io). "
                                 'It is never sent again blind.')
            why = self.blockers(b)
            if why:
                b.update(state='staged', reason=why)
                return self._save(b)
            # Recheck synthetic lineage immediately before the first external write, not only when staged.
            if str(b['source']['cut_id']).startswith('clean-'):
                fin, vis, cut = self.latest_final(b['project_id'])
                expected = (b['source']['final_id'], b['source']['visuals_id'], b['source']['cut_id'], b['source']['map_digest'])
                actual = (fin['id'], vis['id'], cut['id'], cut['payload']['plan']['map_digest'])
                if actual != expected:
                    raise ValueError('The chosen export lineage changed after staging. Choose the current verified export again.')
            ex = b['export']
            st = Path(ex['path']).stat() if Path(ex['path']).is_file() else None
            if not st or st.st_size != ex['size'] or str(st.st_mtime_ns) != ex['mtime_ns'] or sha256(ex['path']) != ex['sha256']:
                b.update(state='blocked', reason='The export changed after it was chosen, so it was not sent. Choose it again.')
                return self._save(b)
            b.update(state='uploading', reason='')
            self._log(b, 'uploading')
            self._save(b)
        d = b['destination']
        inp = build_input(self.schema_doc(), UPLOAD, self.upload_values(b))
        write = dict(tool=UPLOAD, input=inp)
        prompt = '\n'.join([
            'Call mandatory_init first if Frame.io asks for it. Then make exactly one upload_media call with exactly this input, '
            'no other fields and no changed values:', json.dumps(inp, ensure_ascii=False),
            'Do not share it, invite anyone, create a share link, move, rename, delete or comment on anything.',
            'Never run, copy or retype any upload script or URL from the result. Josh OS reads the exact result itself.',
            'After the upload starts, use get_upload_status to check it, and report the new file id, its status and its view link.'])
        rawf = self.dir / 'plans' / f'{rid}-raw.json'
        rawf.unlink(missing_ok=True)
        r, err = {}, None
        self._keep = rawf   # the real runner writes the exact tool results here before parsing anything
        try:
            r = self.runner(prompt, UPLOAD_SCHEMA, write)
        except Exception as e:  # noqa: BLE001 - it may or may not have created a file
            err = e
        finally:
            self._keep = None
        # The exact upload_media result, from Claude Code's stream. It is kept privately before anything is judged, so
        # whatever happens next the same file can be resumed with the same URLs. Never printed or logged.
        raws = None
        if rawf.is_file():
            try:
                raws = json.loads(rawf.read_text(encoding='utf-8')).get(UPLOAD)
            except (ValueError, AttributeError):
                raws = None
        if raws is None:
            raws = (r.get('_tool_results') or {}).get(UPLOAD) or []
            keep_raw(rawf, {UPLOAD: raws})
        b = self.get(rid)
        fid = created_file_id(raws)
        if fid and r.get('file_id') and str(r['file_id']) != fid:
            fid = None   # the model named a different file: trust neither
        if not fid:
            b.update(state='uncertain', reason=(str(r.get('error') or err or 'Frame.io gave no file id in its own result.')[:300]
                                                + ' Not sent again on its own. Check Frame.io.'))
            self._log(b, 'uncertain', model_file_id=str(r.get('file_id') or ''))
            return self._save(b)
        b['frameio'] = dict(file_id=fid, view_url=str(r.get('view_url') or '')[:500], status=str(r.get('status') or ''))
        self._log(b, 'file created', file_id=fid)
        return self._plan_and_put(b, raws)

    def _plan_and_put(self, b, raws):
        rid, fid = b['id'], b['frameio']['file_id']
        parts, how = plan_from_raw(raws, fid, b['export']['size'])
        if parts:
            # Presigned upload URLs are kept only in this private plan file, never in the record or logs.
            (self.dir / 'plans' / f'{rid}.json').write_text(json.dumps(dict(parts=parts, done=[], file_id=fid)), encoding='utf-8')
            b.update(state='putting', reason=f'Sending {len(parts)} parts.',
                     progress=dict(parts=len(parts), parts_done=0, bytes_sent=0, bytes_total=b['export']['size'], plan_from=how))
            self._log(b, 'plan read', parts=len(parts), how=how)
            self._save(b)
            return self.put_parts(rid)
        b['state'] = 'uncertain'
        b['reason'] = (f"Frame.io made file {fid} but Josh OS couldn't read its upload plan safely ({how}), so no bytes were sent. "
                       'The exact result is kept privately, so the same file can be resumed without another upload.')
        self._log(b, 'plan unreadable', file_id=fid, why=how)
        return self._save(b)

    EMPTY_SCHEMA = {'type': 'object', 'required': ['complete'], 'properties': {
        'complete': {'type': 'boolean'}, 'error': {'type': ['string', 'null']}, 'file_id': {'type': ['string', 'null']},
        'status': {'type': ['string', 'null']}, 'upload_complete': {'type': ['boolean', 'null']}, 'upload_failed': {'type': ['boolean', 'null']},
        'has_proxy_or_stream': {'type': ['boolean', 'null']}, 'parts_sent': {'type': ['integer', 'null']}}}

    def replace_empty_upload(self, rid):
        """One replacement upload, only when the first file Frame.io made provably received no bytes and can't be resumed
        (its part URLs exist only in the first upload_media result). The empty file stays in Frame.io with its own audit
        entry, recorded apart from the new one. Allowed once per review, never in a loop."""
        b = self.get(rid)
        old = dict(b.get('frameio') or {})
        if b['state'] != 'uncertain' or not old.get('file_id'):
            raise ValueError('There is no stuck upload to replace.')
        if b.get('replacement'):
            raise ValueError('A replacement upload was already tried once for this review. Nothing is sent again on its own.')
        if (self.dir / 'plans' / f'{rid}.json').exists():
            raise ValueError('Josh OS still has this upload\'s part plan, so it resumes instead of replacing.')
        d = b['destination']
        r = self.runner(f'Read only. With account id {d["account_id"]}, call get_upload_status and read_assets for file id {old["file_id"]}. '
                        'Report its status, whether the upload is complete or failed, whether it has any proxy, stream or thumbnail, and how '
                        'many parts were sent if Frame.io says. Set complete true only if both calls finished without error. Change nothing.',
                        self.EMPTY_SCHEMA, None)
        empty = (r.get('complete') is True and not r.get('error') and str(r.get('file_id') or old['file_id']) == old['file_id']
                 and r.get('upload_complete') is False and r.get('has_proxy_or_stream') is False
                 and str(r.get('status') or '').lower() in ('created', 'pending') and r.get('parts_sent') in (None, 0))
        if not empty:
            b['reason'] = 'The first Frame.io file is not provably empty, so no replacement was sent.'
            self._log(b, 'replacement refused', check={k: r.get(k) for k in ('status', 'upload_complete', 'has_proxy_or_stream', 'parts_sent')})
            return self._save(b)
        b['replacement'] = dict(at=now(), empty_stub=dict(file_id=old['file_id'], status=r.get('status'), upload_complete=False,
                                                          left_in_frameio=True, reason='No bytes sent; its part URLs were not kept.'))
        b['frameio'] = {}
        b['state'] = 'checked_absent'
        b['reason'] = f"Replacing empty Frame.io file {old['file_id']} (left in place) with one new upload."
        self._log(b, 'replacing empty stub', empty_stub=old['file_id'])
        self._save(b)
        b = self.upload(rid)
        b['replacement']['new_file_id'] = (b.get('frameio') or {}).get('file_id')
        return self._save(b)

    def raw_shape(self, rid):
        """The kept upload_media result's structure for diagnosis: keys, list lengths and types, with every URL and long
        value hidden. Never returns a URL."""
        f = self.dir / 'plans' / f'{rid}-raw.json'
        if not f.is_file():
            return None

        def shape(v):
            if isinstance(v, dict):
                return {k: shape(x) for k, x in list(v.items())[:40]}
            if isinstance(v, list):
                return [f'list[{len(v)}]', shape(v[0])] if v else 'list[0]'
            if isinstance(v, str):
                return 'URL' if v.startswith('http') else ('text' if len(v) > 60 or 'http' in v else v)
            return v
        out = []
        for res in self._kept_raws(rid):
            objs = _json_objects(res.get('text'))
            out.append(dict(text=[shape(o) for o in objs] if objs else f"non-JSON result, {len(str(res.get('text')))} characters",
                            structured=shape((res.get('block') or {}).get('structuredContent')) if isinstance(res.get('block'), dict) else None,
                            is_error=res.get('is_error')))
        return out

    def _kept_raws(self, rid):
        """The kept upload_media results. Old files hold a list, new ones every tool's results by name."""
        f = self.dir / 'plans' / f'{rid}-raw.json'
        if not f.is_file():
            return []
        v = json.loads(f.read_text(encoding='utf-8'))
        return (v.get(UPLOAD) or []) if isinstance(v, dict) else v

    def resume_from_raw(self, rid):
        """Read the kept upload_media result again (after the parser learns its shape) and send the parts to the same
        URLs, for the same Frame.io file. upload_media is never called."""
        b = self.get(rid)
        fid = (b.get('frameio') or {}).get('file_id')
        raws = self._kept_raws(rid)
        if not raws or not fid:
            raise ValueError('There is no kept upload result to resume from.')
        if created_file_id(raws) != fid:
            raise ValueError('The kept result is for a different Frame.io file, so nothing was sent.')
        if b['state'] in ('ready', 'processing'):
            return b
        parts, how = plan_from_raw(raws, fid, b['export']['size'])
        if not parts:
            raise ValueError(f"Josh OS still can't read this upload plan safely ({how}), so nothing was sent.")
        f = self.dir / 'plans' / f'{rid}.json'
        if not f.is_file():
            f.write_text(json.dumps(dict(parts=parts, done=[], file_id=fid)), encoding='utf-8')
        b.update(state='putting', reason=f'Sending {len(parts)} parts from the kept plan.',
                 progress=dict(parts=len(parts), parts_done=0, bytes_sent=0, bytes_total=b['export']['size'], plan_from=how))
        self._log(b, 'resumed from kept result', file_id=fid, parts=len(parts), how=how)
        self._save(b)
        return self.put_parts(rid)

    def put_parts(self, rid, putter=None):
        """PUT each part of the export to its presigned URL, resuming after the parts already sent. A part that fails is
        tried again at the same URL, as Frame.io says; upload_media is never called again."""
        import urllib.request
        b = self.get(rid)
        f = self.dir / 'plans' / f'{rid}.json'
        plan = json.loads(f.read_text(encoding='utf-8'))
        ex = b['export']
        put = putter or self.put_fn or curl_put
        total = sum(x['size'] for x in plan['parts'])
        if total != ex['size']:
            b.update(state='uncertain', reason=f"Frame.io's part plan covers {total} bytes, not {ex['size']}, so nothing was sent.")
            return self._save(b)
        st = Path(ex['path']).stat() if Path(ex['path']).is_file() else None
        if not st or st.st_size != ex['size'] or str(st.st_mtime_ns) != ex['mtime_ns']:
            b.update(state='putting', reason='The export file changed or is missing, so no more parts were sent.')
            return self._save(b)
        n = len(plan['parts'])
        with open(ex['path'], 'rb') as fh:
            for i, part in enumerate(plan['parts']):
                if i in plan['done']:
                    continue
                fh.seek(part['offset'])
                data = fh.read(part['size'])
                if len(data) != part['size']:
                    b = self.get(rid)
                    b.update(state='putting', reason=f'Part {i + 1} could not be read in full from the export, so it was not sent.')
                    return self._save(b)
                ok = False
                for _ in range(3):
                    try:
                        status = put(part['url'], data, dict(part.get('headers') or {}))
                        ok = 200 <= int(status) < 300
                    except Exception:  # noqa: BLE001 - retried at the same URL
                        ok = False
                    if ok:
                        break
                    _time.sleep(3)
                if not ok:
                    b = self.get(rid)
                    b.update(state='putting', reason=f'Part {i + 1} of {len(plan["parts"])} did not go through. Resume sends the rest to the same URLs.')
                    return self._save(b)
                plan['done'].append(i)
                f.write_text(json.dumps(plan), encoding='utf-8')
                # Real progress for the status line: parts and bytes Frame.io's storage accepted, never a guessed badge.
                b = self.get(rid)
                sent = sum(plan['parts'][j]['size'] for j in set(plan['done']))
                b['progress'] = dict(b.get('progress') or {}, parts=n, parts_done=len(set(plan['done'])), bytes_sent=sent,
                                     bytes_total=ex['size'], at=now())
                b['reason'] = f'Sent {len(set(plan["done"]))} of {n} parts ({sent * 100 // ex["size"]}%).'
                self._save(b)
        b = self.get(rid)
        b.update(state='processing', reason='All parts sent. Waiting for Frame.io to finish processing.')
        self._log(b, 'parts sent', parts=n, bytes_sent=sum(x['size'] for x in plan['parts']))
        self._save(b)
        return self.poll(rid)

    def poll(self, rid, tries=1):
        """Read-only: has Frame.io finished the upload and can it play?"""
        b = self.get(rid)
        fid, d = b['frameio']['file_id'], b['destination']
        r = {}
        for _ in range(tries):
            r = self.runner(f'Read only. With account id {d["account_id"]}, use get_upload_status for file id {fid}, then read_assets for that '
                            'file. Report whether the upload is complete, its status, whether Frame.io lists a playable or streamable media '
                            'link, its duration, size and its signed-in web view link (not a share link). Change nothing.', STATUS_SCHEMA, None)
            if r.get('upload_complete') and r.get('playable'):
                break
            _time.sleep(20)
        b = self.get(rid)
        # Frame.io's own get_upload_status result wins over the model's summary when the two disagree.
        for res in ((r.get('_tool_results') or {}).get(P + 'get_upload_status') or []):
            for o in _json_objects(res.get('text')):
                if o.get('file_id') == fid and isinstance(o.get('upload_complete'), bool):
                    r['upload_complete'] = o['upload_complete'] and r.get('upload_complete') is not False
        b['frameio'].update(status=str(r.get('status') or ''), view_url=str(r.get('view_url') or b['frameio'].get('view_url') or '')[:500],
                            upload_complete=r.get('upload_complete'), playable=r.get('playable'), duration=r.get('duration'),
                            file_size=r.get('file_size'))
        want_dur = b['export'].get('duration')
        dur_ok = r.get('duration') is None or not want_dur or abs(float(r['duration']) - float(want_dur)) <= 1.0
        if r.get('upload_complete') and r.get('playable') and r.get('file_size') == b['export']['size'] and dur_ok:
            b.update(state='ready', reason='Uploaded and playable in Frame.io.')
            (self.dir / 'plans' / f'{rid}.json').unlink(missing_ok=True)
            (self.dir / 'plans' / f'{rid}-raw.json').unlink(missing_ok=True)
        elif r.get('upload_complete') and r.get('file_size') not in (None, b['export']['size']):
            b.update(state='uncertain', reason=f"Frame.io reports {r.get('file_size')} bytes, not {b['export']['size']}, so this is not the export.")
        elif r.get('upload_complete') and not dur_ok:
            b.update(state='uncertain', reason=f"Frame.io reports {r.get('duration')} s, not {want_dur} s, so it doesn't match the export.")
        else:
            b.update(state='processing', reason=r.get('error') or ('Upload complete. Frame.io is still processing it for playback.'
                                                                   if r.get('upload_complete') else 'Frame.io has not confirmed the upload yet.'))
        self._log(b, 'status checked', status=b['frameio']['status'], playable=r.get('playable'), upload_complete=r.get('upload_complete'),
                  file_size=r.get('file_size'), duration=r.get('duration'))
        return self._save(b)

    def stack_revision(self, reviewed_id, revised_id, approval):
        """Stack a revised export, already uploaded to the same folder, on top of the reviewed version. One guarded
        manage_versions call with exactly those two Frame.io file ids. Both originals stay, nothing is replaced."""
        if not isinstance(approval, dict) or not str(approval.get('said') or '').strip():
            raise ValueError('Josh has to ask for the revision to be stacked first.')
        old, new = self.get(reviewed_id), self.get(revised_id)
        if old['project_id'] != new['project_id'] or old.get('destination') != new.get('destination'):
            raise ValueError('Both versions must be the same video in the same review folder.')
        if old['export']['sha256'] == new['export']['sha256']:
            raise ValueError('That is the same export, not a revision.')
        ids = (old.get('frameio') or {}).get('file_id'), (new.get('frameio') or {}).get('file_id')
        if not all(ids) or new['state'] not in ('ready', 'processing'):
            raise ValueError('Both versions need to be in Frame.io first.')
        a = self.auth()
        if a['state'] != 'connected':
            raise ValueError(a['detail'])
        if any(r.get('reviewed_id') == reviewed_id and r.get('revised_id') == revised_id for r in old.get('revisions') or []):
            return old
        d = old.get('destination') or {}
        inp = build_input(self.schema_doc(), VERSIONS, dict(account_id=d.get('account_id'), folder_id=d.get('folder_id'),
                                                            file_ids=[ids[0], ids[1]]))
        try:
            r = self.runner('Make exactly one manage_versions call with exactly this input, no other fields and no changed values: '
                            + json.dumps(inp) + ' It stacks the revised file as the newest version on the reviewed one. '
                            'Do not delete, move, share or change anything else. Report ok and the version stack id.',
                            {'type': 'object', 'required': ['ok'], 'properties': {'ok': {'type': 'boolean'}, 'version_stack_id': {'type': ['string', 'null']},
                                                                                  'error': {'type': ['string', 'null']}}},
                            dict(tool=VERSIONS, input=inp))
        except Exception as e:  # noqa: BLE001
            r = dict(ok=False, error=str(e)[:200])
        old = self.get(reviewed_id)
        entry = dict(reviewed_id=reviewed_id, revised_id=revised_id, revised_sha256=new['export']['sha256'], at=now(),
                     ok=bool(r.get('ok')), version_stack_id=r.get('version_stack_id'), error=r.get('error'), approval=approval)
        old.setdefault('revisions', []).append(entry)
        self._log(old, 'revision stacked' if entry['ok'] else 'revision stack unclear')
        return self._save(old)

    # 3. Feedback ----------------------------------------------------------------------------------------------------
    def import_feedback(self, rid):
        """Read this exact file's comments, read only, and keep them with this version."""
        b = self.get(rid)
        fid = (b.get('frameio') or {}).get('file_id')
        if not fid:
            raise ValueError('This version is not on Frame.io yet.')
        why = self.blockers(b)
        if why:
            raise ValueError(why)
        cut = self._resolve_cut(b['project_id'], b['source']['cut_id'], map_digest=b['source'].get('map_digest')) if self.flow or str(b['source']['cut_id']).startswith('clean-') else None
        cmap = cut['payload']['plan']['map'] if cut else {}
        prompt = (f'Read only. Use read_comments for Frame.io file id {fid} (this exact version, not others in its stack). '
                  'Return every comment and reply as Frame.io gives them. Give frame stamps exactly as Frame.io reports them '
                  '(they start at 1) and never convert them. Do not write, resolve or change anything.')
        r = self.runner(prompt, FEEDBACK_SCHEMA, None)
        ex = b['export']
        # Frame.io's own read_comments result wins. Its "timestamp" is a timecode HH:MM:SS:FF on this export, not a frame
        # number, and it is converted here, never by the model.
        raw = raw_comments(r.get('_tool_results'), fid)
        if raw is None and (not r.get('file_found') or (r.get('file_id') and str(r['file_id']) != fid)):
            raise ValueError("Frame.io didn't return that exact file, so no feedback was imported.")
        out = []
        if raw is not None:
            for c in raw:
                item = dict(id=str(c.get('id')), text=str(c.get('text') or '')[:4000], author=(c.get('owner') or {}).get('name'),
                            completed=c.get('completed'), has_annotation=c.get('has_annotation'), replies=c.get('replies') or [],
                            file_id=fid, export_sha256=ex['sha256'], timecode=c.get('timestamp'), times_from='frameio')
                sec = timecode_seconds(c.get('timestamp'), ex['fps'])
                if sec is None:
                    item.setdefault('problems', []).append(f"Frame.io's time {c.get('timestamp')!r} could not be read, so it has no time.")
                elif not 0 <= sec <= float(ex.get('duration') or 10 ** 9):
                    item.setdefault('problems', []).append(f'{c.get("timestamp")} is outside this version.')
                else:
                    item['seconds_start'] = sec
                    # The cut map is only an estimate (it has been off by seconds); place it by the export's sound.
                    item['map_source_ticks_start'] = to_source(sec, cmap)
                    if c.get('duration') not in (None, 0):
                        item.setdefault('problems', []).append(f"Frame.io gave a range length {c.get('duration')!r} in a unit Josh OS hasn't confirmed.")
                item['at'] = stamp(sec) if sec is not None else 'no time'
                out.append(item)
        for c in (r.get('comments') or []) if raw is None else []:
            item = dict(id=str(c['id']), text=str(c['text'])[:4000], author=c.get('author'), completed=c.get('completed'),
                        has_annotation=c.get('has_annotation'), replies=c.get('replies') or [], file_id=fid, export_sha256=ex['sha256'])
            for k in ('frame_start', 'frame_end'):
                f = c.get(k)
                if f is None:
                    continue
                if not isinstance(f, int) or not 1 <= f <= (ex.get('frames') or 10 ** 9):
                    item.setdefault('problems', []).append(f'{k} {f} is outside this version, which has {ex.get("frames")} frames.')
                    continue
                sec = frame_to_seconds(f, ex['fps'])
                item[k.replace('frame', 'seconds')] = sec
                item[k.replace('frame', 'source_ticks')] = to_source(sec, cmap)
            item['at'] = stamp(item['seconds_start']) if 'seconds_start' in item else 'no time'
            if 'seconds_end' in item:
                item['at'] += ' to ' + stamp(item['seconds_end'])
            out.append(item)
        b['feedback'] = out
        b['feedback_read_at'] = now()
        if b.get('review_closed') and b['review_closed'].get('feedback_read_at') != b['feedback_read_at']:
            b['review_closed'] = dict(b['review_closed'], reopened_at=b['feedback_read_at'], reason='New comments were imported.')
        self._log(b, 'feedback imported', count=len(out))
        return self._save(b)

    def close_review(self, rid, approval):
        """Josh says the review is finished. Only after the latest comments were imported and each one is done. Bound to
        that import, so comments imported later reopen it. "ready" alone only means the upload plays."""
        if not isinstance(approval, dict) or approval.get('by') != 'Josh' or approval.get('trusted') is not True:
            raise ValueError('Only Josh can close a review, from Josh OS itself.')
        b = self.get(rid)
        if b.get('state') != 'ready' or not b.get('feedback_read_at'):
            raise ValueError('Import the latest comments first.')
        left = [c for c in b.get('feedback') or [] if not c.get('completed')]
        if left:
            raise ValueError(f'{len(left)} comment(s) are not done yet.')
        b['review_closed'] = dict(by='Josh', at=now(), feedback_read_at=b['feedback_read_at'], said=str(approval.get('said') or '')[:300])
        self._log(b, 'review closed by Josh')
        return self._save(b)

    def for_cut(self, rid, cut_id):
        """This version's comments placed on another cut. A moment that was cut out is flagged, never guessed."""
        b = self.get(rid)
        same = cut_id == b['source']['cut_id']
        # Do not let the same-ID shortcut conceal a changed approval or map for a synthetic cut.
        if same and str(cut_id).startswith('clean-'):
            self._resolve_cut(b['project_id'], cut_id, map_digest=b['source'].get('map_digest'))
        if same:
            return [dict(c, now_at=c['at'], moved=False) for c in b['feedback']]
        cut = self._resolve_cut(b['project_id'], cut_id, require_digest=False)
        cmap = cut['payload']['plan']['map']
        out = []
        for c in b['feedback']:
            if c.get('source_ticks_start') is None:
                out.append(dict(c, now_at=None, moved=True, note='No time, or its time could not be tied to the recording.'))
                continue
            s = from_source(c['source_ticks_start'], cmap)
            e = from_source(c['source_ticks_end'], cmap) if c.get('source_ticks_end') is not None else None
            if s is None:
                out.append(dict(c, now_at=None, moved=True, note='That moment was cut out of the newer cut.'))
            else:
                out.append(dict(c, now_at=stamp(s) + (' to ' + stamp(e) if e is not None else ''), moved=True))
        return out

    def context(self, pid):
        """What Tariq reads in his chat: the reviewed version and its feedback, never mixed with another version."""
        rows = self.all(pid)
        if not rows:
            return ''
        b = rows[0]
        lines = [f"Frame.io review of {b['export']['name']} (SHA-256 {b['export']['sha256'][:12]}, {b['export']['fps']:g} fps, "
                 f"{b['export']['frames']} frames, from cut {b['source']['sequence_name']}): {b['state']}"
                 + (f", {b['reason']}" if b.get('reason') else '') + '.']
        if b.get('feedback'):
            lines.append(f"Feedback on that exact version, read {b.get('feedback_read_at', '')[:16]} (times are on that version):")
            for c in b['feedback'][:60]:
                lines.append(f"- {c['at']} {'(done) ' if c.get('completed') else ''}{c.get('author') or 'Josh'}: {c['text'][:400]}")
            lines.append('These are imported comments only. Nothing has been changed for them yet unless an applied edit is listed.')
        for a in b.get('applied') or []:
            lines.append(f"- Applied: {a.get('what')}")
        return '\n'.join(lines)

    def status(self, pid, rid):
        """Exact-review, local-only chat status. Never refresh auth or remote state."""
        from .frameio_progress import present_review, safe_id
        if not safe_id(pid) or not safe_id(rid):
            raise KeyError('Review not found.')
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM frameio_reviews WHERE id=? AND project_id=?', (rid, pid)).fetchone()
        if not row:
            raise KeyError('Review not found.')
        record = json.loads(row['body'])
        if record.get('id') != rid or record.get('project_id') != pid:
            raise KeyError('Review not found.')
        return present_review(record)

    def view(self, pid):
        rows = self.all(pid)
        a = self.auth()
        dest = self.settings().get('destination')
        for r in rows[:1]:
            # A staged send's blocker is worked out now, not when it was staged, and it picks up the chosen folder.
            if r.get('state') in ('staged', 'checked_absent'):
                if dest and not r.get('destination') and r.get('state') == 'staged':
                    r['destination'] = dest
                r['reason'] = self.blockers(r) or 'Ready to send when you choose.'

        return dict(connection=a, destination=dest, reviews=[{k: r.get(k) for k in ('id', 'state', 'reason', 'export', 'source', 'frameio', 'feedback',
                                                                                         'feedback_read_at', 'applied', 'revisions', 'created_at')} for r in rows[:5]])
