import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from studio.broll import catalog, selection
from studio.chat_edit_actions import ChatEditActions, current_request_allows

class BrollTests(unittest.TestCase):
    def fixture(self, root):
        (root/'home.mp4').write_bytes(b'test video fixture')
        path=root/'clip-manifest.json'
        path.write_text(json.dumps({'clips':[{'file':'home.mp4','duration_seconds':3,'source_start_seconds':0}]}))
        return path
    def test_catalog_hash_and_missing(self):
        with tempfile.TemporaryDirectory() as t:
            p=self.fixture(Path(t));rows=catalog([p])
            self.assertEqual(len(rows),1);self.assertEqual(len(rows[0]['sha256']),64)
            (Path(t)/'home.mp4').write_bytes(b'changed')
            self.assertNotEqual(rows[0]['sha256'],catalog([p])[0]['sha256'])
        self.assertEqual(catalog([Path(t)/'missing.json']),[])
    def test_bad_paths_and_timing(self):
        with tempfile.TemporaryDirectory() as t:
            p=self.fixture(Path(t))
            for name,duration in [('../outside.mp4',1),('home.mp4',-1),('home.mp4',float('nan'))]:
                p.write_text(json.dumps({'clips':[{'file':name,'duration_seconds':duration,'source_start_seconds':0}]}))
                with self.assertRaises((ValueError,OSError)):catalog([p])
    def test_selection_rejects_unknown_duplicate_empty(self):
        rows=[{'id':'home'}]
        for ids in [[],['missing'],['home','home']]:
            with self.assertRaises(ValueError):selection(rows,ids)
        result=selection(rows,['home'])
        self.assertFalse(result['timeline_modified']);self.assertFalse(result['audio_modified'])
    def adapter(self):
        store=Mock();store.get.return_value={'name':'Test video'}
        clean=Mock();clean._file.return_value.is_file.return_value=False
        archive=Mock();archive.manifest.return_value=None;archive.settings.return_value={}
        flow=Mock();flow.latest.return_value=None;flow.progress.return_value=None
        rows=[{'id':'library/home','title':'home','sha256':'abc'}]
        a=ChatEditActions(store,clean,archive,flow=flow,broll_catalog=lambda:rows)
        return a,flow,clean,rows
    def test_dispatch_only_saves_unapproved_manifest(self):
        a,flow,clean,rows=self.adapter();rev=a.context('p1')['revision']
        a.dispatch({'action':'select_broll','project_id':'p1','revision':rev,'clip_ids':['library/home']},role='tariq',project_id='p1',current_message='Use home B-roll')
        args=flow._put.call_args.args
        self.assertEqual(args[1],'edit_broll_selection');self.assertEqual(args[2]['status'],'selected_not_inserted')
        clean.edit.assert_not_called();flow.finish.assert_not_called();flow._approve.assert_not_called()
    def test_stale_asset_and_wrong_role_rejected(self):
        a,flow,clean,rows=self.adapter();rev=a.context('p1')['revision']
        args={'action':'select_broll','project_id':'p1','revision':rev,'clip_ids':['library/home']}
        rows[0]['sha256']='changed'
        with self.assertRaisesRegex(ValueError,'Revision'):a.dispatch(args,role='tariq',project_id='p1',current_message='Use home B-roll')
        with self.assertRaisesRegex(ValueError,'Only Tariq'):a.dispatch(args,role='sam',project_id='p1',current_message='Use home B-roll')
        flow._put.assert_not_called()
    def test_catalog_failure_preserves_other_context(self):
        a,flow,clean,rows=self.adapter()
        a.broll_catalog=Mock(side_effect=ValueError('Missing clip'))
        c=a.context('p1')
        self.assertEqual(c['broll_catalog'],[]);self.assertEqual(c['broll_error'],'Missing clip')
        flow._put.assert_not_called()
    def test_request_exact_selection(self):
        ctx={'broll_catalog':[{'id':'library/home','title':'home'}]};args={'action':'select_broll','clip_ids':['library/home']}
        self.assertTrue(current_request_allows('Use home B-roll',args,ctx))
        for text in ['What B-roll exists?', 'Use other B-roll', 'Do not use home B-roll']:
            self.assertFalse(current_request_allows(text,args,ctx))

if __name__=='__main__':unittest.main()
