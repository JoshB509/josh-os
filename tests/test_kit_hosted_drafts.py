import copy,json,unittest
from unittest.mock import patch
from PIL import Image
from studio.kit_hosted_drafts import prepare,verified_asset,authorize,DraftWriteGate,hosted_url
from tests.test_kit_drafts import Base


ORIGIN=dict(actor_id='owner',channel='authenticated-local',message_id='request-1')


class MockTransport:
    """Isolated test double only. No connector, browser or network implementation."""
    def __init__(self,discovery,baseline=None):
        self.discovery=discovery;self.baseline=baseline;self.writes=[];self.result=None
        self.caps=dict(image_html_drafts=True,audience_unset=True,conditional_update=True)
    def capabilities(self):return self.caps
    def inspect(self,packet):return dict(discovery=self.discovery,baseline=self.baseline)
    def save(self,command):
        self.writes.append(command);bid=command['broadcast_id'] or 123
        self.result=dict(command['payload'],id=bid,status='draft',published_at=None,public_url=None,audience_selection='unset')
        return bid
    def read(self,bid):return self.result


class HostedDrafts(Base):
    def setUp(self):
        super().setUp();p=self.fresh()
        p=self.emails.save(p['id'],dict(body='Hey {{ subscriber.first_name }},\n\nA <point> & its evidence.\n\nClosing.'),p['revision'],sync_remote=False)
        def render(slides,folder):
            for i in range(8):Image.new('RGB',(640,360),(i*20,90,100)).save(folder/f'slide_{i+1:02d}.png')
        with patch.object(self.emails.visuals,'render',side_effect=render):
            self.email=self.emails.visuals.generate(dict(email_id=p['id'],revision=p['revision'],visuals=[dict(lines=[str(i)],width=640,height=360,alt='Image '+str(i)+' <alt> "text"',after_line=0 if i==0 else 3) for i in range(8)]))
        self.assets=[]
        for i,im in enumerate(self.email['images'],1):
            data=self.emails.visuals.read_png(p['id'],im['version'],str(im['index']))
            self.assets.append(verified_asset(self.emails,p['id'],self.email['revision'],i,f'https://embed.filekitcdn.com/e/account/asset{i}.png',data,f'raw-read-{i}'))
        self.discovery=dict(complete=True,broadcasts=[],evidence_id='complete-list-1')
        self.enterContext(patch('urllib.request.urlopen',side_effect=AssertionError('No network allowed')))

    def packet(self,**kw):return prepare(self.email,self.assets,self.discovery,**kw)
    def gate(self,packet=None,transport=None):
        packet=packet or self.packet();transport=transport or MockTransport(self.discovery)
        return DraftWriteGate(self.emails,transport),packet,authorize(packet,ORIGIN),transport

    def test_eight_exact_images_html_escape_links_liquid_and_unset_audience(self):
        p=self.packet();h=p['payload']['content']
        self.assertEqual(h.count('<img '),8);self.assertTrue(h.startswith('<p><img'))
        self.assertIn('{{ subscriber.first_name }}',h);self.assertIn('A &lt;point&gt; &amp;',h)
        self.assertIn('&lt;alt&gt; &quot;text&quot;',h);self.assertNotIn('figcaption',h)
        self.assertFalse(p['payload']['public']);self.assertIsNone(p['payload']['send_at'])
        self.assertNotIn('subscriber_filter',p['payload']);self.assertIsNone(p['audience_intent'])
        self.assertEqual(p,self.packet())

    def test_receipt_requires_exact_hosted_bytes_and_safe_host(self):
        im=self.email['images'][0]
        with self.assertRaisesRegex(ValueError,'bytes'):
            verified_asset(self.emails,self.email['id'],self.email['revision'],1,self.assets[0]['url'],b'fake','read-1')
        for url in ['http://embed.filekitcdn.com/e/a/b','https://evil.test/e/a/b','https://embed.filekitcdn.com.evil.test/e/a/b','https://u:p@embed.filekitcdn.com/e/a/b','https://embed.filekitcdn.com/e/a/../b','https://embed.filekitcdn.com/e/a/b?redirect=x','https://embed.filekitcdn.com:443/e/a/b']:
            with self.subTest(url=url),self.assertRaises(ValueError):hosted_url(url)

    def test_stale_revision_alt_placement_and_missing_receipts_rejected(self):
        for field,value in [('alt','changed'),('after_line',4),('png_hash','0'*64),('source_hash','1'*64)]:
            changed=copy.deepcopy(self.email);changed['images'][0][field]=value
            with self.assertRaises(ValueError):prepare(changed,self.assets,self.discovery)
        changed=dict(self.email,revision=self.email['revision']+1)
        with self.assertRaises(ValueError):prepare(changed,self.assets,self.discovery)
        with self.assertRaises(ValueError):prepare(self.email,self.assets[:-1],self.discovery)

    def test_duplicate_incomplete_or_protected_target_never_creates(self):
        for d in [dict(self.discovery,complete=False),dict(self.discovery,broadcasts=[dict(id=1,subject=self.email['subject'])]*2)]:
            with self.assertRaises(ValueError):prepare(self.email,self.assets,d)
        d=dict(self.discovery,broadcasts=[dict(id=44,subject=self.email['subject'])])
        with self.assertRaises(ValueError):prepare(self.email,self.assets,d)
        for status in ['scheduled','completed','sending']:
            with self.assertRaises(ValueError):prepare(self.email,self.assets,d,baseline=dict(id=44,status=status,public=False,send_at=None))

    def test_default_transport_is_unavailable_without_reservation_or_write(self):
        p=self.packet();g=DraftWriteGate(self.emails)
        with self.assertRaisesRegex(ValueError,'unavailable'):g.execute(p,authorize(p,ORIGIN))
        self.assertFalse(g.root.exists())

    def test_mock_create_is_single_attempt_and_verified_retry_only_reads(self):
        g,p,a,t=self.gate();first=g.execute(p,a);again=g.execute(p,a)
        self.assertEqual(first,again);self.assertEqual(len(t.writes),1)
        self.assertEqual(set(t.writes[0]['payload']),{'subject','preview_text','content','public','send_at'})
        t.result['status']='scheduled'
        with self.assertRaises(ValueError):g.execute(p,a)
        self.assertEqual(len(t.writes),1)

    def test_existing_target_reused_and_baseline_bound(self):
        d=dict(self.discovery,broadcasts=[dict(id=44,subject=self.email['subject'])])
        before=dict(id=44,subject=self.email['subject'],status='draft',public=False,send_at=None,content='Old',subscriber_filter=None)
        p=prepare(self.email,self.assets,d,baseline=before);t=MockTransport(d,before);g,p,a,t=self.gate(p,t)
        t.baseline=dict(before,content='Concurrent edit')
        with self.assertRaisesRegex(ValueError,'baseline'):g.execute(p,a)
        self.assertEqual(t.writes,[])
        t.baseline=before;result=g.execute(p,a);self.assertEqual(result['id'],44)
        self.assertEqual(t.writes[0]['operation'],'update');self.assertEqual(t.writes[0]['broadcast_id'],44)

    def test_authorization_and_packet_tampering_blocked(self):
        g,p,a,t=self.gate()
        for bad in [dict(a,action='send'),dict(a,packet_digest='bad'),dict(a,broadcast_id=999)]:
            with self.assertRaises(ValueError):g.execute(p,bad)
        p['payload']['public']=True
        with self.assertRaises(ValueError):g.execute(p,a)
        self.assertEqual(t.writes,[])

    def test_unsupported_unset_audience_and_conditional_update_blocked(self):
        g,p,a,t=self.gate();t.caps['audience_unset']=False
        with self.assertRaisesRegex(ValueError,'audience unset'):g.execute(p,a)
        self.assertEqual(t.writes,[])
        d=dict(self.discovery,broadcasts=[dict(id=44,subject=self.email['subject'])]);b=dict(id=44,status='draft',public=False,send_at=None)
        p=prepare(self.email,self.assets,d,baseline=b);t=MockTransport(d,b);t.caps['conditional_update']=False
        g,p,a,t=self.gate(p,t)
        with self.assertRaisesRegex(ValueError,'baseline guard'):g.execute(p,a)

    def test_explicit_audience_is_exact_and_not_inferred(self):
        p=self.packet(audience_rule=dict(all=[dict(type='segment',ids=[607863])]))
        self.assertEqual(p['payload']['subscriber_filter'],[dict(all=[dict(type='segment',ids=[607863])])])
        g,p,a,t=self.gate(p);g.execute(p,a);self.assertEqual(len(t.writes),1)

    def test_uncertain_write_and_bad_readback_are_never_retried(self):
        g,p,a,t=self.gate()
        def fail(command):t.writes.append(command);raise TimeoutError('unknown')
        t.save=fail
        with self.assertRaises(TimeoutError):g.execute(p,a)
        with self.assertRaisesRegex(ValueError,'uncertain'):g.execute(p,a)
        self.assertEqual(len(t.writes),1)

    def test_local_edit_during_inspection_wins(self):
        g,p,a,t=self.gate();original=t.inspect
        def inspect(packet):
            q=self.fresh();self.emails.save(q['id'],dict(subject='New local edit'),q['revision'],sync_remote=False)
            return original(packet)
        t.inspect=inspect
        with self.assertRaisesRegex(ValueError,'changed during inspection'):g.execute(p,a)
        self.assertEqual(t.writes,[]);self.assertEqual(self.fresh()['subject'],'New local edit')

    def test_failed_saved_image_or_audience_readback_is_uncertain(self):
        g,p,a,t=self.gate();original=t.read
        t.read=lambda bid:dict(original(bid),content='Missing images')
        with self.assertRaisesRegex(ValueError,'Saved content differs'):g.execute(p,a)
        with self.assertRaisesRegex(ValueError,'uncertain'):g.execute(p,a)
        self.assertEqual(len(t.writes),1)


if __name__=='__main__':unittest.main()
