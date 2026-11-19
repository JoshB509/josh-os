import contextlib
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from studio.approval_engine import TTL_SECONDS, ApprovalEngine, ApprovalError


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def synthetic_action(**overrides):
    action = {
        'job_id': 'job-synthetic-1',
        'project_id': 'proj-synthetic',
        'revision': 3,
        'channel_id': 'chan-synthetic',
        'file_sha256': 'ab' * 32,
        'visibility': 'unlisted',
        'schedule': None,
    }
    action.update(overrides)
    return action


def approve_job(con, action):
    con.execute(
        "INSERT INTO jobs (job_id, revision, state) VALUES (?, ?, 'approved')",
        (action['job_id'], action['revision']),
    )
    return {'job_id': action['job_id'], 'state': 'approved'}


def allow(action):
    return True


def must_not_call(*args):
    raise AssertionError('callback must not be called')


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / 'approvals.sqlite3'
        self.clock = FakeClock()
        self.engine = ApprovalEngine(self.db, self.clock)
        self.sql('CREATE TABLE jobs (job_id TEXT PRIMARY KEY, revision INTEGER, state TEXT)')

    def sql(self, query, params=()):
        with contextlib.closing(sqlite3.connect(self.db)) as con:
            rows = con.execute(query, params).fetchall()
            con.commit()
            return rows

    def jobs(self):
        return self.sql('SELECT job_id, revision, state FROM jobs')

    def consumed_flags(self):
        return [r[0] for r in self.sql('SELECT consumed FROM chat_upload_offers')]


class CorrectPath(Base):
    def test_issue_and_consume(self):
        nonce, digest = self.engine.issue('alice', synthetic_action())
        self.assertGreaterEqual(len(nonce), 43)
        result = self.engine.consume(nonce, digest, 'alice', allow, approve_job)
        self.assertFalse(result.replay)
        self.assertEqual(result.receipt, {'job_id': 'job-synthetic-1', 'state': 'approved'})
        self.assertEqual(self.jobs(), [('job-synthetic-1', 3, 'approved')])
        self.assertEqual(self.consumed_flags(), [1])

    def test_only_nonce_hash_stored(self):
        nonce, _ = self.engine.issue('alice', synthetic_action())
        dump = repr(self.sql('SELECT * FROM chat_upload_offers'))
        self.assertNotIn(nonce, dump)
        self.assertNotIn('alice', dump)
        self.assertIn('job-synthetic-1', dump)  # full action persisted

    def test_survives_restart(self):
        nonce, digest = self.engine.issue('alice', synthetic_action())
        restarted = ApprovalEngine(self.db, self.clock)
        self.assertFalse(restarted.consume(nonce, digest, 'alice', allow, approve_job).replay)
        again = ApprovalEngine(self.db, self.clock)
        self.assertTrue(again.consume(nonce, digest, 'alice', must_not_call, must_not_call).replay)


class Refusals(Base):
    def setUp(self):
        super().setUp()
        self.nonce, self.digest = self.engine.issue('alice', synthetic_action())

    def assert_untouched(self):
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.consumed_flags(), [0])

    def test_missing_principal(self):
        for bad in ('', None, 7):
            with self.subTest(bad=bad):
                with self.assertRaises(ApprovalError):
                    self.engine.issue(bad, synthetic_action())
                with self.assertRaises(ApprovalError):
                    self.engine.consume(self.nonce, self.digest, bad, must_not_call, must_not_call)
        self.assert_untouched()

    def test_non_owner(self):
        with self.assertRaises(ApprovalError):
            self.engine.consume(self.nonce, self.digest, 'mallory', must_not_call, must_not_call)
        self.assert_untouched()
        self.assertFalse(self.engine.consume(self.nonce, self.digest, 'alice', allow, approve_job).replay)

    def test_expiry(self):
        self.clock.t += TTL_SECONDS
        with self.assertRaises(ApprovalError):
            self.engine.consume(self.nonce, self.digest, 'alice', must_not_call, must_not_call)
        self.assert_untouched()

    def test_wrong_nonce_or_digest(self):
        cases = [('x' * 43, self.digest), (self.nonce, '0' * 64), (self.nonce, 'not-ascii-é'), (None, self.digest)]
        for nonce, digest in cases:
            with self.subTest(nonce=nonce, digest=digest):
                with self.assertRaises(ApprovalError):
                    self.engine.consume(nonce, digest, 'alice', must_not_call, must_not_call)
        self.assert_untouched()

    def test_invalid_actions_rejected_at_issue(self):
        extra = synthetic_action()
        extra['actor'] = 'alice'
        missing = synthetic_action()
        del missing['schedule']
        bad_actions = [
            synthetic_action(visibility='public'),
            synthetic_action(visibility='private'),
            synthetic_action(schedule='2030-01-01T00:00:00Z'),
            extra,
            missing,
            synthetic_action(revision=-1),
            synthetic_action(revision=True),
            synthetic_action(revision='3'),
            synthetic_action(file_sha256='AB' * 32),
            synthetic_action(file_sha256='ab' * 31),
            synthetic_action(job_id=''),
            synthetic_action(project_id=5),
            ['not', 'a', 'dict'],
        ]
        for action in bad_actions:
            with self.subTest(action=action):
                with self.assertRaises(ApprovalError):
                    self.engine.issue('alice', action)

    def test_tampered_stored_action(self):
        public = '{"visibility":"public"}'
        with self.assertRaises(sqlite3.DatabaseError):
            self.sql('UPDATE chat_upload_offers SET action = ?', (public,))
        self.sql('DROP TRIGGER chat_upload_offers_immutable')  # simulate an attacker bypassing the trigger
        self.sql("UPDATE chat_upload_offers SET action = replace(action, 'unlisted', 'public')")
        with self.assertRaises(ApprovalError):
            self.engine.consume(self.nonce, self.digest, 'alice', must_not_call, must_not_call)
        self.assert_untouched()

    def test_failed_validate(self):
        def boom(action):
            raise RuntimeError('synthetic validation failure')

        with self.assertRaises(RuntimeError):
            self.engine.consume(self.nonce, self.digest, 'alice', boom, must_not_call)
        with self.assertRaises(ApprovalError):
            self.engine.consume(self.nonce, self.digest, 'alice', lambda a: False, must_not_call)
        self.assert_untouched()
        self.assertFalse(self.engine.consume(self.nonce, self.digest, 'alice', allow, approve_job).replay)

    def test_failed_commit_rolls_back(self):
        def commit_then_fail(con, action):
            approve_job(con, action)
            raise RuntimeError('synthetic commit failure')

        def bad_receipt(con, action):
            approve_job(con, action)
            return {'not_json': object()}

        def ends_transaction(con, action):
            con.execute('ROLLBACK')
            return {'ok': True}

        with self.assertRaises(RuntimeError):
            self.engine.consume(self.nonce, self.digest, 'alice', allow, commit_then_fail)
        with self.assertRaises(TypeError):
            self.engine.consume(self.nonce, self.digest, 'alice', allow, bad_receipt)
        with self.assertRaises(ApprovalError):
            self.engine.consume(self.nonce, self.digest, 'alice', allow, lambda c, a: ['list'])
        with self.assertRaises(ApprovalError):
            self.engine.consume(self.nonce, self.digest, 'alice', allow, ends_transaction)
        self.assert_untouched()
        self.assertFalse(self.engine.consume(self.nonce, self.digest, 'alice', allow, approve_job).replay)
        self.assertEqual(len(self.jobs()), 1)

    def test_consumed_replay_runs_no_callbacks(self):
        dispatched = []
        first = self.engine.consume(self.nonce, self.digest, 'alice', allow, approve_job)
        if not first.replay:
            dispatched.append(first.receipt)
        second = self.engine.consume(self.nonce, self.digest, 'alice', must_not_call, must_not_call)
        if not second.replay:
            dispatched.append(second.receipt)
        self.assertTrue(second.replay)
        self.assertEqual(first.receipt, second.receipt)
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(len(self.jobs()), 1)
        with self.assertRaises(ApprovalError):
            self.engine.consume(self.nonce, self.digest, 'mallory', must_not_call, must_not_call)
        with self.assertRaises(sqlite3.DatabaseError):
            self.sql('UPDATE chat_upload_offers SET consumed = 0')


class Concurrency(Base):
    def test_two_instances_commit_once(self):
        nonce, digest = self.engine.issue('alice', synthetic_action())
        engines = [ApprovalEngine(self.db, self.clock), ApprovalEngine(self.db, self.clock)]
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        calls, results, errors = [], [], []

        def slow_commit(con, action):
            with lock:
                calls.append(action['job_id'])
            time.sleep(0.2)
            return approve_job(con, action)

        def worker(engine):
            try:
                barrier.wait(5)
                res = engine.consume(nonce, digest, 'alice', allow, slow_commit)
                with lock:
                    results.append(res)
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(e,)) for e in engines]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(calls, ['job-synthetic-1'])
        self.assertEqual(sorted(r.replay for r in results), [False, True])
        self.assertEqual(results[0].receipt, results[1].receipt)
        self.assertEqual(len(self.jobs()), 1)
        self.assertEqual(self.consumed_flags(), [1])


if __name__ == '__main__':
    unittest.main()
