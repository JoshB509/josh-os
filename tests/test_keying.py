import json,tempfile,unittest,threading,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as E
from dajoshi.keying import settings,defaults,digest,validate_coverage,prepare
from dajoshi.export import export_xml,local_file_url
from dajoshi.timing import build_map
from studio.store import Store,Conflict
from studio.jobs import Jobs
from studio.server import Server

class KeyingTests(unittest.TestCase):
    def test_premiere_local_urls_keep_escaped_characters(self):
        from urllib.parse import urlsplit,unquote
        p=Path('source with space # and %.mov').resolve();u=urlsplit(local_file_url(p))
        self.assertEqual(u.netloc,'localhost');self.assertEqual(u.scheme,'file')
        path=unquote(u.path);path=path[1:] if len(path)>2 and path[2]==':' else path
        self.assertEqual(Path(path),p)
        self.assertIn('%23',local_file_url(p));self.assertIn('%25',local_file_url(p))
    def test_reject_bad_settings(self):
        for change in [dict(end=0),dict(scale=float('nan')),dict(polygon=[[0,0],[0,0],[0,0]]),dict(polygon=[[0,0],[1,0],[1,2]]),dict(height=720),dict(enabled='true'),dict(unknown=1)]:
            with self.subTest(change=change),self.assertRaises(ValueError):settings(change)
    def test_independent_defaults(self):
        a=defaults();a['polygon'][0][0]=0;self.assertEqual(defaults()['polygon'][0][0],.31)
    def test_mask_and_framing_reuse_raw_cache_but_despill_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);engine=root/'Engines/CorridorKey';video=root/'source.mov';video.write_bytes(b'original');(root/'keying_runner.py').write_bytes(b'runner')
            for rel in ['venv/Scripts/python.exe','models/CorridorKey.safetensors','source/CorridorKeyModule/inference_engine.py']:
                p=engine/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'fixture')
            info=dict(path=str(video),fps='25',duration=60,width=1920,height=1080);caches=[]
            def runner(command,job,*a,**kw):
                req=json.loads((job/'keying/request.json').read_text());caches.append(req['cache']);media=job/'keying/master.mov';media.write_bytes(b'asset');manifest={}
                for name in ['masked_media','raw_media']:manifest[name]=str(media);manifest[name+'_sha256']=digest(media)
                (job/'keying/manifest.json').write_text(json.dumps(manifest))
            for i,change in enumerate([{},dict(scale=1.7,x=.2,crop_bottom=.2,feather=10),dict(despill=.7)]):
                job=root/str(i);job.mkdir()
                with patch('dajoshi.keying.clock_audit',return_value={}),patch('dajoshi.keying.run',side_effect=runner),patch('dajoshi.keying.probe',return_value=dict(fps='25',duration=7)):
                    prepare(root,job,info,settings(dict(enabled=True,start=25,end=30,**change)),'ffmpeg')
            self.assertEqual(caches[0],caches[1]);self.assertNotEqual(caches[0],caches[2])
    def test_store_revision_and_preview_request(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);store=Store(root/'Data');p=store.create('Keying fixture');v=root/'source.mov';v.touch()
            p=store.save(p['id'],dict(media={'video':str(v)},keying=dict(enabled=True,start=25,end=30)),1)
            jobs=Jobs(store,root)
            with patch.object(jobs,'_spawn') as spawn:
                with self.assertRaises(Conflict):jobs.start(p['id'],1,True)
                spawn.assert_not_called();j=jobs.start(p['id'],p['revision'],True)
                request=json.loads(Path(j['request']).read_text());self.assertTrue(request['keying']['preview']);self.assertFalse(request.get('render_review_preview',False));self.assertEqual(store.get(p['id'])['stage'],'Idea')
            latest=store.get(p['id']);saved=store.save(p['id'],{'notes':'Older UI saving another field'},latest['revision']);self.assertEqual(saved['keying']['start'],25)
    def test_nested_source_framing_and_layout_clocks(self):
        for fps in ['24','25','30000/1001','50','60000/1001']:
            with self.subTest(fps=fps),tempfile.TemporaryDirectory() as d:
                p=Path(d);original=p/'camera.mov';mic=p/'mic.wav';key=p/'key.mov'
                for f in [original,mic,key]:f.write_bytes(b'fixture')
                v=dict(path=str(original),fps=fps,duration=60,width=3840,height=2160,channels=0,sample_rate=None)
                a=dict(path=str(mic),fps=None,duration=65,width=0,height=0,channels=2,sample_rate=48000)
                m=build_map([[25,26],[28,30]],fps,1.8,60,65);first=m['spans'][0]['source_in_frame']-20;last=m['spans'][-1]['source_out_frame']+20
                m['keying']=dict(first_frame=first,end_frame_exclusive=last,frames=last-first,settings=settings(dict(scale=1.2,x=.1)),raw_media=str(key),masked_media=str(key),raw_media_sha256=digest(key),masked_media_sha256=digest(key))
                info=dict(path=str(key),fps=fps,duration=60,width=1920,height=1080,channels=0,sample_rate=None)
                shots=[dict(out_start=0,out_end=m['duration_frames'],layout='pip')]
                with patch('dajoshi.export.probe',return_value=info):xml=export_xml(p,m,v,a,shots,[])
                tree=E.parse(xml);tracks=tree.findall('./sequence/media/video/track');self.assertEqual(len(tracks),3)
                self.assertTrue(all(c.findtext('enabled')=='FALSE' for c in tracks[1].findall('clipitem')))
                # 4K recovery media uses source-normalized XMEML center offsets.
                recovery=tracks[1].find('clipitem')
                self.assertEqual(recovery.findtext('filter/effect/parameter/value/horiz'),'0.155')
                self.assertEqual(recovery.findtext('filter/effect/parameter/value/vert'),'0.15')
                presenter=tracks[2].find('clipitem');self.assertEqual(int(presenter.findtext('in')),20)
                self.assertEqual(presenter.findtext('filter/effect/parameter/value'),'32')
                nest=presenter.find('sequence');self.assertEqual(nest.findtext('.//clipitem/filter/effect/parameter/value'),'120.0')
                self.assertIn(local_file_url(original),[e.text for e in tree.findall('.//pathurl')]);self.assertIn(local_file_url(mic),[e.text for e in tree.findall('.//pathurl')])
                m['keying']['first_frame']+=100
                with self.assertRaises(ValueError):validate_coverage(m)
                m['keying']['first_frame']=first;key.write_bytes(b'changed')
                with self.assertRaises(ValueError):validate_coverage(m)
    def test_private_preview_range_and_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);server=Server(root,root/'Data');t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
            try:
                p=server.store.create('Test');job=root/'run';job.mkdir();(job/'KEYING_PREVIEW.mp4').write_bytes(b'0123456789')
                server.store.put_job(dict(id='fixture',project_id=p['id'],path=str(job)))
                url='http://127.0.0.1:'+str(server.server_port)+'/api/jobs/fixture/asset/'
                with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(url+'keying.mp4')
                req=urllib.request.Request(url+'keying.mp4',headers={'X-DJ-Token':server.token,'Range':'bytes=2-5'})
                with urllib.request.urlopen(req) as response:self.assertEqual(response.status,206);self.assertEqual(response.read(),b'2345');self.assertEqual(response.headers['Content-Range'],'bytes 2-5/10')
                for bad in ['../request.json','manifest.json']:
                    with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(urllib.request.Request(url+bad,headers={'X-DJ-Token':server.token}))
            finally:server.shutdown();server.server_close();t.join()

if __name__=='__main__':unittest.main()
