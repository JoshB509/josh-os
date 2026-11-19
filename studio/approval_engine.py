'''Generic one-use authorization engine: stdlib only, SQLite, no network.

Trusted adapters authenticate callers externally and pass the principal in.
The engine never trusts an actor field inside an action; unknown keys are rejected.
The engine never dispatches. A caller may dispatch only after consume() returns
replay=False, and must never auto-dispatch when replay=True.
'''
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterator, NamedTuple

TTL_SECONDS = 15 * 60
BUSY_TIMEOUT_MS = 10_000
ACTION_KEYS = frozenset(
    {'job_id', 'project_id', 'revision', 'channel_id', 'file_sha256', 'visibility', 'schedule'}
)
_SHA256_HEX = re.compile(r'[0-9a-f]{64}')

_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS chat_upload_offers (
        token_hash TEXT PRIMARY KEY,
        principal_hash TEXT NOT NULL,
        action_digest TEXT NOT NULL,
        action TEXT NOT NULL,
        expires REAL NOT NULL,
        consumed INTEGER NOT NULL DEFAULT 0 CHECK (consumed IN (0, 1)),
        receipt TEXT
    )''',
    # Offers are immutable apart from the single 0 -> 1 consumption update.
    """CREATE TRIGGER IF NOT EXISTS chat_upload_offers_immutable BEFORE UPDATE ON chat_upload_offers
    WHEN OLD.consumed = 1
      OR NEW.token_hash IS NOT OLD.token_hash
      OR NEW.principal_hash IS NOT OLD.principal_hash
      OR NEW.action_digest IS NOT OLD.action_digest
      OR NEW.action IS NOT OLD.action
      OR NEW.expires IS NOT OLD.expires
    BEGIN SELECT RAISE(ABORT, 'offer is immutable'); END""",
)


class ApprovalError(ValueError):
    '''Authorization refused. The transaction was rolled back.'''


class Result(NamedTuple):
    receipt: dict
    replay: bool


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode('utf-8'), b.encode('utf-8'))


def _principal_hash(principal: Any) -> str:
    if not isinstance(principal, str) or not principal.strip():
        raise ApprovalError('principal must be a non-empty string')
    return _sha('principal:' + principal)


def _token_hash(nonce: str) -> str:
    return _sha('token:' + nonce)


def check_action(action: Any) -> dict:
    '''Return a copy of a valid unlisted, unscheduled action or raise ApprovalError.'''
    if not isinstance(action, dict) or set(action) != ACTION_KEYS:
        raise ApprovalError('action must have exactly the allowed keys')
    for key in ('job_id', 'project_id', 'channel_id'):
        if type(action[key]) is not str or not action[key]:
            raise ApprovalError(key + ' must be a non-empty string')
    if type(action['revision']) is not int or action['revision'] < 0:
        raise ApprovalError('revision must be an integer >= 0')
    sha = action['file_sha256']
    if type(sha) is not str or not _SHA256_HEX.fullmatch(sha):
        raise ApprovalError('file_sha256 must be 64 lowercase hex characters')
    if type(action['visibility']) is not str or action['visibility'] != 'unlisted':
        raise ApprovalError('only unlisted actions can be authorized')
    if action['schedule'] is not None:
        raise ApprovalError('scheduled actions cannot be authorized')
    return dict(action)


class ApprovalEngine:
    def __init__(self, db_path: Path, clock: Callable[[], float]):
        self._path = Path(db_path)
        self._clock = clock
        with contextlib.closing(self._connect()) as con:
            for statement in _SCHEMA:
                con.execute(statement)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        con.execute('PRAGMA busy_timeout = %d' % BUSY_TIMEOUT_MS)
        return con

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            yield con
            con.execute('COMMIT')
        except BaseException:
            if con.in_transaction:
                con.execute('ROLLBACK')
            raise
        finally:
            con.close()

    def _now(self) -> float:
        now = self._clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ApprovalError('clock must return epoch seconds')
        return float(now)

    def issue(self, principal: str, action: dict) -> tuple[str, str]:
        '''Create a one-use 256-bit nonce for (principal, action). Returns (nonce, digest).'''
        principal_hash = _principal_hash(principal)
        action_json = canonical_json(check_action(action))
        digest = _sha(action_json)
        nonce = secrets.token_urlsafe(32)
        with self._transaction() as con:
            con.execute(
                'INSERT INTO chat_upload_offers (token_hash, principal_hash, action_digest, action, expires)'
                ' VALUES (?, ?, ?, ?, ?)',
                (_token_hash(nonce), principal_hash, digest, action_json, self._now() + TTL_SECONDS),
            )
        return nonce, digest

    def consume(
        self,
        nonce: str,
        digest: str,
        principal: str,
        validate: Callable[[dict], Any],
        commit: Callable[[sqlite3.Connection, dict], dict],
    ) -> Result:
        '''Consume once. commit() must write job state on the given connection
        (same transaction) and return a JSON object receipt.'''
        principal_hash = _principal_hash(principal)
        if not isinstance(nonce, str) or not isinstance(digest, str):
            raise ApprovalError('not authorized')
        with self._transaction() as con:
            row = con.execute(
                'SELECT principal_hash, action_digest, action, expires, consumed, receipt'
                ' FROM chat_upload_offers WHERE token_hash = ?',
                (_token_hash(nonce),),
            ).fetchone()
            if row is None:
                raise ApprovalError('not authorized')
            stored_principal, stored_digest, action_json, expires, consumed, receipt = row
            ok = _eq(stored_principal, principal_hash)
            ok &= _eq(stored_digest, digest)
            ok &= _eq(stored_digest, _sha(action_json))  # detects stored-row tampering
            if not ok:
                raise ApprovalError('not authorized')
            if consumed:
                return Result(json.loads(receipt), True)  # receipts remain idempotent after expiry
            if self._now() >= expires:
                raise ApprovalError('offer expired')

            action = check_action(json.loads(action_json))
            if validate(dict(action)) is False:
                raise ApprovalError('validation refused')
            receipt_obj = commit(con, dict(action))
            if not con.in_transaction:
                raise ApprovalError('commit callback must not end the transaction')
            if not isinstance(receipt_obj, dict):
                raise ApprovalError('commit callback must return a JSON object receipt')
            receipt_json = canonical_json(receipt_obj)
            cursor = con.execute(
                'UPDATE chat_upload_offers SET consumed = 1, receipt = ? WHERE token_hash = ? AND consumed = 0',
                (receipt_json, _token_hash(nonce)),
            )
            if cursor.rowcount != 1:
                raise ApprovalError('offer state changed')
        return Result(json.loads(receipt_json), False)
