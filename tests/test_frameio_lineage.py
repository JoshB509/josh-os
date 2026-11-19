"""Synthetic clean-cut lineage regressions. Fakes and temporary DBs only; never Frame.io/Premiere."""
import copy
import unittest
from unittest.mock import Mock
from studio import frameio_review as F
from dajoshi import edit_plan as ep
import test_frameio_review as existing

CID = 'clean-c8b13e8d416532a6'
OTHER = 'clean-1111111111111111'

class CleanLineage(unittest.TestCase):
    fake_run = existing.Flow.fake_run
    tearDown = existing.Flow.tearDown

    def setUp(self):
        existing.Flow.setUp(self)
        self.c['id'] = CID
        self.c['payload']['plan']['map_digest'] = ep.digest(self.c['payload']['plan']['map'])
        self.vis = dict(id='vis1', project_id='pid', kind='edit_visuals', parent=CID,
                        payload=dict(map_digest=self.c['payload']['plan']['map_digest']))
        self.current = self.c
        self.clean_calls = []
        self.get_calls = []
        def clean(pid):
            self.clean_calls.append(pid)
            return self.current if pid == 'pid' else None
        def get(pid, aid):
            self.get_calls.append((pid, aid))
            if pid != 'pid' or aid not in ('vis1','fin1'): raise KeyError(aid)
            return self.vis if aid == 'vis1' else self.fin
        self.r.flow.clean_cut = clean
        self.r.flow.get = get
        self.r.flow.approved = Mock(return_value=True)

    def stage(self):
        return self.r.stage('pid', self.ok)

    def review(self):
        b = self.stage()
        b.update(state='ready', frameio=dict(file_id='file-9'), feedback=[dict(id='comment',at='0:01',source_ticks_start=101*F.TICKS)])
        self.r._save(b)
        return b

    def test_latest_final_exact_synthetic_cut_never_db_gets_clean_id(self):
        fin, vis, c = self.r.latest_final('pid')
        self.assertEqual((fin['id'],vis['id'],c['id']),('fin1','vis1',CID))
        self.assertEqual(self.clean_calls,['pid'])
        self.assertNotIn(('pid',CID),self.get_calls)
        self.r.flow.approved.assert_called_once_with(self.vis)

    def test_stage_persists_exact_identity_and_digest(self):
        b = self.stage()
        self.assertEqual(b['source']['cut_id'],CID)
        self.assertEqual(b['source']['map_digest'],self.vis['payload']['map_digest'])

    def test_missing_current_cut_fails_closed(self):
        self.current = None
        with self.assertRaisesRegex(ValueError,'stale'): self.stage()
        self.assertEqual(self.r.all('pid'),[])

    def test_different_current_id_is_not_substituted(self):
        self.current = copy.deepcopy(self.c);self.current['id'] = OTHER
        with self.assertRaisesRegex(ValueError,'stale'): self.stage()

    def test_missing_clean_service_fails_closed(self):
        del self.r.flow.clean_cut
        with self.assertRaisesRegex(ValueError,'stale'): self.stage()

    def test_wrong_project_is_not_resolved(self):
        with self.assertRaises(ValueError):
            self.r._resolve_cut('other',CID,map_digest=self.vis['payload']['map_digest'])
        self.assertEqual(self.clean_calls,['other'])

    def test_explicit_other_project_result_is_rejected(self):
        self.c['project_id']='other'
        with self.assertRaises(ValueError): self.stage()

    def test_approval_verification_failure_propagates(self):
        self.r.flow.clean_cut=Mock(side_effect=ValueError('timeline no longer approved'))
        with self.assertRaisesRegex(ValueError,'timeline no longer approved'): self.stage()

    def test_unapproved_or_superseded_storyboard_is_rejected(self):
        self.r.flow.approved.return_value=False
        with self.assertRaisesRegex(ValueError,'storyboard'): self.stage()
        self.assertEqual(self.clean_calls,[])

    def test_missing_or_changed_stored_digest_is_rejected(self):
        for digest in (None,'wrong'):
            with self.subTest(digest=digest):
                self.vis['payload']['map_digest']=digest
                with self.assertRaises(ValueError): self.stage()

    def test_recomputed_map_digest_catches_tampering(self):
        self.c['payload']['plan']['map']['duration_ticks'] += 1
        with self.assertRaisesRegex(ValueError,'map'): self.stage()

    def test_nonverified_and_wrong_kind_clean_record_are_rejected(self):
        for key,value in [('state','running'),('kind','edit_final')]:
            with self.subTest(key=key):
                c=copy.deepcopy(self.c)
                if key=='state': c['payload']['state']=value
                else: c['kind']=value
                self.current=c
                with self.assertRaises(ValueError): self.stage()

    def test_malformed_clean_id_is_rejected_without_db_fallback(self):
        self.vis['parent']='clean-invalid'
        with self.assertRaisesRegex(ValueError,'invalid'): self.stage()
        self.assertEqual(self.get_calls,[('pid','vis1')])

    def test_upload_rechecks_changed_approval_before_external_write(self):
        b=self.stage();self.current=None
        with self.assertRaises(ValueError): self.r.upload(b['id'])
        self.assertEqual(self.calls,[]);self.assertEqual(self.puts,[])
        self.assertEqual(self.r.get(b['id'])['state'],'staged')

    def test_upload_rejects_newer_final_after_stage(self):
        b=self.stage();self.fin=dict(self.fin,id='new-final')
        with self.assertRaisesRegex(ValueError,'lineage changed'): self.r.upload(b['id'])
        self.assertEqual(self.calls,[])

    def test_fake_upload_normal_guarded_path_still_succeeds_once(self):
        b=self.stage()
        self.assertEqual(self.r.upload(b['id'])['state'],'ready')
        self.r.upload(b['id'])
        self.assertEqual(sum(bool(c['write']) for c in self.calls),1)

    def test_feedback_resolves_exact_synthetic_map(self):
        b=self.review();self.answers=[dict(file_found=True,file_id='file-9',comments=[dict(id='c',text='Fix',frame_start=31)])]
        result=self.r.import_feedback(b['id'])
        self.assertEqual(result['feedback'][0]['source_ticks_start'],101*F.TICKS)
        self.assertNotIn(('pid',CID),self.get_calls)

    def test_feedback_changed_digest_fails_before_external_read_or_save(self):
        b=self.review();b['source']['map_digest']='bad';self.r._save(b)
        with self.assertRaises(ValueError):self.r.import_feedback(b['id'])
        self.assertEqual(self.calls,[])
        self.assertEqual(self.r.get(b['id'])['feedback'],b['feedback'])

    def test_same_id_reprojection_validates_instead_of_shortcutting(self):
        b=self.review()
        self.assertFalse(self.r.for_cut(b['id'],CID)[0]['moved'])
        self.current=None
        with self.assertRaises(ValueError):self.r.for_cut(b['id'],CID)

    def test_other_exact_current_clean_target_can_reproject(self):
        b=self.review();self.current=copy.deepcopy(self.c);self.current['id']=OTHER
        rows=self.r.for_cut(b['id'],OTHER)
        self.assertTrue(rows[0]['moved']);self.assertEqual(rows[0]['now_at'],F.stamp(1))
        with self.assertRaises(ValueError):self.r.for_cut(b['id'],'clean-2222222222222222')

    def test_missing_flow_rejects_clean_feedback(self):
        b=self.review();self.r.flow=None
        with self.assertRaises(ValueError):self.r.import_feedback(b['id'])
        self.assertEqual(self.calls,[])

    def test_one_shot_opt_out_does_not_recover_any_live_uploading_rows(self):
        b=self.stage();b['state']='uploading';self.r._save(b)
        other=copy.deepcopy(b);other.update(id='other',key='other',project_id='other')
        with self.store.connect() as c:
            import json
            c.execute('INSERT INTO frameio_reviews VALUES(?,?,?,?)',(other['id'],other['project_id'],other['key'],json.dumps(other)))
        r=F.FrameioReview(self.store,self.tmp.name,recover_interrupted=False)
        self.assertEqual(r.get(b['id'])['state'],'uploading');self.assertEqual(r.get('other')['state'],'uploading')

    def test_default_startup_recovery_is_unchanged(self):
        b=self.stage();b['state']='uploading';self.r._save(b)
        r=F.FrameioReview(self.store,self.tmp.name)
        self.assertEqual(r.get(b['id'])['state'],'uncertain')
