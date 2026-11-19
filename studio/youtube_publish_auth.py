"""Isolated Windows-user-encrypted OAuth for YouTube publishing.

Importing this module does not authenticate or change an account. Run the CLI
only after the user approves a separate upload/read publishing connection.
Analytics credentials and its OAuth implementation are never modified.
"""
import argparse
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener
from .youtube_auth import protect
from .youtube_analytics import _NoRedirect

SCOPES = frozenset(('https://www.googleapis.com/auth/youtube.upload',
                    'https://www.googleapis.com/auth/youtube.readonly'))
SCOPE = ' '.join(sorted(SCOPES))
TOKEN_URL = 'https://oauth2.googleapis.com/token'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'


class PublishingAuthError(RuntimeError):
    pass


def validate_reply(body, require_scope=False, scopes=SCOPES):
    if not isinstance(body, dict) or not isinstance(body.get('access_token'), str) or not body['access_token']:
        raise ValueError('Missing access token')
    if str(body.get('token_type', '')).lower() != 'bearer':
        raise ValueError('Unexpected token type')
    if 'scope' in body:
        if not isinstance(body['scope'], str) or set(body['scope'].split()) != set(scopes):
            raise ValueError('Unexpected scopes')
    elif require_scope:
        raise ValueError('No granted scope confirmation')
    expires = body.get('expires_in', 3600)
    if isinstance(expires, bool) or not 0 < int(expires) <= 86400:
        raise ValueError('Invalid expiry')
    return body


def exchange(params, scopes=SCOPES):
    """Only Google token endpoint; never surface HTTP response bodies/secrets."""
    request = Request(TOKEN_URL, data=urlencode(params).encode('ascii'),
                      headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with build_opener(_NoRedirect).open(request, timeout=30) as response:
            body = json.loads(response.read(65537))
        # Initial consent must confirm exact grants. A refresh response may omit
        # scope, which OAuth defines as unchanged from the original grant.
        return validate_reply(body, require_scope=params.get('grant_type') == 'authorization_code', scopes=scopes)
    except Exception:
        raise PublishingAuthError('Google publishing authorization failed. Reconnect with upload and read-only YouTube access only.') from None


def callback_handler(state, outcome):
    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            valid = (parsed.path == '/callback' and not parsed.fragment
                     and len(query.get('state', [])) == 1
                     and hmac.compare_digest(query['state'][0], state))
            if not valid:
                self.send_error(400, 'Invalid authorization callback')
                return
            if (len(query.get('code', [])) == 1 and query['code'][0]
                    and 'error' not in query and not outcome):
                outcome['code'] = query['code'][0]
            elif not outcome:
                outcome['error'] = True
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'none'")
            self.end_headers()
            self.wfile.write(b'Google response received. Return to JoshOS publishing setup. No video was uploaded.')
    return Callback


class PublishingAuth:
    SCOPES = SCOPES
    FILE = 'oauth.dpapi'

    def __init__(self, data):
        self.path = Path(data) / 'youtube-publishing' / self.FILE
        self.lock = threading.RLock()
        self.connect_lock = threading.Lock()
        self.cached = None

    @property
    def connected(self):
        return self.path.is_file()

    def _load(self):
        try:
            body = json.loads(protect(self.path.read_bytes(), decrypt=True))
            if set(body.get('scope', '').split()) != set(self.SCOPES):
                raise ValueError()
            for key in ('client_id', 'client_secret', 'refresh_token'):
                if not isinstance(body.get(key), str) or not body[key]:
                    raise ValueError()
            return body
        except Exception:
            raise PublishingAuthError('YouTube publishing is not connected for this Windows user. Run publishing connection setup.') from None

    def _save(self, body):
        try:
            encrypted = protect(json.dumps(body).encode('utf-8'))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix('.' + secrets.token_hex(8) + '.tmp')
            try:
                temp.write_bytes(encrypted)
                temp.replace(self.path)
            finally:
                if temp.exists():
                    temp.unlink()
        except Exception:
            raise PublishingAuthError('Could not securely save publishing credentials for this Windows user.') from None

    def invalidate(self):
        with self.lock:
            self.cached = None

    def token(self):
        with self.lock:
            if self.cached and self.cached['expires_at'] > time.time() + 90:
                return self.cached['access_token']
            body = self._load()
            reply = exchange(dict(client_id=body['client_id'], client_secret=body['client_secret'],
                                  refresh_token=body['refresh_token'], grant_type='refresh_token'), scopes=self.SCOPES)
            if reply.get('refresh_token'):
                body['refresh_token'] = reply['refresh_token']
                self._save(body)
            self.cached = dict(access_token=reply['access_token'],
                               expires_at=time.time() + int(reply.get('expires_in', 3600)))
            return self.cached['access_token']

    def connect(self, client_file, timeout=180):
        if not self.connect_lock.acquire(blocking=False):
            raise PublishingAuthError('A publishing connection is already in progress.')
        try:
            return self._connect(client_file, timeout)
        finally:
            self.connect_lock.release()

    def _connect(self, client_file, timeout):
        try:
            config = json.loads(Path(client_file).read_text(encoding='utf-8-sig'))['installed']
            client_id, client_secret = config['client_id'], config['client_secret']
            if not isinstance(client_id, str) or not client_id.endswith('.apps.googleusercontent.com'):
                raise ValueError()
            if not isinstance(client_secret, str) or not client_secret:
                raise ValueError()
        except Exception:
            raise PublishingAuthError('Choose the separate downloaded Google Desktop OAuth client JSON file.') from None
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
        outcome = {}
        server = HTTPServer(('127.0.0.1', 0), callback_handler(state, outcome))
        server.timeout = 1
        redirect = 'http://127.0.0.1:%d/callback' % server.server_port
        url = AUTH_URL + '?' + urlencode(dict(client_id=client_id, redirect_uri=redirect,
            response_type='code', scope=' '.join(sorted(self.SCOPES)), state=state, access_type='offline', prompt='consent',
            include_granted_scopes='false', code_challenge=challenge, code_challenge_method='S256'))
        try:
            if not webbrowser.open(url):
                raise PublishingAuthError('Could not open Google publishing consent in the browser.')
            end = time.monotonic() + min(max(float(timeout), 1), 300)
            while not outcome and time.monotonic() < end:
                server.handle_request()
        finally:
            server.server_close()
        if not outcome.get('code'):
            raise PublishingAuthError('Publishing connection was cancelled or timed out. Existing credentials were not changed.')
        reply = exchange(dict(client_id=client_id, client_secret=client_secret, code=outcome['code'],
                              code_verifier=verifier, redirect_uri=redirect, grant_type='authorization_code'), scopes=self.SCOPES)
        if not isinstance(reply.get('refresh_token'), str) or not reply['refresh_token']:
            raise PublishingAuthError('Google did not return offline publishing access. Repeat the consent flow.')
        body = dict(client_id=client_id, client_secret=client_secret, refresh_token=reply['refresh_token'], scope=' '.join(sorted(self.SCOPES)))
        body = self.before_save(body, reply)
        with self.lock:
            self._save(body)
            self.cached = None

    def before_save(self, body, reply):
        """A hook for a subclass to check the new grant before it is stored. The default stores it as it is."""
        return body


def main():
    parser = argparse.ArgumentParser(description='Connect separate YouTube upload/read permissions. Does not upload a video.')
    parser.add_argument('--connect', required=True, help='Separate downloaded Desktop OAuth client JSON path')
    args = parser.parse_args()
    try:
        PublishingAuth(Path(__file__).resolve().parents[1] / 'Data').connect(args.connect)
        print('YouTube publishing permissions connected. Verify the channel before approving any upload.')
    except PublishingAuthError as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
