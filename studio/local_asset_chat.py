"""Prepared all-role local visual action and owner-browser preview integration.

No model-supplied actor, filesystem path, attachment mutation or outbound action.
The source key is an opaque generated batch/asset pair, resolved through the
immutable manifest and verified PNG reader. Only same-owner reuse is supported.
"""
import copy
import json
import re

from .local_assets import AuthorizedImage, LocalAssets, digest
from .local_asset_spec import CREATE_LOCAL_ASSETS_SCHEMA, MAX_MANIFEST_BYTES

ROLES = frozenset(('sam', 'tariq', 'laia', 'saskia', 'anouk', 'max'))
KEY = re.compile(r'([0-9a-f]{32})_([0-9a-f]{32})')


_VISUAL = r'\b(?:visuals?|images?|pictures?|graphics?|diagrams?|charts?|infographics?|cards?|slides?|illustrations?|flowcharts?|timelines?|thumbnails?|assets?|posters?|banners?|logos?|storyboards?|mockups?|icons?|flyers?|covers?|collages?|wallpapers?)\b'
_CREATE = r'(?:create|make|build|draw|render|design|generate|sketch|redraw|redesign|revise|resize|recolor|recolour|change|update)'


def requested(message):
    """Recognize affirmative current requests, not mentions of making visuals.

    This deliberately accepts a small request grammar. Quoted instructions,
    explanations, generic capability questions and conditional plans cannot
    authorize a model-supplied action. Schema exposure and dispatch share it.
    """
    text = str(message or '').lower().replace('\u2019', "'").replace('\u2018', "'")
    # Quoted material is context, including examples pasted as Markdown/code.
    text = re.sub(r'```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)', ' ', text)
    text = re.sub(r'(?m)^\s*>.*$', ' ', text)
    text = re.sub(r'`[^`]*(?:`|$)|["\u201c][^"\u201d]*(?:["\u201d]|$)', ' ', text)
    text = re.sub(r"(?<!\w)'[^']*(?:'(?!\w)|$)", ' ', text)
    # A cancellation wins over an earlier request, but 'do not send it' does
    # not cancel local creation. Negated desires are not positive requests.
    negative = rf"\b(?:don't|do not|never|not to|no need to)\s+{_CREATE}\b"
    negated_desire = rf"\b(?:don't|do not|never)\s+(?:want|need)\b[^.!?;\n]*?(?:{_VISUAL}|\b{_CREATE}\b|\b(?:it|one|them)\b)"
    if re.search(negative, text) or re.search(negated_desire, text) or re.search(r'\b(?:stop|cancel)\b', text):
        return False
    if re.search(r"\b(?:hypothetically|suppose|pretend|for example|not a request|not asking|only asking)\b", text):
        return False
    for sentence in re.split(r'[.!?;\n]+', text):
        sentence = sentence.strip(' ,:')
        if not sentence:
            continue
        if re.search(r'\b(?:if|unless|when|once|hypothetically|suppose|imagine|pretend|example|quoted?)\b', sentence):
            continue
        if re.search(r'\b(?:after|until|before)\s+(?:i|we|you|josh)\b', sentence):
            continue
        if re.match(r'(?:explain\s+how|tell me\s+(?:how|whether)|how\b|what\b|why\b|are you\b|do you\b|could you explain\b|can you explain\b)', sentence):
            continue
        # Coordinated imperatives are requests too: 'Explain the plan and
        # create a diagram'. Do not mine subordinate/explanatory clauses.
        for clause in re.split(r'\s+(?:and then|and|then)\s+(?=(?:please\s+)?' + _CREATE + r'\b)', sentence):
            clause = re.sub(r'^(?:(?:please|kindly|just)\s+|go ahead and\s+)+', '', clause.strip())
            modal = re.match(r'^(?:can|could|would|will) you\s+(?:please\s+)?', clause)
            if modal:
                clause = clause[modal.end():]
            desire = re.match(r"^i (?:want|need|would like)\s+|^i'd like\s+", clause)
            if desire:
                clause = clause[desire.end():]
                clause = re.sub(r'^(?:you to|to)\s+(?=' + _CREATE + r'\b)', '', clause)
            command = re.match(r'^' + _CREATE + r'\s+', clause)
            give = re.match(r'^(?:give|show) me\s+(?=(?:a|an|some|new)\b)', clause)
            if command or give:
                obj = clause[(command or give).end():]
            elif desire:
                obj = clause
            else:
                continue
            # The visual must be the requested object, not a noun elsewhere
            # in an explanation ('Create an email and explain the images').
            obj = re.split(r'\b(?:and|but|then|because|how|whether|to|for|with|about|that|which)\b|[,;:]', obj, maxsplit=1)[0].strip()
            target = re.search(_VISUAL, obj)
            if not target or len(obj[:target.start()].split()) > 8:
                continue
            if re.search(r'\b(?:not|no|never|without|avoid|stop|cancel|know|learn|understand|explain|discuss)\b', obj):
                continue
            # 'Can you create images?' asks about capability; a concrete
            # object or recipient makes the common polite request actionable.
            if modal and not re.match(r'^(?:me|us|a|an|the|this|that|these|those|some|one|two|three|\d+)\b', obj):
                continue
            if re.search(r'\b(?:possible|capable|ability|capabilities)\b', sentence):
                continue
            return True
    return False


class LocalAssetChat:
    def __init__(self, root):
        self.assets = LocalAssets(root, resolve_image=self._reuse)

    @staticmethod
    def enabled(owner, message, proactive=False):
        return owner in ROLES and not proactive and requested(message)

    def schema(self, owner, message, proactive=False):
        return copy.deepcopy(CREATE_LOCAL_ASSETS_SCHEMA) if self.enabled(owner, message, proactive) else None

    @staticmethod
    def context(owner, history):
        known = []
        for message in reversed(history):
            for row in message.get('saved') or []:
                if row.get('kind') == 'local_asset' and row.get('action') == 'added' and row.get('owner') == owner and KEY.fullmatch(str(row.get('id') or '')):
                    known.append({k: row[k] for k in ('id', 'title', 'alt') if k in row})
                    if len(known) == 16:
                        break
            if len(known) == 16:
                break
        return ['The optional create_local_assets action makes standalone PNG previews only. '
                'It does not replace role-specific email, carousel, course or video actions; use those for record changes. '
                'Choose dimensions/composition for the content; templates are overridable defaults and branding is optional. '
                'Creation alone never authorizes send_files, attachments, uploads or publication. '
                'Any separately requested delivery must follow its existing authorized workflow. '
                'For create_local_assets use structured layers, not SVG/HTML/paths/URLs. This action has no generative backend. '
                'Existing image layers may use only your own local '
                'visual ids listed here: ' + json.dumps(known, ensure_ascii=False)]

    def _reference(self, owner, key):
        if owner not in ROLES:
            raise PermissionError('Unknown local visual owner.')
        match = KEY.fullmatch(str(key))
        if not match:
            raise ValueError('Invalid local visual id.')
        batch, asset = match.groups()
        path = self.assets._path('batches', batch, 'manifest.json')
        try:
            manifest = json.loads(self.assets._read(path, MAX_MANIFEST_BYTES))
        except FileNotFoundError:
            raise KeyError('Local visual not found.') from None
        if not isinstance(manifest, dict) or manifest.get('owner') != owner:
            raise PermissionError('This local visual belongs to another agent.')
        rows = manifest.get('assets')
        if not isinstance(rows, list):
            raise ValueError('Invalid local visual manifest.')
        candidates = [r.get('reference') for r in rows if isinstance(r, dict) and isinstance(r.get('reference'), dict)
                      and r['reference'].get('asset_id') == asset and r['reference'].get('batch_id') == batch]
        if len(candidates) != 1:
            raise KeyError('Local visual not found.')
        return candidates[0]

    def read_png(self, owner, key):
        return self.assets.read_png(self._reference(owner, key), owner=owner)

    def _reuse(self, owner, key):
        png = self.read_png(owner, key)
        return AuthorizedImage(png, digest(png))

    def dispatch(self, owner, request, *, message, proactive=False):
        if not self.enabled(owner, message, proactive):
            return [dict(kind='local_asset', action='failed', title='local visual',
                         error='Ask to make or change a visual in your current message.')], 'No local visual was created.'
        try:
            made = self.assets.create(request, owner=owner)
            saved = []
            for item in made['assets']:
                ref = item['reference']
                key = ref['batch_id'] + '_' + ref['asset_id']
                saved.append(dict(kind='local_asset', action='added', owner=owner, id=key,
                    title=item['title'] or 'Local visual', alt=item['alt'], width=item['width'], height=item['height'], reference=ref))
            return saved, f"Created {len(saved)} local visual{'s' if len(saved) != 1 else ''}. Preview or download the PNGs in CareerOS. This action did not attach, upload or send them."
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            error = str(exc).strip("'\"")[:240]
            return [dict(kind='local_asset', action='failed', title='local visual', error=error)], 'Local visual creation failed: ' + error


def route(handler, method, path):
    """Called only inside Handler.route's existing authorized() boundary."""
    if not path.startswith('/api/local-assets/'):
        return False
    # A token alone cannot expose these private previews; use the existing owner
    # browser-session predicate (this read does not issue or consume any approval).
    if not handler.publishing_browser():
        handler.send({'error': 'Open local visuals from your signed-in CareerOS browser.'}, 403)
        return True
    if method != 'GET':
        handler.send({'error': 'Local visual previews are read only.'}, 405)
        return True
    match = re.fullmatch(r'/api/local-assets/([a-z]+)/([0-9a-f]{32}_[0-9a-f]{32})\.png', path)
    if not match:
        handler.send({'error': 'Invalid local visual preview.'}, 400)
        return True
    try:
        png = handler.server.local_asset_chat.read_png(*match.groups())
        handler.send(png, mime='image/png', cache='private, no-store')
    except PermissionError:
        handler.send({'error': 'This visual belongs to a different agent.'}, 403)
    except KeyError:
        handler.send({'error': 'Local visual not found.'}, 404)
    except (ValueError, OSError):
        handler.send({'error': 'Local visual verification failed.'}, 400)
    return True
