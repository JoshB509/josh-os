"""discord_setup.py with made-up tokens in a temp folder. Never touches Data/discord.json or Discord."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
import discord_setup as D  # noqa: E402

OLD = 'MTAwMDAwMDAwMDAwMDAwMDAx.GoodOk.' + 'a' * 38          # fake, shaped like a token
NEW = 'MTIwMDAwMDAwMDAwMDAwMDAy.Ab-c_D.' + 'b' * 38


class Setup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / 'Data' / 'discord.json'
        self.file.parent.mkdir()
        self.saved = dict(owners=['123456789012345678'], bots=dict(sam=dict(token=OLD, channels=['sam']), tariq=dict(token=OLD, channels=['tariq'])))
        self.file.write_text(json.dumps(self.saved), encoding='utf-8')
        self.out = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_setup(self, answers, secrets, **kwargs):
        answers, secrets = list(answers), list(secrets)

        def ask(prompt):
            v = answers.pop(0) if answers else ''
            if isinstance(v, BaseException):
                raise v
            return v

        def secret(prompt):
            v = secrets.pop(0) if secrets else ''
            if isinstance(v, BaseException):
                raise v
            return v
        return D.main(self.file, ask, secret, self.out.append, **kwargs)

    def now(self):
        return json.loads(self.file.read_text(encoding='utf-8'))

    def test_a_two_character_or_ctrl_v_paste_never_replaces_a_working_token(self):
        for bad in (['ab', 'ab', 'ab'], ['\x16', '\x16v', 'x'], ['not a token', 'a.b.c', OLD[:-30]]):
            self.run_setup(['', 'y', ''], bad)
            self.assertEqual(self.now()['bots']['sam']['token'], OLD)
        said = ' '.join(self.out)
        self.assertIn('control character', said)
        self.assertIn('saved token was kept', said)
        self.assertNotIn(OLD, said)
        self.assertNotIn(OLD[:-30], said)

    def test_a_good_token_is_saved_and_the_old_file_kept(self):
        self.run_setup(['', 'y', ''], ['  Bot ' + NEW + '  '])
        self.assertEqual(self.now()['bots']['sam']['token'], NEW)
        self.assertEqual(self.now()['bots']['tariq']['token'], OLD)
        self.assertEqual(json.loads(self.file.with_suffix('.json.previous').read_text(encoding='utf-8'))['bots']['sam']['token'], OLD)
        self.assertNotIn(NEW, ' '.join(self.out))

    def test_blank_cancel_and_closed_window_change_nothing(self):
        before = self.file.read_bytes()
        self.run_setup(['', 'y', ''], [''])
        self.run_setup(['', 'y'], [KeyboardInterrupt()])
        self.run_setup(['', EOFError()], [])
        self.run_setup(['', 'y', ''], [NEW, ''] + [''])  # Sam changed...
        self.file.write_bytes(before)
        self.run_setup(['', 'y', '', 'y'], [NEW, KeyboardInterrupt()])  # ...but cancelled before the end
        self.assertEqual(self.file.read_bytes(), before)
        self.assertIn('Nothing was changed', ' '.join(self.out))

    def test_an_unreadable_file_is_never_overwritten(self):
        self.file.write_text('{"owners": ["1"], "bots": {"sam": {"token": "', encoding='utf-8')
        broken = self.file.read_bytes()
        self.assertEqual(self.run_setup(['', 'y', ''], [NEW]), 1)
        self.assertEqual(self.file.read_bytes(), broken)

    def test_bad_user_id_and_channel_keep_the_saved_ones(self):
        self.run_setup(['me', 'y', '#General Chat!'], [NEW])
        c = self.now()
        self.assertEqual(c['owners'], ['123456789012345678'])
        self.assertEqual(c['bots']['sam']['channels'], ['sam'])

    def test_targeted_sam_max_preserves_other_bots_and_owner_and_unknown_fields(self):
        saved = self.saved
        for name in ('anouk', 'saskia', 'laia'):
            saved['bots'][name] = {'token': OLD, 'channels': [name], 'extra': {'keep': True}}
        saved['custom'] = {'a': [1, 2]}
        self.file.write_text(json.dumps(saved), encoding='utf-8')
        result = self.run_setup(['y', '', 'y', 'max'], [NEW, NEW], agents=('sam', 'max'), keep_owner=True)
        self.assertEqual(result, 0)
        now = self.now()
        self.assertEqual(now['owners'], saved['owners'])
        self.assertEqual(now['custom'], saved['custom'])
        for name in ('tariq', 'anouk', 'saskia', 'laia'):
            self.assertEqual(now['bots'][name], saved['bots'][name])
        self.assertEqual(now['bots']['sam']['token'], NEW)
        self.assertEqual(now['bots']['max']['token'], NEW)
        self.assertEqual(json.loads(self.file.with_suffix('.json.previous').read_text()), saved)
        self.assertNotIn(NEW, ' '.join(self.out))
        self.assertNotIn(OLD, ' '.join(self.out))

    def test_targeted_cancel_rolls_back_both_updates(self):
        before = self.file.read_bytes()
        self.run_setup(['y', '', 'y'], [NEW, KeyboardInterrupt()], agents=('sam', 'max'), keep_owner=True)
        self.assertEqual(self.file.read_bytes(), before)

    def test_targeted_blank_keeps_sam_and_does_not_add_empty_max(self):
        before = self.file.read_bytes()
        self.run_setup(['y', '', 'y'], ['', ''], agents=('sam', 'max'), keep_owner=True)
        self.assertEqual(self.file.read_bytes(), before)

    def test_targeted_invalid_selection_never_writes(self):
        before = self.file.read_bytes()
        self.assertEqual(self.run_setup([], [], agents=('typo',)), 1)
        self.assertEqual(self.file.read_bytes(), before)

    def test_token_shape(self):
        self.assertEqual(D.clean_token(f'"{NEW}"'), NEW)
        for bad in ('', 'ab', NEW.replace('.', ''), NEW + '.x', NEW.replace('b', '!'), 'mfa.' + 'x' * 80):
            self.assertIsNone(D.clean_token(bad), bad)


if __name__ == '__main__':
    unittest.main()
