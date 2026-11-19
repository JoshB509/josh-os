import copy
import json
import unittest
from datetime import datetime, timedelta, timezone

from studio.kit_draft_recovery import recover, confirm_created
from studio.kit_drafts import KIT, CREATE, GET
from tests.test_kit_drafts import Base, AUD


class RecoveryTests(Base):
    def setUp(self):
        super().setUp()
        stamp = datetime.now(timezone.utc)
        origin = dict(channel='chatgpt:room', actor_id='owner', message_id='original-request')
        d = self.drafts.propose(self.fresh(), AUD)
        d = self.drafts.approve(d['id'], d['hash'], self.fresh(), origin=origin)
        d.update(status='uncertain', write_started_at=(stamp - timedelta(seconds=30)).isoformat())
        self.old = self.drafts._save(d)
        self.confirmation = dict(origin=dict(origin, message_id='owner-confirmation'),
                                 kind='owner_reported_no_draft', text='I checked Kit and cannot see it.',
                                 sent_at=(stamp - timedelta(seconds=10)).isoformat())
        self.reconciliation = dict(checked_at=stamp.isoformat(), receipts=[dict(tool_name=KIT+'list_broadcasts',
            tool_input={'per_page': 100}, tool_response=[{'type': 'text', 'text':
                '{"broadcasts": [], "pagination": {"has_next_page": false}}'}])])

    def test_linked_recovery_preserves_history_payload_and_original_origin(self):
        d = recover(self.drafts, self.old['id'], self.fresh(), self.confirmation, self.reconciliation)
        self.assertEqual(self.drafts.get(self.old['id']), self.old)
        self.assertEqual(d['recovery_of'], self.old['id'])
        self.assertEqual(d['hash'], self.old['hash'])
        self.assertEqual(d['payload'], self.old['payload'])
        self.assertEqual(d['authorization']['origin'], self.old['authorization']['origin'])
        self.assertFalse(self.drafts.live())
        self.assertEqual(d['status'], 'approved')
        with self.assertRaises(ValueError):
            recover(self.drafts, self.old['id'], self.fresh(), self.confirmation, self.reconciliation)

    def test_other_actor_channel_or_original_message_is_not_recovery(self):
        for change in ({'actor_id':'someone-else'}, {'channel':'discord:room'}, {'message_id':'original-request'}):
            c = copy.deepcopy(self.confirmation);c['origin'].update(change)
            with self.assertRaises(ValueError): recover(self.drafts,self.old['id'],self.fresh(),c,self.reconciliation)

    def test_incomplete_filtered_stale_or_missing_reconciliation_is_rejected(self):
        variants = [dict(self.reconciliation, receipts=[]), dict(self.reconciliation, checked_at='2020-01-01T00:00:00Z')]
        filtered = copy.deepcopy(self.reconciliation);filtered['receipts'][0]['tool_input']['status']='draft';variants.append(filtered)
        incomplete = copy.deepcopy(self.reconciliation)
        incomplete['receipts'][0]['tool_response']={'broadcasts':[], 'pagination':{'has_next_page':True,'end_cursor':'next'}}
        variants.append(incomplete)
        for recon in variants:
            with self.assertRaises(ValueError): recover(self.drafts,self.old['id'],self.fresh(),self.confirmation,recon)

    def test_existing_broadcast_prevents_recovery(self):
        r = copy.deepcopy(self.reconciliation)
        r['receipts'][0]['tool_response']={'broadcasts':[{'id':555,'subject':self.old['payload']['subject']}],
                                          'pagination':{'has_next_page':False}}
        with self.assertRaises(ValueError): recover(self.drafts,self.old['id'],self.fresh(),self.confirmation,r)

    def confirmation_fixture(self):
        stamp = datetime.now(timezone.utc).isoformat()
        b = dict(self.old['payload'], id=555, status='draft', send_at=None, published_at=None, public_url=None)
        folder = self.root/'Data'/'kit-draft-audit';folder.mkdir(parents=True,exist_ok=True)
        records = [dict(event='PreToolUse', tool_name=CREATE, allow=True, reserved=True,
                        tool_input=self.old['payload'], run_id='actual-run', at=stamp),
                   dict(event='PostToolUse', tool_name=CREATE, tool_response={'broadcast':b}, run_id='actual-run', at=stamp)]
        (folder/(self.old['id']+'.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in records),encoding='utf-8')
        return dict(checked_at=stamp,_kit_receipts=[dict(tool_name=GET,tool_input={'id':555},tool_response={'broadcast':b})])

    def test_known_creation_can_be_confirmed_from_actual_audit_and_exact_read(self):
        read = self.confirmation_fixture()
        d = confirm_created(self.drafts,self.old['id'],self.fresh(),read)
        self.assertEqual((d['status'],d['broadcast_id'],d['verified_send_at']),('drafted',555,None))
        self.assertFalse(d['verified_public'])

    def test_model_claim_without_create_audit_cannot_confirm(self):
        with self.assertRaises(ValueError):
            confirm_created(self.drafts,self.old['id'],self.fresh(),{'reply':'Created 555'})

    def test_mismatched_or_scheduled_read_cannot_confirm(self):
        for change in ({'content':'wrong'}, {'status':'scheduled'}, {'public':True}):
            read = self.confirmation_fixture()
            read['_kit_receipts'][0]['tool_response']['broadcast'].update(change)
            with self.assertRaises(ValueError):
                confirm_created(self.drafts,self.old['id'],self.fresh(),read)

    def test_changed_email_needs_fresh_review(self):
        e = self.fresh();self.emails.save(e['id'], {'body':'Changed meaning'}, e['revision'])
        with self.assertRaises(ValueError): recover(self.drafts,self.old['id'],self.fresh(),self.confirmation,self.reconciliation)


if __name__ == '__main__':
    unittest.main()
