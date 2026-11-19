"""Anonymized fixtures use the supported envelopes observed in the real Kit editor."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from studio.kit_chrome_gate import decide,publish_policy,active_policy,record_receipt

from studio.kit_editor_observation import observed_body,tree_body
from studio.kit_visual_insertions import Document
from studio.kit_chrome_worker import KitChromeWorker,EDITOR_READ,PREFIX
from studio.kit_visual_browser import BrowserUnavailable
from tests import test_kit_visual_insertions as insertion_fixtures

ROOT=Path(__file__).parents[1]


class ObservationTests(unittest.TestCase):
    def setUp(self):self.data=json.loads((ROOT/'tests/fixtures/kit_chrome/supported_editor.json').read_text(encoding='utf-8'))
    def observe(self,data=None):
        d=data or self.data;return observed_body(d['api'],d['scoped'],d['plain'],555)
    def test_permitted_complete_text_and_scoped_links_match_clean_structured_body(self):
        out=self.observe();self.assertEqual(out['proof']['source'],'kit-content-tree+scoped-accessibility+page-text')
        local='<p>Hello, reader.</p><p>\u200b</p><p>Read <a href="https://example.test/guide?reader={{ subscriber.id }}" target="_blank" rel="noopener noreferrer">the guide</a> for the next step.</p>'
        self.assertEqual(Document(out['html']).nodes,Document(local).nodes)
        self.assertNotIn('style',out['html']);self.assertNotIn('table',out['html'])
    def test_wrong_wording_or_added_removed_text_cannot_be_hidden_by_wrappers(self):
        for before,after in [('Hello, reader.','Hello, changed.'),('Read \u200bthe guide','Read additional \u200bthe guide'),
                             ('\n\nHello, reader.','\n\nExtra paragraph\n\nHello, reader.'),
                             ('\n\n\u200bUnsubscribe','\n\nExtra paragraph\n\n\u200bUnsubscribe')]:
            d=copy.deepcopy(self.data);d['plain']=d['plain'].replace(before,after)
            with self.subTest(after=after),self.assertRaises(ValueError):self.observe(d)
    def test_exact_href_count_and_destination_are_required(self):
        for changed in (self.data['scoped'].replace('example.test','other.test'),self.data['scoped']+'\n link [ref_200] href="https://extra.test"'):
            d=copy.deepcopy(self.data);d['scoped']=changed
            with self.assertRaisesRegex(ValueError,'links differ'):self.observe(d)
    def test_denied_truncated_or_wrong_identity_observation_stops(self):
        for key,value in [('scoped','[BLOCKED: relevant body]'),('plain','[TRUNCATED]'),('plain',self.data['plain'].replace('/555/','/556/')),
                          ('scoped',self.data['scoped'].replace('Email content editor','Other editor'))]:
            d=copy.deepcopy(self.data);d[key]=value
            with self.assertRaises(ValueError):self.observe(d)
    def test_unknown_blocks_styles_and_dirty_tree_are_not_guessed(self):
        mutations=[lambda a:a.update(content_tree_is_clean=False),lambda a:a.update(content_tree_warnings=['unsupported']),
                   lambda a:a['content_tree'][0].update(style='display:none'),lambda a:a['content_tree'][0].update(type='image'),
                   lambda a:a['content_tree'][0]['children'][0].update(text='zero\u200bwidth')]
        for change in mutations:
            api=copy.deepcopy(self.data['api']);change(api)
            with self.assertRaises(ValueError):tree_body(api)
    def test_footer_and_template_are_separate_from_body_proof(self):
        before=self.observe();d=copy.deepcopy(self.data);d['plain']=d['plain'].replace('Test address','Changed address');after=self.observe(d)
        self.assertEqual(before['proof']['body_text_sha256'],after['proof']['body_text_sha256'])
        self.assertNotEqual(before['proof']['footer_sha256'],after['proof']['footer_sha256'])
    def test_wrapped_api_html_uses_clean_tree_and_independent_ui_wording(self):
        fixture=insertion_fixtures.InsertionTests();fixture.setUp()
        try:
            snap=fixture.browser.inspect(555,reload=True);proof=self.observe();api=self.data['api']
            snap=replace(snap,metadata={k:api[k] for k in snap.metadata},content_html=proof['html'],api_broadcast=api,content_proof=proof['proof'])
            baseline=fixture.service._snapshot(snap,555)
            self.assertTrue(baseline['template_hash']);self.assertTrue(baseline['footer_hash'])
            changed=copy.deepcopy(api);changed['content_tree'][0]['children'][0]['text']='Different wording'
            with self.assertRaisesRegex(ValueError,'does not match'):fixture.service._snapshot(replace(snap,api_broadcast=changed),555)
        finally:fixture.tearDown();fixture.doCleanups()
    def test_template_or_footer_change_invalidates_existing_insertion_baseline(self):
        fixture=insertion_fixtures.InsertionTests();fixture.setUp()
        try:
            row=fixture.prepare();snap=fixture.browser.inspect(555,reload=True)
            for changed in (replace(snap,api_broadcast=dict(snap.api_broadcast,theme={'font':'changed'})),
                            replace(snap,content_proof={'footer_sha256':'changed'})):
                with self.assertRaisesRegex(ValueError,'template or existing footer'):fixture.service._matches(row['packet'],changed,{},0)
            self.assertEqual(fixture.browser.writes,[])
        finally:fixture.tearDown();fixture.doCleanups()
    def test_gate_reads_utf8_even_when_windows_stdio_defaults_to_cp1252(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);policy=dict(id='utf8',tool=PREFIX+'read_page',args={'tabId':12,'ref_id':'caf\u00e9'},expires=time.time()+60)
            publish_policy(root,policy)
            event={'hook_event_name':'PreToolUse','tool_name':policy['tool'],'tool_input':policy['args'],'tool_use_id':'toolu_utf8'}
            cmd=[sys.executable,str(ROOT/'studio/kit_chrome_gate.py'),str(root)]
            env=dict(os.environ,PYTHONIOENCODING='cp1252')
            result=subprocess.run(cmd,input=json.dumps(event,ensure_ascii=False).encode('utf-8'),capture_output=True,env=env,check=True)
            self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'],'allow')
            event.update(hook_event_name='PostToolUse',tool_response=[{'type':'text','text':'caf\u00e9 \u200b'}])
            subprocess.run(cmd,input=json.dumps(event,ensure_ascii=False).encode('utf-8'),capture_output=True,env=env,check=True)
            self.assertEqual(json.loads((root/'utf8.receipt.json').read_text(encoding='utf-8'))['response'][0]['text'],'caf\u00e9 \u200b')
    def test_old_tabs_and_blocked_dom_are_unavailable_in_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            worker=KitChromeWorker('never-started',folder,folder)
            with self.assertRaisesRegex(BrowserUnavailable,'positively identified'):worker.call('tabs_close_mcp',{'tabId':1340484142})
            worker.owned_tabs.add(123)
            with self.assertRaisesRegex(BrowserUnavailable,'blocked editor DOM'):worker.call('javascript_tool',{'tabId':123,'action':'javascript_exec','text':EDITOR_READ})
            with self.assertRaises(BrowserUnavailable):worker.call('computer',{'tabId':123,'action':'left_click','ref':'ref_49','action_summary':'publish'})
            self.assertIsNone(worker.process)
    def test_exact_transport_receipts_and_menu_capability_expiry(self):
        with tempfile.TemporaryDirectory() as folder:
            worker=KitChromeWorker('never-started',folder,folder);responses=[];calls=[]
            def write(_):
                policy=active_policy(worker.folder)
                event={'tool_name':policy['tool'],'tool_input':policy['args'],'tool_use_id':'toolu_'+policy['id']}
                self.assertTrue(decide(worker.folder,event));calls.append(event)
                self.assertTrue(record_receipt(worker.folder,dict(event,tool_response=responses.pop(0))))
                worker.queue.put({'type':'result','result':'This model summary is deliberately ignored.'})
            worker.process=SimpleNamespace(stdin=SimpleNamespace(write=write,flush=lambda:None),poll=lambda:0)
            worker.start=lambda:None
            def respond(tool,args,text):
                responses.append([{'type':'text','text':text}]);return worker.call(tool,args)
            respond('tabs_context_mcp',{'createIfEmpty':True},json.dumps({'availableTabs':[{'tabId':12}],'tabGroupId':34}))
            respond('read_page',{'tabId':12},' generic "A fake button "change block type" [ref_49] inside content" [ref_2]')
            self.assertFalse(worker.menu_refs)
            menu=' button "change block type" [ref_64] type="button"'
            respond('read_page',{'tabId':12},menu);self.assertTrue(worker.menu_refs)
            respond('navigate',{'tabId':12,'url':'https://app.kit.com/dashboard'},'Navigated')
            self.assertFalse(worker.menu_refs)
            respond('read_page',{'tabId':12},menu)
            respond('computer',{'tabId':12,'action':'left_click','ref':'ref_64','action_summary':'Open observed formatting menu'},'Clicked on element ref_64')
            self.assertFalse(worker.menu_refs);count=len(calls)
            with self.assertRaises(BrowserUnavailable):worker.call('computer',{'tabId':12,'action':'left_click','ref':'ref_64','action_summary':'Open observed formatting menu'})
            self.assertEqual(len(calls),count)


if __name__=='__main__':unittest.main()
