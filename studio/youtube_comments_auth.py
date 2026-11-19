"""A separate, Windows-user-encrypted OAuth connection for YouTube comments (Anouk's replies, Sam's lead-magnet comment).

Scope is exactly https://www.googleapis.com/auth/youtube.force-ssl, which Google's docs list as the one scope for
comments.insert (reply) and commentThreads.insert (new comment). It is its own file, so the analytics connection and
the publishing connection are never changed. Importing this module connects nothing. Josh connects it himself:
    python -m studio.youtube_comments_auth --connect PATH_TO_DESKTOP_CLIENT_JSON
"""
import argparse
import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from http.server import HTTPServer
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from .youtube_auth import protect
from .youtube_analytics import _NoRedirect
from .youtube_publish_auth import AUTH_URL, TOKEN_URL, callback_handler

SCOPES = frozenset(('https://www.googleapis.com/auth/youtube.force-ssl',))
SCOPE = ' '.join(sorted(SCOPES))


class CommentsAuthError(RuntimeError):
    pass


def validate(body, require_scope=False):
    if not isinstance(body, dict) or not isinstance(body.get('access_token'), str) or not body['access_token']:
        raise ValueError('Missing access token')
    if str(body.get('token_type', '')).lower() != 'bearer':
        raise ValueError('Unexpected token type')
    if 'scope' in body:
        if not isinstance(body['scope'], str) or set(body['scope'].split()) != SCOPES:
            raise ValueError('Unexpected scopes')
    elif require_scope:
        raise ValueError('No granted scope confirmation')
    return body


def exchange(params):
    request = Request(TOKEN_URL, data=urlencode(params).encode('ascii'), headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with build_opener(_NoRedirect).open(request, timeout=30) as response:
            return validate(json.loads(response.read(65537)), require_scope=params.get('grant_type') == 'authorization_code')
    except Exception:
        raise CommentsAuthError('Google comment authorization failed. Reconnect with the YouTube comments permission only.') from None


class CommentsAuth:
    def __init__(self, data):
        self.path = Path(data) / 'youtube-comments' / 'oauth.dpapi'
        self.lock = threading.RLock()
        self.cached = None

    @property
    def connected(self):
        return self.path.is_file()

    def granted(self):
        """The scopes the saved connection holds, read from the encrypted file. Never the tokens themselves."""
        try:
            return set(json.loads(protect(self.path.read_bytes(), decrypt=True)).get('scope', '').split())
        except Exception:
            return set()

    def _load(self):
        try:
            body = json.loads(protect(self.path.read_bytes(), decrypt=True))
            if set(body.get('scope', '').split()) != SCOPES or not all(isinstance(body.get(k), str) and body[k] for k in ('client_id', 'client_secret', 'refresh_token')):
                raise ValueError()
            return body
        except Exception:
            raise CommentsAuthError('YouTube comments are not connected for this Windows user.') from None

    def _save(self, body):
        encrypted = protect(json.dumps(body).encode('utf-8'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.' + secrets.token_hex(8) + '.tmp')
        try:
            temp.write_bytes(encrypted)
            temp.replace(self.path)
        finally:
            if temp.exists():
                temp.unlink()

    def token(self):
        with self.lock:
            if self.cached and self.cached['expires_at'] > time.time() + 90:
                return self.cached['access_token']
            body = self._load()
            reply = exchange(dict(client_id=body['client_id'], client_secret=body['client_secret'], refresh_token=body['refresh_token'],
                                  grant_type='refresh_token'))
            if reply.get('refresh_token'):
                body['refresh_token'] = reply['refresh_token']
                self._save(body)
            self.cached = dict(access_token=reply['access_token'], expires_at=time.time() + int(reply.get('expires_in', 3600)))
            return self.cached['access_token']

    def client_file(self):
        """The Google desktop OAuth client Josh already set up for publishing (Data/youtube-publishing). Read in this
        process only; its secret is never printed, logged or stored anywhere new."""
        return self.path.parents[1] / 'youtube-publishing' / 'desktop-client.json'

    def consent(self, client_file=None, timeout=1800, opened=None):
        """Start Google's consent for exactly the comments scope, on this PC. Returns the consent page address (it holds
        the public client id and a one-time state, no secret) and waits in the background for Josh to choose Allow.
        A connection that already exists is never overwritten."""
        if self.connected:
            raise CommentsAuthError('YouTube comments are already connected. Nothing was changed.')
        done = {}

        def run():
            try:
                self.connect(client_file or self.client_file(), timeout=timeout, opened=opened)
                done['ok'] = True
            except Exception as e:  # noqa: BLE001
                done['error'] = str(e)[:200]
        t = threading.Thread(target=run, daemon=True, name='youtube-comments-consent')
        t.start()
        return t, done

    def connect(self, client_file, timeout=180, opened=None):
        if self.connected:
            raise CommentsAuthError('YouTube comments are already connected. Nothing was changed.')
        try:
            config = json.loads(Path(client_file).read_text(encoding='utf-8-sig'))['installed']
            client_id, client_secret = config['client_id'], config['client_secret']
            if not str(client_id).endswith('.apps.googleusercontent.com') or not client_secret:
                raise ValueError()
        except Exception:
            raise CommentsAuthError('Choose the downloaded Google Desktop OAuth client JSON file.') from None
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
        outcome = {}
        server = HTTPServer(('127.0.0.1', 0), callback_handler(state, outcome))
        server.timeout = 1
        redirect = 'http://127.0.0.1:%d/callback' % server.server_port
        url = AUTH_URL + '?' + urlencode(dict(client_id=client_id, redirect_uri=redirect, response_type='code', scope=SCOPE, state=state,
                                              access_type='offline', prompt='consent', include_granted_scopes='false',
                                              code_challenge=challenge, code_challenge_method='S256'))
        try:
            if opened is not None:
                opened(url)   # the address goes to whoever hands the consent to Josh; it holds no secret
            if not webbrowser.open(url):
                raise CommentsAuthError('Could not open Google consent in the browser.')
            end = time.monotonic() + min(max(float(timeout), 1), 1800)
            while not outcome and time.monotonic() < end:
                server.handle_request()
        finally:
            server.server_close()
        if not outcome.get('code'):
            raise CommentsAuthError('The connection was cancelled or timed out. Nothing was changed.')
        reply = exchange(dict(client_id=client_id, client_secret=client_secret, code=outcome['code'], code_verifier=verifier,
                              redirect_uri=redirect, grant_type='authorization_code'))
        if not reply.get('refresh_token'):
            raise CommentsAuthError('Google did not return offline access. Repeat the consent.')
        with self.lock:
            self._save(dict(client_id=client_id, client_secret=client_secret, refresh_token=reply['refresh_token'], scope=SCOPE))
            self.cached = None


def main():
    p = argparse.ArgumentParser(description='Connect the separate YouTube comments permission (youtube.force-ssl). Posts nothing.')
    p.add_argument('--connect', default=None, help='Desktop OAuth client JSON (default: the publishing client already set up)')
    a = p.parse_args()
    auth = CommentsAuth(Path(__file__).resolve().parents[1] / 'Data')
    auth.connect(a.connect or auth.client_file(), timeout=1800, opened=lambda u: print('Consent page:', u, flush=True))
    print('YouTube comments connected (youtube.force-ssl only). Nothing has been posted.')


if __name__ == '__main__':
    main()


class SharedCommentsAuth:
    """Anouk's comment replies through the edit grant Josh already connected (studio/youtube_manage.py, 13 Nov 2026),
    when the comments file of their own isn't there. That grant holds youtube.force-ssl, the scope Google needs for
    commentThreads.list and comments.insert, and it was saved only after Google confirmed it is Josh's channel.

    It lends a token for each call and nothing else. It never copies, writes or shows the credential, and it counts as
    connected only while the grant holds force-ssl and names the expected channel. What the comments code can do with
    it is limited by that code: list threads, post one reply, post one comment. No delete, edit or moderation exists."""

    source = 'edit grant (Data/youtube-publishing/oauth-manage.dpapi)'

    def __init__(self, data, expected_channel=None, manage=None):
        from .youtube_manage import JOSH_CHANNEL, ManageAuth
        self.expected_channel = expected_channel or JOSH_CHANNEL
        self.manage = manage or ManageAuth(data, expected_channel=self.expected_channel)

    @property
    def connected(self):
        try:
            return (self.manage.connected and SCOPES <= set(self.manage._load().get('scope', '').split())
                    and self.manage.verified_channel() == self.expected_channel)
        except Exception:  # noqa: BLE001 - anything unreadable means not connected
            return False

    def granted(self):
        try:
            return set(self.manage._load().get('scope', '').split()) & SCOPES
        except Exception:  # noqa: BLE001
            return set()

    def token(self):
        if not self.connected:
            raise CommentsAuthError('The edit grant is not connected for Josh\'s channel, so YouTube comments are off.')
        return self.manage.token()


def comments_auth(data):
    """The comments connection to use: the dedicated comments file if Josh connected one, else the edit grant."""
    own = CommentsAuth(data)
    if own.connected:
        own.source = 'comments grant (Data/youtube-comments/oauth.dpapi)'
        return own
    return SharedCommentsAuth(data)
