"""Sam publishes exactly what CareerOS shows (19 Nov 2026).

Josh: "CareerOS, the details there are what reflects the latest version. When I hit publish it takes what is there and
publishes that, not some unseeable version somewhere." And an unlisted upload must not need a time.

These tests use the real Store on a disposable folder, real image assets and a small real export file. Publishing is a
fake that only records the packet it is asked to prepare, so nothing is uploaded and no YouTube or Claude call is made.
"""
import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from studio.agent import Agents
from studio.chat_release_actions import ChatReleaseActions
from studio.project_publishing import prepare_for_sam, preview, summary
from studio.release_ready import sha256, text_sha
from studio.store import Store
from studio.team import Team
from studio.youtube_release import ReleaseError, prepare as packet_prepare

CHANNEL = 'UC3F6JjRjqDNrK_QzvyGwppA'


def png(colour):
    b = io.BytesIO()
    Image.new('RGB', (32, 18), colour).save(b, 'PNG')
    return b.getvalue()


class FakePublishing:
    """Records the source packet. prepare() runs the real offline packet builder, then stops."""
    def __init__(self):
        self.sources = []
        self.auth = SimpleNamespace(connected=True)
        self.client = SimpleNamespace(channel=lambda: CHANNEL)

    def prepare(self, source):
        self.sources.append(source)
        return dict(id='job-1', state='needs_review', packet=packet_prepare(source))


class SamPublishing(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = Store(self.root / 'Data')
        self.thumbs = [self.store.import_image(f'cand_{i}.png', png(c))['asset'] for i, c in enumerate(((200, 0, 0), (0, 200, 0), (0, 0, 200)))]
        exports = self.root / 'video' / 'exports'
        exports.mkdir(parents=True)
        self.export = exports / 'final_v3.mp4'
        self.export.write_bytes(b'\x00\x00\x00\x18ftypmp42' + b'test video bytes' * 64)
        self.release = SimpleNamespace(store=self.store, exports=lambda pid: [self.export])
        self.pid = str(uuid.uuid4())
        t1, t2 = str(uuid.uuid4()), str(uuid.uuid4())
        self.store.create('Working name', pid=self.pid)
        self.desc = 'The description Josh approved in CareerOS.\n\nLinks below.'
        self.cta = 'Grab the free guide.'
        st = self.export.stat()
        self.store.save(self.pid, dict(
            titles=[dict(id=t1, text='An older idea'), dict(id=t2, text='The title CareerOS shows')], chosen_title=t2,
            thumbnails=[dict(asset=a, name=f'cand_{i}.png', title_id='') for i, a in enumerate(self.thumbs)], top_three=list(self.thumbs),
            locks=dict(title='2026-11-18T10:00:00', thumbnail='2026-11-18T10:00:00'),
            release=dict(description=dict(text=self.desc, by='sam', approved=dict(sha=text_sha(self.desc), by='Josh', at='2026-11-18T10:00:00')),
                         cta=dict(text=self.cta, approved=dict(sha=text_sha(self.cta), by='Josh', at='2026-11-18T10:00:00')),
                         links=[dict(source_type='pinned_comment', url='https://linklr.app/r/test')],
                         export_approval=dict(path=str(self.export.resolve()), size=st.st_size, mtime_ns=str(st.st_mtime_ns),
                                              sha256=sha256(self.export), at='2026-11-18T11:00:00'))), 1)
        self.pub = FakePublishing()
        self.no = dict(made_for_kids=False, contains_paid_promotion=False, contains_synthetic_media=False)

    def test_summary_is_what_the_video_page_shows(self):
        s = summary(self.store, self.pid)
        self.assertEqual(s['title'], 'The title CareerOS shows')
        self.assertEqual(s['description'], self.desc)
        self.assertTrue(s['description_approved'])
        self.assertEqual(s['pinned_comment'], self.cta + '\n\nhttps://linklr.app/r/test')
        self.assertEqual([t['slot'] for t in s['thumbnails']], ['Main', 'B', 'C'])
        self.assertEqual(s['approved_export']['file'], 'final_v3.mp4')
        self.assertEqual(s['missing_to_upload_unlisted'], [], 'nothing missing for unlisted, with no time set')
        self.assertEqual(s['also_missing_to_schedule'], ['Choose a publish date and time to schedule.'])

    def test_summary_matches_the_publish_panel_preview(self):
        s, (view, _) = summary(self.store, self.pid), preview(self.store, self.release, self.pub, self.pid)
        for k in ('title', 'description', 'pinned_comment', 'revision'):
            self.assertEqual(s[k], view[k], k)
        self.assertEqual(view['blockers']['unlisted'], [])

    def test_sams_packet_is_built_from_the_video_and_unlisted_needs_no_time(self):
        job = prepare_for_sam(self.store, self.release, self.pub, self.pid, 'unlisted', dict(self.no, title='A title Sam made up', description='His own copy'))
        src = self.pub.sources[-1]
        self.assertEqual(src['title'], 'The title CareerOS shows')
        self.assertEqual(src['description'], self.desc)
        self.assertEqual(src['pinned_comment'], self.cta + '\n\nhttps://linklr.app/r/test')
        self.assertEqual(Path(src['video']).resolve(), self.export.resolve())
        self.assertEqual(len(src['thumbnail_variants']), 3)
        self.assertEqual(src['project_revision'], self.store.get(self.pid)['revision'])
        self.assertIsNone(job['packet']['schedule'])
        self.assertEqual(job['packet']['initial_privacy'], 'unlisted')

    def test_disclosures_come_from_josh(self):
        with self.assertRaisesRegex(ReleaseError, 'Josh has to answer'):
            prepare_for_sam(self.store, self.release, self.pub, self.pid, 'unlisted', dict(made_for_kids=False))
        self.assertEqual(self.pub.sources, [])

    def test_scheduling_still_needs_a_time(self):
        with self.assertRaisesRegex(ReleaseError, 'date and time'):
            prepare_for_sam(self.store, self.release, self.pub, self.pid, 'schedule', self.no)
        self.assertEqual(self.pub.sources, [])

    def test_a_change_in_careeros_is_what_goes_out(self):
        p = self.store.get(self.pid)
        new = 'An edited description, saved in CareerOS.'
        self.store.save(self.pid, dict(release=dict(p['release'], description=dict(text=new, by='Josh'))), p['revision'])
        prepare_for_sam(self.store, self.release, self.pub, self.pid, 'unlisted', self.no)
        self.assertEqual(self.pub.sources[-1]['description'], new)
        self.assertEqual(summary(self.store, self.pid)['description'], new)


class SamChat(unittest.TestCase):
    """Sam's chat wiring: he is given the publishing block and can only prepare the video Josh named."""
    def setUp(self):
        base = SamPublishing('test_summary_is_what_the_video_page_shows')
        base.setUp()
        self.addCleanup(base.doCleanups)
        self.b = base
        self.agents = Agents(base.store, base.root, Team(base.store))
        self.agents.training = lambda owner: ('', [])
        self.agents.publishing = base.pub
        self.agents.chat_release_actions = ChatReleaseActions(base.release)
        self.calls, self.answer = [], dict(reply='Done.')

        def run(system, prompt, schema, *args, **kwargs):
            self.calls.append(dict(system=system, prompt=prompt, schema=schema))
            return self.answer['reply'], self.answer
        self.agents.run = run

    def ask(self, message):
        return self.agents.chat(dict(agent_id='sam', message=message), [])

    def text(self):
        c = self.calls[-1]
        return c['system'] + '\n' + c['prompt']

    def test_sam_sees_exactly_what_publish_will_use(self):
        self.ask(f'Publish {self.b.pid} unlisted please')
        seen = self.text()
        self.assertIn('publishing: what CareerOS shows for this video right now', seen)
        self.assertIn(json.dumps(self.b.desc, ensure_ascii=False)[1:-1], seen)
        self.assertIn('The title CareerOS shows', seen)
        self.assertIn('Unlisted needs no date or time', seen)
        props = self.calls[-1]['schema']['properties']['prepare_release']['properties']
        self.assertEqual(set(props), {'project_id', 'mode', 'made_for_kids', 'contains_paid_promotion', 'contains_synthetic_media'})

    def test_sams_prepare_uses_the_named_video_only(self):
        self.answer = dict(reply='Prepared.', prepare_release=dict(project_id=self.b.pid, mode='unlisted', **self.b.no))
        out = self.ask(f'Publish {self.b.pid} unlisted, not for kids, no paid promotion, no synthetic content')['reply']['text']
        self.assertIn('Release prepared for your review.', out)
        self.assertEqual(self.b.pub.sources[-1]['title'], 'The title CareerOS shows')
        other = str(uuid.uuid4())
        self.answer = dict(reply='Prepared.', prepare_release=dict(project_id=other, mode='unlisted', **self.b.no))
        out = self.ask(f'Publish {self.b.pid} unlisted')['reply']['text']
        self.assertIn('Name the exact video', out)
        self.assertEqual(len(self.b.pub.sources), 1, 'nothing prepared for a video Josh did not name')

    def test_old_free_form_packets_are_refused(self):
        self.answer = dict(reply='Prepared.', prepare_release=dict(channel_id=CHANNEL, video=str(self.b.export), title='Sam title',
                                                                 description='Sam copy', local_time='2026-12-01T10:00', timezone='Asia/Dubai'))
        with patch.object(self.b.pub, 'prepare', side_effect=AssertionError('must not be called with a free-form packet')):
            out = self.ask(f'Publish {self.b.pid}')['reply']['text']
        self.assertIn('Release preparation needs attention', out)


if __name__ == '__main__':
    unittest.main()
