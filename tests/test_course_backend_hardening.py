import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from studio.course_lessons import CourseLessons, digest
from studio.lessons import Lessons
from studio.store import Conflict


class CourseBackendHardeningTests(unittest.TestCase):
    def setUp(self):
        self.c = CourseLessons.__new__(CourseLessons)
        self.c.lock = threading.Lock()
        self.lesson = {'id': 'lesson-1', 'revision': 4, 'script': 'Current script', 'stage': 'Script review'}
        self.c._get = Mock(side_effect=lambda lid: dict(self.lesson))
        self.c._put = Mock()
        self.c.write_script = Mock(return_value='new-script.md')

    def test_stale_approval_cannot_create_script_file(self):
        for revision in (3, None, True, '4'):
            with self.assertRaises(Conflict):
                self.c.approve('lesson-1', revision)
        self.c.write_script.assert_not_called()
        self.c._put.assert_not_called()

    def test_current_approval_keeps_exact_revision(self):
        self.c.approve('lesson-1', 4)
        self.c.write_script.assert_called_once()
        self.assertEqual(self.c._put.call_args.args[1], 4)

    def test_slide_result_preserves_source_identity_and_rejects_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'slides.html'
            path.write_bytes(b'<html>original</html>')
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            result = {'path': str(path), 'ok': True}
            saved = self.c._save_slides_check('lesson-1', result, 4, sha, digest('Current script'))
            self.assertEqual(saved['source_sha256'], sha)
            self.assertEqual(saved['source_revision'], 4)
            self.c._put.reset_mock()
            with self.assertRaises(Conflict):
                self.c._save_slides_check('lesson-1', result, 3, sha, digest('Current script'))
            with self.assertRaises(Conflict):
                self.c._save_slides_check('lesson-1', result, 4, sha, digest('Old script'))
            path.write_bytes(b'<html>changed</html>')
            with self.assertRaises(Conflict):
                self.c._save_slides_check('lesson-1', result, 4, sha, digest('Current script'))
            path.unlink()
            with self.assertRaises(Conflict):
                self.c._save_slides_check('lesson-1', result, 4, sha, digest('Current script'))
            self.c._put.assert_not_called()


class HandoverLockTests(unittest.TestCase):
    def setUp(self):
        self.rec = Lessons.__new__(Lessons)
        self.rec.entry = Mock()
        self.rec.binding = Mock(return_value=None)
        self.rec.handover_lock = threading.Lock()
        self.rec._handover_bound = Mock(return_value={'status': 'ready'})

    def test_released_fallback_runs_after_unlock(self):
        def fallback(lid):
            self.assertTrue(self.rec.handover_lock.acquire(False))
            self.rec.handover_lock.release()
            return {'status': 'ready'}
        self.rec.handover_released = Mock(side_effect=fallback)
        self.assertEqual(self.rec.handover('lesson-1'), {'status': 'ready'})
        self.rec.handover_released.assert_called_once_with('lesson-1')

    def test_exact_session_never_falls_back_to_other_sessions(self):
        self.rec.handover_released = Mock()
        for bound in (None, {'id': 'session-1', 'lesson_id': 'other'},
                      {'id': 'session-2', 'lesson_id': 'lesson-1'}):
            self.rec.binding.return_value = bound
            with self.assertRaises(Conflict):
                self.rec.handover('lesson-1', 'session-1')
        self.rec.handover_released.assert_not_called()
        self.rec._handover_bound.assert_not_called()
        self.assertFalse(self.rec.handover_lock.locked())

    def test_matching_active_session_uses_existing_evidence_path(self):
        bound = {'id': 'session-1', 'lesson_id': 'lesson-1'}
        self.rec.binding.return_value = bound
        self.rec.handover('lesson-1', 'session-1')
        self.rec._handover_bound.assert_called_once_with(bound)
        self.assertFalse(self.rec.handover_lock.locked())


if __name__ == '__main__':
    unittest.main()
