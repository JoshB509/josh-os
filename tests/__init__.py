"""Unit tests never reach a real connector (added 13 Nov 2026).

A Scorecard refresh in two tests once started a real Claude Code run that read CareerOS (its Supabase backend), because
only the Linklr read was mocked. So while anything under tests/ runs, two things fail at once instead of reaching out:
- starting Claude Code (claude or claude.exe), which is how Josh OS reads Linklr, Kit, CareerOS, Gmail and the rest
- opening a connection to anything other than this PC (127.0.0.1, ::1, localhost)

Browsers that Playwright starts are separate programs and keep working against routed or local pages. A deliberate
live check can opt out by setting DAJOSHI_TESTS_ALLOW_NETWORK=1 for that one run.
"""
import os
import socket
import subprocess

LOCAL = {'127.0.0.1', '::1', 'localhost', '0.0.0.0', '', None}


class BlockedInTests(OSError):
    pass


def _is_claude(args):
    first = args if isinstance(args, str) else (args[0] if args else '')
    name = os.path.basename(str(first).strip('"')).lower()
    return name in ('claude', 'claude.exe') or (isinstance(args, str) and 'claude.exe' in args.lower())


def install():
    if os.environ.get('DAJOSHI_TESTS_ALLOW_NETWORK') == '1' or getattr(subprocess.Popen, '_dajoshi_guard', False):
        return
    real_init, real_connect, real_gai = subprocess.Popen.__init__, socket.socket.connect, socket.getaddrinfo

    def popen(self, args, *a, **k):
        if _is_claude(args):
            raise BlockedInTests('Unit tests may not start Claude Code. Mock the connector read instead.')
        return real_init(self, args, *a, **k)

    def connect(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in LOCAL and self.family in (socket.AF_INET, socket.AF_INET6):
            raise BlockedInTests(f'Unit tests may not connect to {host}. Mock the network call instead.')
        return real_connect(self, address, *a, **k)

    def getaddrinfo(host, *a, **k):
        if host not in LOCAL:
            raise BlockedInTests(f'Unit tests may not look up {host}. Mock the network call instead.')
        return real_gai(host, *a, **k)

    subprocess.Popen.__init__ = popen
    subprocess.Popen._dajoshi_guard = True
    socket.socket.connect = connect
    socket.getaddrinfo = getaddrinfo


install()
