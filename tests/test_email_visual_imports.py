import ast,copy,io,json,unittest,urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from studio.email_visual_imports import register_png,location
from studio.email_visuals import sha
from studio.store import Conflict
from studio.kit import content_hash,body_html
from tests.test_kit_drafts import Base


class Imports(Base):
    def setUp(self):
        super().setUp();b=io.BytesIO();Image.new('RGB',(1520,1040),'white').save(b,format='PNG');self.png=b.getvalue()
        self.source=dict(source_asset_id='carousel:123:r14:s2',source_content_hash='a'*64,design_hash='b'*64,description='Reviewed slide')
        self.enterContext(patch('urllib.request.urlopen',side_effect=AssertionError('No remote calls')))

    def register(self,**kw):
        args=dict(email_id=self.email['id'],revision=self.fresh()['revision'],png=self.png,source=self.source,alt='A reviewed slide',after_line=1);args.update(kw)
        return register_png(self.emails.visuals,**args)

    def attach(self,items):
        return self.emails.visuals.generate(dict(email_id=self.email['id'],revision=self.fresh()['revision'],visuals=items))

    def test_eight_exact_pngs_preserve_body_history_and_invalidate_approval(self):
        refs=[self.register() for _ in range(8)];before=self.fresh();saved=self.attach(refs)
        self.assertEqual(saved['body'],before['body']);self.assertEqual(saved['revision'],before['revision']+1)
        self.assertEqual(saved['approval_status'],'Pending final approval');self.assertNotEqual(content_hash(saved),content_hash(before))
        self.assertEqual(len(saved['images']),8)
        for i,v in enumerate(saved['images'],1):
            self.assertEqual(self.emails.visuals.read_png(saved['id'],v['version'],str(i)),self.png)
            self.assertEqual(v['source_hash'],refs[i-1]['source_hash'])
        self.assertEqual(self.emails.history(saved['id'])[-1]['images'],before.get('images',[]))
        with self.assertRaises(ValueError):body_html(saved)

    def test_stale_registration_and_attachment_do_not_change_email(self):
        ref=self.register();old=self.fresh();self.emails.save(old['id'],{'preview':'New edit'},old['revision'],sync_remote=False)
        before=self.fresh()
        with self.assertRaises(Conflict):self.attach([ref])
        with self.assertRaises(Conflict):self.register(revision=old['revision'])
        self.assertEqual(self.fresh(),before)

    def test_forged_identity_hash_paths_mixed_specs_and_duplicates_fail(self):
        ref=self.register();before=self.fresh()
        for key,value in [('import_id','../secret'),('source_hash','c'*64),('png_hash','d'*64),('alt',''),('after_line',True),('after_line',3)]:
            with self.subTest(key=key),self.assertRaises(ValueError):self.attach([dict(ref,**{key:value})])
        for items in [[ref,ref],[ref,dict(lines=['Not an import'],alt='x',after_line=1)],[dict(ref,path='C:/secret')]]:
            with self.assertRaises(ValueError):self.attach(items)
        self.assertEqual(self.fresh(),before)

    def test_cross_email_reference_fails(self):
        ref=self.register();other=self.emails.create({'changes':{'subject':'Other','body':'Other'}})
        with self.assertRaises(ValueError):self.emails.visuals.generate(dict(email_id=other['id'],revision=other['revision'],visuals=[ref]))

    def test_tampered_png_or_record_fails_without_commit(self):
        for name in ('image.png','record.json'):
            ref=self.register();folder=location(self.emails.visuals,self.email['id'],ref['import_id'])
            (folder/name).write_bytes(b'changed')
            with self.assertRaises(ValueError):self.attach([ref])
        self.assertEqual(self.fresh()['revision'],1)

    def test_invalid_png_source_and_over_limit_rejected(self):
        for args in [dict(png=b'not PNG'),dict(source=dict(self.source,source_asset_id='file:///secret')),dict(source=dict(self.source,design_hash='bad'))]:
            with self.assertRaises(ValueError):self.register(**args)
        refs=[self.register() for _ in range(17)]
        with self.assertRaises(ValueError):self.attach(refs)
        saved=self.attach(refs[:16]);self.assertEqual(len(saved['images']),16)
        self.assertEqual(self.emails.visuals.read_png(saved['id'],saved['images'][-1]['version'],'16'),self.png)

    def test_concurrent_commit_wins_and_orphan_is_not_served(self):
        ref=self.register();real=self.emails.save;folders=[]
        def race(eid,changes,revision,**kw):
            real(eid,{'subject':'Concurrent'},revision,sync_remote=False)
            folders.append(changes['images'][0]['version'])
            return real(eid,changes,revision,**kw)
        with patch.object(self.emails,'save',side_effect=race):
            with self.assertRaises(Conflict):self.attach([ref])
        self.assertEqual(self.fresh()['subject'],'Concurrent');self.assertFalse(self.fresh().get('images'))
        with self.assertRaises(KeyError):self.emails.visuals.read_png(self.email['id'],folders[0],'1')

    def test_copy_failure_keeps_last_good(self):
        saved=self.attach([self.register()]);refs=[self.register()];real=Path.open
        def failed(path,*a,**kw):
            if path.name=='slide_01.png' and 'xb' in a:raise OSError('Injected write failure')
            return real(path,*a,**kw)
        with patch.object(Path,'open',failed):
            with self.assertRaises(OSError):self.attach(refs)
        self.assertEqual(self.fresh(),saved)

    def test_existing_authenticated_route_accepts_references_and_serves_eighth(self):
        refs=[self.register() for _ in range(8)]
        source=Path('E:/Videos/DaJoshiResolve/studio/server.py');tree=ast.parse(source.read_text(encoding='utf8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Handler');fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='route')
        ns={'__name__':'studio.server','__package__':'studio','urllib':urllib,'json':json,'Path':Path,'Conflict':Conflict,'NotAllowed':PermissionError}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),ns)
        responses=[];body=json.dumps(dict(revision=self.fresh()['revision'],visuals=refs)).encode()
        h=SimpleNamespace(server=SimpleNamespace(emails=self.emails,store=self.store),authorized=lambda:False,send=lambda data,status=200,**kw:responses.append((data,status,kw)),path='/api/emails/'+self.email['id']+'/visuals',headers={'Content-Length':str(len(body))},rfile=io.BytesIO(body))
        ns['route'](h,'POST');self.assertEqual(responses[-1][1],403)
        h.authorized=lambda:True;ns['route'](h,'POST');self.assertEqual(responses[-1][1],200)
        h.path+='/'+self.fresh()['images'][0]['version']+'/8';ns['route'](h,'GET');self.assertEqual(responses[-1][0],self.png)

if __name__=='__main__':unittest.main()
