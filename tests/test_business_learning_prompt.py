import json
import unittest
from types import SimpleNamespace
from test_business import Base, at
from studio.business import RULES

class LearningPrompt(unittest.TestCase):
    def test_aggregate_window_and_tracking_semantics_replace_obsolete_claims(self):
        text=' '.join(RULES)
        for old in ('reviews submitted on that UTC day','learning minutes have no read across all learners','CareerOS records no watch time'):
            self.assertNotIn(old,text)
        for required in ('get_learning_activity_summary','last completed Asia/Dubai calendar day','start-inclusive, end-exclusive',
                         'partial count is a lower bound','window_complete does not mean tracking is complete',
                         'visible-tab video playback wall-clock time','status is unavailable or value is null',
                         'tracked_since','not a count of lesson opens alone','later review edits are excluded',
                         'Never relabel an older last-good period as today'):
            self.assertIn(required,text)

    def test_other_metric_and_action_guards_remain(self):
        text=' '.join(RULES)
        for rule in ('Linklr days are UTC calendar days','YouTube days are Pacific','unique openers per broadcast, added across broadcasts',
                     'Clicks are not downloads','Linklr has upsell revenue but no upsell count',
                     'You never send emails, message clients or learners, publish anything, spend money or change any account.'):
            self.assertIn(rule,text)

class Context(Base):
    def test_model_gets_exact_learning_provenance_without_changing_brief(self):
        learning=dict(available=True,stale=False,requested=dict(start='2026-11-11T00:00',end='2026-11-12T00:00',time_zone='Asia/Dubai'),
                      summary=dict(window_complete=True,metrics=dict(active_learners=dict(value=7,status='partial',tracked_since=None,definition='Distinct source activity'),
                                                                   minutes_learned=dict(value=None,status='unavailable'))))
        self.biz.learning_activity=SimpleNamespace(facts=lambda now:learning)
        moment=at('2026-11-12T03:00:00');before=self.biz.build(moment)
        context=self.biz.context(moment)
        self.assertIn(json.dumps(learning,ensure_ascii=False),context)
        self.assertIn('not delivery proof',context)
        self.assertEqual(before,self.biz.build(moment))
        self.assertEqual(self.bridge.calls,[])
        self.assertNotIn('tracked_since',before['text'])

if __name__=='__main__':unittest.main()
