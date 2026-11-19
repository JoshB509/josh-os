"""No browser/network use: Windows file semantics and exact-call receipt binding."""
from concurrent.futures import ThreadPoolExecutor
import ctypes
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

from studio.kit_chrome_gate import active_policy,decide,publish_policy,retire_policy,record_receipt,write_once,fingerprint
from studio.kit_chrome_worker import KitChromeWorker,BOUNDARY_CONTROLS,hook_command
from studio.kit_visual_browser import BrowserUnavailable


class PermitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def policy(self,cid='call1'):
        return dict(id=cid,tool='mcp__claude-in-chrome__read_page',args={'tabId':123},expires=time.time()+60)
    def event(self,uid='toolu_first'):
        p=self.policy();return dict(tool_name=p['tool'],tool_input=p['args'],tool_use_id=uid)
    def test_held_old_reader_does_not_prevent_next_immutable_permit(self):
        publish_policy(self.root,self.policy())
        with (self.root/'call1.policy.json').open('r',encoding='utf-8') as held:
            self.assertTrue(decide(self.root,self.event()));retire_policy(self.root,'call1')
            publish_policy(self.root,self.policy('call2'))
            self.assertEqual(active_policy(self.root)['id'],'call2')
            self.assertEqual(json.load(held)['id'],'call1')
        self.assertFalse((self.root/'policy.json').exists())
    def test_many_sequential_permits_in_default_sandbox_temp(self):
        for i in range(40):
            cid='call'+str(i);publish_policy(self.root,self.policy(cid));event=self.event('toolu_'+str(i))
            self.assertTrue(decide(self.root,event));self.assertFalse(decide(self.root,event))
            self.assertTrue(record_receipt(self.root,dict(event,tool_response=[{'type':'text','text':'result'}])))
            retire_policy(self.root,cid)
        with self.assertRaises(ValueError):active_policy(self.root)
    def test_late_identical_response_is_bound_to_old_tool_use_id(self):
        publish_policy(self.root,self.policy());old=self.event();self.assertTrue(decide(self.root,old));retire_policy(self.root,'call1')
        publish_policy(self.root,self.policy('call2'));new=self.event('toolu_second');self.assertTrue(decide(self.root,new))
        self.assertTrue(record_receipt(self.root,dict(old,tool_response='old response')))
        self.assertFalse((self.root/'call2.receipt-complete').exists())
        self.assertTrue(record_receipt(self.root,dict(new,tool_response='new response')))
        self.assertEqual(json.loads((self.root/'call2.receipt.json').read_text())['response'],'new response')
    def test_concurrent_claims_allow_exactly_one(self):
        publish_policy(self.root,self.policy())
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(lambda i:decide(self.root,self.event('toolu_'+str(i))),range(16)))
        self.assertEqual(sum(results),1)
    def test_no_ready_marker_expired_ambiguous_or_changed_policy_fails_closed(self):
        write_once(self.root/'partial.policy.json',self.policy('partial'))
        with self.assertRaises(ValueError):active_policy(self.root)
        p=self.policy();publish_policy(self.root,p)
        with self.assertRaises(ValueError):publish_policy(self.root,self.policy('call2'))
        p['args']['tabId']=124;(self.root/'call1.policy.json').write_text(json.dumps(p))
        with self.assertRaises(ValueError):decide(self.root,self.event())
        retire_policy(self.root,'call1');p=self.policy('expired');p['expires']=0;publish_policy(self.root,p)
        with self.assertRaises(ValueError):active_policy(self.root)
    def test_missing_tool_identity_or_unclaimed_response_never_counts(self):
        publish_policy(self.root,self.policy());event=self.event();event.pop('tool_use_id')
        self.assertFalse(decide(self.root,event));self.assertFalse(record_receipt(self.root,self.event()))
        self.assertTrue(decide(self.root,self.event()))
        self.assertFalse(record_receipt(self.root,dict(self.event(),tool_input={'tabId':124})))

    def test_completed_model_refusal_allows_only_owned_tab_cleanup_without_retry(self):
        worker=KitChromeWorker('never-started',self.root,self.root);worker.owned_tabs.add(12);calls=[]
        def write(_):
            policy=active_policy(worker.folder);calls.append(policy['tool'])
            if policy['tool'].endswith('tabs_close_mcp'):
                event=dict(tool_name=policy['tool'],tool_input=policy['args'],tool_use_id='toolu_'+policy['id'])
                self.assertTrue(decide(worker.folder,event))
                self.assertTrue(record_receipt(worker.folder,dict(event,tool_response=[{'type':'text','text':'Closed tab 12. Group is now empty (auto-removed).'}])))
            worker.queue.put({'type':'result','result':'I did not call the tool.'})
        worker.process=SimpleNamespace(stdin=SimpleNamespace(write=write,flush=lambda:None),poll=lambda:0)
        worker.start=lambda:None
        with self.assertRaisesRegex(BrowserUnavailable,'No complete trusted'):
            worker.call('javascript_tool',dict(tabId=12,action='javascript_exec',text=BOUNDARY_CONTROLS),timeout=.02)
        self.assertTrue(worker.cleanup_only);self.assertFalse(worker.closed)
        with self.assertRaisesRegex(BrowserUnavailable,'Only closing'):
            worker.call('read_page',{'tabId':12})
        with self.assertRaisesRegex(BrowserUnavailable,'positively identified'):
            worker.call('tabs_close_mcp',{'tabId':13})
        worker.call('tabs_close_mcp',{'tabId':12})
        self.assertEqual(len(calls),2);self.assertFalse(worker.owned_tabs)
        self.assertEqual(len(list(worker.folder.glob('*.retired'))),2)

    def test_unfinished_turn_timeout_closes_worker_and_never_reuses_permit(self):
        worker=KitChromeWorker('never-started',self.root,self.root);worker.owned_tabs.add(12)
        worker.process=SimpleNamespace(stdin=SimpleNamespace(write=lambda _:None,flush=lambda:None),poll=lambda:0)
        worker.start=lambda:None
        with self.assertRaisesRegex(BrowserUnavailable,'timed out'):
            worker.call('read_page',{'tabId':12},timeout=.01)
        self.assertTrue(worker.closed);self.assertEqual(len(list(worker.folder.glob('*.retired'))),1)

    def test_hook_command_preserves_no_space_paths_and_literal_shell_characters(self):
        paths=[r'C:\Tools\python.exe',r'E:\Videos\DaJoshiResolve\studio\kit_chrome_gate.py',r'C:\Audit folder\literal$(marker)`text`']
        self.assertEqual(shlex.split(hook_command(*paths)),[p.replace('\\','/') for p in paths])

    @unittest.skipUnless(os.name=='nt' and os.environ.get('KIT_TEST_GIT_BASH')=='1',
                         'Run the local Git Bash integration with KIT_TEST_GIT_BASH=1 outside the restricted DLL sandbox')
    def test_hook_really_runs_in_git_bash_with_no_space_script_path(self):
        bash=Path(r'C:\Program Files\Git\bin\bash.exe')
        if not bash.exists():self.skipTest('Git Bash is not installed')
        gate=self.root/'gate.py';shutil.copyfile(Path(__file__).parents[1]/'studio/kit_chrome_gate.py',gate)
        buf=ctypes.create_unicode_buffer(32768)
        self.assertTrue(ctypes.windll.kernel32.GetShortPathNameW(str(gate),buf,len(buf)))
        script_path=buf.value;self.assertNotIn(' ',script_path)
        audit=self.root/'audit space $literal';audit.mkdir()
        publish_policy(audit,self.policy())
        event=dict(self.event(),hook_event_name='PreToolUse')
        result=subprocess.run([str(bash),'-c',hook_command(sys.executable,script_path,audit)],
                              input=json.dumps(event).encode('utf-8'),capture_output=True,timeout=10,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'],'allow')
        self.assertTrue((audit/'call1.used').exists())


if __name__=='__main__':unittest.main()
