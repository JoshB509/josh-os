"""Tests use captured supported Chrome-tool responses, never a live account."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest

from studio.kit_chrome_gate import decide,publish_policy
from studio.kit_chrome_worker import KitChromeWorker,PREFIX,EDITOR_READ
from studio.kit_chrome_browser import KitChromeBrowser,objects
from studio.kit_visual_browser import BrowserUnavailable
from studio.kit_visual_insertions import Document

FIXTURES=Path(__file__).parent/'fixtures/kit_chrome'


def fixture(name):return json.loads((FIXTURES/(name+'.json')).read_text(encoding='utf-8'))


class RecordedWorker:
    """Replays actual supported response envelopes; all mutation attempts fail."""
    def __init__(self,folder,mode):self.folder=folder;self.mode=mode;self.closed=False;self.calls=[]
    def __enter__(self):return self
    def __exit__(self,*args):self.closed=True
    def call(self,tool,args,**kw):
        self.calls.append((tool,args))
        if tool=='tabs_context_mcp':return fixture('context')
        if tool=='read_page':
            if self.mode=='locked':return fixture('locked')
            if 'ref_id' in args and self.mode=='blocked':return fixture('dom_blocked')
            if self.mode=='menu' and any(t=='computer' and a.get('action_summary')=='Open observed paragraph insertion menu' for t,a in self.calls):
                return {'response':[{'type':'text','text':fixture('image_menu')['menu']}]}
            return {'response':[{'type':'text','text':'textbox "Email content editor" [ref_148]\n generic "Fixture" [ref_149]'}]}
        if tool=='javascript_tool' and self.mode=='menu':
            return {'response':[{'type':'text','text':json.dumps(fixture('image_menu')['button_metadata'])}]}
        if tool=='computer' and self.mode=='menu':
            if args.get('action_summary') not in ('Focus existing paragraph without editing','Open observed paragraph insertion menu'):
                raise AssertionError('Image selection, typing, uploads and other clicks remain unavailable')
            return {'response':[{'type':'text','text':'Observed non-mutating control clicked'}]}
        if tool=='get_page_text':return {'response':[{'type':'text','text':'Title: Fixture - Kit\nURL: https://app.kit.com/campaigns/555/draft\nSource element: <main>\n---\nSubject Line\nSuggest subject lines\nAB test toggle\nPreview Text\n7/150\n\nAppears after the subject line in the inbox.\n\nFixture\n\nUnsubscribe | Update your profile | Test address\n\n1 words'}]}
        if tool not in ('select_browser','navigate','tabs_close_mcp'):raise AssertionError('Unsupported browser action')
        return {'response':[{'type':'text','text':'Supported tool call completed'}]}


class ChromeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name)
    def browser(self,mode):
        self.worker=RecordedWorker(self.folder,mode)
        self.api=dict(id=555,status='draft',public=False,send_at=None,published_at=None,public_url=None,subject='Fixture',
                      preview_text='Preview',subscriber_filter=[],content='<p>Fixture</p>',content_tree_is_clean=True,content_tree_warnings=[],
                      content_tree=[{'type':'paragraph','children':[{'text':'Fixture'}]}])
        return KitChromeBrowser(self.folder,self.folder,lambda bid:copy.deepcopy(self.api),device_id='fixture-device',worker_factory=lambda:self.worker)
    def test_actual_lock_response_stops_before_dom_or_any_mutation_and_releases_tab(self):
        browser=self.browser('locked')
        with self.assertRaisesRegex(BrowserUnavailable,'locked'):browser.inspect(555,reload=True)
        self.assertNotIn('javascript_tool',[t for t,a in self.worker.calls])
        self.assertEqual(self.worker.calls[-1][0],'tabs_close_mcp')
        self.assertIn('locked',browser.readiness()['reason']);self.assertFalse(browser.readiness()['available'])
    def test_actual_dom_protection_response_is_never_treated_as_content(self):
        browser=self.browser('blocked')
        with self.assertRaisesRegex(BrowserUnavailable,'body read was denied'):browser.inspect(555,reload=True)
        self.assertEqual(self.worker.calls[-1][0],'tabs_close_mcp')
        self.assertNotIn('get_page_text',[t for t,a in self.worker.calls])
        with self.assertRaises(BrowserUnavailable):browser.insert_image(None)
        self.assertNotIn('file_upload',[t for t,a in self.worker.calls])
    def test_private_unscheduled_identity_preflight_before_browser(self):
        for change in ({'public':True},{'status':'scheduled'},{'send_at':'tomorrow'},{'id':556}):
            browser=self.browser('locked');self.api.update(change)
            with self.assertRaises(BrowserUnavailable):browser.inspect(555,reload=True)
            self.assertEqual(self.worker.calls,[])
    def test_readable_dom_fixture_separates_content_from_approval_metadata(self):
        browser=self.browser('synthetic-readable');snap=browser.inspect(555,reload=True)
        self.assertEqual(snap.content_html,'<p>Fixture</p>');self.assertNotIn('content',snap.metadata)
        self.assertEqual(snap.api_broadcast['content'],snap.content_html);self.assertFalse(browser.readiness()['available'])
        self.assertEqual(self.worker.calls[-1][0],'tabs_close_mcp')
        self.assertNotIn('javascript_tool',[t for t,a in self.worker.calls])
    def test_observed_menu_recipe_binds_anchor_and_closes_without_selecting_image(self):
        browser=self.browser('menu');out=browser.inspect_image_menu(555,after_line=1)
        self.assertTrue(out['menu_observed']);self.assertFalse(out['media_gallery_observed']);self.assertFalse(out['upload_available'])
        self.assertEqual(out['image_entry_ref'],'ref_265');self.assertEqual(out['anchor']['right_hash'],None)
        clicks=[args for tool,args in self.worker.calls if tool=='computer']
        self.assertEqual(len(clicks),2);self.assertEqual(clicks[0]['ref'],'ref_149');self.assertEqual(clicks[1]['coordinate'],[57,426])
        self.assertEqual(self.worker.calls[-1][0],'tabs_close_mcp');self.assertFalse(browser.readiness()['available'])
    def test_menu_recipe_rejects_invalid_anchor_and_remote_changes(self):
        browser=self.browser('menu')
        with self.assertRaises(ValueError):browser.inspect_image_menu(555,after_line=2)
        self.assertNotIn('computer',[t for t,a in self.worker.calls]);self.assertEqual(self.worker.calls[-1][0],'tabs_close_mcp')
        browser=self.browser('menu');reads=[]
        def read(_):
            reads.append(1);value=copy.deepcopy(self.api)
            if len(reads)>1:value['subject']='Changed independently'
            return value
        browser.read_broadcast=read
        with self.assertRaisesRegex(BrowserUnavailable,'changed during inspection'):browser.inspect_image_menu(555,after_line=1)
        self.assertEqual(self.worker.calls[-1][0],'tabs_close_mcp')
    def test_reader_uses_supported_tab_context_not_assistant_claims(self):
        item=list(objects(fixture('context')))[0]
        self.assertEqual(len(item['availableTabs']),1);self.assertIs(type(item['availableTabs'][0]['tabId']),int)
        self.assertEqual(list(objects({'response':[{'type':'text','text':'I successfully uploaded it.'}]})),[])
    def test_exact_call_gate_is_single_use_and_rejects_changed_tool_arguments(self):
        policy=dict(id='test',tool=PREFIX+'read_page',args={'tabId':123},expires=time.time()+60)
        publish_policy(self.folder,policy)
        for bad in ({'tool_name':PREFIX+'computer','tool_input':{'action':'left_click','tabId':123,'ref':'ref_49'}},
                    {'tool_name':policy['tool'],'tool_input':{'tabId':124}},
                    {'tool_name':'Bash','tool_input':{'command':'anything'}},
                    {'tool_name':'ToolSearch','tool_input':{'query':'send broadcast'}}):
            self.assertFalse(decide(self.folder,bad))
        event={'tool_name':policy['tool'],'tool_input':policy['args'],'tool_use_id':'toolu_test'}
        self.assertTrue(decide(self.folder,event));self.assertFalse(decide(self.folder,event))
    def test_transport_has_no_upload_clipboard_send_schedule_or_generic_js_call(self):
        worker=KitChromeWorker('never-launched',self.folder,self.folder)
        for tool in ('computer','file_upload','form_input','upload_image','send','schedule','update_broadcast'):
            with self.assertRaises(BrowserUnavailable):worker.call(tool,{})
        for args in ({'url':'https://evil.example'}, {'url':'https://app.kit.com/editor/starting-points'}):
            with self.assertRaises(BrowserUnavailable):worker.call('navigate',args)
        with self.assertRaises(BrowserUnavailable):worker.call('javascript_tool',{'action':'javascript_exec','text':'document.cookie'})
        self.assertIsNone(worker.process)
    def test_wrapper_normalization_keeps_exact_text_links_marks_and_boundaries(self):
        plain='<p>Hello <a href="https://example.com/?x=1">world</a>.</p><p>Next</p>'
        wrapped='<div><p><span>Hello </span><a href="https://example.com/?x=1"><span>world</span></a>.</p><p>Next</p></div>'
        self.assertEqual(Document(plain).nodes,Document(wrapped).nodes)
        for changed in (plain.replace('x=1','x=2'),plain.replace('Hello','HELLO'),plain.replace('</p><p>','<br>'),
                        plain.replace('<p>','<p style="display:none">',1),plain.replace('world','<strong>world</strong>')):
            self.assertNotEqual(Document(plain).nodes,Document(changed).nodes)
        self.assertEqual(Document('<p><b>bold</b></p>').nodes,Document('<p><strong>bold</strong></p>').nodes)


if __name__=='__main__':unittest.main()
