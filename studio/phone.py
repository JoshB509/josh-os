"""Optional phone access through Tailscale.

Josh OS only listens on 127.0.0.1. To use it from a phone, Tailscale Serve forwards
https://<pc-name>.<tailnet>.ts.net to the app's fixed local port, and only devices signed
in to Josh's tailnet can reach that address. This module reads the setting below from
settings.json and keeps one long-lived access key in Data/phone-access.json, so the phone
stays signed in across restarts.

    "phone_access": {"host": "josh-pc.tail1234.ts.net", "port": 8765}

Delete Data/phone-access.json and restart Josh OS to sign every phone out.
"""
import json
import re
import secrets
from pathlib import Path

HOST = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$')


class PhoneAccess:
    def __init__(self, root, data):
        self.host, self.port, self.key, self.error = '', 0, '', ''
        try:
            settings = json.loads((Path(root) / 'settings.json').read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            settings = {}
        config = settings.get('phone_access') or {}
        if not config:
            return
        host = str(config.get('host', '')).strip().lower()
        port = config.get('port', 8765)
        if not HOST.match(host) or type(port) is not int or not 1024 <= port <= 65535:
            self.error = 'phone_access in settings.json needs a Tailscale host name and a port between 1024 and 65535.'
            return
        self.host, self.port = host, port
        file = Path(data) / 'phone-access.json'
        try:
            self.key = json.loads(file.read_text(encoding='utf-8'))['key']
        except (OSError, ValueError, KeyError):
            self.key = secrets.token_urlsafe(32)
            file.write_text(json.dumps(dict(key=self.key)), encoding='utf-8')

    @classmethod
    def off(cls):
        """Phone access switched off, for servers built without settings (tests)."""
        off = cls.__new__(cls)
        off.host, off.port, off.key, off.error = '', 0, '', ''
        return off

    def hosts(self):
        return (self.host, self.host + ':443') if self.host else ()

    def origins(self):
        return ('https://' + self.host,) if self.host else ()

    def status(self, token):
        if not self.host:
            return dict(enabled=False, error=self.error)
        return dict(enabled=True, host=self.host, port=self.port, url=f'https://{self.host}/?token={token}')

    def manifest(self):
        return dict(name='Josh OS', short_name='Josh OS', start_url='/', scope='/', display='standalone',
                    background_color='#ffffff', theme_color='#ffffff',
                    icons=[dict(src='/icon.png', sizes='256x256', type='image/png')])
