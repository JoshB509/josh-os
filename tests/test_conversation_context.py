import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio.conversation_context import recent, origin, MAX_CHARS
from studio.agent import Agents, DESK
from studio.store import Store
from studio.team import Team

class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'Data')
        with self.store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS notices(key TEXT PRIMARY KEY,body TEXT NOT NULL)')

    def notice(self,key,**kw):
        n=dict(key=key,agent_id='anouk',state='sent',kind='mail',text='Danielle emailed',sent_at='2026-11-15T14:00:00Z',message_ids=['123'])
        n.update(kw)
        with self.store.connect() as c:
            c.execute('INSERT INTO notices VALUES(?,?)',(key,json.dumps(n)))

    def test_only_own_delivered_notices_and_batch_dedup(self):
        self.notice('a',batch='batch',sent_text='Actual complete outgoing text')
        self.notice('b',batch='batch',sent_text='Actual complete outgoing text')
        self.notice('c',agent_id='sam')
        for state in ('queued','failed','partial','uncertain','sending'):
            self.notice(state,state=state)
        rows=recent(self.store,'anouk',[])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['text'],'Actual complete outgoing text')
        self.assertNotIn('offers',rows[0])
        self.assertNotIn('approval',rows[0])

    def test_banter_remains_labelled_and_existing_copies_not_duplicated(self):
        self.notice('fun',kind='banter',text='Cat holiday')
        rows=recent(self.store,'anouk',[])
        self.assertTrue(rows[0]['banter'])
        rows=recent(self.store,'anouk',[dict(role='agent',text='Cat holiday',at='2026-11-15T14:00:00Z')])
        self.assertEqual(len(rows),1)

    def test_history_is_expanded_but_bounded(self):
        rows=[dict(id=str(i),role='josh',text='x'*2000,at=f'2026-11-15T14:{i:02}:00Z') for i in range(50)]
        result=recent(self.store,'sam',rows)
        self.assertEqual(result[-1]['id'],'49')
        self.assertLessEqual(sum(len(x['text']) for x in result),MAX_CHARS)
        self.assertGreater(len(result),12)

    def test_reference_and_origin_are_discussion_only(self):
        self.notice('a')
        d=dict(author_id='42',channel_id='99',message_id='456',reply_to='123',is_dm=True)
        o=origin(dict(via='discord',discord=d))
        self.assertEqual(o['conversation'],'discord:dm:42')
        self.assertEqual(o['request_message_id'],'456')
        self.assertEqual(o['reply_to'],'123')
        self.assertNotIn('offers',o)
        self.assertEqual(recent(self.store,'anouk',[],d)[0]['discord_message_ids'],['123'])

    def test_chat_includes_notice_and_saves_channel_provenance(self):
        self.notice('a')
        a=Agents(self.store,Path(self.tmp.name),Team(self.store))
        d=dict(author_id='42',channel_id='99',message_id='456',is_dm=True)
        with patch.object(a,'run',return_value=('Here is the reply',{})) as run:
            out=a.chat(dict(agent_id='anouk',message='Show that email',via='discord',discord=d),[])
        self.assertIn('Danielle emailed',run.call_args.args[1])
        self.assertIn('not action approval',run.call_args.args[1])
        for m in out['messages'][-2:]:
            self.assertEqual(m['conversation'],'discord:dm:42')
            self.assertEqual(m['request_message_id'],'456')

if __name__=='__main__': unittest.main()
