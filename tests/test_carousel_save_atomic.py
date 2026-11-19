"""Isolated save/render transactions. No live records, subprocess or browser."""
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from studio.agent import Agents
from studio.carousels import Carousels, save_receipt
from studio.store import Store, Conflict
from studio.team import Team


def png(color):
    output = io.BytesIO()
    Image.new('RGB', (32, 32), color).save(output, format='PNG')
    return output.getvalue()


class AtomicSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.slides = self.root / 'slides'
        self.original = self.slides / 'Carousel' / 'original'
        self.original.mkdir(parents=True)
        (self.original / 'slide_01.png').write_bytes(png('red'))
        self.store = Store(self.root / 'Data')
        self.tracker = Carousels(self.store)
        # Audit trigger makes even an intermediate write/rollback observable if
        # accidentally committed before render. Production has no carousel history table.
        with self.store.connect() as c:
            c.executescript('''CREATE TABLE save_audit(body TEXT);
                CREATE TRIGGER audit_update AFTER UPDATE ON carousels
                BEGIN INSERT INTO save_audit VALUES(new.body); END;
                CREATE TRIGGER audit_insert AFTER INSERT ON carousels
                BEGIN INSERT INTO save_audit VALUES(new.body); END;''')
        self.item = self.tracker.create(dict(title='Paper post', slides='Original words',
            folder='Carousel/original', design=dict(style='Paper', words=self.tracker.words_hash(dict(slides='Original words')))))
        root_patch = patch('studio.carousels.SLIDE_ROOT', self.slides)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.renderer = patch('studio.carousels.subprocess.run', side_effect=self.render).start()
        self.addCleanup(patch.stopall)

    def render(self, args, **kwargs):
        job = json.loads(Path(args[-1]).read_text(encoding='utf-8'))
        output = Path(job['out'])
        output.mkdir(parents=True)
        for i, slide in enumerate(job['slides']):
            (output / f'slide_{i + 1:02d}.png').write_bytes(png('blue'))
        return SimpleNamespace(returncode=0, stderr='', stdout='')

    def db_snapshot(self):
        with self.store.connect() as c:
            return list(c.iterdump())

    def files(self):
        return {str(p.relative_to(self.slides)): p.read_bytes() for p in self.slides.rglob('*') if p.is_file()}

    def assert_unchanged(self, before, files):
        self.assertEqual(self.db_snapshot(), before)
        self.assertEqual(self.files(), files)
        self.assertEqual(self.tracker.get(self.item['id']), self.item)

    def update(self, **changes):
        return self.tracker.apply('sam', [dict(id=self.item['id'], slides='New words', **changes)])[0]

    def test_custom_guard_rejects_before_any_save_render_or_history(self):
        self.item = self.tracker.save(self.item['id'], {'design': dict(self.item['design'], override_version='a' * 32)}, self.item['revision'])
        before, files = self.db_snapshot(), self.files()
        for changes in ({}, {'make_design': True}, {'caption': 'Also changed'}):
            with self.subTest(changes=changes):
                row = self.update(**changes)
                self.assertEqual(row['action'], 'failed')
                self.assertIn('custom slides', row['error'])
                self.assert_unchanged(before, files)
        with self.assertRaisesRegex(ValueError, 'custom slides'):
            self.tracker.save(self.item['id'], {'slides': 'Changed directly'}, self.item['revision'])
        with self.assertRaisesRegex(ValueError, 'custom slides'):
            self.tracker.save(self.item['id'], {'slides': 'Changed', 'design': {}}, self.item['revision'], make_design=True)
        self.assert_unchanged(before, files)
        self.renderer.assert_not_called()

    def test_custom_metadata_save_preserves_images(self):
        self.item = self.tracker.save(self.item['id'], {'design': dict(self.item['design'], override_version='a' * 32)}, self.item['revision'])
        files = self.files()
        row = self.tracker.apply('sam', [dict(id=self.item['id'], caption='Only the caption', tags={'theme': 'Meetings'})])[0]
        self.assertEqual(row['action'], 'updated')
        self.assertEqual(row['revision'], self.item['revision'] + 1)
        saved = self.tracker.get(self.item['id'])
        self.assertEqual(saved['design'], self.item['design'])
        self.assertEqual(self.files(), files)
        self.renderer.assert_not_called()

    def test_failed_partial_render_timeout_missing_output_and_os_error_are_atomic(self):
        before, files = self.db_snapshot(), self.files()
        def partial(args, **kwargs):
            self.render(args, **kwargs)
            return SimpleNamespace(returncode=1, stderr='Rendering failed', stdout='')
        outcomes = [partial, subprocess.TimeoutExpired('render', 240), OSError('Renderer unavailable'),
                    lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr='', stdout='')]
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                self.renderer.side_effect = outcome
                self.assertEqual(self.update(caption='Not saved')['action'], 'failed')
                self.assert_unchanged(before, files)

    def test_invalid_source_does_not_save_or_render(self):
        before, files = self.db_snapshot(), self.files()
        result = self.tracker.apply('sam', [dict(id=self.item['id'], slides='Title\n[image: missing.png]')])
        self.assertEqual(result[0]['action'], 'failed')
        self.assert_unchanged(before, files)
        self.renderer.assert_not_called()

    def test_make_design_failure_keeps_record_and_previous_images(self):
        before, files = self.db_snapshot(), self.files()
        self.renderer.side_effect = OSError('No renderer')
        with self.assertRaises(OSError):
            self.tracker.make_design(self.item['id'])
        self.assert_unchanged(before, files)

    def test_failed_new_render_leaves_no_record_or_history(self):
        before, files = self.db_snapshot(), self.files()
        self.renderer.side_effect = subprocess.TimeoutExpired('render', 240)
        result = self.tracker.apply('sam', [dict(title='New post', slides='New text', make_design=True)])
        self.assertEqual(result[0]['action'], 'failed')
        self.assert_unchanged(before, files)

    def test_paper_text_and_render_commit_once_and_keep_old_version(self):
        files = self.files()
        row = self.update()
        saved = self.tracker.get(self.item['id'])
        self.assertEqual((row['action'], row['revision']), ('designed', self.item['revision'] + 1))
        self.assertEqual(saved['slides'], 'New words')
        self.assertEqual(saved['design']['words'], self.tracker.words_hash(saved))
        self.assertEqual(len(self.tracker.images(saved)), 1)
        self.assertNotEqual(saved['folder'], self.item['folder'])
        self.assertEqual({name: self.files()[name] for name in files}, files)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM save_audit').fetchone()[0], 2)

    def test_new_paper_posts_have_complete_images_at_revision_one_and_unique_folders(self):
        rows = self.tracker.apply('sam', [dict(title='Same title', slides='One\n---\nTwo', make_design=True) for _ in range(2)])
        items = [self.tracker.get(row['id']) for row in rows]
        self.assertNotEqual(items[0]['folder'], items[1]['folder'])
        for item in items:
            self.assertEqual(item['revision'], 1)
            self.assertEqual(len(self.tracker.images(item)), 2)

    def test_stale_chat_snapshot_and_explicit_revision_reject_without_render(self):
        saved = self.tracker.save(self.item['id'], {'caption': 'Concurrent winner'}, self.item['revision'])
        before, files = self.db_snapshot(), self.files()
        row = self.tracker.apply('sam', [dict(id=self.item['id'], slides='Lost update')], revisions={self.item['id']: self.item['revision']})[0]
        self.assertEqual(row['action'], 'failed')
        row = self.tracker.apply('sam', [dict(id=self.item['id'], slides='Lost update', revision=self.item['revision'])])[0]
        self.assertEqual(row['action'], 'failed')
        self.assertEqual(self.tracker.get(self.item['id']), saved)
        self.assertEqual(self.db_snapshot(), before)
        self.assertEqual(self.files(), files)
        self.renderer.assert_not_called()

    def test_concurrent_edit_during_render_wins_and_discards_new_images(self):
        files = self.files()
        winner = {}
        def concurrent(args, **kwargs):
            winner['item'] = self.tracker.save(self.item['id'], {'caption': 'Concurrent winner'}, self.item['revision'])
            winner['db'] = self.db_snapshot()
            return self.render(args, **kwargs)
        self.renderer.side_effect = concurrent
        row = self.update()
        self.assertEqual(row['action'], 'failed')
        self.assertEqual(self.tracker.get(self.item['id']), winner['item'])
        self.assertEqual(self.db_snapshot(), winner['db'])
        self.assertEqual(self.files(), files)

    def test_database_failure_rolls_back_record_history_and_render_files(self):
        with self.store.connect() as c:
            c.execute("CREATE TRIGGER reject_save AFTER UPDATE ON carousels BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        before, files = self.db_snapshot(), self.files()
        row = self.update()
        self.assertEqual(row['action'], 'failed')
        self.assert_unchanged(before, files)

    def test_partial_copy_failure_removes_only_new_render_files(self):
        before, files = self.db_snapshot(), self.files()
        def partial(source, destination, **kwargs):
            (destination / 'partial.png').write_bytes(b'incomplete')
            raise OSError('Copy failed')
        with patch('studio.carousels.shutil.copytree', side_effect=partial):
            self.assertEqual(self.update()['action'], 'failed')
        self.assert_unchanged(before, files)

    def test_render_destination_collision_preserves_existing_folder(self):
        version = 'b' * 32
        existing = self.slides / 'Carousel' / f"{self.item['id']}_{version}"
        existing.mkdir()
        (existing / 'keep.txt').write_text('Existing history')
        before, files = self.db_snapshot(), self.files()
        with patch('studio.carousels.uuid.uuid4', return_value=SimpleNamespace(hex=version)):
            self.assertEqual(self.update()['action'], 'failed')
        self.assert_unchanged(before, files)

    def test_invalid_render_image_is_not_saved(self):
        before, files = self.db_snapshot(), self.files()
        def invalid(args, **kwargs):
            result = self.render(args, **kwargs)
            job = json.loads(Path(args[-1]).read_text(encoding='utf-8'))
            (Path(job['out']) / 'slide_01.png').write_bytes(b'invalid png')
            return result
        self.renderer.side_effect = invalid
        self.assertEqual(self.update()['action'], 'failed')
        self.assert_unchanged(before, files)

    def test_receipts_are_host_outcomes_for_mixed_success_and_failure(self):
        rows = self.tracker.apply('sam', [dict(id=self.item['id'], caption='Saved caption'), dict(title='Bad', make_design=True)])
        receipt = save_receipt(rows)
        self.assertIn('Saved Paper post in Josh OS (revision 2; updated)', receipt)
        self.assertIn('Could not save Bad', receipt)
        self.assertIn('No carousel changes were saved for this request', receipt)


class CarouselChatSaveTests(AtomicSaveTests):
    def setUp(self):
        super().setUp()
        self.agents = Agents(self.store, self.root, Team(self.store))
        self.agents.training = lambda owner: ('', [])
        self.answer = dict(reply="I've saved and redrawn the carousel.", carousels=[dict(id=self.item['id'], slides='New words')])
        self.agents.run = lambda *args, **kwargs: (self.answer['reply'], self.answer)

    def chat(self):
        return self.agents.chat(dict(agent_id='sam', message='Save the corrected copy.', context=dict(kind='carousel', id=self.item['id'])),
            [self.item], self.tracker)['reply']

    def test_failed_save_removes_premature_model_success_claim(self):
        self.renderer.side_effect = OSError('Renderer unavailable')
        reply = self.chat()
        self.assertNotIn("I've saved", reply['text'])
        self.assertIn('Could not save', reply['text'])
        self.assertEqual(reply['saved'][0]['action'], 'failed')
        self.assertEqual(self.tracker.get(self.item['id']), self.item)

    def test_successful_save_reply_uses_committed_revision(self):
        reply = self.chat()
        self.assertNotIn("I've saved", reply['text'])
        self.assertIn('Saved Paper post in Josh OS (revision 2; designed)', reply['text'])
        self.assertEqual(reply['saved'][0]['revision'], self.tracker.get(self.item['id'])['revision'])

    def test_chat_snapshot_revision_is_bound_before_model_returns(self):
        def concurrent(*args, **kwargs):
            self.tracker.save(self.item['id'], {'caption': 'Newer edit'}, self.item['revision'])
            return self.answer['reply'], self.answer
        self.agents.run = concurrent
        reply = self.chat()
        self.assertEqual(reply['saved'][0]['action'], 'failed')
        self.assertEqual(self.tracker.get(self.item['id'])['caption'], 'Newer edit')
        self.assertEqual(self.tracker.get(self.item['id'])['slides'], self.item['slides'])
        self.renderer.assert_not_called()


for _name in tuple(AtomicSaveTests.__dict__):
    if _name.startswith('test_'):
        setattr(CarouselChatSaveTests, _name, None)


if __name__ == '__main__':
    unittest.main()
