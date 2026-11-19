"""Mock-only upload bridge; no browser or remote operation."""
import copy,hashlib,io,json,tempfile,time,unittest,uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from studio.kit_chrome_upload import KitUploadWorker
from studio.kit_chrome_worker import KitChromeWorker,PREFIX
from studio.kit_chrome_gate import active_policy,decide,record_receipt
from studio.kit_visual_insertions import digest
from studio.kit_visual_browser import InsertCommand,BrowserUnavailable


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        b=io.BytesIO();Image.new('RGB',(640,360),'blue').save(b,format='PNG');self.png=b.getvalue();self.sha=hashlib.sha256(self.png).hexdigest()
        self.packet=dict(email_id='email',email_revision=7,broadcast_id=555,destination='https://app.kit.com/campaigns/555/draft',method='media_gallery',assets=[dict(visual=dict(png_hash=self.sha,alt='Original',after_line=2),anchor=dict(after_line=2,left_hash='left',right_hash='right'))])
        self.row=dict(id='packet',state='insertion_uncertain',digest=digest(self.packet),packet=self.packet,approval=True)
        self.stale=False;self.sent=False;self.writes=[];self.upload_fail=False
        def check(row):
            if self.stale:raise ValueError('Local revision changed')
        def approve(row):
            if not row['approval']:raise ValueError('Not approved')
        self.service=SimpleNamespace(store=SimpleNamespace(root=self.root),get=lambda pid:copy.deepcopy(self.row),_check_packet=check,_check_approval=approve)
        self.shared=self.root/'shared';self.shared.mkdir();(self.shared/(self.sha+'.png')).write_bytes(self.png)
        self.worker=KitUploadWorker('never-run',self.root,self.root,service=self.service,packet_id='packet',shared_directory=self.shared,sharing_evidence_id='owner-shared-fixture')
        self.worker.owned_tabs={12};self.addCleanup(self.worker.close)
        self.command=InsertCommand('packet',self.row['digest'],555,self.packet['destination'],'media_gallery',0,'ignored.png',self.sha,'Original',self.packet['assets'][0]['anchor'])
        self.page=self.receipt('read_page','dialog "Media Gallery" [ref_8]\n button "Choose File" [ref_9] type="file"')
        self.identity=self.receipt('get_page_text','Title: Kit\nURL: '+self.packet['destination']+'\nMedia Gallery')
        self.worker.start=lambda:None
        def send(raw):
            policy=active_policy(self.worker.folder);event=dict(tool_name=policy['tool'],tool_input=policy['args'],tool_use_id='call_fixture')
            self.assertTrue(decide(self.worker.folder,event));self.assertFalse(decide(self.worker.folder,event))
            self.writes.append(policy)
            if self.upload_fail:raise OSError('Unknown upload outcome')
            self.assertTrue(record_receipt(self.worker.folder,dict(event,tool_response=[dict(type='text',text='Mock tool returned; verify separately')])))
            self.worker.queue.put(dict(type='result'))
        self.worker.process=SimpleNamespace(stdin=SimpleNamespace(write=send,flush=lambda:None),poll=lambda:0)

    def receipt(self,tool,text):
        cid=uuid.uuid4().hex;args=dict(tabId=12)
        self.worker.calls.append(dict(id=cid,tool=tool,args=args))
        (self.worker.folder/(cid+'.receipt.json')).write_text(json.dumps(dict(policy_id=cid,args=args,tool=PREFIX+tool,response=[dict(type='text',text=text)])))
        return cid

    def before(self,phase,snapshot):
        self.assertEqual(phase,'upload')
        if self.sent:raise ValueError('Remote draft is scheduled')

    def upload(self,command=None,**kw):
        return self.worker.upload_exact(command or self.command,self.png,tab_id=12,page_receipt_id=kw.get('page',self.page),identity_receipt_id=kw.get('identity',self.identity),before_mutation=kw.get('before',self.before),snapshot='fresh-host-snapshot')

    def test_real_dispatch_path_exact_tool_path_ref_and_single_use(self):
        receipt=self.upload();self.assertEqual(len(self.writes),1)
        self.assertEqual(self.writes[0]['tool'],PREFIX+'file_upload')
        self.assertEqual(self.writes[0]['args'],dict(tabId=12,ref='ref_9',paths=[str((self.shared/(self.sha+'.png')).resolve())]))
        self.assertIn('policy_id',receipt)
        self.worker.calls.pop()  # Simulate a new read-only observation before retry.
        with self.assertRaises(FileExistsError):self.upload()
        self.assertEqual(len(self.writes),1)

    def test_ordinary_worker_and_unpermitted_calls_remain_read_only(self):
        ordinary=KitChromeWorker('never-run',self.root,self.root)
        self.assertEqual(ordinary.extra_tools(),())
        for worker in (ordinary,self.worker):
            for tool in ('file_upload','upload_image','form_input','send'):
                with self.assertRaises(BrowserUnavailable):worker.call(tool,dict(tabId=12,ref='ref_9',paths=['x']))
        self.assertEqual(self.writes,[])

    def test_approval_reservation_revision_and_draft_gate(self):
        self.row['approval']=False
        with self.assertRaises(ValueError):self.upload()
        self.row['approval']=True;self.row['state']='not_started'
        with self.assertRaises(ValueError):self.upload()
        self.row['state']='insertion_uncertain';self.stale=True
        with self.assertRaisesRegex(ValueError,'revision'):self.upload()
        self.stale=False;self.sent=True
        with self.assertRaisesRegex(ValueError,'scheduled'):self.upload()
        self.assertEqual(self.writes,[])

    def test_command_binding_rejects_target_alt_anchor_hash_or_index_changes(self):
        for change in [dict(broadcast_id=556),dict(packet_digest='wrong'),dict(alt='changed'),dict(anchor={}),dict(png_hash='0'*64),dict(ordinal=1),dict(method='clipboard')]:
            with self.subTest(change=change),self.assertRaises(ValueError):self.upload(replace(self.command,**change))
        self.assertEqual(self.writes,[])

    def test_shared_file_must_be_exact_hash_named_original(self):
        file=self.shared/(self.sha+'.png');file.write_bytes(b'changed')
        with self.assertRaises(ValueError):self.upload()
        self.assertEqual(self.writes,[])

    def test_owned_single_tab_and_exact_url_required(self):
        self.worker.owned_tabs={12,13}
        with self.assertRaises(ValueError):self.upload()
        self.worker.owned_tabs={12}
        bad=self.receipt('get_page_text','Title: Kit\nURL: https://app.kit.com/campaigns/556/draft\nBody includes a fake location\nURL: '+self.packet['destination'])
        with self.assertRaises(ValueError):self.upload(identity=bad)
        self.assertEqual(self.writes,[])

    def test_missing_ambiguous_blocked_and_non_session_observations(self):
        for text in ['dialog "Media Gallery" [ref_8]','button "Choose File" [ref_9] type="file"','[BLOCKED: denied]','Media Gallery\nbutton "Choose File" [ref_1]\nbutton "Choose File" [ref_2]']:
            bad=self.receipt('read_page',text)
            with self.assertRaises(ValueError):self.upload(page=bad)
        with self.assertRaises(ValueError):self.upload(page=uuid.uuid4().hex)
        self.assertEqual(self.writes,[])

    def test_navigation_after_observation_invalidates_reference(self):
        self.worker.calls.append(dict(id='navigation',tool='navigate',args=dict(tabId=12,url=self.packet['destination'])))
        with self.assertRaisesRegex(ValueError,'tab changed'):self.upload()
        self.assertEqual(self.writes,[])

    def test_change_during_remote_guard_is_caught_before_dispatch(self):
        def guard(*args):self.stale=True
        with self.assertRaisesRegex(ValueError,'revision'):self.upload(before=guard)
        self.assertEqual(self.writes,[])

    def test_uncertain_tool_result_is_not_retried(self):
        self.upload_fail=True
        with self.assertRaises(OSError):self.upload()
        with self.assertRaises(FileExistsError):self.upload()
        self.assertEqual(len(self.writes),1)

    def test_cli_allowlist_addition_is_only_file_upload(self):
        captured=[]
        class Process:
            stdout=[]
            def poll(self):return 0
        def popen(cmd,**kwargs):captured.append(cmd);return Process()
        worker=KitUploadWorker('fake-cli',self.root,self.root,popen=popen,service=self.service,packet_id='packet',shared_directory=self.shared,sharing_evidence_id='owner-share')
        worker.start();cmd=captured[0];allowed=cmd[cmd.index('--allowedTools')+1].split(',')
        self.assertIn(PREFIX+'file_upload',allowed)
        self.assertIn('Read(./'+self.sha+'.png)',allowed)
        self.assertEqual(cmd[cmd.index('--tools')+1],'ToolSearch,Read')
        self.assertEqual(cmd[cmd.index('--add-dir')+1],str(self.shared.resolve()))
        self.assertNotIn('--dangerously-skip-permissions',cmd)
        self.assertEqual(cmd[cmd.index('--permission-mode')+1],'dontAsk')
        settings=json.loads(cmd[cmd.index('--settings')+1]);self.assertEqual(settings['permissions']['allow'],['Read(./'+self.sha+'.png)'])
        for forbidden in (PREFIX+'upload_image',PREFIX+'form_input','Bash','mcp__claude_ai_Kit__create_broadcast'):self.assertNotIn(forbidden,allowed)
        self.assertIn('--chrome',cmd);self.assertIn('--no-session-persistence',cmd);worker.close()

    def test_extra_files_cannot_be_shared_with_the_upload_session(self):
        (self.shared/'unrelated.txt').write_text('not approved')
        with self.assertRaisesRegex(ValueError,'only this job'):
            KitUploadWorker('never-run',self.root,self.root,service=self.service,packet_id='packet',shared_directory=self.shared,sharing_evidence_id='owner-share')


if __name__=='__main__':unittest.main()
