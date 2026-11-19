"""Tariq's archive (studio/archive.py). Fakes only: temp folders stand in for drives, a fake feed for YouTube."""
import gzip
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
from studio import archive as A  # noqa: E402
from studio.store import Store  # noqa: E402

CH = 'UC' + 'a' * 22
VID = 'dQw4w9WgXcQ'
T0 = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)


def feed(*ids, channel=CH):
    entries = ''.join(f'<entry><yt:videoId>{i}</yt:videoId><title>Video {i}</title><published>2026-11-13T08:00:00+00:00</published></entry>'
                      for i in ids)
    return (f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
            f'<yt:channelId>{channel}</yt:channelId>{entries}</feed>').encode()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.store = Store(t / 'Data')
        self.pid = self.store.create('4 Signs You Sound Junior')['id']
        # The video folder, a dependency outside it, and a Premiere project that uses both.
        self.src = t / 'E' / 'CareerOS' / '4-signs'
        (self.src / 'video' / 'exports').mkdir(parents=True)
        (self.src / 'video' / 'Tariq').mkdir(parents=True)
        self.master = self.src / 'video' / 'exports' / 'FINAL_ASSEMBLY_r08_v02.mp4'
        self.master.write_bytes(b'final master ' * 1000)
        (self.src / 'script.md').write_text('script')
        self.ext = t / 'E' / 'Recordings' / 'mic.wav'
        self.ext.parent.mkdir(parents=True)
        self.ext.write_bytes(b'mic ' * 500)
        self.cam = self.src / 'Recorded' / 'cam.mp4'
        self.cam.parent.mkdir()
        self.cam.write_bytes(b'cam ' * 800)
        self.prproj = self.src / 'video' / 'Tariq' / '4 Signs - Tariq.prproj'
        self.write_prproj([self.ext, self.cam])
        (self.src / 'video' / 'Tariq' / '~lock.tmp').write_text('x')
        # Drives: the Passport and a separate backup, as temp folders.
        self.vols = [dict(letter='P', volume_id='\\\\?\\Volume{pass}\\', serial='WXPASS', disk='WD My Passport 2627', label='My Passport',
                          size=2 * 10**12, free=10**12, root=str(t / 'passport')),
                     dict(letter='Q', volume_id='\\\\?\\Volume{back}\\', serial='BACK1', disk='Samsung T7', label='Backup',
                          size=10**12, free=10**11, root=str(t / 'backup'))]
        self.now = T0
        self.feed_body = feed(VID)
        self.busy = ''
        self.posts = []
        self.flow_items = []
        self.reviews = []
        flow = SimpleNamespace(_all=lambda pid, kind: [a for a in self.flow_items if a['kind'] == kind])
        fio = SimpleNamespace(all=lambda pid: self.reviews)
        folders = SimpleNamespace(path=lambda pid: self.src)
        agents = SimpleNamespace(post=lambda who, text, notice=True: self.posts.append((who, text, notice)))
        self.trashed = []
        self.a = A.Archive(self.store, t, flow, folders, fio, agents, volumes=lambda: self.vols,
                           fetch=lambda url: (200, self.feed_body), clock=lambda: self.now, busy=lambda: self.busy,
                           trash=lambda p: self.trashed.append(str(p)))
        sha = hashlib.sha256(self.master.read_bytes()).hexdigest()
        self.a.final_master = lambda pid: dict(path=str(self.master), sha256=sha, size=self.master.stat().st_size)

    def tearDown(self):
        self.tmp.cleanup()

    def write_prproj(self, refs):
        xml = '<Project>' + ''.join(f'<Media><ActualMediaFilePath>{p}</ActualMediaFilePath><PeakFilePath>{p}.pek</PeakFilePath></Media>'
                                    for p in refs) + '</Project>'
        self.prproj.write_bytes(gzip.compress(xml.encode()))

    def setup_drives(self, backup=True):
        self.a.set_channel(CH)
        self.a.set_drive('passport', self.vols[0]['volume_id'], 'WXPASS')
        if backup:
            self.a.set_drive('backup', self.vols[1]['volume_id'], 'BACK1')

    def proven(self, days=8):
        self.setup_drives()
        self.a.record_public(self.pid, VID)
        self.now = T0 + timedelta(days=days)


class Proof(Base):
    def test_public_needs_the_feed_not_a_claim_or_a_date(self):
        self.setup_drives()
        self.feed_body = feed('otherVideo1')                       # scheduled or unlisted: not in the public feed
        p = self.a.record_public(self.pid, VID)
        self.assertNotIn('first_verified_at', p)
        el = self.a.eligibility(self.pid)
        self.assertFalse(el['eligible'])
        self.assertEqual(self.posts, [])
        self.feed_body = feed(VID)
        p = self.a.record_public(self.pid, VID)
        self.assertEqual(p['first_verified_at'], T0.isoformat())
        self.assertEqual(p['eligible_from'], (T0 + timedelta(days=7)).isoformat())
        self.assertEqual([(w, n) for w, _, n in self.posts], [('tariq', False)])   # Tariq is told once, no Discord notice
        self.a.record_public(self.pid, VID)
        self.assertEqual(len(self.posts), 1)

    def test_bad_input_and_another_channel_prove_nothing(self):
        with self.assertRaises(ValueError):
            self.a.record_public(self.pid, VID)                    # no channel id set
        self.setup_drives()
        with self.assertRaises(ValueError):
            self.a.record_public(self.pid, 'not an id!')
        self.feed_body = feed(VID, channel='UC' + 'b' * 22)
        self.assertNotIn('first_verified_at', self.a.record_public(self.pid, VID))
        self.a.fetch = lambda url: (500, b'')
        self.assertNotIn('first_verified_at', self.a.record_public(self.pid, VID))

    def test_seven_days_from_proof_and_still_public(self):
        self.setup_drives()
        self.a.record_public(self.pid, VID)
        self.now = T0 + timedelta(days=6, hours=23)
        self.assertFalse(self.a.eligibility(self.pid)['eligible'])
        self.now = T0 + timedelta(days=7, minutes=1)
        self.feed_body = feed('otherVideo1')                       # taken private again
        el = self.a.eligibility(self.pid)
        self.assertFalse(el['eligible'])
        self.feed_body = feed(VID)
        self.assertTrue(self.a.eligibility(self.pid)['eligible'])

    def test_open_revisions_block(self):
        self.proven()
        self.flow_items = [dict(id='cut-12345678', kind='edit_cut', payload=dict(state='running'))]
        self.assertIn('cut cut-1234 is running', self.a.eligibility(self.pid)['blockers'])
        self.flow_items = []
        self.reviews = [dict(id='rev-12345678', state='ready', feedback=[dict(text='Fix typo', completed=False)])]
        self.assertFalse(self.a.eligibility(self.pid)['eligible'])
        self.reviews = [dict(id='rev-12345678', state='ready', feedback=[dict(text='Fix typo', completed=True)], feedback_read_at='t1', review_closed=dict(by='Josh', at='t2', feedback_read_at='t1'))]
        self.assertTrue(self.a.eligibility(self.pid)['eligible'])


class Copy(Base):
    def test_prepare_finds_dependencies_and_the_final_master(self):
        man = self.a.prepare(self.pid)
        rels = {f['rel']: f['kind'] for f in man['files']}
        self.assertEqual(man['blockers'], [])
        self.assertIn('Recorded/cam.mp4', rels)
        self.assertEqual(rels['video/exports/FINAL_ASSEMBLY_r08_v02.mp4'], 'final_master')
        ext = [r for r, k in rels.items() if k == 'dependency']
        self.assertEqual(len(ext), 1)
        self.assertTrue(ext[0].startswith('external/') and ext[0].endswith('Recordings/mic.wav'))
        self.assertFalse(any('lock' in r for r in rels))           # temp and lock files are skipped

    def test_a_missing_dependency_blocks(self):
        self.write_prproj([self.ext, Path(self.tmp.name) / 'gone.wav'])
        man = self.a.prepare(self.pid)
        self.assertTrue(man['blockers'])
        self.proven()
        with self.assertRaises(ValueError):
            self.a.copy(self.pid)

    def test_copy_needs_eligibility(self):
        self.setup_drives()
        self.a.prepare(self.pid)
        with self.assertRaises(ValueError):
            self.a.copy(self.pid)
        self.assertFalse((Path(self.tmp.name) / 'passport').exists())

    def test_drive_found_by_identity_not_letter(self):
        self.proven()
        self.vols[0]['letter'] = 'Z'                               # plugged into another port
        self.a.prepare(self.pid)
        self.a.copy(self.pid)
        self.vols[0]['serial'] = 'SOMEONE-ELSE'                    # same volume id, different disk
        with self.assertRaises(ValueError):
            self.a.copy(self.pid, 'passport')
        with self.assertRaises(ValueError):
            self.a.set_drive('backup', self.vols[1]['volume_id'], 'WXPASS')

    def test_verified_resumable_copy_restore_and_relink(self):
        self.proven()
        man = self.a.prepare(self.pid)
        calls = {'n': 0}
        self.a.copy(self.pid, stop=lambda: calls.__setitem__('n', calls['n'] + 1) or calls['n'] > 2)   # stopped part way
        j = json.loads((self.a.dir / self.pid / 'passport-copy.json').read_text())
        self.assertFalse(j.get('complete'))
        done = {r for r, v in j['files'].items() if v['state'] == 'verified'}
        self.assertEqual(len(done), 2)
        before = {r: (Path(self.tmp.name) / 'passport' / A.ARCHIVE_DIR / man['folder_name'] / r).stat().st_mtime_ns for r in done}
        st = self.a.copy(self.pid)
        self.assertEqual(st['state'], 'passport_copied')
        for r, m in before.items():                               # resumed, not redone
            self.assertEqual((Path(self.tmp.name) / 'passport' / A.ARCHIVE_DIR / man['folder_name'] / r).stat().st_mtime_ns, m)
        self.assertFalse(list((Path(self.tmp.name) / 'passport').rglob('*.partial')))
        res = self.a.verify(self.pid)
        self.assertTrue(res['ok'], res['problems'])
        self.assertEqual([(x['prproj'], x['media'], x['missing'], x['outside']) for x in res['relinked']], [('video/Tariq/4 Signs - Tariq.prproj', 2, 0, 0)])
        self.assertTrue(self.master.is_file() and self.ext.is_file())   # the source is untouched

    def test_a_bad_copy_or_changed_source_blocks(self):
        self.proven()
        self.a.prepare(self.pid)
        real = self.a._copy_one
        self.a._copy_one = lambda s, d: (False, 'x')
        self.assertEqual(self.a.copy(self.pid)['state'], 'blocked')
        self.a._copy_one = real
        self.cam.write_bytes(b'changed')
        self.assertEqual(self.a.copy(self.pid)['state'], 'blocked')

    def test_corruption_after_copy_fails_verification(self):
        self.proven()
        man = self.a.prepare(self.pid)
        self.a.copy(self.pid)
        (Path(self.tmp.name) / 'passport' / A.ARCHIVE_DIR / man['folder_name'] / 'script.md').write_text('rot')
        self.assertFalse(self.a.verify(self.pid)['ok'])

    def test_pauses_while_recording_and_checks_space(self):
        self.proven()
        self.a.prepare(self.pid)
        self.busy = 'OBS'
        self.assertEqual(self.a.copy(self.pid)['state'], 'paused')
        self.busy = ''
        self.vols[0]['free'] = 10
        with self.assertRaises(ValueError):
            self.a.copy(self.pid)


class Cleanup(Base):
    def copy_both(self):
        self.proven()
        self.a.prepare(self.pid)
        for role in ('passport', 'backup'):
            self.a.copy(self.pid, role)
            self.assertTrue(self.a.verify(self.pid, role)['ok'])

    def restore_ok(self):
        self.a.record_premiere_restore(self.pid, dict(opened=True, rendered=True, offline_media=0, role='passport'))

    def enabled(self):
        old = A.CLEANUP_ENABLED
        A.CLEANUP_ENABLED = True
        self.addCleanup(setattr, A, 'CLEANUP_ENABLED', old)

    def test_cleanup_is_switched_off(self):
        self.copy_both()
        self.restore_ok()
        r = self.a.cleanup_review(self.pid)
        self.assertFalse(r['ready'])
        self.assertIn('switched off', r['blockers'][0])
        with self.assertRaises(ValueError):
            self.a.cleanup(self.pid, 'any-id')
        self.assertEqual(self.trashed, [])

    def test_no_backup_blocks_cleanup(self):
        self.enabled()
        self.proven()
        self.a.prepare(self.pid)
        self.a.copy(self.pid)
        self.a.verify(self.pid)
        self.restore_ok()
        r = self.a.cleanup_review(self.pid)
        self.assertFalse(r['ready'])
        self.assertTrue(any('backup' in b for b in r['blockers']))

    def test_shared_dependencies_and_the_master_are_never_offered_for_removal(self):
        self.enabled()
        bg = Path(self.tmp.name) / 'E' / 'CareerOS' / 'Backgrounds' / 'Josh-Video-Background-Office-4K.png'
        bg.parent.mkdir(parents=True)
        bg.write_bytes(b'background used by every video')
        self.write_prproj([self.ext, self.cam, bg])
        self.copy_both()
        self.restore_ok()
        r = self.a.cleanup_review(self.pid)
        self.assertTrue(r['ready'], r['blockers'])
        paths = [f['path'] for f in r['files']]
        for keep in (bg, self.ext, self.master):
            self.assertNotIn(str(keep), paths)
        self.assertIn(str(self.cam), paths)

    def test_backup_is_checked_again_now_not_from_an_old_flag(self):
        self.enabled()
        self.copy_both()
        self.restore_ok()
        man = self.a.manifest(self.pid)
        (Path(self.tmp.name) / 'backup' / A.ARCHIVE_DIR / man['folder_name'] / 'script.md').write_text('rot')
        r = self.a.cleanup_review(self.pid)
        self.assertFalse(r['ready'])
        self.assertTrue(any('backup copy did not pass' in b for b in r['blockers']))
        self.vols.pop(1)                                           # the backup drive unplugged
        r = self.a.cleanup_review(self.pid)
        self.assertTrue(any('backup copy could not be checked' in b for b in r['blockers']))

    def test_no_premiere_restore_means_not_ready(self):
        self.enabled()
        self.copy_both()
        self.assertFalse(self.a.cleanup_review(self.pid)['ready'])
        self.a.record_premiere_restore(self.pid, dict(opened=True, rendered=False, offline_media=0))
        self.assertFalse(self.a.cleanup_review(self.pid)['ready'])

    def test_approval_must_be_josh_in_josh_os_for_the_exact_review(self):
        self.enabled()
        self.copy_both()
        self.restore_ok()
        r = self.a.cleanup_review(self.pid)
        with self.assertRaises(ValueError):
            self.a.approve_cleanup(self.pid, r['digest'], 'yes')          # not from Josh OS itself
        with self.assertRaises(ValueError):
            self.a.approve_cleanup(self.pid, 'other-digest', 'yes', trusted=True)
        with self.assertRaises(ValueError):
            self.a.cleanup(self.pid, {'by': 'Josh', 'said': 'anything'})  # a made-up approval is not one
        aid = self.a.approve_cleanup(self.pid, r['digest'], 'yes, clear the source', trusted=True)
        with self.assertRaises(ValueError):
            self.a.cleanup(self.pid, aid, permanent=True)                 # permanent needs his own words
        out = self.a.cleanup(self.pid, aid)
        self.assertEqual(out['permanent'], False)
        self.assertIn(str(self.cam), self.trashed)
        with self.assertRaises(ValueError):
            self.a.cleanup(self.pid, aid)                                 # used once only

    def test_a_source_edited_after_the_copy_stops_cleanup(self):
        self.enabled()
        self.copy_both()
        self.restore_ok()
        r = self.a.cleanup_review(self.pid)
        aid = self.a.approve_cleanup(self.pid, r['digest'], 'yes', trusted=True)
        st = self.cam.stat()
        self.cam.write_bytes(b'cam ' * 799 + b'edit')                   # same size, different bytes
        import os
        os.utime(self.cam, ns=(st.st_atime_ns, st.st_mtime_ns))
        with self.assertRaises(ValueError):
            self.a.cleanup(self.pid, aid)
        self.assertEqual(self.trashed, [])

    def test_status_reads_plainly(self):
        self.copy_both()
        self.a.cleanup_review(self.pid)
        lines = self.a.status(self.pid)['lines']
        self.assertTrue(lines[0].startswith('Public on YouTube'))
        self.assertTrue(any('relinked Premiere project saved' in x for x in lines))
        self.assertTrue(any('Premiere has not opened' in x for x in lines))
        audit = (self.a.dir / 'audit.jsonl').read_text().splitlines()
        self.assertTrue(any('"cleanup reviewed"' in x for x in audit))


class Relink(Base):
    def test_relinked_project_is_saved_and_works_with_the_originals_gone(self):
        odd = Path(self.tmp.name) / 'E' / 'Music' / "Tom & Jerry's mix.wav"
        odd.parent.mkdir(parents=True)
        odd.write_bytes(b'music')
        xml = ('<Project>' + ''.join(f'<Media><ActualMediaFilePath>{A.xml_escape(p)}</ActualMediaFilePath></Media>'
                                     for p in (self.ext, self.cam, odd)) + '</Project>')
        self.prproj.write_bytes(gzip.compress(xml.encode()))
        self.proven()
        man = self.a.prepare(self.pid)
        self.assertEqual(man['blockers'], [])
        self.a.copy(self.pid)
        self.ext.unlink()
        odd.unlink()                                               # original drives "unplugged"
        res = self.a.verify(self.pid)
        self.assertTrue(res['ok'], res['problems'])
        dest = Path(self.tmp.name) / 'passport' / A.ARCHIVE_DIR / man['folder_name']
        saved = dest / res['relinked'][0]['saved']
        self.assertTrue(saved.is_file())
        refs = A.prproj_paths(saved)
        self.assertEqual(len(refs), 3)
        self.assertTrue(all(str(r).startswith(str(dest)) and Path(r).is_file() for r in refs))
        self.assertIn('Tom &amp; Jerry', gzip.decompress(saved.read_bytes()).decode())

    def test_frameio_ready_is_not_a_finished_review(self):
        self.proven()
        self.reviews = [dict(id='rev-12345678', state='ready', feedback=[])]
        self.assertTrue(any('not been closed' in b for b in self.a.eligibility(self.pid)['blockers']))
        self.reviews = [dict(id='rev-12345678', state='ready', feedback=[dict(text='x', completed=True)], feedback_read_at='2026-11-20T10:00:00',
                             review_closed=dict(by='Josh', at='2026-11-20T11:00:00', feedback_read_at='2026-11-20T10:00:00'))]
        self.assertTrue(self.a.eligibility(self.pid)['eligible'])
        self.reviews[0]['feedback_read_at'] = '2026-11-20T12:00:00'   # comments imported after he closed it
        self.assertFalse(self.a.eligibility(self.pid)['eligible'])
        self.reviews[0]['review_closed'] = dict(by='Tariq', at='2026-11-20T13:00:00', feedback_read_at='2026-11-20T12:00:00')
        self.assertFalse(self.a.eligibility(self.pid)['eligible'])


class Prproj(unittest.TestCase):
    def test_paths_from_gzip_xml_skip_caches(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / 'x.prproj'
            p.write_bytes(gzip.compress(b'<P><ActualMediaFilePath>E:\\a &amp; b.mp4</ActualMediaFilePath><PeakFilePath>C:\\c.pek</PeakFilePath>'
                                        b'<FilePath>relative.mp4</FilePath></P>'))
            self.assertEqual(A.prproj_paths(p), ['E:\\a & b.mp4'])


if __name__ == '__main__':
    unittest.main()
