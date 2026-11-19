import threading
import unittest
from unittest.mock import Mock, patch

from studio.chat_course_actions import (course_action_context, current_request_authorizes,
    dispatch_course_action, fingerprint, GUARDED)
from studio.store import Conflict


class ChatCourseActionTests(unittest.TestCase):
    def setUp(self):
        self.courses = Mock()
        self.courses.lock = threading.Lock()
        self.courses.recording = None
        self.courses._get.return_value = {'id': 'lesson-1', 'revision': 4, 'script': 'Approved source'}
        self.courses.cues.return_value = 'Original cues'
        self.context = course_action_context(self.courses, 'lesson-1')

    def action(self, name='cues', **kwargs):
        return dict(self.context, action=name, **kwargs)

    def call(self, action, **kwargs):
        return dispatch_course_action(self.courses, 'saskia', action, **kwargs)

    def test_requires_trusted_authorization(self):
        for callback in (None, lambda a: False, lambda a: 'yes'):
            with self.assertRaises(ValueError):
                self.call(self.action(text='New cues'), authorize=callback)
        self.courses.save_cues.assert_not_called()

    def test_role_and_model_confirmations_rejected(self):
        with self.assertRaises(ValueError):
            dispatch_course_action(self.courses, 'sam', self.action(text='New'), authorize=lambda a: True)
        with self.assertRaises(ValueError):
            self.call(self.action(text='New', confirmed=True), authorize=lambda a: True)
        self.courses.save_cues.assert_not_called()

    def test_cues_delegate_exact_text(self):
        self.call(self.action(text='New cues'), authorize=lambda a: True)
        self.courses.save_cues.assert_called_once_with('lesson-1', 'New cues')
        self.courses.approve.assert_not_called()
        self.courses.publish.assert_not_called()

    def test_stale_revision_source_or_cues_denied(self):
        for field, value in [('revision', 3), ('source_hash', '0'*64), ('state_hash', '0'*64)]:
            action = self.action(text='New cues'); action[field] = value
            with self.assertRaises(Conflict):
                self.call(action, authorize=lambda a: True)
        self.courses.save_cues.assert_not_called()

    def test_guards_and_unknown_fields(self):
        for name in GUARDED | {'publish', 'approve', 'shell'}:
            with self.assertRaises(ValueError):
                self.call(self.action(name), authorize=lambda a: True)
        with self.assertRaises(ValueError):
            self.call(self.action(text=''), authorize=lambda a: True)
        with self.assertRaises(ValueError):
            self.call(dict(self.action(text='New'), lesson_id='../escape'), authorize=lambda a: True)

    def test_recording_plan_latest_audio(self):
        rec = self.courses.recording = Mock()
        snapshot = {'state': {}, 'takes': [{'id': 'take-1', 'sha256': 'a'*64}]}
        with patch('studio.chat_course_actions._recording_snapshot', return_value=snapshot):
            self.context = course_action_context(self.courses, 'lesson-1')
            self.call(self.action('plan', date='2026-11-20'), authorize=lambda a: True)
            rec.plan.assert_called_once_with('lesson-1', '2026-11-20')
            self.call(self.action('use_latest'), authorize=lambda a: True)
            rec.use_latest.assert_called_once_with('lesson-1')
            self.call(self.action('take_audio_check', take_id='take-1', take_sha256='a'*64), authorize=lambda a: True)
            rec.take_action.assert_called_once_with('lesson-1', {'action': 'audio_check', 'take_id': 'take-1'})
            with self.assertRaises(Conflict):
                self.call(self.action('take_audio_check', take_id='take-1', take_sha256='b'*64), authorize=lambda a: True)
            rec.finish_session.assert_not_called()
            rec.import_take.assert_not_called()

    def test_current_request_matcher(self):
        a = self.action('plan', date='2026-11-20')
        command = 'Set recording date for lesson-1 to 2026-11-20'
        self.assertTrue(current_request_authorizes(a, command))
        for message in [command+'?', "Don't "+command, 'Maybe '+command,
                        'Previously I said '+command, command.replace('lesson-1', 'lesson-2')]:
            self.assertFalse(current_request_authorizes(a, message))


if __name__ == '__main__':
    unittest.main()
