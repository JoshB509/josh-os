"""Kit-shaped local state fixture. No browser process, Kit call or live upload."""
import copy,hashlib,io,json,tempfile,unittest,uuid,struct
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from tests.test_kit_visual_insertions import InsertionTests
from studio.kit_auto_image import KitAutoImageWorker,KitAutoImageRecipe
from studio.kit_chrome_upload import stage_session_assets
from studio.kit_chrome_gate import active_policy,decide,record_receipt
from studio.kit_image_recipe import PLUS_PATH,image_menu_target
from studio.kit_editor_observation import tree_body
from studio.kit_visual_browser import Snapshot,BrowserUnavailable
from studio.kit_visual_insertions import META,Document,OwnerReview,ACTION
from studio.store import now


class KitUIFixture:
    def __init__(self,f,count=1):
        self.f=f;self.service=f.service;self.root=f.root;self.stage='editor';self.calls=[];self.workers=[];self.failure=None
        self.meta=copy.deepcopy(f.browser.meta);self.tree=[dict(type='paragraph',children=[dict(text=t)]) for t in f.fresh()['body'].splitlines()]
        self.saved=True;self.ordinal=0;self.command=None;self.uploads=0;self.reloads=0;self.service.browser=self
    def readiness(self):return dict(available=True,methods=['media_gallery_auto'],reason='ISOLATED TEST FIXTURE ONLY')
    def api(self):return dict(copy.deepcopy(self.meta),content_tree=copy.deepcopy(self.tree),content_tree_is_clean=True,content_tree_warnings=[])
    def inspect(self,bid,*,reload):
        api=self.api();body=tree_body(api,allow_pending_caption=True)
        return Snapshot(uuid.uuid4().hex,now(),f'https://app.kit.com/campaigns/{bid}/draft',reload,copy.deepcopy(self.meta),body['html'],
                        {i['src']:i['resource_id'] for i in body['images']},api)
    def selected(self):return self.tree[self.command.anchor['after_line']+self.command.ordinal]
    def page(self,scoped=False):
        if self.failure=='locked':return 'This broadcast is locked\nbutton "Take over editing" [ref_666]'
        if self.stage=='menu':return 'button "Image Upload or add from a 3rd party" [ref_70]\n button "Image Upload or add from a 3rd party" [ref_71]'
        if self.stage=='gallery':return 'dialog "Media Gallery" [ref_80]\n button "Choose File" [ref_81] type="file"'
        editor='textbox "Email content editor" [ref_1]\n'
        for i,n in enumerate(self.tree):
            editor+=(' generic '+json.dumps(n['children'][0]['text'])+f' [ref_{100+i}]\n') if n['type']=='paragraph' else ' image '+json.dumps(n['alt'][:96])+f' [ref_{100+i}]\n'
        if scoped:return editor
        if self.stage in ('inserted','accessibility'):
            checked='checked' if self.selected()['showCaption'] else 'unchecked'
            editor+=f'switch "Show caption" [ref_90] [{checked}]\nbutton "Accessibility" [ref_91]\n'
            if self.stage=='accessibility':editor+='textbox "Describe this image for screen readers" [ref_92]\n'
        return editor+'generic "Saved" [ref_99]'
    def send(self,worker,raw):
        policy=active_policy(worker.folder);tool=policy['tool'].rsplit('__',1)[-1];args=policy['args'];eid=uuid.uuid4().hex
        event=dict(tool_name=policy['tool'],tool_input=args,tool_use_id=eid)
        assert decide(worker.folder,event);assert not decide(worker.folder,event)
        self.calls.append((tool,copy.deepcopy(args)))
        if tool=='read_page':text=self.page(scoped=args.get('ref_id')=='ref_1')
        elif tool=='get_page_text':text='Title: Kit\nURL: '+self.command.destination+'\nVisible fixture body'
        elif tool=='javascript_tool':text=json.dumps([dict(tag='BUTTON',type='submit',paths=[PLUS_PATH],lines=[],disabled=False,title=None,haspopup=None,bounds=dict(x=50,y=200,width=20,height=20))])
        elif tool=='computer':
            if args['action']=='key':text='Pressed '+args['text']
            else:
                ref=args.get('ref')
                if 'coordinate' in args:self.stage='menu'
                elif ref=='ref_71':self.stage='gallery'
                elif ref=='ref_90':self.selected()['showCaption']=False
                elif ref=='ref_91':self.stage='accessibility'
                text='Clicked on element '+ref if ref else 'Clicked at ('+', '.join(map(str,args['coordinate']))+')'
        elif tool=='file_upload':
            assert self.stage=='gallery' and args['ref']=='ref_81' and len(args['paths'])==1
            assert hashlib.sha256(Path(args['paths'][0]).read_bytes()).hexdigest()==self.command.png_hash
            self.uploads+=1;self.stage='inserted'
            self.tree.insert(self.command.anchor['after_line']+self.command.ordinal,dict(type='image',fullWidth=False,
                src='https://embed.filekitcdn.com/e/FixtureAccount/'+self.command.png_hash+'/email',showCaption=True,alt=''))
            if self.failure=='duplicate':self.tree.insert(self.command.anchor['after_line'],copy.deepcopy(self.selected()))
            if self.failure=='unknown_upload':raise OSError('Unknown autosave result')
            text='Uploaded original PNG; auto-inserted in this local fixture'
        elif tool=='form_input':
            assert args['ref']=='ref_92' and args['value']==self.command.alt
            self.selected()['alt']=args['value'];text='Updated input'
        else:raise AssertionError('Unexpected tool: '+tool)
        record_receipt(worker.folder,dict(event,tool_response=[dict(type='text',text=text)]));worker.queue.put(dict(type='result'))
    def observe(self,worker,tab,command,*,reload):
        if reload:self.reloads+=1
        if self.failure=='body_change' and self.uploads:self.tree[0]['children'][0]['text']='Unexpected rewrite'
        if self.failure=='scheduled' and self.uploads:self.meta['send_at']='2099-01-01T00:00:00Z'
        page=worker.call('read_page',dict(tabId=tab,max_chars=50000))
        editor=worker.call('read_page',dict(tabId=tab,ref_id='ref_1',max_chars=50000)) if self.stage!='gallery' else page
        identity=worker.call('get_page_text',dict(tabId=tab));snap=self.inspect(555,reload=reload)
        o=dict(snapshot=snap,page_id=page['policy_id'],editor_id=editor['policy_id'],identity_id=identity['policy_id'],gallery_id=page['policy_id'],saved=self.saved)
        if self.uploads>command.ordinal:
            im=self.selected();v=self.service.get(command.packet_id)['packet']['assets'][command.ordinal]['visual']
            width,height=struct.unpack('>II',worker.recipe['png'][16:24])
            o['selected_image']=dict(src=im['src'],alt=im['alt'],url='',loaded=True,width=width,height=height)
            if self.failure=='wrong_dimensions':o['selected_image']['width']=1
            if self.failure=='image_link':o['selected_image']['url']='https://unapproved.example/'
        return o
    def insert_image(self,command,png,before_mutation,record_upload):
        self.command=command;self.stage='editor'
        worker=KitAutoImageWorker('never-run',self.root,self.root/'audit',service=self.service,packet_id=command.packet_id,
                                  shared_directory=stage_session_assets(self.service,command.packet_id),sharing_evidence_id='local-fixture')
        self.workers.append(worker);worker.owned_tabs={12};worker.start=lambda:None
        worker.process=SimpleNamespace(stdin=SimpleNamespace(write=lambda raw:self.send(worker,raw),flush=lambda:None),poll=lambda:0)
        KitAutoImageRecipe(self.service,self.observe).run(worker,12,command,png,before_mutation,record_upload)


class AutoRecipeTests(unittest.TestCase):
    def fixture(self,count=1):
        f=InsertionTests();f.setUp();self.addCleanup(f.doCleanups);self.addCleanup(f.tearDown)
        if count>=1:
            p=f.fresh();f.emails.save(p['id'],dict(body='\n\n'.join('Distinct paragraph '+str(i) for i in range(count+1)),images=[]),p['revision'],sync_remote=False)
            def render(slides,folder):
                for i in range(len(slides)):Image.new('RGB',(640,360),(i*25,40,90)).save(folder/f'slide_{i+1:02d}.png')
            p=f.fresh()
            with patch.object(f.emails.visuals,'render',side_effect=render):
                f.emails.visuals.generate(dict(email_id=p['id'],revision=p['revision'],visuals=[dict(lines=['Exact image '+str(i)],alt='Exact full alt '+str(i),after_line=2*i+1,width=640,height=360) for i in range(count)]))
        ui=KitUIFixture(f,count);row=f.prepare(method='media_gallery_auto');row=f.approve(row)
        return f,ui,row
    def test_complete_auto_insert_caption_alt_saved_reload_for_eight(self):
        f,ui,row=self.fixture(8);out=f.service.execute(row['id'],row['digest'])
        self.assertEqual(out['state'],'verified_complete');self.assertEqual(ui.uploads,8);self.assertEqual(ui.reloads,8)
        images=[n for n in ui.tree if n['type']=='image'];self.assertEqual([n['alt'] for n in images],[a['visual']['alt'] for a in row['packet']['assets']])
        self.assertTrue(all(n['showCaption'] is False for n in images))
        self.assertEqual([a['text'] for t,a in ui.calls if t=='computer' and a['action']=='key'],['End','ArrowDown']*8)
        self.assertEqual([e['body']['evidence']['phase'] for e in f.service.events(row['id']) if e['event']=='ui_phase_authorized'],['upload_insert']*8)
        self.assertFalse(any('send' in json.dumps(a).lower() or 'take over' in json.dumps(a).lower() for t,a in ui.calls))
    def test_unknown_upload_stops_without_any_retry_or_alt(self):
        f,ui,row=self.fixture();ui.failure='unknown_upload'
        with self.assertRaises(ValueError):f.service.execute(row['id'],row['digest'])
        with self.assertRaises(ValueError):f.service.execute(row['id'],row['digest'])
        self.assertEqual(ui.uploads,1);self.assertFalse(any(t=='form_input' for t,a in ui.calls))
    def test_lock_stops_before_upload(self):
        f,ui,row=self.fixture();ui.failure='locked'
        with self.assertRaises(ValueError):f.service.execute(row['id'],row['digest'])
        self.assertEqual(ui.uploads,0)
    def test_pending_image_changes_stop_before_configuration(self):
        for failure in ('body_change','scheduled','wrong_dimensions','image_link','duplicate'):
            with self.subTest(failure=failure):
                f,ui,row=self.fixture();ui.failure=failure
                with self.assertRaises(ValueError):f.service.execute(row['id'],row['digest'])
                self.assertEqual(ui.uploads,1);self.assertFalse(any(t=='form_input' for t,a in ui.calls))
    def test_bound_control_not_granted_by_general_worker_or_out_of_order(self):
        f,ui,row=self.fixture();f.service.execute(row['id'],row['digest']);worker=ui.workers[0]
        with self.assertRaises(ValueError):worker.control('alt',page_id='untrusted')
        with self.assertRaises(ValueError):worker.call('form_input',dict(tabId=12,ref='ref_92',value='Rewrite'))
    def test_completed_api_is_never_a_draft(self):
        f,ui,row=self.fixture();ui.meta.update(status='completed',send_at='2026-11-16T15:59:16Z')
        with self.assertRaises(ValueError):f.service.execute(row['id'],row['digest'])
        self.assertEqual(ui.uploads,0)


class ObservedSchemaTests(unittest.TestCase):
    def test_actual_image_schema_and_caption_enforcement(self):
        image=dict(type='image',fullWidth=False,src='https://embed.filekitcdn.com/e/Account/Resource/email',showCaption=False,alt='Full alt <exact>')
        api=dict(content_tree=[image],content_tree_is_clean=True,content_tree_warnings=[])
        result=tree_body(api);self.assertEqual(result['images'][0]['alt'],image['alt'])
        for change in [dict(showCaption=True),dict(url='https://x.test/'),dict(fullWidth=True),dict(src='https://unapproved.example/image.png')]:
            with self.subTest(change=change),self.assertRaises(ValueError):tree_body(dict(api,content_tree=[dict(image,**change)]))
    def test_nested_menu_uses_inner_unique_button(self):
        self.assertEqual(image_menu_target('button "Image Upload or add from a 3rd party" [ref_1]\n button "Image Upload or add from a 3rd party" [ref_2]'),'ref_2')
        with self.assertRaises(ValueError):image_menu_target('button "Image Upload or add from a 3rd party" [ref_1]\nbutton "Image Upload or add from a 3rd party" [ref_2]')

if __name__=='__main__':unittest.main()
