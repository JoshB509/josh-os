"""Picker and process dispatch contracts. No native dialogs, OBS or media processing."""
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from studio.server import Handler, Server


class SetupRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'settings.json').write_text(json.dumps(dict(python=sys.executable)), encoding='utf-8')
        self.server = SimpleNamespace(root=self.root, picker_lock=threading.Lock(), edit_flow=Mock())
        self.server.edit_flow_action = lambda pid, action, data: Server.edit_flow_action(self.server, pid, action, data)

    def request(self, path, body):
        h = object.__new__(Handler)
        raw = json.dumps(body).encode()
        h.path, h.server, h.headers, h.rfile = path, self.server, {'Content-Length': str(len(raw))}, io.BytesIO(raw)
        h.authorized, h.send = Mock(return_value=True), Mock()
        h.route('POST')
        return h.send.call_args

    def test_audio_picker_dispatch_and_unicode_result(self):
        selected = 'E:/Audio/good microphone \u00e9.wav'
        with patch('studio.server.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=selected+'\n')) as run:
            response = self.request('/api/pick', dict(kind='audio'))
        self.assertEqual(response.args[0], dict(path=selected))
        args, kwargs = run.call_args
        self.assertEqual(args[0][0], sys.executable)
        self.assertEqual(Path(args[0][1]).name, 'picker.py')
        self.assertEqual(args[0][2], 'audio')
        self.assertEqual(kwargs['encoding'], 'utf-8')
        self.assertFalse(self.server.picker_lock.locked())

    def test_cancel_and_picker_error_release_lock(self):
        with patch('studio.server.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='\n')):
            self.assertEqual(self.request('/api/pick', dict(kind='audio')).args[0], dict(path=''))
        with patch('studio.server.subprocess.run', return_value=SimpleNamespace(returncode=1, stdout='')):
            response = self.request('/api/pick', dict(kind='audio'))
        self.assertEqual(response.args[1], 400)
        self.assertFalse(self.server.picker_lock.locked())

    def test_picker_busy_does_not_launch_second_dialog(self):
        self.server.picker_lock.acquire()
        with patch('studio.server.subprocess.run') as run:
            response = self.request('/api/pick', dict(kind='audio'))
        self.assertEqual(response.args[1], 409)
        run.assert_not_called()
        self.server.picker_lock.release()

    def test_sources_and_go_dispatch_to_selected_project_without_processing(self):
        body = dict(camera=r'E:\video\recording.mp4', mic=r'E:\audio\good.wav')
        self.request('/api/projects/selected-project/edit-flow/sources', body)
        self.server.edit_flow.register.assert_called_once_with('selected-project', body['camera'], body['mic'], '')
        self.server.edit_flow.go_ahead.assert_not_called()
        self.request('/api/projects/selected-project/edit-flow/go', {})
        self.server.edit_flow.go_ahead.assert_called_once_with('selected-project')


if __name__ == '__main__':
    unittest.main()
