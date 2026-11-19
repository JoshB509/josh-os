"""The loaded-code diagnostic (studio/lineage_diagnostic.py): its self-check passes on the current code, fails on the
old waiting resolver, and its hashes change when the code does. No network, no Gmail, a temporary store only."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from studio import approval_lineage as L
from studio import lineage_diagnostic as D


class Diagnostic(unittest.TestCase):
    def test_self_check_passes_on_the_fixed_resolver(self):
        r = D.self_check()
        self.assertTrue(r['passed'], json.dumps(r, indent=1))
        self.assertEqual(r['checks']['send_it_while_another_message_is_going_out']['got'], 'busy')
        self.assertEqual(r['checks']['message_created_after_josh_is_never_answered']['got'], 'arrived_after')

    def test_self_check_fails_if_the_resolver_waits_again(self):
        orig = L.Lineage.resolve

        def waiting(self, agent_id, conv, *a, **k):          # the old behaviour: wait for the send, then answer
            lock = self._lock(agent_id, conv)
            if lock.locked():
                threading.Timer(0.6, lambda: None).start()
                import time
                time.sleep(0.6)
                return None, 'busy'
            return orig(self, agent_id, conv, *a, **k)
        with mock.patch.object(L.Lineage, 'resolve', waiting):
            self.assertFalse(D.self_check()['passed'])

    def test_fingerprints_follow_the_loaded_code(self):
        a = D.fingerprints()
        self.assertEqual(set(a), {'.'.join(f) for f in D.FUNCTIONS})
        with mock.patch.object(L.Lineage, '_delivered_after', lambda self, *x: []):
            self.assertNotEqual(D.fingerprints()['approval_lineage.Lineage._delivered_after'], a['approval_lineage.Lineage._delivered_after'])

    def test_report_holds_no_credentials_and_says_gmail_state(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'settings.json').write_text(json.dumps(dict(gmail_actions=dict(enabled=False), secret='x')), encoding='utf-8')
            body = D.report(root, root / 'Data', port=1234, started_at='t')
            saved = (root / 'Data' / 'diagnostics' / 'approval-lineage.json').read_text(encoding='utf-8')
        self.assertEqual((body['port'], body['gmail_writes_on'], body['self_check']['passed']), (1234, False, True))
        self.assertNotIn('secret', saved)
        self.assertNotIn('token', saved.lower().replace('tokens', ''))


if __name__ == '__main__':
    unittest.main()
