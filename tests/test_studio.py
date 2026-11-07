import io,json,sys,tempfile,time,unittest,uuid,threading,urllib.request,urllib.error
from pathlib import Path
from PIL import Image
from studio.store import Store,Conflict,STAGES
from studio.jobs import Jobs
from studio.server import Server

class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent);self.root=Path(self.temp.name);self.store=Store(self.root/'Data');self.p=self.store.create('A video')
    def tearDown(self):self.temp.cleanup()
    def image(self,color):
        b=io.BytesIO();Image.new('RGB',(160,90),color).save(b,format='PNG');return self.store.import_image('original.png',b.getvalue())
    def test_persistent_project_edits_titles_script_date(self):
        title=dict(id=str(uuid.uuid4()),text='A better title')
        p=self.store.save(self.p['id'],dict(stage='Review',publish_date='2026-11-05',notes='Direction',script='Saved words',titles=[title],chosen_title=title['id']),1)
        self.assertEqual(Store(self.root/'Data').get(p['id']),p)
    def test_stage_validation_and_all_stages(self):
        p=self.p
        for stage in STAGES:p=self.store.save(p['id'],{'stage':stage},p['revision'])
        with self.assertRaises(ValueError):self.store.save(p['id'],{'stage':'invalid'},p['revision'])
    def test_stale_edit_does_not_overwrite(self):
        self.store.save(self.p['id'],{'notes':'First window'},1)
        with self.assertRaises(Conflict):self.store.save(self.p['id'],{'notes':'Second window'},1)
        self.assertEqual(self.store.get(self.p['id'])['notes'],'First window')
    def test_create_retry_is_idempotent(self):
        again=self.store.create('Duplicate click',self.p['id']);self.assertEqual(again,self.p);self.assertEqual(len(self.store.projects()),1)
    def test_invalid_date_preserves_previous(self):
        with self.assertRaises(ValueError):self.store.save(self.p['id'],{'publish_date':'2026-02-30'},1)
        self.assertEqual(self.store.get(self.p['id'])['revision'],1)
    def test_import_deduplicates_and_preserves_bytes(self):
        a=self.image('red');b=self.image('red');self.assertEqual(a['asset'],b['asset']);self.assertTrue(self.store.asset(a['asset'])[0].exists())
    def test_top_three_and_assigned_titles_persist(self):
        thumbs=[self.image(c) for c in ['red','blue','green','white']];title=dict(id=str(uuid.uuid4()),text='Compare this');thumbs[0]['title_id']=title['id']
        p=self.store.save(self.p['id'],dict(thumbnails=thumbs,top_three=[x['asset'] for x in thumbs[:3]],titles=[title]),1)
        self.assertEqual(Store(self.root/'Data').get(p['id'])['top_three'],p['top_three'])
        with self.assertRaises(ValueError):self.store.save(p['id'],{'top_three':[x['asset'] for x in thumbs]},p['revision'])
        with self.assertRaises(ValueError):self.store.save(p['id'],{'top_three':[thumbs[0]['asset']]*2},p['revision'])
    def test_reject_corrupt_or_nonimage_upload(self):
        with self.assertRaises(ValueError):self.store.import_image('bad.png',b'<script>bad</script>')
    def test_archive_is_reversible(self):
        p=self.store.save(self.p['id'],{'script':'Keep me','archived':True},1);p=self.store.save(p['id'],{'archived':False},p['revision']);self.assertEqual(p['script'],'Keep me')
    def test_asset_path_traversal_rejected(self):
        with self.assertRaises(ValueError):self.store.asset('../studio.sqlite')
    def test_missing_editor_input_does_not_start_job(self):
        jobs=Jobs(self.store,self.root)
        with self.assertRaises(ValueError):jobs.start(self.p['id'])
        self.assertEqual(self.store.jobs(),[])
    def test_handoff_repeat_cancel_resume_and_snapshot(self):
        source=self.root/'source.mp4';source.write_bytes(b'test fixture')
        shots=self.root/'shots.json';shots.write_text('[]')
        p=self.store.save(self.p['id'],dict(media={'video':str(source),'shots':str(shots)},script='Untouched script'),1)
        jobs=Jobs(self.store,self.root,[sys.executable,str(Path(__file__).parent/'studio_fixture_worker.py')])
        j=jobs.start(p['id']);again=jobs.start(p['id']);self.assertEqual(j['id'],again['id'])
        req=json.loads(Path(j['request']).read_text());self.assertEqual(req['video'],str(source));self.assertEqual(Path(req['script']).read_text(),'Untouched script');self.assertNotIn('overrides_inline',req)
        jobs.cancel(j['id']);jobs.cancel(j['id'])
        for _ in range(60):
            if jobs.processes[j['id']].poll() is not None:break
            time.sleep(.1)
        self.assertEqual(jobs.status(self.store.job(j['id']))['status']['state'],'cancelled')
        resumed=jobs.resume(j['id']);self.assertEqual(resumed['id'],j['id']);jobs.cancel(j['id'])
        jobs.processes[j['id']].wait(timeout=5);time.sleep(.2)
        self.assertEqual(source.read_bytes(),b'test fixture');self.assertEqual(len(self.store.jobs()),1)
    def test_http_auth_origin_and_crud(self):
        server=Server(self.root,self.root/'HTTP');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
        try:
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(base+'/api/state')
            req=urllib.request.Request(base+'/api/projects',data=json.dumps({'name':'Via API'}).encode(),headers={'X-DJ-Token':server.token,'Content-Type':'application/json'})
            with urllib.request.urlopen(req) as r:p=json.load(r)
            self.assertEqual(server.store.get(p['id'])['name'],'Via API')
            bad=urllib.request.Request(base+'/api/state',headers={'X-DJ-Token':server.token,'Origin':'https://example.com'})
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(bad)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
