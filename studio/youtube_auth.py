"""Windows-user-encrypted OAuth for JoshOS's read-only YouTube Analytics.

Run python -m studio.youtube_auth --connect PATH_TO_DESKTOP_CLIENT_JSON to
start Google's consent screen. Never put client secrets or tokens in chat.
No account change occurs merely by importing this module.
"""
import argparse
import base64
import ctypes
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener
from .youtube_analytics import AnalyticsError, SCOPE, _NoRedirect

TOKEN_URL = 'https://oauth2.googleapis.com/token'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'


def protect(data, decrypt=False):
    if os.name != 'nt':
        raise AnalyticsError('YouTube token storage requires this Windows user account.')
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    api = ctypes.windll.crypt32
    fn = api.CryptUnprotectData if decrypt else api.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    fn.restype = ctypes.c_int
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise AnalyticsError('Windows could not unlock YouTube credentials for this user.')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(result.data)


def exchange(params):
    req = Request(TOKEN_URL, data=urlencode(params).encode('ascii'),
                  headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with build_opener(_NoRedirect).open(req, timeout=30) as response:
            body = json.loads(response.read(65536))
        if not body.get('access_token') or body.get('token_type', '').lower() != 'bearer':
            raise ValueError()
        if body.get('scope') and set(body['scope'].split()) != {SCOPE}:
            raise ValueError()
        return body
    except Exception:
        raise AnalyticsError('Google authorization failed. Reconnect with only YouTube Analytics read-only access.') from None


class YouTubeAuth:
    def __init__(self, data):
        self.path = Path(data) / 'youtube' / 'oauth.dpapi'
        self.lock = threading.RLock()
        self.cached = None

    @property
    def connected(self):
        return self.path.is_file()

    def _load(self):
        try:
            body = json.loads(protect(self.path.read_bytes(), decrypt=True))
            if body.get('scope') != SCOPE:
                raise ValueError()
            return body
        except Exception:
            raise AnalyticsError('YouTube is not connected for this Windows account. Run the connection setup.') from None

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
            reply = exchange(dict(client_id=body['client_id'], client_secret=body['client_secret'],
                                  refresh_token=body['refresh_token'], grant_type='refresh_token'))
            if reply.get('refresh_token'):
                body['refresh_token'] = reply['refresh_token']
                self._save(body)
            self.cached = dict(access_token=reply['access_token'], expires_at=time.time() + int(reply.get('expires_in', 3600)))
            return self.cached['access_token']

    def connect(self, client_file, timeout=180):
        try:
            config = json.loads(Path(client_file).read_text(encoding='utf-8-sig'))['installed']
            client_id, client_secret = config['client_id'], config['client_secret']
            if not isinstance(client_id, str) or not client_id.endswith('.apps.googleusercontent.com') or not client_secret:
                raise ValueError()
        except Exception:
            raise AnalyticsError('Choose the downloaded Google Desktop OAuth client JSON file.') from None
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
        outcome = {}
        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass  # Authorization codes and state must never reach access logs.
            def do_GET(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                valid = (parsed.path == '/callback' and len(query.get('state', [])) == 1
                         and hmac.compare_digest(query['state'][0], state))
                if not valid:
                    self.send_error(400, 'Invalid authorization callback')
                    return
                if len(query.get('code', [])) == 1:
                    outcome['code'] = query['code'][0]
                else:
                    outcome['error'] = True
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Security-Policy', "default-src 'none'")
                self.end_headers()
                self.wfile.write(b'Google response received. You can close this tab and return to JoshOS setup.')
        server = HTTPServer(('127.0.0.1', 0), Callback)
        server.timeout = 1
        redirect = 'http://127.0.0.1:%d/callback' % server.server_port
        url = AUTH_URL + '?' + urlencode(dict(client_id=client_id, redirect_uri=redirect,
            response_type='code', scope=SCOPE, state=state, access_type='offline', prompt='consent',
            code_challenge=challenge, code_challenge_method='S256'))
        try:
            if not webbrowser.open(url):
                raise AnalyticsError('Could not open the Google consent screen in the browser.')
            end = time.monotonic() + timeout
            while not outcome and time.monotonic() < end:
                server.handle_request()
        finally:
            server.server_close()
        if not outcome.get('code'):
            raise AnalyticsError('YouTube connection was cancelled or timed out. No credentials were changed.')
        reply = exchange(dict(client_id=client_id, client_secret=client_secret, code=outcome['code'],
                              code_verifier=verifier, redirect_uri=redirect, grant_type='authorization_code'))
        if not reply.get('refresh_token'):
            raise AnalyticsError('Google did not return offline access. Repeat the consent flow.')
        with self.lock:
            self._save(dict(client_id=client_id, client_secret=client_secret, refresh_token=reply['refresh_token'], scope=SCOPE))
            self.cached = None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Connect JoshOS to read-only YouTube Analytics')
    parser.add_argument('--connect', required=True, help='Downloaded Desktop OAuth client JSON path')
    args = parser.parse_args()
    try:
        YouTubeAuth(Path(__file__).resolve().parents[1] / 'Data').connect(args.connect)
        print('YouTube Analytics connected. Refresh the JoshOS scorecard.')
    except AnalyticsError as exc:
        print(str(exc))
        raise SystemExit(1)
