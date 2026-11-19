"""Browser-bound pairing. Approval is a local CLI operation, never an HTTP route.

Only hashes of browser/session secrets are stored. SQLite IMMEDIATE transactions
serialize approval/consume across threads and CLI processes. No app agents run here.
"""
import argparse
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import time

TTL = 300
SESSION_TTL = 12 * 60 * 60
MAX_REQUESTS = 32
MAX_SESSIONS = 64
ISSUE_LIMIT = 10
POLL_LIMIT = 240
CODE = re.compile(r'^[A-F0-9]{4}(?:-[A-F0-9]{4}){2}$')
SECRET = re.compile(r'^[A-Za-z0-9_-]{43}$')


class PairingError(ValueError):
    pass


class RateLimited(PairingError):
    pass


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or getattr(path, 'is_junction', lambda:False)():
        raise PairingError('Pairing directory must be local private storage.')
    if os.name == 'nt':
        # Set the directory DACL BEFORE creating the database or its journal.
        who = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'],
                             capture_output=True, text=True, check=True,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        sid = re.search(r'S-1-5-\d+(?:-\d+)+', who.stdout)
        if not sid:
            raise PairingError('Cannot establish private pairing storage.')
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                        '*' + sid.group() + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'],
                       capture_output=True, check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        path.chmod(0o700)


class Pairing:
    def __init__(self, data, master=None, clock=time.time):
        self.path = Path(data) / 'device-pairing' / 'state.sqlite3'
        self.clock = clock
        self.epoch = digest(master) if master else None
        if master is None:
            if not self.path.is_file():
                raise PairingError('No pairing state. Start the updated app first.')
            return  # CLI never creates a new database or reads the master token.
        private_directory(self.path.parent)
        with self.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS requests (
                    code TEXT PRIMARY KEY, secret_hash TEXT NOT NULL,
                    origin TEXT NOT NULL, epoch TEXT NOT NULL,
                    created REAL NOT NULL, expires REAL NOT NULL,
                    approved INTEGER NOT NULL DEFAULT 0, last_poll REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions (
                    secret_hash TEXT PRIMARY KEY, origin TEXT NOT NULL,
                    epoch TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS limits (
                    kind TEXT PRIMARY KEY, start REAL NOT NULL, count INTEGER NOT NULL);
            ''')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=3, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except RateLimited:
            db.commit()  # Failed attempts still spend the global request budget.
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def clean(self, db, now):
        db.execute('DELETE FROM requests WHERE expires<=?', (now,))
        db.execute('DELETE FROM sessions WHERE expires<=?', (now,))
        if self.epoch:
            db.execute('DELETE FROM requests WHERE epoch<>?', (self.epoch,))
            db.execute('DELETE FROM sessions WHERE epoch<>?', (self.epoch,))

    def rate(self, db, kind, limit, now):
        row = db.execute('SELECT * FROM limits WHERE kind=?', (kind,)).fetchone()
        if row and row['start'] <= now < row['start'] + 60:
            if row['count'] >= limit:
                raise RateLimited('Too many pairing requests. Wait a minute and try again.')
            db.execute('UPDATE limits SET count=count+1 WHERE kind=?', (kind,))
        else:
            db.execute('INSERT OR REPLACE INTO limits VALUES (?,?,1)', (kind, now))

    def issue(self, origin):
        if not self.epoch:
            raise PairingError('Issuance requires the running server.')
        now = self.clock()
        secret = secrets.token_urlsafe(32)
        with self.transaction() as db:
            self.clean(db, now)
            self.rate(db, 'issue', ISSUE_LIMIT, now)
            if db.execute('SELECT count(*) FROM requests').fetchone()[0] >= MAX_REQUESTS:
                raise RateLimited('Pairing is busy. Try again in five minutes.')
            while True:
                raw = secrets.token_hex(6).upper()
                code = '-'.join(raw[i:i+4] for i in range(0, 12, 4))
                if not db.execute('SELECT 1 FROM requests WHERE code=?', (code,)).fetchone():
                    break
            db.execute('INSERT INTO requests (code,secret_hash,origin,epoch,created,expires) VALUES (?,?,?,?,?,?)',
                       (code, digest(secret), origin, self.epoch, now, now + TTL))
        return dict(code=code, expires_at=now + TTL), secret

    def poll(self, code, secret, origin):
        now = self.clock()
        result, session = 'unavailable', None
        with self.transaction() as db:
            self.clean(db, now)
            self.rate(db, 'poll', POLL_LIMIT, now)
            row = db.execute('SELECT * FROM requests WHERE code=?', (code,)).fetchone() if CODE.fullmatch(code) else None
            if (row and SECRET.fullmatch(secret) and row['epoch'] == self.epoch
                    and row['origin'] == origin
                    and secrets.compare_digest(row['secret_hash'], digest(secret))):
                if now - row['last_poll'] < 2:
                    raise RateLimited('Wait a few seconds before checking again.')
                db.execute('UPDATE requests SET last_poll=? WHERE code=?', (now, code))
                result = 'pending'
                if row['approved']:
                    if db.execute('SELECT count(*) FROM sessions').fetchone()[0] >= MAX_SESSIONS:
                        raise RateLimited('Device limit reached. Ask Josh to revoke old sessions locally.')
                    session = secrets.token_urlsafe(32)
                    db.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                               (digest(session), origin, self.epoch, now + SESSION_TTL))
                    db.execute('DELETE FROM requests WHERE code=?', (code,))
                    result = 'paired'
        return result, session

    def valid_session(self, secret, origin):
        if not self.epoch or not SECRET.fullmatch(secret):
            return False
        with closing(sqlite3.connect(self.path)) as db:
            return bool(db.execute('SELECT 1 FROM sessions WHERE secret_hash=? AND origin=? AND epoch=? AND expires>?',
                                   (digest(secret), origin, self.epoch, self.clock())).fetchone())

    def pending(self):
        with self.transaction() as db:
            self.clean(db, self.clock())
            return [dict(r) for r in db.execute('SELECT code,origin,created,expires,approved FROM requests ORDER BY created')]

    def approve(self, code):
        # This method is called only by the local CLI. Never wire it into HTTP/agents.
        if not CODE.fullmatch(code):
            raise PairingError('Use the exact request code shown in the requesting browser.')
        with self.transaction() as db:
            row = db.execute('SELECT code,origin,expires,approved FROM requests WHERE code=? AND expires>?',
                             (code, self.clock())).fetchone()
            if not row or row['approved']:
                raise PairingError('Request unavailable, expired, or already approved.')
            db.execute('UPDATE requests SET approved=1 WHERE code=?', (code,))
            return dict(code=code, origin=row['origin'], expires_at=row['expires'], state='approved')

    def revoke_all(self):
        with self.transaction() as db:
            db.execute('DELETE FROM sessions')
            db.execute('DELETE FROM requests')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local JoshOS device approval. Confirm the exact browser code with Josh in authenticated chat first.')
    parser.add_argument('--data', type=Path, default=Path(__file__).resolve().parents[1] / 'Data')
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('list', help='Show only safe pending request metadata.')
    approve = commands.add_parser('approve', help='Approve the exact code confirmed by Josh.')
    approve.add_argument('code')
    commands.add_parser('revoke-all', help='Revoke paired devices and pending requests; legacy token is unchanged.')
    args = parser.parse_args(argv)
    try:
        pairing = Pairing(args.data)
        if args.action == 'list':
            result = pairing.pending()
        elif args.action == 'approve':
            result = pairing.approve(args.code)
        else:
            pairing.revoke_all()
            result = dict(state='revoked')
        print(json.dumps(result))
        return 0
    except (PairingError, OSError, sqlite3.Error):
        print(json.dumps(dict(error='Pairing operation unavailable. Check the exact code, expiry, and local state access.')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
