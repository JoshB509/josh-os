"""Optional Frame.io review (studio/frameio_review.py, frameio_guard.py). Fakes only: no Frame.io, no uploads."""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
from studio import frameio_guard as G  # noqa: E402
from studio import frameio_review as F  # noqa: E402
from studio.store import Store  # noqa: E402

T = F.TICKS
UPLOAD_SCHEMA = {'type': 'object', 'required': ['file_path', 'parent_id'],
                 'properties': {'file_path': {'type': 'string'}, 'parent_id': {'type': 'string'}, 'name': {'type': 'string'},
                                'share': {'type': 'boolean'}}}
BINDING = {'roles': {'path': 'file_path', 'folder_id': 'parent_id'}, 'checked_by': 'test'}


def signed(name, n):
    """A presigned-looking S3 part URL. Fake: nothing is ever sent to it."""
    return f'https://frameio-uploads-test.s3-accelerate.amazonaws.com/{name}/part{n}?X-Amz-Algorithm=AWS4&X-Amz-Signature=sig{n}'


def cut(pieces):
    return dict(id='cut1', kind='edit_cut', parent='prop', payload=dict(state='verified', plan=dict(
        sequence_name='CLEAN_CUT_r08', map_digest='m1', revision=8,
        map=dict(duration_ticks=sum(b - a for a, b, _ in pieces), pieces=[
            dict(kind='source', out_start=a, out_end=b, camera_in=c, camera_out=c + (b - a)) for a, b, c in pieces]))))


class Guard(unittest.TestCase):
    POLICY = dict(reads=[F.P + 'read_comments'], write=dict(tool=F.UPLOAD, input={'file_path': 'E:/v/FINAL.mp4', 'parent_id': 'folder-1'}))

    def test_exact_fields_only(self):
        ok = dict(tool_name=F.UPLOAD, tool_input={'file_path': 'E:/v/FINAL.mp4', 'parent_id': 'folder-1'})
        self.assertTrue(G.decision(ok, self.POLICY)[0])
        bad = [
            # approved strings in the wrong fields
            {'file_path': 'E:/other.mp4', 'parent_id': 'folder-2', 'name': 'E:/v/FINAL.mp4 folder-1'},
            {'file_path': 'folder-1', 'parent_id': 'E:/v/FINAL.mp4'},
            # extra share / publish parameter
            {'file_path': 'E:/v/FINAL.mp4', 'parent_id': 'folder-1', 'share': True},
            # mismatched target
            {'file_path': 'E:/v/FINAL.mp4', 'parent_id': 'folder-9'},
            # missing field
            {'file_path': 'E:/v/FINAL.mp4'},
        ]
        for given in bad:
            self.assertFalse(G.decision(dict(tool_name=F.UPLOAD, tool_input=given), self.POLICY)[0], given)
        for name in (F.P + 'delete_resources', F.P + 'manage_shares', F.P + 'manage_reviewers', F.P + 'write_comments', F.VERSIONS):
            self.assertFalse(G.decision(dict(tool_name=name, tool_input=ok['tool_input']), self.POLICY)[0], name)
        self.assertTrue(G.decision(dict(tool_name=F.P + 'read_comments', tool_input={'file_id': 'x'}), self.POLICY)[0])
        self.assertFalse(G.decision(ok, dict(reads=[], write=dict(tool=F.UPLOAD)))[0])   # no exact input, no write

    def test_one_use_reservation_survives_a_race(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        marker = Path(tmp.name) / 'p.used'
        wins = []
        threads = [threading.Thread(target=lambda: wins.append(G.reserve(marker))) for _ in range(20)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(wins.count(True), 1)

    def test_writes_fail_closed_until_the_schema_is_inspected_and_bound(self):
        v = dict(path='E:/v/FINAL.mp4', folder_id='folder-1')
        for doc in ({}, {'tools': {'upload_media': UPLOAD_SCHEMA}}, {'tools': {'upload_media': UPLOAD_SCHEMA}, 'bindings': {'upload_media': {'roles': BINDING['roles']}}},
                    {'tools': {'upload_media': UPLOAD_SCHEMA}, 'bindings': {'upload_media': dict(BINDING, roles={'path': 'path', 'folder_id': 'parent_id'})}}):
            with self.assertRaises(F.SchemaNotVerified, msg=doc):
                F.build_input(doc, F.UPLOAD, v)
        doc = {'tools': {'upload_media': UPLOAD_SCHEMA}, 'bindings': {'upload_media': BINDING}}
        self.assertEqual(F.build_input(doc, F.UPLOAD, v), {'file_path': 'E:/v/FINAL.mp4', 'parent_id': 'folder-1'})
        need_more = {'tools': {'upload_media': dict(UPLOAD_SCHEMA, required=['file_path', 'parent_id', 'workspace'])}, 'bindings': {'upload_media': BINDING}}
        with self.assertRaises(F.SchemaNotVerified):
            F.build_input(need_more, F.UPLOAD, v)


class LiveBinding(unittest.TestCase):
    LIVE = ROOT / 'Data' / 'frameio' / 'schema.json'

    @unittest.skipUnless(LIVE.is_file(), 'no live schema captured')
    def test_the_live_upload_schema_binds_to_exactly_six_fields(self):
        doc = json.loads(self.LIVE.read_text(encoding='utf-8'))
        inp = F.build_input(doc, F.UPLOAD, dict(path='E:/v/FINAL.mp4', folder_id='fold', account_id='acct', size=123))
        self.assertEqual(inp, {'account_id': 'acct', 'folder_id': 'fold', 'local_path': 'E:/v/FINAL.mp4', 'file_size': 123, 'action': 'file',
                               'user_intent': 'Upload the approved export to the private review folder'})
        policy = dict(reads=[], write=dict(tool=F.UPLOAD, input=inp))
        self.assertTrue(G.decision(dict(tool_name=F.UPLOAD, tool_input=dict(inp)), policy)[0])
        for extra in ({'source_url': 'https://x'}, {'files': []}, {'name': 'other.mp4'}, {'share': True}):
            self.assertFalse(G.decision(dict(tool_name=F.UPLOAD, tool_input=dict(inp, **extra)), policy)[0], extra)
        # manage_versions: the nested create_version_stack request, bound exactly.
        mv = F.build_input(doc, F.VERSIONS, dict(account_id='acct', folder_id='fold', file_ids=['old', 'new']))
        self.assertEqual(mv, {'request': {'account_id': 'acct', 'folder_id': 'fold', 'file_ids': ['old', 'new'], 'operation': 'create_version_stack'},
                              'user_intent': 'Stack the revised export on the reviewed version'})
        policy = dict(reads=[], write=dict(tool=F.VERSIONS, input=mv))
        self.assertTrue(G.decision(dict(tool_name=F.VERSIONS, tool_input=json.loads(json.dumps(mv))), policy)[0])
        bad = [
            dict(mv, request=dict(mv['request'], file_ids=['old', 'other'])),                       # wrong file
            dict(mv, request=dict(mv['request'], file_ids=['new', 'old'])),                         # wrong order
            dict(mv, request=dict(mv['request'], folder_id='elsewhere')),
            dict(mv, request=dict(mv['request'], operation='reorder_version_stack')),
            dict(mv, request=dict(mv['request'], extra='x')),
            dict(mv, share=True),
            {'request': mv['request']},                                                                # missing field
        ]
        for given in bad:
            self.assertFalse(G.decision(dict(tool_name=F.VERSIONS, tool_input=given), policy)[0], given)
        for n in (1, 11):
            with self.assertRaises(F.SchemaNotVerified):
                F.build_input(doc, F.VERSIONS, dict(account_id='a', folder_id='f', file_ids=[str(i) for i in range(n)]))


class Status(unittest.TestCase):
    def test_only_the_exact_positive_status_counts(self):
        self.assertEqual(F.parse_status('Status: ✔ Connected')['state'], 'connected')
        for line in ('Status: ✘ Disconnected', 'Status: not connected', 'Status: Connected? maybe', 'Status: failed to connect',
                     'Status: connected (stale)', ''):
            self.assertNotEqual(F.parse_status(line)['state'], 'connected', line)
        self.assertEqual(F.parse_status('Status: ! Needs authentication')['state'], 'needs_sign_in')

    def test_connected_still_needs_the_account_check(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        answers = [dict(ok=False)]
        r = F.FrameioReview(Store(Path(tmp.name) / 'Data'), tmp.name, None, runner=lambda *a, **k: answers.pop(0))
        self.assertEqual(r.account_check(dict(state='connected'))['state'], 'unverified')
        r.set_destination(dict(workspace_id='w', project_id='p', folder_id='f', project_name='n', account_id='acct-1'))
        answers.append(dict(ok=True, account_id='acct-2'))
        self.assertEqual(r.account_check(dict(state='connected'))['state'], 'wrong_account')
        answers.append(dict(ok=True, account_id='acct-1'))
        self.assertEqual(r.account_check(dict(state='connected'))['state'], 'connected')


class Time(unittest.TestCase):
    # Reviewed cut: 0-10 s from recording 100-110 s, then 10-20 s from recording 200-210 s.
    MAP = cut([(0, 10 * T, 100 * T), (10 * T, 20 * T, 200 * T)])['payload']['plan']['map']

    def test_frame_stamps_start_at_one(self):
        self.assertEqual(F.frame_to_seconds(1, 30), 0.0)
        self.assertAlmostEqual(F.frame_to_seconds(31, 30), 1.0)
        with self.assertRaises(ValueError):
            F.frame_to_seconds(0, 30)

    def test_ranges_map_through_the_reviewed_cut_and_survive_a_ripple_edit(self):
        s, e = F.frame_to_seconds(301, 30), F.frame_to_seconds(451, 30)   # 10.0 s to 15.0 s on the reviewed version
        src_s, src_e = F.to_source(s, self.MAP), F.to_source(e, self.MAP)
        self.assertEqual((src_s, src_e), (200 * T, 205 * T))
        # A new cut drops the first 4 seconds of the opening piece: everything after slides 4 s earlier.
        new = cut([(0, 6 * T, 104 * T), (6 * T, 16 * T, 200 * T)])['payload']['plan']['map']
        self.assertAlmostEqual(F.from_source(src_s, new), 6.0)
        self.assertAlmostEqual(F.from_source(src_e, new), 11.0)
        self.assertIsNone(F.from_source(F.to_source(1.0, self.MAP), new))   # that moment was cut out


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.store = Store(t / 'Data')
        self.export = t / 'FINAL_ASSEMBLY_r08_v02.mp4'
        self.export.write_bytes(b'export v02 ' * 50)
        self.c = cut([(0, 10 * T, 100 * T), (10 * T, 20 * T, 200 * T)])
        vis = dict(id='vis1', kind='edit_visuals', parent='cut1', payload={})
        self.fin = dict(id='fin1', kind='edit_final', parent='vis1', payload=dict(state='verified', output=str(self.export)))
        arts = {'cut1': self.c, 'vis1': vis, 'fin1': self.fin}
        flow = SimpleNamespace(_all=lambda pid, kind: [self.fin] if kind == 'edit_final' else [], get=lambda pid, aid: arts[aid])
        self.auth_state = 'connected'
        self.calls, self.answers = [], []
        self.r = F.FrameioReview(self.store, t, flow, auth=lambda: dict(state=self.auth_state, detail=f'Frame.io is {self.auth_state}.'),
                                 runner=self.fake_run, probe=lambda f: dict(duration=20.0, fps='30', frames=600, width=3840, height=2160))
        (self.r.dir / 'schema.json').write_text(json.dumps({'tools': {'upload_media': UPLOAD_SCHEMA}, 'bindings': {'upload_media': BINDING}}))
        self.r.set_destination(dict(workspace_id='w1', project_id='p1', folder_id='folder-1', project_name='CareerOS review', account_id='acct-1'))
        self.ok = dict(by='josh', said='Send it to Frame.io')
        self.puts = []
        self.r.put_fn = lambda url, data, headers: self.puts.append((url, len(data), headers)) or 200

    def tearDown(self):
        self.tmp.cleanup()

    def fake_run(self, prompt, schema, write=None, show=(), reads=None):
        self.calls.append(dict(prompt=prompt, write=write))
        size = self.export.stat().st_size
        if self.answers:
            a = self.answers.pop(0)
        elif schema is F.STATUS_SCHEMA:
            a = dict(ok=True, upload_complete=True, playable=True, status='ready', file_size=size, duration=20.0)
        else:
            a = dict(ok=True, file_id='file-9', status='ready')
        if isinstance(a, Exception):
            raise a
        if write and write['tool'] == F.UPLOAD and a.get('file_id') and '_tool_results' not in a and not a.get('no_raw'):
            # What Frame.io itself returns: the file it made and a one-part presigned plan for the exact size.
            a = dict(a, _tool_results={F.UPLOAD: [dict(is_error=False, text=json.dumps(
                {'file_id': a['file_id'], 'upload_urls': [{'url': signed(a['file_id'], 1), 'size': size}]}))]})
        return a

    def test_stage_fingerprints_and_is_idempotent(self):
        a = self.r.stage('pid', self.ok)
        self.assertEqual(a['export']['sha256'], F.sha256(self.export))
        self.assertEqual((a['export']['fps'], a['export']['frames'], a['source']['cut_id'], a['source']['sequence_name']), (30.0, 600, 'cut1', 'CLEAN_CUT_r08'))
        self.assertEqual(self.r.stage('pid', self.ok)['id'], a['id'])
        with self.assertRaises(ValueError):
            self.r.stage('pid', dict(by='josh', said=''))

    def test_upload_once_with_the_exact_input(self):
        a = self.r.stage('pid', self.ok)
        b = self.r.upload(a['id'])
        self.assertEqual((b['state'], b['frameio']['file_id']), ('ready', 'file-9'))
        self.assertEqual(self.calls[0]['write'], dict(tool=F.UPLOAD, input={'file_path': str(self.export), 'parent_id': 'folder-1'}))
        self.r.upload(a['id'])
        self.assertEqual(sum(1 for c in self.calls if c['write']), 1)   # one upload_media; the rest were status reads
        self.assertEqual([(n, h) for _, n, h in self.puts], [(self.export.stat().st_size, {})])

    def test_auth_interruption_is_never_retried_blind(self):
        a = self.r.stage('pid', self.ok)
        self.answers = [RuntimeError('OAuth token expired')]
        self.assertEqual(self.r.upload(a['id'])['state'], 'uncertain')
        with self.assertRaises(ValueError):
            self.r.upload(a['id'])
        self.answers = [self.listing([dict(file_id='file-9', name=self.export.name, size=self.export.stat().st_size, status='processing')])]
        b = self.r.check_destination(a['id'])
        self.assertEqual((b['state'], b['frameio']['file_id']), ('processing', 'file-9'))
        self.assertEqual(sum(1 for c in self.calls if c['write']), 1)

    def test_checked_absent_allows_a_new_send(self):
        a = self.r.stage('pid', self.ok)
        self.answers = [dict(ok=False, error='network')]
        self.r.upload(a['id'])
        self.answers = [self.listing([])]
        self.assertEqual(self.r.check_destination(a['id'])['state'], 'checked_absent')
        self.answers = [dict(ok=True, file_id='file-10', status='ready')]
        self.assertEqual(self.r.upload(a['id'])['frameio']['file_id'], 'file-10')

    def listing(self, candidates, **kw):
        return dict(dict(complete=True, error=None, account_id='acct-1', project_id='p1', folder_id='folder-1', candidates=candidates), **kw)

    def unclear(self):
        a = self.r.stage('pid', self.ok)
        self.answers = [RuntimeError('timeout')]
        self.r.upload(a['id'])
        return a

    def test_an_inconclusive_check_never_makes_a_resend_safe(self):
        size = self.export.stat().st_size
        cases = [
            self.listing([], complete=False),                                    # paginated or cut short
            self.listing([], error='rate limited'),
            self.listing([], folder_id='folder-2'),                              # wrong folder
            self.listing([], project_id='p9'),                                   # wrong project
            self.listing([dict(file_id='f1', name=self.export.name, size=None)]),  # size unknown is not a match
            self.listing([dict(file_id='f1', name=self.export.name, size=size), dict(file_id='f2', name=self.export.name, size=size)]),
            self.listing([dict(file_id='f1', name=self.export.name, size=size + 1)]),
            RuntimeError('network'),
        ]
        for answer in cases:
            a = self.unclear()
            self.answers = [answer]
            b = self.r.check_destination(a['id'])
            self.assertEqual(b['state'], 'uncertain', answer)
            self.assertFalse((b.get('frameio') or {}).get('file_id'), answer)
            with self.assertRaises(ValueError):
                self.r.upload(a['id'])
            with self.r.store.connect() as c:
                c.execute('DELETE FROM frameio_reviews')

    def test_a_check_on_another_account_is_rejected(self):
        self.r.set_destination(dict(workspace_id='w1', project_id='p1', folder_id='folder-1', project_name='CareerOS review', account_id='acct-1'))
        a = self.unclear()
        self.answers = [self.listing([], account_id='acct-2')]
        self.assertEqual(self.r.check_destination(a['id'])['state'], 'uncertain')

    def test_a_destination_needs_the_account(self):
        with self.assertRaises(ValueError):
            self.r.set_destination(dict(workspace_id='w1', project_id='p1', folder_id='folder-1', project_name='CareerOS review'))

    def test_not_signed_in_or_no_destination_stages_only(self):
        self.auth_state = 'needs_sign_in'
        a = self.r.stage('pid', self.ok)
        b = self.r.upload(a['id'])
        self.assertEqual(b['state'], 'staged')
        self.assertIn('needs_sign_in', b['reason'])
        self.assertEqual(self.calls, [])

    def test_source_version_mismatch(self):
        with self.assertRaises(ValueError):
            self.r.stage('pid', self.ok, path=str(Path(self.tmp.name) / 'FINAL_ASSEMBLY_r08_v01.mp4'))
        a = self.r.stage('pid', self.ok)
        self.export.write_bytes(b're-rendered ' * 50)
        b = self.r.upload(a['id'])
        self.assertEqual(b['state'], 'blocked')
        self.assertEqual(self.calls, [])

    def test_feedback_is_tied_to_the_exact_version(self):
        a = self.r.stage('pid', self.ok)
        self.r.upload(a['id'])
        self.answers = [dict(file_found=True, file_id='file-9', comments=[
            dict(id='c1', text='Hold this card longer', frame_start=301, frame_end=451),
            dict(id='c2', text='Typo', frame_start=601), dict(id='c3', text='General note')])]
        b = self.r.import_feedback(a['id'])
        c1, c2, c3 = b['feedback']
        self.assertEqual((c1['at'], c1['source_ticks_start'], c1['file_id'], c1['export_sha256']), ('0:10.00 to 0:15.00', 200 * T, 'file-9', a['export']['sha256']))
        self.assertIn('outside this version', c2['problems'][0])
        self.assertEqual(c3['at'], 'no time')
        self.answers = [dict(file_found=True, file_id='other-version', comments=[])]
        with self.assertRaises(ValueError):
            self.r.import_feedback(a['id'])
        self.assertIn('Hold this card longer', self.r.context('pid'))
        self.assertIn('imported comments only', self.r.context('pid'))

    def test_real_comment_timecodes_come_from_frameio_not_the_model(self):
        """Frame.io V4 read_comments for the 4 Signs export (12 Nov 2026, 14 comments): "timestamp" is HH:MM:SS:FF."""
        raw = json.loads((ROOT / 'tests' / 'fixtures' / 'frameio_read_comments_redacted.json').read_text(encoding='utf-8'))
        a = self.r.stage('pid', self.ok)
        self.r.upload(a['id'])
        b = self.r.get(a['id'])
        b['frameio']['file_id'] = 'ba208e24-10b1-4635-86b1-b2ccb4561ff0'
        b['export'].update(fps=30.0, duration=899.7)
        self.r._save(b)
        self.answers = [dict(file_found=True, file_id='wrong', comments=[dict(id='x', text='model retyped', frame_start=5)], _tool_results=raw)]
        b = self.r.import_feedback(a['id'])
        self.assertEqual(len(b['feedback']), 14)
        first = next(c for c in b['feedback'] if c['id'] == '0d4c2a8a-0743-401c-9ed9-a3d7ab228b5a')
        self.assertEqual((first['timecode'], first['seconds_start'], first['times_from']), ('00:01:20:07', 80.233333, 'frameio'))
        self.assertNotIn('model retyped', [c['text'] for c in b['feedback']])
        self.assertEqual(F.timecode_seconds('00:06:42:03', 30), 402.1)
        for bad in ('00:01:10:30', '1:2:3', '00:61:00:00', None):
            self.assertIsNone(F.timecode_seconds(bad, 30))

    def test_a_review_closes_only_by_josh_after_all_comments_are_done(self):
        a = self.r.stage('pid', self.ok)
        self.r.upload(a['id'])
        with self.assertRaises(ValueError):
            self.r.close_review(a['id'], dict(by='Josh', trusted=True))       # nothing imported yet
        self.answers = [dict(file_found=True, file_id='file-9', comments=[dict(id='c1', text='Typo', frame_start=31, completed=False)])]
        self.r.import_feedback(a['id'])
        with self.assertRaises(ValueError):
            self.r.close_review(a['id'], dict(by='Josh', trusted=True))       # a comment is still open
        self.answers = [dict(file_found=True, file_id='file-9', comments=[dict(id='c1', text='Typo', frame_start=31, completed=True)])]
        self.r.import_feedback(a['id'])
        with self.assertRaises(ValueError):
            self.r.close_review(a['id'], dict(by='Josh', said='done'))        # not from Josh OS itself
        b = self.r.close_review(a['id'], dict(by='Josh', said='done', trusted=True))
        self.assertEqual(b['review_closed']['feedback_read_at'], b['feedback_read_at'])
        self.answers = [dict(file_found=True, file_id='file-9', comments=[dict(id='c2', text='New note', frame_start=61)])]
        b = self.r.import_feedback(a['id'])
        self.assertIn('reopened_at', b['review_closed'])

    def test_stacking_a_revision_needs_an_inspected_binding_and_two_real_versions(self):
        a = self.r.stage('pid', self.ok)
        self.r.upload(a['id'])
        with self.assertRaises(ValueError):
            self.r.stack_revision(a['id'], a['id'], self.ok)   # same export is not a revision
        self.export.write_bytes(b'revised v03 ' * 50)
        self.fin['payload']['output'] = str(self.export)
        b = self.r.stage('pid', self.ok)
        self.answers = [dict(ok=True, file_id='file-10', status='ready')]
        self.r.upload(b['id'])
        with self.assertRaises(F.SchemaNotVerified):
            self.r.stack_revision(a['id'], b['id'], self.ok)
        doc = json.loads((self.r.dir / 'schema.json').read_text())
        variant = {'additionalProperties': True, 'required': ['operation', 'account_id', 'folder_id', 'file_ids'], 'properties': {
            'operation': {'const': 'create_version_stack'}, 'account_id': {}, 'folder_id': {}, 'file_ids': {'minItems': 2, 'maxItems': 10}}}
        doc['tools']['manage_versions'] = {'required': ['request'], 'properties': {'request': {'oneOf': [variant]}, 'user_intent': {}}}
        doc['bindings']['manage_versions'] = {'nested': {'field': 'request', 'variant': 'create_version_stack', 'roles': {
            'account_id': 'account_id', 'folder_id': 'folder_id', 'file_ids': 'file_ids'}, 'fixed': {'operation': 'create_version_stack'}},
            'fixed': {'user_intent': 'stack'}, 'checked_by': 'test'}
        (self.r.dir / 'schema.json').write_text(json.dumps(doc))
        with self.assertRaises(F.SchemaNotVerified):   # the operation must forbid extra fields
            self.r.stack_revision(a['id'], b['id'], self.ok)
        variant['additionalProperties'] = False
        (self.r.dir / 'schema.json').write_text(json.dumps(doc))
        self.answers = [dict(ok=True, version_stack_id='stack-1')]
        out = self.r.stack_revision(a['id'], b['id'], self.ok)
        self.assertEqual(self.calls[-1]['write'], dict(tool=F.VERSIONS, input={'request': {
            'account_id': 'acct-1', 'folder_id': 'folder-1', 'file_ids': ['file-9', 'file-10'], 'operation': 'create_version_stack'},
            'user_intent': 'stack'}))
        self.assertEqual(out['revisions'][0]['version_stack_id'], 'stack-1')
        n = len(self.calls)
        self.r.stack_revision(a['id'], b['id'], self.ok)
        self.assertEqual(len(self.calls), n)   # never stacked twice

    def test_part_plan_is_read_exactly_or_not_at_all(self):
        plan = F.part_plan({'file_id': 'x', 'upload': {'parts': [{'url': signed('x', 1), 'size': 4}, {'url': signed('x', 2), 'size': 3}]}})
        self.assertEqual([(x['offset'], x['size']) for x in plan], [(0, 4), (4, 3)])
        self.assertEqual(F.part_plan({'parts': [{'url': 'https://u/1'}]}), [])                       # no size: not guessed
        self.assertEqual(F.part_plan({'a': [{'url': 'u'}], 'b': [{'url': 'v'}]}), [])               # two lists: ambiguous
        self.assertEqual(F.part_plan({'parts': [{'url': 'u', 'offset': 5, 'size': 3}]}), [])        # gap at the start

    def test_parts_resume_at_the_same_urls_and_upload_media_is_never_called_twice(self):
        a = self.r.stage('pid', self.ok)
        size = self.export.stat().st_size
        half = size // 2
        resp = json.dumps({'file_id': 'file-9', 'parts': [{'url': signed('file-9', 1), 'size': half}, {'url': signed('file-9', 2), 'size': size - half}]})
        self.answers = [dict(ok=True, file_id='file-9', response_json='the model retyped this wrongly',
                             _tool_results={F.UPLOAD: [dict(text=resp, is_error=False)]})]
        sent, fail = [], {'n': 0}

        def put(url, data, headers):
            if 'part2' in url and fail['n'] < 3:
                fail['n'] += 1
                raise OSError('network')
            sent.append((url, len(data)))
            return 200
        import studio.frameio_review as mod
        real = mod.FrameioReview.put_parts
        mod.FrameioReview.put_parts = lambda self, rid, putter=None: real(self, rid, putter=put)
        sleep = mod._time.sleep
        mod._time.sleep = lambda s: None
        try:
            b = self.r.upload(a['id'])
            self.assertEqual((b['state'], sent), ('putting', [(signed('file-9', 1), half)]))
            self.answers = [dict(ok=True, upload_complete=True, playable=True, status='ready', file_size=size, view_url='https://next.frame.io/x')]
            b = self.r.put_parts(a['id'])
            self.assertEqual(b['state'], 'ready')
            self.assertEqual(sent, [(signed('file-9', 1), half), (signed('file-9', 2), size - half)])
        finally:
            mod.FrameioReview.put_parts = real
            mod._time.sleep = sleep
        self.assertEqual(sum(1 for c in self.calls if c['write']), 1)
        self.assertFalse((self.r.dir / 'plans' / f"{a['id']}.json").exists())   # presigned URLs removed once done

    def test_a_plan_for_the_wrong_size_sends_nothing(self):
        a = self.r.stage('pid', self.ok)
        self.answers = [dict(ok=True, file_id='file-9', _tool_results={F.UPLOAD: [dict(text=json.dumps({'file_id': 'file-9', 'parts': [{'url': signed('file-9', 1), 'size': 5}]}), is_error=False)]})]
        b = self.r.upload(a['id'])
        self.assertEqual(b['state'], 'uncertain')
        self.assertIn("couldn't read its upload plan safely", b['reason'])
        self.assertEqual(self.puts, [])

    def test_review_project_is_created_once_and_never_duplicated(self):
        self.r.auth = lambda: dict(state='connected', detail='ok', account_id='acct-1')
        (self.r.dir / 'schema.json').write_text(json.dumps({'tools': {'upload_media': UPLOAD_SCHEMA, 'change_structure': {'properties': {}}},
                                                            'bindings': {'upload_media': BINDING}}))
        listing = lambda m: dict(complete=True, matches=m)
        self.answers = [listing([]), dict(ok=True, project_id='proj-1', root_folder_id='root-1'),
                        listing([dict(project_id='proj-1', name='CareerOS Reviews', root_folder_id='root-1', restricted=True)])]
        pr = self.r.create_review_project('acct-1', 'w1', dict(by='Josh', said='yes upload the video'))
        self.assertEqual((pr['project_id'], pr['root_folder_id'], pr['created_by_josh_os']), ('proj-1', 'root-1', True))
        w = [c['write'] for c in self.calls if c['write']]
        self.assertEqual(w, [dict(tool=F.STRUCTURE, input={'request': {'operation': 'create_project', 'account_id': 'acct-1', 'workspace_id': 'w1',
                                                                    'name': 'CareerOS Reviews', 'restricted': True},
                                                         'user_intent': 'Create the private CareerOS Reviews project Josh approved for video review'})])
        self.assertEqual(self.r.create_review_project('acct-1', 'w1', dict(by='Josh', said='again'))['project_id'], 'proj-1')
        self.assertEqual(len([c for c in self.calls if c['write']]), 1)

    def test_review_project_is_not_created_when_the_check_is_incomplete_or_on_another_account(self):
        self.r.auth = lambda: dict(state='connected', detail='ok', account_id='acct-1')
        self.answers = [dict(complete=False, matches=[])]
        with self.assertRaises(ValueError):
            self.r.create_review_project('acct-1', 'w1', dict(by='Josh', said='yes'))
        with self.assertRaises(ValueError):
            self.r.create_review_project('acct-2', 'w1', dict(by='Josh', said='yes'))
        self.assertFalse([c for c in self.calls if c['write']])

    def test_an_unrestricted_project_needs_josh_to_approve_it_as_the_destination(self):
        (self.r.dir / 'setup.json').write_text(json.dumps({'project': {'name': 'CareerOS Reviews', 'project_id': 'proj-u', 'root_folder_id': 'root-u',
                                                                       'restricted': False}}))
        dest = dict(account_id='acct-1', workspace_id='w1', project_id='proj-u', folder_id='root-u', project_name='CareerOS Reviews')
        with self.assertRaises(ValueError):
            self.r.set_destination(dest)
        saved = self.r.set_destination(dict(dest, unrestricted_approval=dict(by='Josh', said='Yes, use it unrestricted')))
        self.assertEqual((saved['restricted'], saved['unrestricted_approval']['said']), (False, 'Yes, use it unrestricted'))

    def test_a_plan_only_in_model_text_is_never_used(self):
        a = self.r.stage('pid', self.ok)
        self.answers = [dict(ok=True, file_id='file-9', response_json=json.dumps({'file_id': 'file-9', 'parts': [{'url': signed('file-9', 1), 'size': 4}]}), no_raw=True)]
        b = self.r.upload(a['id'])
        self.assertEqual(b['state'], 'uncertain')
        self.assertFalse((self.r.dir / 'plans' / f"{a['id']}.json").exists())

    def test_stream_parsing_keeps_exact_tool_results(self):
        lines = [json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't1', 'name': F.UPLOAD}]}}),
                 json.dumps({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': [{'type': 'text', 'text': '{"file_id":"f"}'}]}]}}),
                 json.dumps({'type': 'result', 'structured_output': {'ok': True}})]
        r = F.parse_stream('\n'.join(lines))
        self.assertEqual((r['ok'], r['_tool_results'][F.UPLOAD][0]['text']), (True, '{"file_id":"f"}'))
        with self.assertRaises(RuntimeError):
            F.parse_stream(json.dumps({'type': 'result', 'is_error': True, 'result': 'boom'}))

    def test_one_replacement_only_for_a_provably_empty_stub(self):
        a = self.r.stage('pid', self.ok)
        self.answers = [dict(ok=True, file_id='stub-1', _tool_results={F.UPLOAD: [dict(is_error=False, text='{"file_id": "stub-1"}')]})]   # file made, no plan
        self.assertEqual(self.r.upload(a['id'])['state'], 'uncertain')
        self.answers = [dict(complete=True, file_id='stub-1', status='created', upload_complete=False, has_proxy_or_stream=True)]
        self.assertEqual(self.r.replace_empty_upload(a['id'])['state'], 'uncertain')   # not provably empty: nothing sent
        self.answers = [dict(complete=True, file_id='stub-1', status='created', upload_complete=False, has_proxy_or_stream=False),
                        dict(ok=True, file_id='file-new', status='ready')]
        b = self.r.replace_empty_upload(a['id'])
        self.assertEqual((b['frameio']['file_id'], b['replacement']['empty_stub']['file_id'], b['replacement']['new_file_id']),
                         ('file-new', 'stub-1', 'file-new'))
        self.assertEqual(sum(1 for c in self.calls if c['write']), 2)   # the first create and one replacement
        b['state'] = 'uncertain'
        self.r._save(b)
        with self.assertRaises(ValueError):
            self.r.replace_empty_upload(a['id'])                     # never twice

    def test_the_raw_result_is_kept_privately_and_resumes_without_a_new_upload(self):
        a = self.r.stage('pid', self.ok)
        size = self.export.stat().st_size
        odd = json.dumps({'file_id': 'file-9', 'upload_script': 'curl ...', 'upload': {'parts': [{'url': signed('file-9', 1), 'size': size}]}})
        self.answers = [dict(ok=True, file_id='file-9', _tool_results={F.UPLOAD: [dict(text='{"file_id": "file-9", "note": "plan in a shape not known yet"}', is_error=False)]})]
        b = self.r.upload(a['id'])
        self.assertEqual((b['state'], b['frameio']['file_id']), ('uncertain', 'file-9'))
        self.assertEqual(self.r.raw_shape(a['id']), [dict(text=[{'file_id': 'file-9', 'note': 'plan in a shape not known yet'}], structured=None, is_error=False)])
        (self.r.dir / 'plans' / f"{a['id']}-raw.json").write_text(json.dumps([dict(text=odd, is_error=False)]))
        shape = self.r.raw_shape(a['id'])
        self.assertNotIn('https://', json.dumps(shape))
        import studio.frameio_review as mod
        real = mod.FrameioReview.put_parts
        mod.FrameioReview.put_parts = lambda self, rid, putter=None: real(self, rid, putter=lambda u, d, h: 200)
        try:
            self.answers = [dict(ok=True, upload_complete=True, playable=True, status='ready', file_size=size)]
            b = self.r.resume_from_raw(a['id'])
        finally:
            mod.FrameioReview.put_parts = real
        self.assertEqual(b['state'], 'ready')
        self.assertEqual(sum(1 for c in self.calls if c['write']), 1)
        self.assertFalse((self.r.dir / 'plans' / f"{a['id']}-raw.json").exists())

    def test_optional_stage_skip(self):
        self.assertEqual(self.r.all('pid'), [])
        self.assertEqual(self.r.context('pid'), '')
        self.assertEqual(self.r.view('pid')['reviews'], [])
        self.assertEqual(self.calls, [])



class PlanReading(unittest.TestCase):
    """The exact upload_media result is read as data. A returned script is never run, and nothing is guessed."""
    SIZE = 25

    def script(self, part=10, extra=''):
        urls = ' '.join(f'"{signed("f1", i)}"' for i in (1, 2, 3))
        nl = chr(10)
        return (f'#!/bin/bash{nl}FILE="$1"{nl}PART_SIZE={part}{nl}URLS=({urls}){nl}{extra}'
                'for i in "${!URLS[@]}"; do dd if="$FILE" bs=$PART_SIZE skip=$i count=1 | '
                'curl -sf -X PUT -H "Content-Type: video/mp4" -H "x-amz-acl: private" -H "Authorization: x" --data-binary @- "${URLS[$i]}"; done' + nl)

    def raw(self, obj, structured=None):
        blk = {'structuredContent': structured} if structured is not None else {}
        return [dict(is_error=False, text=json.dumps(obj) if obj is not None else 'Upload started.', block=blk)]

    def test_script_inside_response_json_gives_exact_offsets_and_safe_headers(self):
        inner = json.dumps({'file_id': 'f1', 'media_type': 'video/mp4', 'upload_script': self.script()})
        parts, how = F.plan_from_raw(self.raw({'status': 'ok', 'response_json': inner}), 'f1', self.SIZE)
        self.assertEqual(how, 'upload script read as text')
        self.assertEqual([(p['offset'], p['size']) for p in parts], [(0, 10), (10, 10), (20, 5)])
        self.assertEqual(parts[0]['headers'], {'Content-Type': 'video/mp4', 'x-amz-acl': 'private'})   # Authorization dropped
        self.assertEqual(parts[2]['url'], signed('f1', 3))

    def test_structured_content_alone_is_enough(self):
        sc = {'file_id': 'f1', 'upload_urls': [{'url': signed('f1', 1), 'size': 20}, {'url': signed('f1', 2), 'size': 5}]}
        parts, how = F.plan_from_raw(self.raw(None, structured=sc), 'f1', self.SIZE)
        self.assertEqual((how, [p['size'] for p in parts]), ('part list', [20, 5]))
        self.assertEqual(F.created_file_id(self.raw(None, structured=sc)), 'f1')

    def test_a_part_size_is_never_guessed(self):
        urls = ' '.join(f'"{signed("f1", i)}"' for i in (1, 2, 3))
        bare = {'file_id': 'f1', 'upload_script': f'for u in {urls}; do curl -X PUT --data-binary @- "$u"; done'}
        self.assertEqual(F.plan_from_raw(self.raw(bare), 'f1', self.SIZE)[0], [])

    def test_wrong_size_wrong_file_or_unsafe_urls_send_nothing(self):
        ok = {'file_id': 'f1', 'upload_script': self.script()}
        self.assertEqual(F.plan_from_raw(self.raw(ok), 'f1', 31)[0], [])          # three parts of 10 can't make 31
        self.assertEqual(F.plan_from_raw(self.raw(ok), 'other', self.SIZE)[0], [])  # a different file
        evil = {'file_id': 'f1', 'upload_urls': [{'url': 'https://evil.example.com/x?sig=1', 'size': 25}]}
        self.assertEqual(F.plan_from_raw(self.raw(evil), 'f1', self.SIZE)[0], [])
        plain = {'file_id': 'f1', 'upload_urls': [{'url': 'http://bucket.s3.amazonaws.com/x?sig=1', 'size': 25}]}
        self.assertEqual(F.plan_from_raw(self.raw(plain), 'f1', self.SIZE)[0], [])
        self.assertEqual(F.plan_from_raw(self.raw({'file_id': 'f1', 'upload_script': self.script(extra='CT=$TYPE' + chr(10)).replace('video/mp4', '$CT')}), 'f1', self.SIZE)[0], [])

    def test_two_file_ids_in_the_result_name_no_file(self):
        self.assertIsNone(F.created_file_id(self.raw({'file_id': 'a'}) + self.raw({'file_id': 'b'})))
        self.assertIsNone(F.created_file_id([dict(is_error=True, text='{"file_id": "a"}')]))

    def test_the_real_upload_media_result_shape(self):
        """Frame.io's actual result for the 4 Signs export (12 Nov 2026), with every URL replaced: result.parts with
        part_number, byte_start, byte_length and a short at.adobe.com url, plus shared upload_headers and a bash script."""
        raws = json.loads((ROOT / 'tests' / 'fixtures' / 'frameio_upload_media_result_redacted.json').read_text(encoding='utf-8'))
        fid = F.created_file_id(raws)
        self.assertEqual(fid, 'ba208e24-10b1-4635-86b1-b2ccb4561ff0')
        parts, how = F.plan_from_raw(raws, fid, 1223590242)
        self.assertEqual((how, len(parts)), ('part list', 35))
        self.assertEqual([(p['offset'], p['size']) for p in parts[:2]], [(0, 34959722), (34959722, 34959722)])
        self.assertEqual((parts[-1]['offset'], parts[-1]['size']), (1188630548, 34959694))
        self.assertEqual(parts[0]['headers'], {'x-amz-acl': 'private', 'Content-Type': 'video/mp4'})
        self.assertEqual(F.plan_from_raw(raws, fid, 1223590241)[0], [])          # one byte off: nothing
        self.assertEqual(F.plan_from_raw(raws, 'another-file', 1223590242)[0], [])
        bad = json.loads(json.dumps(raws).replace('https://at.adobe.com/TESTpart07', 'https://at.adobe.com.evil.io/x'))
        self.assertEqual(F.plan_from_raw(bad, fid, 1223590242)[0], [])

    def test_only_https_short_links_or_signed_storage_urls_are_accepted(self):
        self.assertTrue(F.safe_url('https://at.adobe.com/AbC123xyz'))
        self.assertTrue(F.safe_url(signed('f', 1)))
        for u in ('http://at.adobe.com/AbC123', 'https://at.adobe.com/AbC123?x=1', 'https://at.adobe.com/a/b', 'https://at.adobe.com:8443/AbC123',
                  'https://user@at.adobe.com/AbC123', 'https://bucket.s3.amazonaws.com/x', 'https://evil.com/x?X-Amz-Signature=1'):
            self.assertFalse(F.safe_url(u), u)
        self.assertEqual(F.curl_put('https://evil.com/x?sig=1', b'x', {}), 0)    # refused before anything runs

    def test_raw_results_are_kept_before_the_final_answer_is_judged(self):
        lines = [json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't1', 'name': F.UPLOAD}]}}),
                 json.dumps({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1',
                                                                      'content': [{'type': 'text', 'text': '{"file_id":"f1"}'}]}]}}),
                 json.dumps({'type': 'result', 'is_error': True, 'result': 'model gave up'})]
        raw, final = F.stream_results(chr(10).join(lines))
        self.assertTrue(final['is_error'])
        with tempfile.TemporaryDirectory() as t:
            F.keep_raw(Path(t) / 'plans' / 'r-raw.json', raw)
            kept = json.loads((Path(t) / 'plans' / 'r-raw.json').read_text())
        self.assertEqual(F.created_file_id(kept[F.UPLOAD]), 'f1')


if __name__ == '__main__':
    unittest.main()
