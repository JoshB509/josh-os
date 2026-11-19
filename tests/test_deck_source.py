import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from studio import deck_source as ds
from studio.clean_edit import CleanEditError
from studio.edit_flow import EditFlow
from studio.agent import Agents
from tests import test_clean_handoff as fixture


class DeckSource(unittest.TestCase):
    def setUp(self):
        fixture.Handoff.setUp(self)
        self.svc.approve('p1','Josh',True,'AUTOCUT_RAW_r01')
        self.notes='Show a pyramid; reveal the three cards one at a time.'
        self.root=self.tmp/'output';self.root.mkdir()
        self.paths=(self.root/'test_slides_v01.html', self.root/'test_shot-list_v01.md')
        self.flow=SimpleNamespace(folder=lambda pid:self.tmp/'project',clean_edit=self.svc,
            clean_gate=lambda pid:self.svc.approved(pid),anim_notes=lambda pid:dict(text=self.notes),
            animation_gaps=Mock(return_value=[]),_setup_new=Mock(),_setup_step=Mock(),_setup_finish=Mock(),_phase_fail=Mock())
        self.review=SimpleNamespace(project_folder=lambda pid:self.root,
            deck_files=lambda pid:self.paths if all(p.exists() for p in self.paths) else (None,None))

    def make_outputs(self):
        self.paths[0].write_text('<html>pyramid</html>');self.paths[1].write_text('one two three four five')

    def agent(self):
        a=object.__new__(Agents)
        a.edit_review=self.review;a.edit_flow=self.flow
        a.team=SimpleNamespace(all=lambda:dict(agents=[dict(id='tariq')],learnings=[]))
        a.training=Mock(return_value=(None,[]));a.focus=Mock(return_value='Project folder metadata')
        a.anim_notes_text=Mock(return_value=self.notes);a.skill_text=Mock(return_value='')
        a.desk_system_prompt=Mock(return_value='Test worker');a._deck_progress=Mock()
        a.apply_edit=Mock(return_value=[]);a.post=Mock()
        a.run=Mock(side_effect=lambda *args,**kw:(self.make_outputs() or 'Created review files',{}))
        return a

    def test_first_deck_receives_clean_words_notes_and_identity_without_any_prior_deck(self):
        a=self.agent();a._deck_job('tariq','p1','Make the deck','')
        a.run.assert_called_once();prompt=a.run.call_args.args[1]
        source=ds.capture(self.flow,'p1')
        self.assertIn(ds.brief(source),prompt);self.assertIn(self.notes,prompt)
        self.assertIn('directly in '+str(self.root),prompt)
        self.assertIn('shot list must cite the approved clean transcript digest',prompt)
        self.assertNotIn('three,',[w['text'] for w in source['words']])
        self.assertIn('three',[w['text'] for w in source['words']])
        self.assertNotIn('source_start',source['words'][0])
        self.flow.animation_gaps.assert_not_called()
        ds.require(self.flow,self.review,'p1')
        self.flow._phase_fail.assert_not_called()
        self.assertIsNotNone(self.svc._load('p1')['approved'])

    def test_stale_live_timeline_prevents_worker_start(self):
        self.pieces=fixture.AUTO[:1]+fixture.AUTO[2:]
        a=self.agent();a._deck_job('tariq','p1','Make the deck','')
        a.run.assert_not_called();self.flow._phase_fail.assert_called_once()
        self.assertFalse(any(p.exists() for p in self.paths))

    def test_missing_approval_prevents_worker_start(self):
        st=self.svc._load('p1');st['approved']=None;self.svc._save('p1',st)
        a=self.agent();a._deck_job('tariq','p1','Make the deck','')
        a.run.assert_not_called();self.flow._phase_fail.assert_called_once()

    def test_tampered_transcript_fails_before_authoring(self):
        p=Path(self.svc._load('p1')['clean_transcript']['file'])
        body=json.loads(p.read_text());body['words'][0]['text']='RAW UNAPPROVED';p.write_text(json.dumps(body))
        with self.assertRaises(CleanEditError):ds.capture(self.flow,'p1')

    def test_notes_change_during_job_retains_outputs_but_does_not_verify(self):
        a=self.agent()
        def run(*args,**kw):
            self.make_outputs();self.notes='New request during job';return 'Done',{}
        a.run.side_effect=run;a._deck_job('tariq','p1','Make the deck','')
        self.assertTrue(all(p.exists() for p in self.paths))
        self.flow._phase_fail.assert_called_once();a.apply_edit.assert_not_called()
        with self.assertRaisesRegex(CleanEditError,'no verified'):ds.require(self.flow,self.review,'p1')

    def test_modified_output_or_source_cannot_be_placed(self):
        self.make_outputs();source=ds.capture(self.flow,'p1');ds.record(self.flow,self.review,'p1',source)
        self.paths[0].write_text('<html>changed</html>')
        with self.assertRaisesRegex(CleanEditError,'Placement is blocked'):ds.require(self.flow,self.review,'p1')
        self.make_outputs();self.notes='different notes'
        with self.assertRaisesRegex(CleanEditError,'Placement is blocked'):ds.require(self.flow,self.review,'p1')

    def test_existing_unverified_deck_blocks_final_before_assembly_dispatch(self):
        self.make_outputs()
        f=object.__new__(EditFlow);f.folder=self.flow.folder;f.clean_gate=self.flow.clean_gate
        f.clean_edit=self.svc;f.anim_notes=self.flow.anim_notes;f.edit_review=self.review
        f.clean_cut=Mock(return_value=dict(id='cut',payload=dict(plan=dict(map_digest='map'))))
        f.get=Mock(return_value=dict(parent='cut',payload=dict(map_digest='map')));f.approved=Mock(return_value=True)
        f.bridge_call=Mock()
        with self.assertRaisesRegex(CleanEditError,'no verified'):f.final('p1','visuals')
        f.bridge_call.assert_not_called()

    def test_binding_does_not_approve_storyboard_or_change_clean_decisions(self):
        self.make_outputs();before=(self.tmp/'project/clean-edit.json').read_bytes()
        ds.record(self.flow,self.review,'p1',ds.capture(self.flow,'p1'))
        self.assertEqual((self.tmp/'project/clean-edit.json').read_bytes(),before)
        self.assertEqual(ds.require(self.flow,self.review,'p1')['state'],'source-bound')


if __name__=='__main__':unittest.main()
