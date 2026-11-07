import importlib.util,json,sqlite3,sys,tempfile,unittest,uuid
from pathlib import Path
ROOT=Path(r'E:\Videos\DaJoshiResolve');HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT));import studio
if (HERE/'tasks.py').exists():studio.__path__.insert(0,str(HERE))
from studio.tasks import Tasks,SEEDS
from studio.store import Store,Conflict

class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name).resolve();self.store=Store(self.folder);self.tasks=Tasks(self.store)
    def tearDown(self):self.temp.cleanup()
    def test_seed_exact_user_plans_and_only_launch_deadline(self):
        items=self.tasks.all()['items'];self.assertEqual(len(items),6);self.assertEqual(sum(x['kind']=='goal' for x in items),2)
        self.assertEqual([x['due_date'] for x in items if x['due_date']],['2026-11-01']);self.assertTrue(all(x['status']!='Done' and x['completed_at'] is None for x in items))
        trial=next(x for x in items if x['title']=='Evaluate the AutoCut clean-cut workflow');self.assertEqual(trial['status'],'Waiting');self.assertIn('not been completed',trial['next_step'])
    def test_seed_rerun_preserves_user_edits(self):
        item=self.tasks.all()['items'][0];self.tasks.save(item['id'],{'title':'My revised plan','status':'Later'},item['revision'])
        items=Tasks(self.store).all()['items'];self.assertEqual(len(items),6);self.assertEqual(next(x for x in items if x['id']==item['id'])['title'],'My revised plan')
    def test_create_edit_complete_reopen_survive_new_connection(self):
        item=self.tasks.create({'title':'One useful next step','next_step':'Make the outline'})
        item=self.tasks.save(item['id'],{'kind':'goal','status':'In progress','title':'A meaningful outcome'},1)
        item=self.tasks.save(item['id'],{'status':'Done'},2);self.assertIsNotNone(item['completed_at'])
        read=next(x for x in Tasks(Store(self.folder)).all()['items'] if x['id']==item['id']);self.assertEqual(read,item)
        reopened=self.tasks.save(item['id'],{'status':'Next'},3);self.assertIsNone(reopened['completed_at']);self.assertEqual(reopened['revision'],4)
    def test_stale_revision_cannot_overwrite(self):
        item=self.tasks.create({'title':'Original'});self.tasks.save(item['id'],{'title':'Other window'},1)
        with self.assertRaises(Conflict):self.tasks.save(item['id'],{'title':'Stale edit'},1)
        self.assertEqual(next(x for x in self.tasks.all()['items'] if x['id']==item['id'])['title'],'Other window')
    def test_idempotent_creation_and_conflicting_retry(self):
        data={'id':str(uuid.uuid4()),'title':'Exactly once'};first=self.tasks.create(data);self.assertEqual(self.tasks.create(data),first)
        with self.assertRaises(Conflict):self.tasks.create(dict(data,title='Different'))
    def test_validation_and_unsafe_links(self):
        for changes in [{'title':'   '},{'status':'invented'},{'kind':'campaign'},{'link':'javascript:alert(1)'},{'link':'E:\\private'},{'due_date':'2026-02-30'},{'next_step':'x'*1001},{'unknown':'secret'}]:
            with self.subTest(changes=changes),self.assertRaises((ValueError,KeyError)):self.tasks.create(dict(title='Task',**{k:v for k,v in changes.items() if k!='title'}) if 'title' not in changes else changes)
    def test_project_link_reads_exact_stage_without_changing_project(self):
        project=self.store.create('Related video');project=self.store.save(project['id'],{'stage':'Editing','notes':'PRIVATE: never expose this'},1)
        item=self.tasks.create({'title':'Review the video','link':'project:'+project['id']});payload=self.tasks.all();target=next(x for x in payload['targets'] if x['value']==item['link'])
        self.assertEqual(target['stage'],'Editing');self.assertEqual(target['url'],'/?project='+project['id']);self.assertNotIn('PRIVATE',json.dumps(payload));self.assertEqual(self.store.get(project['id']),project)
    def test_other_tables_and_approval_states_unchanged(self):
        with self.store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS email_drafts(id TEXT PRIMARY KEY,body TEXT)');c.execute('INSERT INTO email_drafts VALUES(?,?)',('email','{"status":"Draft","approved":false}'))
        before=self.store.projects();item=self.tasks.create({'title':'New work'});self.tasks.save(item['id'],{'status':'Done'},1);Tasks(self.store)
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT body FROM email_drafts').fetchone()[0],'{"status":"Draft","approved":false}')
        self.assertEqual(self.store.projects(),before)
    def test_noop_save_keeps_revision_and_completion_timestamp(self):
        item=self.tasks.create({'title':'Finish','status':'Done'});self.assertEqual(self.tasks.save(item['id'],{'status':'Done'},1),item)
    def test_missing_item_and_revision_validation(self):
        with self.assertRaises(KeyError):self.tasks.save(str(uuid.uuid4()),{'status':'Done'},1)
        item=self.tasks.create({'title':'Task'})
        with self.assertRaises(ValueError):self.tasks.save(item['id'],{},True)
    def test_http_crud_and_auth(self):
        import threading,urllib.request,urllib.error
        from http.server import ThreadingHTTPServer
        from studio.server import Handler
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.tasks=self.tasks;server.store=self.store;server.token='fixture-only';thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(path='/api/tasks',data=None,method=None,token='fixture-only'):
            with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,data=None if data is None else json.dumps(data).encode(),method=method,headers={'X-DJ-Token':token,'Content-Type':'application/json'})) as r:return json.load(r)
        try:
            self.assertEqual(len(request()['items']),6)
            item=request(data={'title':'Through HTTP'});item=request('/api/tasks/'+item['id'],{'revision':1,'changes':{'status':'Done'}},'PUT');self.assertEqual(item['status'],'Done')
            with self.assertRaises(urllib.error.HTTPError) as e:request('/api/tasks/'+item['id'],{'revision':1,'changes':{'status':'Next'}},'PUT')
            self.assertEqual(e.exception.code,409)
            with self.assertRaises(urllib.error.HTTPError) as e:request(token='wrong')
            self.assertEqual(e.exception.code,403)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TaskTests));(HERE/'unit-results.json').write_text(json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors)},indent=2));sys.exit(not result.wasSuccessful())
