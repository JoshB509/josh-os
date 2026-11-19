"""Customer center course-progress lookups. Every test here mocks the Claude run; none reads CareerOS."""
import tempfile
import time
import unittest
from pathlib import Path
from studio.agent import Agents, AgentError, CAREEROS_READ
from studio.learners import Learners, clean
from studio.store import Store
from studio.team import Team

FOUND = dict(reply='Found one learner.', status='found', tools_used=['find_learners', 'get_learner', 'get_learner_progress', 'get_learner_timeline'],
             learner=dict(name='Test Learner', email='t@example.test', recorded_sign_ins=3),
             courses=[dict(course='Test core', lessons_available_to_learner=47, lessons_completed=12, completed_without_current_access=0),
                      dict(course='Test extra', lessons_available_to_learner=6, lessons_completed=6)],
             access=[dict(item='Test course', status='active', reason='Stripe purchase')],
             events=[dict(at='2026-11-11T08:00:00Z', kind='signed_in'), dict(at='2026-11-10T08:00:00Z', kind='made_up_kind')],
             tracking=[dict(kind='signed_in', live_from='2026-11-11T06:12:00Z')])


class LearnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'Data')
        self.agents = Agents(self.store, Path(self.tmp.name), Team(self.store))
        self.calls = []
        self.answer = FOUND
        self.agents.run = lambda system, prompt, schema, **k: (self.calls.append(k), ('ok', self.answer))[1]
        self.learners = Learners(self.store, self.agents)

    def wait(self):
        for _ in range(100):
            if not self.learners.all()['running']:
                return
            time.sleep(0.02)

    def test_uses_only_the_named_careeros_reads_with_the_guard(self):
        self.learners.lookup(dict(email='T@example.test'))
        self.wait()
        k = self.calls[0]
        self.assertEqual(tuple(k['extra_tools']), CAREEROS_READ)
        self.assertTrue(k['read_only'])
        self.assertFalse(k.get('chrome'))
        item = self.learners.all()['items']['t@example.test']
        self.assertEqual((item['status'], item['main']['course'], item['main']['percent']), ('found', 'Test core', 26))
        self.assertEqual([e['kind'] for e in item['events']], ['signed_in'])
        self.assertTrue(item['fetched_at'])

    def test_cached_result_is_reused_until_forced(self):
        self.learners.lookup(dict(email='t@example.test')); self.wait()
        self.assertEqual(self.learners.lookup(dict(email='t@example.test'))['state'], 'cached')
        self.assertEqual(len(self.calls), 1)
        self.learners.lookup(dict(email='t@example.test', force=True)); self.wait()
        self.assertEqual(len(self.calls), 2)

    def test_failures_are_reported_and_not_cached(self):
        def boom(*a, **k):
            raise AgentError('CareerOS needs reconnecting.')
        self.agents.run = boom
        self.learners.lookup(dict(email='t@example.test')); self.wait()
        state = self.learners.all()
        self.assertEqual(state['items'], {})
        self.assertIn('reconnecting', state['failed']['t@example.test']['error'])

    def test_unavailable_answer_is_a_failure(self):
        self.answer = dict(reply='Could not read.', status='unavailable', tools_used=['find_learners'], problem='Authentication needed.')
        self.learners.lookup(dict(email='t@example.test')); self.wait()
        self.assertEqual(self.learners.all()['items'], {})
        self.assertIn('Authentication', self.learners.all()['failed']['t@example.test']['error'])

    def test_bad_email_is_refused(self):
        for bad in ('', 'nobody', 'a b@c.d', None):
            with self.assertRaises(ValueError):
                self.learners.lookup(dict(email=bad))
        self.assertEqual(self.calls, [])

    def test_percent_is_eligible_completed_over_unlocked(self):
        c = lambda **k: clean(dict(FOUND, courses=[dict(dict(course='Test core'), **k)]), 'x@example.test')['main']
        self.assertEqual(c(lessons_available_to_learner=40, lessons_completed=10)['percent'], 25)
        # Completions on lessons they can no longer open don't count.
        self.assertEqual(c(lessons_available_to_learner=40, lessons_completed=12, completed_without_current_access=2)['percent'], 25)
        # No honest denominator, no percentage.
        for bad in (dict(lessons_available_to_learner=0, lessons_completed=0), dict(lessons_completed=5),
                    dict(lessons_available_to_learner=10, lessons_completed=12), dict(lessons_available_to_learner='40', lessons_completed=10),
                    dict(lessons_available_to_learner=40.5, lessons_completed=10)):
            self.assertIsNone(c(**bad), bad)
        # CareerOS percent_complete is never used.
        self.assertEqual(c(lessons_available_to_learner=40, lessons_completed=10, percent_complete=99)['percent'], 25)
        hidden = clean(dict(FOUND, courses=[dict(course='Hidden', lessons_available_to_learner=5, lessons_completed=1, hidden_from_learners=True)]), 'x@example.test')
        self.assertEqual((hidden['courses'], hidden['main']), ([], None))

    def test_clean_drops_odd_values(self):
        out = clean(dict(FOUND, tools_used=['get_learner', 'mcp__careeros__update_learner', 'send_message']), 'x@example.test')
        self.assertEqual(out['status'], 'unavailable')
        self.assertEqual(out['tools_used'], ['mcp__careeros__get_learner'])
        out = clean(dict(FOUND, status='not_found'), 'x@example.test')
        self.assertEqual((out['courses'], out['main'], out['access'], out['events']), ([], None, [], []))
        self.assertEqual(clean('nonsense', 'x@example.test')['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
