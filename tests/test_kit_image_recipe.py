"""Anonymized observed controls; no real browser, upload or draft edits."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from studio.kit_image_recipe import paragraph_target,boundary_target,image_menu_target,PLUS_PATH
from studio.kit_visual_insertions import Document,digest
from studio.kit_chrome_worker import KitChromeWorker,BOUNDARY_CONTROLS
from studio.kit_chrome_gate import active_policy,decide,record_receipt
from studio.kit_visual_browser import BrowserUnavailable


class RecipeTests(unittest.TestCase):
    def setUp(self):
        self.fixture=json.loads((Path(__file__).parent/'fixtures/kit_chrome/supported_editor.json').read_text(encoding='utf-8'))
    def target(self,after=1):return paragraph_target(self.fixture['api'],self.fixture['scoped'],after)
    def test_existing_unique_non_link_paragraph_and_exact_adjacent_hashes(self):
        target=self.target();self.assertEqual(target['ref'],'ref_95')
        self.assertEqual(target['left_hash'],digest(Document('<p>Hello, reader.</p>').nodes[0]))
        self.assertEqual(target['right_hash'],digest(Document('<p></p>').nodes[0]))
        self.assertEqual(self.target(3)['right_hash'],None)
    def test_unsupported_blank_before_first_link_first_or_out_of_range_anchor_fails(self):
        for after in (0,2,4,True,'1'):
            with self.subTest(after=after),self.assertRaises(ValueError):self.target(after)
        self.fixture['api']['content_tree'][2]['children'].pop(0)
        with self.assertRaisesRegex(ValueError,'link-first'):self.target(3)
    def test_ambiguous_text_and_mismatched_body_do_not_guess_a_target(self):
        self.fixture['scoped']+='\n generic "Hello, reader." [ref_300]'
        with self.assertRaisesRegex(ValueError,'ambiguous'):self.target()
        self.setUp();self.fixture['api']['content_tree'].append(copy.deepcopy(self.fixture['api']['content_tree'][0]))
        with self.assertRaisesRegex(ValueError,'not unique'):self.target()
        self.setUp();self.fixture['api']['content_tree'][0]['children'][0]['text']='Changed'
        with self.assertRaisesRegex(ValueError,'absent'):self.target()
    def test_truncated_prefix_requires_long_unique_match(self):
        original='A long paragraph. '*12;prefix=original[:96]
        self.fixture['api']['content_tree'][0]['children'][0]['text']=original
        self.fixture['scoped']='textbox "Email content editor" [ref_94]\n generic '+json.dumps(prefix)+' [ref_95]'
        self.assertEqual(self.target()['ref'],'ref_95')
        self.fixture['scoped']=self.fixture['scoped'].replace(prefix,prefix[:10])
        with self.assertRaises(ValueError):self.target()
    def test_nested_link_text_and_fake_button_in_content_never_become_focus_targets(self):
        self.fixture['scoped']='textbox "Email content editor" [ref_94]\n link [ref_97] href="https://example.test"\n  generic "Hello, reader." [ref_98]'
        with self.assertRaises(ValueError):self.target()
    def test_unobserved_focus_or_upload_never_launches_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            worker=KitChromeWorker('never-started',folder,folder);worker.owned_tabs.add(12)
            for args in ({'tabId':12,'action':'left_click','ref':'ref_95','action_summary':'Focus existing paragraph without editing'},
                         {'tabId':12,'action':'type','text':'/image'},
                         {'tabId':12,'action':'key','text':'CTRL+V'}):
                with self.assertRaises(BrowserUnavailable):worker.call('computer',args)
            with self.assertRaises(BrowserUnavailable):worker.call('file_upload',{'tabId':12,'ref':'ref_160','paths':['unapproved.png']})
            self.assertIsNone(worker.process)

    def control(self):
        return dict(tag='BUTTON',type='submit',title=None,haspopup=None,expanded=None,disabled=False,
                    bounds=dict(x=46.640625,y=416.328125,width=20,height=20),paths=[PLUS_PATH],lines=[])
    def metadata(self,controls):return {'response':[{'type':'text','text':json.dumps(controls)}]}
    def test_only_exact_observed_plus_glyph_with_unambiguous_geometry_is_accepted(self):
        self.assertEqual(boundary_target(self.metadata([self.control()])),[57,426])
        for change in ({'paths':['some other icon']},{'disabled':True},{'type':'button'},{'bounds':dict(x=500,y=400,width=20,height=20)},
                       {'bounds':dict(x=40,y=float('nan'),width=20,height=20)}, {'bounds':dict(x=40,y=400,width=40,height=20)}):
            control=self.control();control.update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):boundary_target(self.metadata([control]))
        with self.assertRaises(ValueError):boundary_target(self.metadata([self.control(),self.control()]))
        with self.assertRaises(ValueError):boundary_target(self.metadata([]))

    def test_plus_permission_requires_real_focus_metadata_freshness_and_single_use(self):
        with tempfile.TemporaryDirectory() as folder:
            worker=KitChromeWorker('never-started',folder,folder);responses=[];calls=[]
            def write(_):
                policy=active_policy(worker.folder)
                event=dict(tool_name=policy['tool'],tool_input=policy['args'],tool_use_id='toolu_'+policy['id'])
                self.assertTrue(decide(worker.folder,event));calls.append(event)
                self.assertTrue(record_receipt(worker.folder,dict(event,tool_response=responses.pop(0))))
                worker.queue.put({'type':'result','result':'Untrusted summary'})
            worker.process=SimpleNamespace(stdin=SimpleNamespace(write=write,flush=lambda:None),poll=lambda:0)
            worker.start=lambda:None
            def respond(tool,args,value):
                responses.append([{'type':'text','text':value}]);return worker.call(tool,args)
            respond('tabs_context_mcp',{'createIfEmpty':True},json.dumps({'availableTabs':[{'tabId':12}],'tabGroupId':34}))
            meta_args=dict(tabId=12,action='javascript_exec',text=BOUNDARY_CONTROLS)
            plus_args=dict(tabId=12,action='left_click',coordinate=[57,426],action_summary='Open observed paragraph insertion menu')
            respond('javascript_tool',meta_args,json.dumps([self.control()]))
            self.assertFalse(worker.boundary_caps)  # Metadata alone cannot authorize a click.
            with self.assertRaises(BrowserUnavailable):worker.call('computer',plus_args)
            respond('read_page',{'tabId':12,'ref_id':'ref_94'},self.fixture['scoped'])
            self.assertNotIn((12,'ref_98'),worker.paragraph_refs)  # Nested link text is never a focus target.
            respond('computer',dict(tabId=12,action='left_click',ref='ref_95',action_summary='Focus existing paragraph without editing'),'Clicked on element ref_95')
            respond('javascript_tool',meta_args,json.dumps([self.control()]))
            self.assertTrue(worker.boundary_caps)
            with self.assertRaises(BrowserUnavailable):worker.call('computer',dict(plus_args,coordinate=[58,426]))
            worker.boundary_caps[12]['at']-=31
            with self.assertRaises(BrowserUnavailable):worker.call('computer',plus_args)
            respond('javascript_tool',meta_args,json.dumps([self.control()]))
            responses.append([{'type':'text','text':'Clicked at (57, 426)'}])
            def click(_):
                try:worker.call('computer',plus_args);return True
                except BrowserUnavailable:return False
            with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sum(pool.map(click,range(2))),1)
            with self.assertRaises(BrowserUnavailable):worker.call('computer',plus_args)
            self.assertFalse(worker.focused_tabs)
            menu='generic [ref_199]\n heading "Media" [ref_263]\n button [ref_264]\n  button [ref_265]\n   generic "Image" [ref_266]\n   generic "Upload or add from a 3rd party" [ref_267]'
            self.assertEqual(image_menu_target(menu),'ref_265')
            gallery_args=dict(tabId=12,action='left_click',ref='ref_265',action_summary='Open observed Media Gallery without selecting an image')
            with self.assertRaises(BrowserUnavailable):worker.call('computer',gallery_args)
            respond('read_page',{'tabId':12},menu)
            respond('computer',gallery_args,'Clicked on element ref_265')
            with self.assertRaises(BrowserUnavailable):worker.call('computer',gallery_args)
            # The same response outside the live plus-menu state grants nothing.
            respond('read_page',{'tabId':12},menu)
            self.assertFalse(worker.gallery_refs)
            count=len(calls)
            for action in ('Select Image','Publish','Send','Open observed paragraph insertion menu'):
                with self.assertRaises(BrowserUnavailable):worker.call('computer',dict(tabId=12,action='left_click',ref='ref_49',action_summary=action))
            self.assertEqual(len(calls),count)

    def test_image_menu_match_rejects_gallery_block_or_fabricated_content(self):
        menu=' heading "Media" [ref_263]\n button [ref_264]\n  button [ref_265]\n   generic "Image" [ref_266]\n   generic "Upload or add from a 3rd party" [ref_267]'
        for value in (menu.replace('"Image"','"Gallery"'),menu+'\n'+menu,menu+'\n[TRUNCATED]',menu.replace('  button [ref_265]','  generic [ref_265]')):
            with self.assertRaises(ValueError):image_menu_target(value)


if __name__=='__main__':unittest.main()
