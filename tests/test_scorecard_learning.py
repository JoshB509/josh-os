import copy,json,unittest
from datetime import datetime,timezone
from unittest.mock import patch
from test_scorecard import ScorecardTest
from studio.learning_activity import validate,request_for,LearningActivity

class LearningScorecard(ScorecardTest):
    def test_daily_learning_is_not_a_month_total(self):
        now=datetime(2026,11,16,10,56,4,tzinfo=timezone.utc)
        raw=json.loads((Path(__file__).parent/'fixtures/learning_activity_summary.json').read_text())
        got=validate(raw,request_for(now),now.isoformat())
        c=LearningActivity('.',self.tmp,fetcher=lambda at:got);c.collect(now)
        v=self.sc.view();card=self.card(v,'learning')
        self.assertIn('2026-11-15 Dubai',card['title'])
        self.assertEqual(card['headline']['value'],7)
        self.assertIn('partial',card['headline']['context'])
        self.assertIsNone(card['headline']['previous'])
        self.assertIsNone(card['headline']['delta'])
        self.assertNotIn('daily',card['headline'])
        self.assertFalse(card['supporting'][0]['available'])
        self.assertEqual(v['careeros']['learning']['summary'],got['summary'])

from pathlib import Path
