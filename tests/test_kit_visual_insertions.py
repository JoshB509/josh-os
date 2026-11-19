"""Mock-only insertion contract tests. Never connects to a browser or remote account."""
import copy
import hashlib
import io
import json
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from studio.kit import body_html
from studio.kit_visual_browser import BrowserUnavailable, Snapshot, UploadReceipt, UnavailableKitVisualBrowser
from studio.kit_visual_insertions import ACTION, KitVisualInsertions, OwnerReview, source_url
from studio.store import now, Conflict
from tests.test_kit_drafts import Base, AUD


class MockBrowser:
    """Test double only; deliberately absent from production modules."""
    def __init__(self,email):
        self.parts=[p+'</p>' for p in body_html(dict(email,images=[])).split('</p>')[:-1]]
        self.meta=dict(id=555,status='draft',public=False,send_at=None,published_at=None,public_url=None,
                       subject=email['subject'],preview_text=email['preview'],subscriber_filter=[{'all':[{'type':'segment','ids':[101]}]}])
        self.resources={};self.writes=[];self.reads=0;self.with_api=True;self.api_change=None
        self.fail='';self.on_phase=None;self.transformed=False;self.available=True;self.stale=False

    def readiness(self):return dict(available=self.available,methods=['media_gallery','clipboard'],reason='Mock adapter disabled')

    def inspect(self,bid,*,reload):
        self.reads+=1
        api=dict(copy.deepcopy(self.meta),content=''.join(self.parts)) if self.with_api else None
        if api is not None and self.api_change:self.api_change(api)
        return Snapshot(str(uuid.uuid4()),'2000-01-01T00:00:00+00:00' if self.stale else now(),
                        f'https://app.kit.com/campaigns/{bid}/draft',reload,copy.deepcopy(self.meta),''.join(self.parts),
                        copy.deepcopy(self.resources),api,'' if api else 'Read-only connector unavailable in this fixture.')

    def insert_image(self,command,png,before_mutation,record_upload):
        assert hashlib.sha256(png).hexdigest()==command.png_hash
        assert command.destination=='https://app.kit.com/campaigns/555/draft'
        phases=('upload','insert') if command.method=='media_gallery' else ('paste',)
        src='https://mock-cdn.example/images/'+command.png_hash+'.png';rid='resource-'+command.png_hash
        for phase in phases:
            if self.on_phase:self.on_phase(phase)
            before_mutation(phase,self.inspect(command.broadcast_id,reload=True))
            if self.fail=='before':raise RuntimeError('Interrupted immediately before UI mutation')
            self.writes.append(phase)
            if phase in ('upload','paste'):
                if self.fail!='missing_receipt':record_upload(UploadReceipt(command.packet_id,command.ordinal,command.png_hash,rid,src,command.method,'mock-upload-audit'))
                if self.fail=='after_upload':raise RuntimeError('Upload autosave uncertain')
            if phase in ('insert','paste'):
                if self.transformed:src='https://transformed.example/resized/'+rid+'.jpg'
                self.resources[src]=rid
                alt=command.alt.replace('&','&amp;').replace('"','&quot;').replace('<','&lt;')
                image=f'<p><img src="{src}" alt="{alt}"></p>'
                at=command.anchor['after_line']+command.ordinal
                self.parts.insert(at,image)
                if self.fail=='duplicate':self.parts.insert(at,image)
                if self.fail=='after_insert':raise RuntimeError('Interrupted after editor autosave')


class InsertionTests(Base):
    def setUp(self):
        super().setUp()
        self.block_network=patch('urllib.request.urlopen',side_effect=AssertionError('No external request permitted'))
        self.block_network.start();self.addCleanup(self.block_network.stop)
        self.drafts._save(dict(id=str(uuid.uuid4()),email_id=self.email['id'],broadcast_id=555,status='drafted',created_at=now()))
        buffer=io.BytesIO();Image.new('RGB',(1080,1080),'yellow').save(buffer,format='PNG');self.png=buffer.getvalue()
        self.add_visuals()
        self.browser=MockBrowser(self.fresh())
        self.service=KitVisualInsertions(self.emails,self.drafts,owner_id='owner',browser=self.browser)

    def add_visuals(self):
        def render(slides,folder):
            for i in range(len(slides)):(folder/f'slide_{i+1:02d}.png').write_bytes(self.png)
        with patch.object(self.emails.visuals,'render',side_effect=render):
            p=self.fresh();return self.emails.visuals.generate(dict(email_id=p['id'],revision=p['revision'],visuals=[
                dict(lines=['Your work','Its impact'],alt='Your work leads to impact.',after_line=1)]))

    def prepare(self,**kw):
        return self.service.prepare(self.email['id'],self.fresh()['revision'],555,**kw)

    def approve(self,row=None):
        row=row or self.prepare()
        return self.service.approve(row['id'],row['digest'],OwnerReview('owner','test-owner-ui','click-'+uuid.uuid4().hex,row['id'],row['digest'],ACTION))

    def execute(self,row=None):
        row=row or self.approve();return self.service.execute(row['id'],row['digest'])

    def test_default_adapter_is_explicitly_unavailable_and_never_acts(self):
        svc=KitVisualInsertions(self.emails,self.drafts,owner_id='owner')
        self.assertFalse(svc.readiness()['available'])
        with self.assertRaises(BrowserUnavailable):svc.prepare(self.email['id'],self.fresh()['revision'],555)
        with self.assertRaises(BrowserUnavailable):UnavailableKitVisualBrowser().insert_image(None)
        self.assertEqual(self.browser.writes,[])

    def test_packet_binds_assets_destination_alt_position_baseline_and_approval(self):
        row=self.prepare();p=row['packet'];v=self.fresh()['images'][0]
        self.assertEqual(row['state'],'not_started');self.assertIsNone(row['approval'])
        self.assertEqual(p['assets'][0]['visual'],v);self.assertEqual(p['broadcast_id'],555)
        self.assertEqual(p['assets'][0]['anchor']['after_line'],1)
        self.assertEqual(p['baseline']['metadata'],self.browser.meta)
        self.assertNotIn('path',json.dumps(p));self.assertEqual(self.browser.writes,[])
        with self.assertRaises(ValueError):self.service.execute(row['id'],row['digest'])
        self.assertEqual(self.service.get(row['id'])['state'],'not_started')

    def test_exact_owner_action_required_not_preview_or_model_dict(self):
        row=self.prepare();review=OwnerReview('owner','ui','event',row['id'],row['digest'],ACTION)
        for bad in (None,{'actor_id':'owner'},replace(review,actor_id='laia'),replace(review,action='preview'),
                    replace(review,packet_digest='wrong'),replace(review,packet_id='another'),replace(review,event_id='')):
            with self.subTest(review=bad),self.assertRaises(ValueError):self.service.approve(row['id'],row['digest'],bad)
        with self.assertRaises(ValueError):self.service.approve(row['id'],'wrong',review)
        self.assertEqual(self.browser.writes,[])

    def test_success_requires_reload_and_readonly_api_evidence(self):
        row=self.execute()
        self.assertEqual(row['state'],'verified_complete')
        self.assertEqual(self.browser.writes,['upload','insert'])
        events=self.service.events(row['id'])
        self.assertIn('upload_source',[e['event'] for e in events])
        self.assertIn('verified_persistence',[e['event'] for e in events])
        self.assertGreaterEqual(self.browser.reads,7)
        self.assertIsNone(self.browser.meta['send_at']);self.assertIs(self.browser.meta['public'],False)
        with self.assertRaises(ValueError):self.service.execute(row['id'],row['digest'])
        self.service.recover(row['id']);self.assertEqual(self.browser.writes,['upload','insert'])

    def test_clipboard_is_separately_bound_and_has_no_upload_endpoint(self):
        row=self.approve(self.prepare(method='clipboard'));self.execute(row)
        self.assertEqual(self.browser.writes,['paste'])
        with self.assertRaises(ValueError):self.prepare(method='api_upload')

    def test_source_identity_handles_kit_cdn_conversion_without_byte_comparison(self):
        self.browser.transformed=True
        row=self.execute();self.assertEqual(row['state'],'verified_complete')
        self.assertIn('.jpg',self.browser.parts[1])
        receipt=next(e['body']['evidence'] for e in self.service.events(row['id']) if e['event']=='upload_source')
        self.assertIn('.png',receipt['observed_src'])
        self.assertEqual(receipt['png_hash'],self.fresh()['images'][0]['png_hash'])

    def test_changed_revision_subject_body_alt_position_or_image_prevents_write(self):
        for field,value in [('subject','Changed'),('body','Different\nBody'),('alt','Changed alt'),('after_line',0),('version','regenerate')]:
            row=self.approve();p=self.fresh()
            if field in ('subject','body'):changes={field:value}
            elif field=='version':self.add_visuals();changes=None
            else:
                images=copy.deepcopy(p['images']);images[0][field]=value;changes={'images':images}
            if field=='body':
                images=copy.deepcopy(p['images']);images[0]['after_line']=2;changes['images']=images
            if changes:self.emails.save(p['id'],changes,p['revision'],sync_remote=False)
            with self.assertRaises((ValueError,Conflict)):self.execute(row)
            self.assertEqual(self.browser.writes,[])
            self.browser=MockBrowser(self.fresh());self.service.browser=self.browser

    def test_replaced_png_or_asset_path_is_rejected(self):
        row=self.approve();v=self.fresh()['images'][0]
        file=self.emails.visuals.folder(self.email['id'],v['version'])/'slide_01.png'
        file.write_bytes(b'changed')
        with self.assertRaises(ValueError):self.execute(row)
        self.assertEqual(self.browser.writes,[])

    def test_wrong_ownership_destination_or_scheduled_public_state_rejected(self):
        with self.assertRaises(ValueError):self.service.prepare(self.email['id'],self.fresh()['revision'],666)
        baseline=copy.deepcopy(self.browser.meta)
        for field,value in [('id',666),('status','scheduled'),('status','completed'),('public',True),('send_at','2099-01-01'),('public_url','https://example.test')]:
            self.browser.meta=dict(baseline,**{field:value})
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):self.prepare()
        self.browser.meta=baseline
        original=self.browser.inspect
        with patch.object(self.browser,'inspect',side_effect=lambda *a,**k:replace(original(*a,**k),url='https://app.kit.com/campaigns/666/draft')):
            with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(self.browser.writes,[])

    def test_stale_readback_and_api_disagreement_fail_closed(self):
        self.browser.stale=True
        with self.assertRaises(ValueError):self.prepare()
        self.browser.stale=False;self.browser.api_change=lambda a:a.update(public=True)
        with self.assertRaises(ValueError):self.prepare()

    def test_unrelated_remote_changes_stop_before_upload(self):
        for field,value in [('subject','Other'),('preview_text','Other'),('subscriber_filter',[]),('send_at','2099-01-01')]:
            row=self.approve();saved=self.browser.meta[field];self.browser.meta[field]=value
            with self.assertRaisesRegex(ValueError,'uncertain'):self.execute(row)
            self.assertEqual(self.browser.writes,[])
            self.browser.meta[field]=saved
            # Reset only this temporary fixture's ledger for the next independent scenario.
            with self.store.connect() as c:
                c.execute('DELETE FROM kit_visual_events');c.execute('DELETE FROM kit_visual_insertions')

    def test_remote_body_or_link_change_before_approval_is_rejected(self):
        row=self.prepare();self.browser.parts[0]='<p>Unrelated edit <a href="https://example.test">link</a></p>'
        with self.assertRaises(ValueError):self.approve(row)
        self.assertEqual(self.browser.writes,[])

    def test_recheck_immediately_before_each_mutation(self):
        row=self.approve()
        self.browser.on_phase=lambda phase:self.browser.meta.update(subject='Concurrent edit') if phase=='insert' else None
        with self.assertRaises(ValueError):self.execute(row)
        self.assertEqual(self.browser.writes,['upload'])
        self.assertEqual(self.service.get(row['id'])['state'],'insertion_uncertain')

    def test_local_revision_recheck_between_upload_and_insertion(self):
        row=self.approve()
        def change(phase):
            if phase=='insert':
                p=self.fresh();self.emails.save(p['id'],{'subject':'Concurrent local edit'},p['revision'],sync_remote=False)
        self.browser.on_phase=change
        with self.assertRaises(ValueError):self.execute(row)
        self.assertEqual(self.browser.writes,['upload'])

    def test_interruption_after_autosave_recovers_without_duplicate(self):
        row=self.approve();self.browser.fail='after_insert'
        with self.assertRaises(ValueError):self.execute(row)
        self.assertEqual(self.service.get(row['id'])['state'],'insertion_uncertain')
        with self.assertRaises(ValueError):self.execute(row)
        self.browser.fail=''
        recovered=self.service.recover(row['id']);self.assertEqual(recovered['state'],'verified_complete')
        self.assertEqual(self.browser.writes,['upload','insert'])
        self.assertEqual(sum('<img' in p for p in self.browser.parts),1)

    def test_uncertain_absent_image_is_inspected_but_never_blindly_retried(self):
        row=self.approve();self.browser.fail='after_upload'
        with self.assertRaises(ValueError):self.execute(row)
        reads=self.browser.reads
        with self.assertRaises(ValueError):self.service.recover(row['id'])
        self.assertGreater(self.browser.reads,reads);self.assertEqual(self.browser.writes,['upload'])
        with self.assertRaises(ValueError):self.execute(row)
        with self.assertRaises(ValueError):self.prepare()

    def test_duplicate_wrong_alt_or_untrusted_source_cannot_complete(self):
        row=self.approve();self.browser.fail='duplicate'
        with self.assertRaises(ValueError):self.execute(row)
        with self.assertRaises(ValueError):self.service.recover(row['id'])
        self.assertEqual(self.service.get(row['id'])['state'],'insertion_uncertain')

    def test_wrong_alt_or_changed_resource_identity_stays_uncertain(self):
        row=self.approve();self.browser.fail='after_insert'
        with self.assertRaises(ValueError):self.execute(row)
        original=self.browser.parts[1]
        self.browser.parts[1]=original.replace('Your work leads to impact.','Wrong alt')
        with self.assertRaises(ValueError):self.service.recover(row['id'])
        self.browser.parts[1]=original;self.browser.resources={k:'untrusted-substitute' for k in self.browser.resources}
        with self.assertRaises(ValueError):self.service.recover(row['id'])
        self.assertEqual(self.service.get(row['id'])['state'],'insertion_uncertain')

    def test_model_claim_and_missing_upload_evidence_are_not_completion(self):
        row=self.approve();self.browser.fail='missing_receipt'
        with self.assertRaises(ValueError):self.execute(row)
        with self.assertRaises(ValueError):self.service.recover(row['id'])
        self.assertNotIn('verified_persistence',[e['event'] for e in self.service.events(row['id'])])

    def test_success_claim_without_browser_evidence_is_not_completion(self):
        row=self.approve()
        with patch.object(self.browser,'insert_image',return_value='Successfully uploaded and saved'):
            with self.assertRaises(ValueError):self.execute(row)
        self.assertEqual(self.service.get(row['id'])['state'],'insertion_uncertain')
        self.assertEqual(self.browser.writes,[])

    def test_api_required_at_review_cannot_disappear_at_verification(self):
        row=self.approve();self.browser.with_api=False
        with self.assertRaises(ValueError):self.execute(row)
        self.assertEqual(self.browser.writes,[])

    def test_explicit_api_unavailability_allows_ui_only_evidence_contract(self):
        self.browser.with_api=False;row=self.execute()
        self.assertFalse(row['packet']['baseline']['api_required']);self.assertEqual(row['state'],'verified_complete')
        proof=next(e['body']['evidence'] for e in self.service.events(row['id']) if e['event']=='verified_persistence')
        self.assertTrue(proof['api_unavailable_reason'])

    def test_guard_prevents_api_from_stripping_images_even_after_local_removal(self):
        row=self.execute();p=self.fresh();self.emails.save(p['id'],{'images':[]},p['revision'],sync_remote=False)
        with self.assertRaisesRegex(ValueError,'strip images'):self.drafts.propose(self.fresh(),AUD)
        with self.assertRaisesRegex(ValueError,'strip images'):
            from studio.kit_visual_insertions import assert_api_update_allowed
            with self.store.connect() as c:assert_api_update_allowed(c,p['id'],555)
        self.assertEqual(row['state'],'verified_complete')

    def test_existing_api_write_reservation_blocks_insertion(self):
        row=self.approve()
        self.drafts._save(dict(id=str(uuid.uuid4()),email_id=self.email['id'],status='writing',created_at=now()))
        with self.assertRaisesRegex(ValueError,'API write'):self.execute(row)
        self.assertEqual(self.browser.writes,[]);self.assertEqual(self.service.get(row['id'])['state'],'not_started')

    def test_api_reservation_rechecks_visual_lock_before_calling_connector(self):
        self.execute();p=self.fresh()
        old=dict(id=str(uuid.uuid4()),email_id=p['id'],email_revision=p['revision'],broadcast_id=555,status='approved',
                 payload={'subject':'Test'},hash='test',created_at=now())
        self.drafts._save(old)
        # Simulate a reservation race after the earlier approval/check succeeded.
        with patch.object(self.drafts,'check',return_value=(True,'')),patch.object(self.drafts,'live',return_value=True),patch.object(self.drafts,'guard_policy',return_value={'write':{'tool':'mcp__kit__update_broadcast','input':{}},'reads':[],'reference_input':{}}):
            run=unittest.mock.Mock()
            with self.assertRaisesRegex(ValueError,'strip images'):self.drafts.write(old['id'],p,run)
            run.assert_not_called()

    def test_two_approved_packets_cannot_duplicate_the_insert(self):
        first=self.approve();second=self.approve();self.execute(first)
        with self.assertRaises(ValueError):self.execute(second)
        self.assertEqual(self.browser.writes,['upload','insert'])

    def test_wrong_anchor_or_nonmatching_remote_body_rejected(self):
        self.browser.parts.reverse()
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(self.browser.writes,[])

    def test_ambiguous_repeated_boundary_rejected(self):
        p=self.fresh();images=copy.deepcopy(p['images']);images[0]['after_line']=2
        p=self.emails.save(p['id'],{'body':'Repeated\nRepeated\nRepeated','images':images},p['revision'],sync_remote=False)
        self.browser=MockBrowser(p);self.service.browser=self.browser
        with self.assertRaisesRegex(ValueError,'unique paragraph'):self.prepare()

    def test_mutation_capability_has_no_schedule_send_or_arbitrary_actions(self):
        row=self.approve()
        def malicious(command,png,before,record):before('schedule',self.browser.inspect(555,reload=True))
        with patch.object(self.browser,'insert_image',side_effect=malicious):
            with self.assertRaises(ValueError):self.execute(row)
        self.assertEqual(self.browser.writes,[])

    def test_unavailable_adapter_after_approval_preserves_not_started(self):
        row=self.approve();self.browser.available=False
        with self.assertRaises(BrowserUnavailable):self.execute(row)
        self.assertEqual(self.service.get(row['id'])['state'],'not_started');self.assertEqual(self.browser.writes,[])

    def test_boundary_before_body_and_after_last_line(self):
        for position in (0,2):
            p=self.fresh();images=copy.deepcopy(p['images']);images[0]['after_line']=position
            self.emails.save(p['id'],{'images':images},p['revision'],sync_remote=False)
            self.browser=MockBrowser(self.fresh());self.service.browser=self.browser
            self.execute();self.assertIn('<img',self.browser.parts[position])
            with self.store.connect() as c:
                c.execute('DELETE FROM kit_visual_events');c.execute('DELETE FROM kit_visual_insertions')

    def test_callback_capability_cannot_be_reused_after_adapter_returns(self):
        saved={};original=self.browser.insert_image
        def capture(command,png,before,record):
            saved.update(before=before,record=record);original(command,png,before,record)
        self.browser.insert_image=capture;self.execute()
        with self.assertRaisesRegex(ValueError,'expired'):saved['before']('upload',self.browser.inspect(555,reload=True))


class SourceTests(unittest.TestCase):
    def test_no_local_authenticated_or_unsafe_image_urls(self):
        for value in ['http://example.test/a','https://localhost/a','https://localhost./a','https://127.0.0.1/a',
                      'https://10.0.0.1/a','https://[::1]/a','https://work.career-os.ai/a','file:///x','data:image/png,x']:
            with self.subTest(value=value),self.assertRaises(ValueError):source_url(value)


if __name__=='__main__':unittest.main()
