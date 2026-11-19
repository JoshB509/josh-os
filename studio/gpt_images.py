"""OpenAI Images API adapter. Uses only an explicitly configured OPENAI_API_KEY.

No browser/Codex tokens, credential discovery, SDK installation, URL inputs,
provider fallback or automatic retries. The host supplies bounded image bytes.
API contract: https://developers.openai.com/api/docs/guides/image-generation
"""
import base64
import binascii
import io
import json
import os
import re
import urllib.error
import urllib.request

from PIL import Image, ImageOps

MODELS = ('gpt-image-2', 'gpt-image-2.5-sunburst', 'gpt-image-2.5-flare',
          'gpt-image-2.6-sunburst', 'gpt-image-2.6-flare')
DEFAULT_MODEL = 'gpt-image-2'
MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 30 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_REFERENCES = 6
SETUP_MESSAGE = ('GPT image creation is unavailable: OPENAI_API_KEY is not configured for the CareerOS server. '
                 'Configure an authorized OpenAI API project key outside chat, with Images access and existing billing/quota, '
                 'then restart CareerOS safely. A ChatGPT login is not this API connection. No local fallback was run.')


class ImageProviderError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ImageProviderError('OpenAI returned an unexpected redirect; no credential was forwarded.')


def image_input(data, role):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_INPUT_BYTES:
        raise ImageProviderError('Each reference image must be at most 10 MB.')
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ('PNG', 'JPEG', 'WEBP') or im.width * im.height > 24_000_000 or getattr(im, 'n_frames', 1) != 1:
                raise ValueError('unsupported image')
            mime = Image.MIME[im.format]
            im.verify()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageProviderError('Use a single-frame PNG, JPEG or WebP reference under 24 megapixels.') from exc
    return dict(data=data, mime=mime, role=str(role)[:200])


class GPTImages:
    def __init__(self, environ=None, transport=None):
        self.environ = os.environ if environ is None else environ
        self.transport = transport or self._request

    def status(self):
        model = self.environ.get('CAREEROS_GPT_IMAGE_MODEL', DEFAULT_MODEL)
        configured = bool(self.environ.get('OPENAI_API_KEY', '').strip())
        valid = model in MODELS
        return dict(provider='gpt-image', configured=configured, ready=configured and valid,
                    model=model if valid else None, access_verified=False,
                    message=SETUP_MESSAGE if not configured else
                    'Unsupported CAREEROS_GPT_IMAGE_MODEL.' if not valid else
                    'API key is configured; image-model access and quota have not been verified.')

    def require_ready(self):
        state = self.status()
        if not state['ready']:
            raise ImageProviderError(state['message'])
        return state

    def render(self, prompt, references=()):
        state = self.require_ready()
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 32000:
            raise ImageProviderError('The GPT image prompt must contain 1 to 32000 characters.')
        if len(references) > MAX_REFERENCES:
            raise ImageProviderError('Use at most six reference images, including the subject and background.')
        refs = [image_input(ref['data'], ref['role']) for ref in references]
        if sum(len(ref['data']) for ref in refs) > MAX_TOTAL_BYTES:
            raise ImageProviderError('Reference images exceed the 30 MB total limit.')
        payload = dict(model=state['model'], prompt=prompt, n=1, size='1536x864',
                       quality='medium', output_format='png', background='opaque')
        endpoint = 'edits' if refs else 'generations'
        if refs:
            payload['images'] = [dict(image_url='data:' + ref['mime'] + ';base64,' + base64.b64encode(ref['data']).decode('ascii')) for ref in refs]
        response, request_id = self.transport(endpoint, payload)
        try:
            rows = response['data']
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0]['b64_json'], str):
                raise ValueError('wrong response shape')
            encoded = rows[0]['b64_json']
            if len(encoded) > MAX_RESPONSE_BYTES:
                raise ValueError('response too large')
            raw = base64.b64decode(encoded, validate=True)
            with Image.open(io.BytesIO(raw)) as im:
                if im.format != 'PNG' or im.size != (1536, 864) or getattr(im, 'n_frames', 1) != 1:
                    raise ValueError('wrong output format or dimensions')
                im.load()
                # Match the existing thumbnail canvas without cropping or stretching.
                oriented = ImageOps.exif_transpose(im)
                if oriented.size != (1536, 864):
                    raise ValueError('unexpected orientation')
                final = oriented.convert('RGB').resize((1920, 1080), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            final.save(out, 'PNG', optimize=True)
        except (KeyError, TypeError, ValueError, OSError, binascii.Error, Image.DecompressionBombError) as exc:
            raise ImageProviderError('OpenAI did not return one valid 1536x864 PNG. No thumbnail was saved; no local fallback was run.') from exc
        safe_id = request_id if isinstance(request_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', request_id) else ''
        return out.getvalue(), dict(provider='gpt-image', model=state['model'], operation=endpoint,
                                    request_id=safe_id, input_count=len(refs), output_size=[1920, 1080])

    def _request(self, endpoint, payload):
        if endpoint not in ('generations', 'edits'):
            raise ImageProviderError('Unsupported OpenAI Images operation.')
        req = urllib.request.Request('https://api.openai.com/v1/images/' + endpoint,
                                     data=json.dumps(payload, allow_nan=False).encode('utf-8'),
                                     headers={'Content-Type': 'application/json',
                                              'Authorization': 'Bearer ' + self.environ['OPENAI_API_KEY']}, method='POST')
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=240) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise ImageProviderError('OpenAI image response exceeded the size limit; no thumbnail was saved.')
                return json.loads(data), response.headers.get('x-request-id', '')
        except urllib.error.HTTPError as exc:
            messages = {400: 'OpenAI rejected the image request. Check the prompt, references and model support.',
                        401: 'OpenAI authentication failed. Check the server API project key outside chat.',
                        403: 'OpenAI denied image-model access. Check API project permissions and any organization verification.',
                        429: 'OpenAI quota or rate limit prevented image creation. Check existing API quota/billing.'}
            raise ImageProviderError(messages.get(exc.code, 'OpenAI image service failed (HTTP ' + str(exc.code) + ').') +
                                     ' No automatic retry or local fallback was run.') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ImageProviderError('The OpenAI image request could not be confirmed. Its outcome may be uncertain; '
                                     'no automatic retry or local fallback was run.') from None
        except (ValueError, UnicodeError):
            raise ImageProviderError('OpenAI returned an unreadable response. No thumbnail was saved.') from None
