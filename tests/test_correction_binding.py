from unittest.mock import patch
from studio import deck_source as ds
from tests.test_deck_source import DeckSource
from studio.transcript_worker import file_digest
import unittest

class CorrectionBinding(unittest.TestCase):
 make_outputs=DeckSource.make_outputs
 def setUp(self):
  DeckSource.setUp(self);self.make_outputs();self.source=ds.capture(self.flow,'p1')
  originals=[]
  for p in self.paths:
   old=p.with_name('original_'+p.name);old.write_bytes(p.read_bytes());originals.append(dict(path=str(old),sha256=file_digest(old)))
  self.audit=dict(kind='bounded-anchor-correction',provenance=self.source['provenance'],outputs=ds.files(self.review,'p1'),originals=originals,
                  original_authoring_source='teleprompter.txt',changes=[],shots=[{'animation':1,'press':1}])
 def record(self):
  with patch('studio.edit_animations.plan_shots',return_value=(self.audit['shots'],[])),patch('studio.edit_animations.deck_gate',return_value=[]):
   return ds.record_correction(self.flow,self.review,'p1',self.audit)
 def test_correction_is_explicit_and_does_not_approve(self):
  before=(self.tmp/'project/clean-edit.json').read_bytes();out=self.record()
  self.assertEqual(out['provenance_kind'],'verified-timing-correction');self.assertEqual(out['original_authoring_source'],'teleprompter.txt')
  self.assertEqual((self.tmp/'project/clean-edit.json').read_bytes(),before);ds.require(self.flow,self.review,'p1')
 def test_changed_corrected_file_rejected(self):
  self.paths[1].write_text('changed')
  with self.assertRaisesRegex(ValueError,'files changed'):self.record()
 def test_changed_original_rejected(self):
  from pathlib import Path
  Path(self.audit['originals'][1]['path']).write_text('changed')
  with self.assertRaisesRegex(ValueError,'Original deck changed'):self.record()
 def test_changed_visuals_rejected(self):
  self.paths[0].write_text('new visuals');self.audit['outputs']=ds.files(self.review,'p1')
  with self.assertRaisesRegex(ValueError,'visual content'):self.record()
 def test_wrong_cut_rejected(self):
  self.audit['provenance']={}
  with self.assertRaisesRegex(ValueError,'different approved cut'):self.record()
 def test_failing_current_timing_gate_rejected(self):
  with patch('studio.edit_animations.plan_shots',return_value=(self.audit['shots'],[])),patch('studio.edit_animations.deck_gate',return_value=['missing press']),self.assertRaisesRegex(ValueError,'timing did not pass'):
   ds.record_correction(self.flow,self.review,'p1',self.audit)
