"""Release readiness (studio/release_ready.py). Temp store and folders, fake Sam, nothing uploaded."""
import os
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
from studio.release_ready import Release, text_sha  # noqa: E402
from studio.store import Store  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.store = Store(t / 'Data')
        self.p = self.store.create('4 Signs You Sound Junior')
        self.folder = t / 'video-folder'
        (self.folder / 'video' / 'exports').mkdir(parents=True)
        self.export = self.folder / 'video' / 'exports' / 'FINAL_ASSEMBLY_r08_v02.mp4'
        self.export.write_bytes(b'fake mp4 ' * 100)
        self.pub = t / 'pub'
        self.pub.mkdir()
        (self.pub / 'desktop-client.json').write_text('{}', encoding='utf-8')
        self.posts = []
        agents = SimpleNamespace(team=SimpleNamespace(all=lambda: dict(agents=[dict(id='sam')], learnings=[])),
                                 desk_system_prompt=lambda a, l: 'sam', post=lambda *a, **k: self.posts.append((a, k)))
        self.r = Release(self.store, SimpleNamespace(path=lambda pid: self.folder), self.pub, lambda: [], agents)
        p = self.store.get(self.p['id'])
        self.store.save(p['id'], dict(publish_date='2026-11-11'), p['revision'])

    def tearDown(self):
        self.tmp.cleanup()

    def items(self, today=date(2026, 11, 12)):
        return {i['key']: i for i in self.r.view(self.p['id'], today)['items']}


class View(Base):
    def test_a_rendered_export_is_not_an_approved_one_and_nothing_is_faked(self):
        i = self.items()
        self.assertEqual(i['export']['state'], 'todo')
        self.assertIn('Nobody has approved it', i['export']['detail'])
        self.assertEqual(i['title']['state'], 'todo')
        self.assertEqual(i['thumbnail']['state'], 'todo')
        self.assertEqual((i['description']['state'], i['description']['action']), ('todo', 'draft_packaging'))
        self.assertIn('already passed', i['date']['detail'])
        self.assertEqual(i['publisher']['state'], 'blocked')
        self.assertIn('not been authorised', i['publisher']['detail'])
        self.assertEqual(i['upload']['state'], 'waiting')
        self.assertEqual(self.r.view(self.p['id'])['done'], 0)

    def test_a_saved_credential_is_never_shown_as_connected(self):
        (self.pub / 'oauth.dpapi').write_bytes(b'x')
        i = self.items()['publisher']
        self.assertEqual(i['state'], 'review')
        self.assertIn('has not been checked with YouTube', i['detail'])
        self.assertNotIn('Connected', i['detail'])
        (self.pub / 'desktop-client.json').unlink()
        (self.pub / 'oauth.dpapi').unlink()
        self.assertIn('not set up', self.items()['publisher']['detail'])


class Approvals(Base):
    def test_export_approval_is_for_that_exact_file(self):
        with self.assertRaises(ValueError):
            self.r.approve_export(self.p['id'], str(Path(self.tmp.name) / 'elsewhere.mp4'))
        self.r.approve_export(self.p['id'], str(self.export))
        self.assertEqual(self.items()['export']['state'], 'done')
        time.sleep(0.02)
        self.export.write_bytes(b'changed ' * 100)
        self.assertIn('changed or went missing', self.items()['export']['detail'])

    def test_description_approval_needs_the_version_shown(self):
        self.r.draft_packaging(self.p['id'], run=lambda *a, **k: ('', dict(description='Watch this.', cta='Grab the handbook.')))
        i = self.items()
        self.assertEqual((i['description']['state'], i['cta']['state']), ('review', 'review'))
        with self.assertRaises(ValueError):
            self.r.approve_description(self.p['id'], text_sha('Something else'))
        self.r.approve_description(self.p['id'], text_sha('Watch this.'))
        self.assertEqual(self.items()['description']['state'], 'done')
        self.assertFalse(self.posts[0][1].get('notice', True))   # no Discord message

    def test_title_and_thumbnail_need_the_existing_locks(self):
        p = self.store.get(self.p['id'])
        tid = '11111111-1111-4111-8111-111111111111'
        p = self.store.save(p['id'], dict(titles=[dict(id=tid, text='4 Signs')], chosen_title=tid), p['revision'])
        self.assertIn('not locked in', self.items()['title']['detail'])
        self.store.save(p['id'], dict(locks=dict(title='2026-11-12T10:00:00')), p['revision'])
        self.assertEqual(self.items()['title']['state'], 'done')

    def test_publish_time(self):
        with self.assertRaises(ValueError):
            self.r.set_publish_time(self.p['id'], '25:00', 'Asia/Dubai')
        p = self.store.get(self.p['id'])
        self.store.save(p['id'], dict(publish_date='2026-11-20'), p['revision'])
        self.assertIn('no time agreed', self.items()['date']['detail'])
        self.r.set_publish_time(self.p['id'], '16:00', 'Asia/Dubai')
        self.assertEqual(self.items()['date']['detail'], '2026-11-20 at 16:00 Asia/Dubai')

    def test_release_record_is_validated(self):
        p = self.store.get(self.p['id'])
        with self.assertRaises(ValueError):
            self.store.save(p['id'], dict(release=dict(published=True)), p['revision'])
        with self.assertRaises(ValueError):
            self.store.save(p['id'], dict(release=dict(export_approval='yes')), p['revision'])


class StaleAndChanged(Base):
    def pick_thumb(self):
        png = self.folder / 'thumb_v03.png'
        png.write_bytes(b'png v03')
        from studio.release_ready import sha256
        p = self.store.get(self.p['id'])
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGB', (16, 9), (255, 230, 0)).save(buf, 'PNG')
        asset = self.store.import_image('thumb_v03.png', buf.getvalue())['asset']
        th = dict(asset=asset, name='thumb_v03.png', title_id='', source_path=str(png))
        rel = dict(thumbnail_approval=dict(asset=asset, name=th['name'], path=str(png), sha256=sha256(png), by='Josh', at='2026-11-12T06:59:07'))
        self.store.save(p['id'], dict(thumbnails=[th], locks=dict(thumbnail='2026-11-12T06:59:07'), release=rel), p['revision'])
        return png

    def test_a_thumbnail_whose_bytes_changed_loses_its_selection(self):
        png = self.pick_thumb()
        self.assertEqual(self.items()['thumbnail']['state'], 'done')
        png.write_bytes(b'png v03 edited')
        i = self.items()['thumbnail']
        self.assertEqual(i['state'], 'todo')
        self.assertIn('changed after it was chosen', i['detail'])
        png.unlink()
        self.assertIn('file is missing', self.items()['thumbnail']['detail'])

    def cta(self, text='Grab the handbook.', decision='yes', approve=True, links=()):
        p = self.store.get(self.p['id'])
        p = self.store.save(p['id'], dict(lead_magnet=dict(decision=decision, name='Executive Communication Handbook')), p['revision'])
        rel = dict(p.get('release') or {}, cta=dict(text=text, by='sam'), links=list(links))
        self.store.save(p['id'], dict(release=rel), p['revision'])
        if approve:
            self.r.approve_cta(self.p['id'], text_sha(text))

    def test_cta_is_only_done_with_its_exact_text_a_decision_and_a_link(self):
        self.cta()
        i = self.items()['cta']
        self.assertNotEqual(i['state'], 'done')
        self.assertIn('No tracked link recorded yet', i['detail'])
        self.assertEqual(i['next'], 'Make the tracked link in Linklr and record it here.')
        p = self.store.get(self.p['id'])
        rel = dict(p['release'], links=[dict(url='https://lnk.example/handbook', purpose='description')])
        self.store.save(p['id'], dict(release=rel), p['revision'])
        i = self.items()['cta']
        self.assertEqual(i['state'], 'done')
        self.assertIn('1 tracked link recorded', i['detail'])
        # The text changes after approval: no longer done.
        p = self.store.get(self.p['id'])
        rel = dict(p['release'], cta=dict(p['release']['cta'], text='Grab it now.'))
        self.store.save(p['id'], dict(release=rel), p['revision'])
        i = self.items()['cta']
        self.assertNotEqual(i['state'], 'done')
        self.assertIn('changed after it was approved', i['detail'])

    def test_cta_with_no_lead_magnet_needs_no_link_and_approval_needs_the_shown_text(self):
        self.cta(decision='none', approve=False)
        with self.assertRaises(ValueError):
            self.r.approve_cta(self.p['id'], text_sha('Something else'))
        self.r.approve_cta(self.p['id'], text_sha('Grab the handbook.'))
        self.assertEqual(self.items()['cta']['state'], 'done')

    def test_cta_is_never_done_while_the_lead_magnet_is_undecided(self):
        self.cta(decision='unknown', links=[dict(url='https://lnk.example/x')])
        i = self.items()['cta']
        self.assertNotEqual(i['state'], 'done')
        self.assertEqual(i['next'], 'Decide the lead magnet.')

    def test_upload_is_tied_to_the_approved_files_fingerprint(self):
        from studio.release_ready import sha256
        self.r.approve_export(self.p['id'], str(self.export))
        good = sha256(self.export)
        jobs = [dict(state='published', video_id='abc', packet=dict(video=dict(path=str(self.export), sha256='0' * 64)))]
        self.r.jobs = lambda: jobs
        self.assertEqual(self.items()['upload']['state'], 'waiting')   # same path, different file: not ours
        jobs.append(dict(state='published', video_id='xyz', packet=dict(video=dict(path='C:/elsewhere.mp4', sha256=good))))
        i = self.items()['upload']
        self.assertEqual((i['state'], i['detail']), ('done', 'Uploaded as xyz, published.'))


if __name__ == '__main__':
    unittest.main()
