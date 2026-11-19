"""Local fixtures only. No app, real draft, credential, connector or Kit write is used."""
import copy
import ast
import io
import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
import urllib.parse
from unittest.mock import patch

from PIL import Image
from studio.agent import Agents, EMAIL_PROPS, show_only
from studio.email_visuals import safe_svg, LOCAL_ONLY
from studio.kit import content_hash, body_html
from studio.store import Conflict
from studio.team import Team
from studio.carousels import Carousels
from tests.test_kit_drafts import Base, AUD

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 260"><rect x="20" y="20" width="200" height="180" fill="#F5C21B"/><text x="35" y="110" font-size="28">Explain impact</text></svg>'
VISUAL = dict(lines=['Make your work visible', 'Connect the work to its impact.'], svg=SVG,
              alt='A yellow box labels the impact of your work.', after_line=1)


class VisualTests(Base):
    def setUp(self):
        super().setUp()
        out=io.BytesIO();Image.new('RGB',(1080,1080),'#F6F5F1').save(out,format='PNG');self.png=out.getvalue()
        self.renderer=patch.object(self.emails.visuals,'render',side_effect=self.render)
        self.renderer.start();self.addCleanup(self.renderer.stop)
        self.network=patch('urllib.request.urlopen',side_effect=AssertionError('No network allowed'))
        self.network.start();self.addCleanup(self.network.stop)

    def render(self,slides,folder):
        for i in range(len(slides)):(folder/f'slide_{i+1:02d}.png').write_bytes(self.png)

    def request(self,**changes):
        return dict(email_id=self.email['id'],revision=self.fresh()['revision'],visuals=[copy.deepcopy(VISUAL)],**changes)

    def generate(self):
        return self.emails.visuals.generate(self.request())

    def test_immutable_versions_history_download_and_no_carousel_mutation(self):
        carousels=Carousels(self.store)
        carousels.create({'title':'Sam carousel'})
        carousel_before=carousels.all()
        before=self.fresh();first=self.generate();second=self.generate()
        a,b=first['images'][0],second['images'][0]
        self.assertNotEqual(a['version'],b['version'])
        self.assertEqual(a['source_hash'],b['source_hash'])
        self.assertEqual(self.emails.visuals.read_png(first['id'],a['version'],'1'),self.png)
        self.assertEqual(self.emails.visuals.read_png(second['id'],b['version'],'1'),self.png)
        self.assertEqual(len(self.emails.history(first['id'])),3)
        self.assertEqual(first['body'],before['body'])
        self.assertEqual(second['revision'],before['revision']+2)
        self.assertEqual(second['approval_status'],'Pending final approval')
        self.assertEqual(carousels.all(),carousel_before)

    def test_preexisting_conflict_never_renders(self):
        request=self.request();self.generate()
        with patch.object(self.emails.visuals,'render') as render:
            with self.assertRaises(Conflict):self.emails.visuals.generate(request)
            render.assert_not_called()

    def test_edit_during_render_wins_and_orphan_is_not_downloadable(self):
        first=self.generate();folders=[]
        def concurrent(slides,folder):
            folders.append(folder);self.render(slides,folder)
            self.emails.save(first['id'],{'subject':'Concurrent edit'},first['revision'],sync_remote=False)
        with patch.object(self.emails.visuals,'render',side_effect=concurrent):
            with self.assertRaises(Conflict):self.generate()
        self.assertEqual(self.fresh()['images'],first['images'])
        self.assertEqual(self.fresh()['subject'],'Concurrent edit')
        with self.assertRaises(KeyError):self.emails.visuals.read_png(first['id'],folders[0].name,'1')

    def test_failed_or_partial_render_preserves_last_good(self):
        first=self.generate()
        for effect in (ValueError('failed'),lambda slides,folder:None):
            with patch.object(self.emails.visuals,'render',side_effect=effect):
                with self.assertRaises(ValueError):self.generate()
            self.assertEqual(self.fresh(),first)

    def test_changed_alt_position_image_and_body_make_approval_stale(self):
        proposal=self.drafts.propose(self.fresh(),AUD)
        self.drafts.approve(proposal['id'],proposal['hash'],self.fresh())
        first=self.generate();original=content_hash(first)
        self.assertFalse(self.drafts.check(self.drafts.get(proposal['id']),first)[0])
        for key,value in [('alt','Another description'),('after_line',0)]:
            old=self.fresh();images=copy.deepcopy(old['images']);images[0][key]=value
            edited=self.emails.save(old['id'],{'images':images},old['revision'],sync_remote=False)
            self.assertNotEqual(content_hash(old),content_hash(edited))
        new=self.generate();self.assertNotEqual(original,content_hash(new))
        # Removing visuals still requires a new approval: old proposal revision is obsolete.
        empty=self.emails.save(new['id'],{'images':[]},new['revision'],sync_remote=False)
        self.assertFalse(self.drafts.check(self.drafts.get(proposal['id']),empty)[0])
        self.assertNotEqual(self.drafts.propose(empty,AUD)['email_revision'],proposal['email_revision'])

    def test_no_kit_proposal_or_html_with_local_images_even_if_url_is_supplied(self):
        email=self.generate()
        for make in (lambda:self.drafts.propose(email,AUD),lambda:body_html(email)):
            with self.assertRaisesRegex(ValueError,'local visuals'):make()
        self.assertEqual(self.drafts.for_email(email['id']),[])
        email['images'][0]['url']='https://work.career-os.ai/secret.png'
        with self.assertRaisesRegex(ValueError,'local visuals'):body_html(email)

    def test_paths_urls_cross_email_assets_and_forged_hashes_rejected(self):
        first=self.generate();image=first['images'][0]
        for bad in ('../x','..%2Fx','C:\\secret','https://example.test/image','../'+image['version']):
            with self.assertRaises(ValueError):self.emails.visuals.read_png(first['id'],bad,'1')
        for bad in ('../1','0','01','17','1.png'):
            with self.assertRaises(ValueError):self.emails.visuals.read_png(first['id'],image['version'],bad)
        for key,value in [('version','../secret'),('source_hash','0'*64),('png_hash','1'*64),('index',True)]:
            images=[dict(image,**{key:value})]
            with self.assertRaises(ValueError):self.emails.save(first['id'],{'images':images},first['revision'],sync_remote=False)
        other=self.emails.create({'changes':{'subject':'Other','body':'Other'}})
        with self.assertRaises(ValueError):self.emails.save(other['id'],{'images':[image]},other['revision'],sync_remote=False)
        request=self.request();request['visuals'][0]['image']='C:\\secret.png'
        with self.assertRaises(ValueError):self.emails.visuals.generate(request)
        self.assertEqual(self.fresh(),first)

    def test_missing_alt_invalid_position_and_excessive_inputs_rejected(self):
        for key,value in [('alt',''),('after_line',3),('after_line',True),('lines',None),('lines',['QR https://example.test']),('svg','<script/>')]:
            request=self.request();request['visuals'][0][key]=value
            with self.assertRaises(ValueError):self.emails.visuals.generate(request)
        request=self.request();request['visuals']*=17
        with self.assertRaises(ValueError):self.emails.visuals.generate(request)

    def test_protected_email_and_changed_file_are_rejected(self):
        email=self.generate();v=email['images'][0]
        file=self.emails.visuals.folder(email['id'],v['version'])/'slide_01.png';file.write_bytes(b'changed')
        with self.assertRaises(ValueError):self.emails.visuals.read_png(email['id'],v['version'],'1')
        self.emails.save(email['id'],{'status':'Sent'},email['revision'],sync_remote=False)
        with self.assertRaises(ValueError):self.generate()

    def test_laia_schema_and_handler_show_only_and_same_turn_edit_conflict(self):
        self.assertFalse(EMAIL_PROPS['email_visuals']['additionalProperties'])
        agents=Agents.__new__(Agents)
        request=self.request()
        done,_=agents.email_answer(self.emails,{'email_visuals':request})
        self.assertEqual(done[0]['fields'],['images'])
        self.assertEqual(done[0]['action'],'updated')
        old=self.fresh();request=self.request()
        done,_=agents.email_answer(self.emails,{'email_visuals':request,'show_email':old['id']})
        self.assertEqual(done[0]['action'],'failed');self.assertEqual(self.fresh(),old)
        done,_=agents.email_answer(self.emails,{'email_visuals':request,'emails':[{'id':old['id'],'body':'Unsaved'}]})
        self.assertEqual(done[0]['action'],'failed');self.assertEqual(self.fresh(),old)
        answer,held=show_only({'show_email':old['id'],'email_visuals':request})
        self.assertNotIn('email_visuals',answer);self.assertIn('email_visuals',held)
        laia=next(a for a in Team(self.store).all()['agents'] if a['id']=='laia')
        self.assertNotIn('carousels',laia['workflows'])

    def test_browser_preview_placement_escaping_and_export_block(self):
        email=self.generate();email['images'][0]['alt']='" onerror="alert(1) <bad>'
        source=(Path(__file__).resolve().parents[1]/'studio/web/emails.js').read_text(encoding='utf-8')
        part=source[source.index('function emailValidHref'):source.index('function emailSubjectAlternatives')]
        program="const document={addEventListener(){}};const esc=s=>String(s).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"+part
        program+='\nconst p='+json.dumps(email)+';let blocked=false;try{emailExportHTML(p)}catch{blocked=true};console.log(JSON.stringify({preview:emailLinkedBody(p),blocked,controls:emailVisualControls(p)}));'
        out=subprocess.run(['node','-e',program],capture_output=True,text=True,check=True)
        result=json.loads(out.stdout);html=result['preview']['html']
        self.assertTrue(result['blocked']);self.assertTrue(result['preview']['valid'])
        self.assertLess(html.index('Line one.'),html.index('<figure'))
        self.assertLess(html.index('<figure'),html.index('Line two.'))
        self.assertIn('&quot; onerror=',html);self.assertNotIn('<bad>',html)
        self.assertIn('download=',result['controls'])
        self.assertNotIn('E:',html);self.assertNotIn('work.career-os.ai',html)

    def test_chat_dispatch_limits_visual_mutation_to_laia_nonproactive(self):
        agents=Agents(self.store,self.root,Team(self.store))
        agents.emails,agents.kit_drafts=self.emails,self.drafts
        seen={}
        def run(system,prompt,schema,*args,**kw):
            seen['schema']=schema
            return 'ok',{'reply':'Local visual requested.','email_visuals':self.request()}
        agents.run=run
        for agent,proactive in [('max',False),('sam',False),('laia',True)]:
            before=self.fresh()
            agents.chat(dict(agent_id=agent,message='Make an explanation visual'),[],proactive=proactive)
            self.assertNotIn('email_visuals',seen['schema']['properties'])
            self.assertEqual(self.fresh(),before)
        agents.chat(dict(agent_id='laia',message='Make an explanation visual'),[])
        self.assertIn('email_visuals',seen['schema']['properties'])
        self.assertEqual(len(self.fresh()['images']),1)
        self.assertEqual(self.drafts.for_email(self.email['id']),[])

    def test_routes_require_auth_and_enforce_revision_and_png_only(self):
        # Execute the actual route method without constructing Server (which starts live workers).
        source=Path(__file__).resolve().parents[1]/'studio/server.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        handler=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Handler')
        route=next(n for n in handler.body if isinstance(n,ast.FunctionDef) and n.name=='route')
        ns={'urllib':urllib,'json':json,'Path':Path,'Conflict':Conflict,'NotAllowed':PermissionError,'__name__':'studio.server','__package__':'studio'}
        exec(compile(ast.Module(body=[route],type_ignores=[]),str(source),'exec'),ns)
        replies=[]
        h=SimpleNamespace(server=SimpleNamespace(emails=self.emails,store=self.store),authorized=lambda:False,
                          send=lambda data,status=200,**kw:replies.append((data,status,kw)))
        h.path='/api/emails/'+self.email['id']+'/visuals'
        ns['route'](h,'POST');self.assertEqual(replies[-1][1],403)
        h.authorized=lambda:True
        body=json.dumps({k:v for k,v in self.request().items() if k!='email_id'}).encode()
        h.headers={'Content-Length':str(len(body))};h.rfile=io.BytesIO(body)
        ns['route'](h,'POST');self.assertEqual(replies[-1][1],200)
        h.rfile=io.BytesIO(body)
        ns['route'](h,'POST');self.assertEqual(replies[-1][1],409)
        v=self.fresh()['images'][0]
        h.path+='/'+v['version']+'/1'
        ns['route'](h,'GET');self.assertEqual(replies[-1][0],self.png)
        self.assertEqual(replies[-1][2],{'mime':'image/png','cache':'private, no-store'})
        h.path=h.path.rsplit('/',1)[0]+'/..%2Fsecret'
        ns['route'](h,'GET');self.assertEqual(replies[-1][1],400)

    def test_renderer_failure_is_bounded_and_has_no_inherited_output_pipes(self):
        from studio.email_visuals import EmailVisuals
        folder=self.root/'renderer-test';folder.mkdir()
        process=SimpleNamespace(wait=lambda **kw:1)
        with patch('studio.email_visuals.subprocess.Popen',return_value=process) as start:
            with self.assertRaisesRegex(ValueError,'renderer failed'):
                EmailVisuals.render(self.emails.visuals,[{'lines':['Test']}],folder)
            self.assertEqual(start.call_args.kwargs['stdout'],subprocess.DEVNULL)
            self.assertEqual(start.call_args.kwargs['stderr'],subprocess.DEVNULL)
        self.assertFalse((folder/'job.json').exists())


class SVGTests(unittest.TestCase):
    def test_static_drawing_and_local_fragment_work(self):
        self.assertIn('<svg',safe_svg(SVG))
        self.assertIn('url(#arrow)',safe_svg('<svg><defs><marker id="arrow"/></defs><path d="M0 0 L10 10" marker-end="url(#arrow)"/></svg>'))

    def test_active_content_external_references_entities_and_css_rejected(self):
        drawings=['<svg><script>alert(1)</script></svg>','<svg onload="alert(1)"/>',
            '<svg><foreignObject><div/></foreignObject></svg>','<svg><image href="file:///secret"/></svg>',
            '<svg><use href="#x"/></svg>','<svg><animate attributeName="href"/></svg>',
            '<svg style="background:url(https://example.test)"/>','<svg><style>@import "x";</style></svg>',
            '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///secret">]><svg>&x;</svg>',
            '<?xml-stylesheet href="https://example.test"?><svg/>',
            '<svg><path fill="url(https://example.test/x)"/></svg>',
            '<svg><path fill="url(//example.test/x)"/></svg>',
            '<svg><path fill="u\\72l(file:///secret)"/></svg>',
            '<svg xmlns:x="http://evil.test"><x:text/></svg>']
        for svg in drawings:
            with self.subTest(svg=svg),self.assertRaises(ValueError):safe_svg(svg)


if __name__=='__main__':unittest.main()
