"""Real local media/files/SQLite; fake OBS only. No capture or production mutations."""
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT=Path(os.environ.get('DAJOSHI_ROOT',r'E:\Videos\DaJoshiResolve'))
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import studio
if (HERE/'lessons.py').exists():studio.__path__.insert(0,str(HERE))
from studio import lessons as m
from studio.store import Store, Conflict

class OBS:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def status(self):return dict(connected=True,recording=False,profile=m.PROFILE,collection=m.COLLECTION,scene=m.SCENE)
    def idle(self):pass
    def prepare(self):return dict(self.status(),prepared=True)

class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared=tempfile.TemporaryDirectory();cls.fixture=Path(cls.shared.name)/'fixture.mp4'
        subprocess.run([str(ROOT/'tools/ffmpeg.exe'),'-v','error','-f','lavfi','-i','color=c=blue:s=160x90:r=10','-t','1','-c:v','libx264','-pix_fmt','yuv420p',str(cls.fixture)],check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    @classmethod
    def tearDownClass(cls):cls.shared.cleanup()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name).resolve()
        self.input=self.folder/'input';self.input.mkdir();self.logs=self.folder/'appdata/obs-studio/logs';self.logs.mkdir(parents=True)
        self.env=patch.dict(os.environ,{'APPDATA':str(self.folder/'appdata')});self.env.start()
        # Preparing a lesson opens its slides and Notion page on this PC. Tests must never open Josh's real files.
        opener=patch.object(os,'startfile',create=True);opener.start();self.addCleanup(opener.stop)
        self.log=self.logs/'2026-10-17 10-59-00.txt';self.log.write_text('10:59:00.000: OBS startup\n')
        cfg=copy.deepcopy(json.loads((ROOT/'Data/lesson-catalog.json').read_text()));cfg['lessons']=cfg['lessons'][:2]
        cfg.update(recording_input=str(self.input),recording_archive=str(self.folder/'archive'))
        self.l=m.Lessons(Store(self.folder/'Data'),ROOT,cfg,OBS);self.lid=cfg['lessons'][0]['id'];self.other=cfg['lessons'][1]['id']
        self.bound=dict(id='session-one',lesson_id=self.lid,prepared_at='2026-10-17T07:00:00Z',prepared_ns=str(int(datetime(2026,10,17,11).timestamp()*1e9)),existing_paths=[],obs_log_cursor=self.l.obs_log_cursor())
        self.bind(self.bound)
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def bind(self,b):
        with self.l.store.connect() as c:c.execute('INSERT OR REPLACE INTO lesson_binding VALUES(1,?)',(json.dumps(b),))
    def append(self,text):
        with self.log.open('a',encoding='utf-8') as f:f.write(text)
    def restart(self,name='2026-10-18 10-00-00.txt',scene=None):
        self.log=self.logs/name
        self.log.write_text("10:00:01.000: Switched to scene '"+(scene or m.SCENE)+"'\n")
    def media(self,name='take-a',proof=True,stop=True,clock='11:01',pair=True):
        mp4=self.input/(name+'.mp4');shutil.copyfile(self.fixture,mp4);paths=[mp4]
        if pair:
            mkv=mp4.with_suffix('.mkv')
            subprocess.run([str(ROOT/'tools/ffmpeg.exe'),'-v','error','-i',str(mp4),'-c','copy',str(mkv)],check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));paths.append(mkv)
        for p in paths:os.utime(p,(time.time()-15,)*2)
        if proof:
            self.append(f"{clock}:00.000: [ffmpeg muxer: 'adv_file_output'] Writing file '{paths[-1]}'...\n")
            if stop:self.append(f"{clock}:01.000: [ffmpeg muxer: 'adv_file_output'] Output of file '{paths[-1]}' stopped\n")
        return paths
    def scan(self):return self.l.session_evidence(self.l.binding())
    def handover(self):return self.l.handover(self.lid,'session-one')
    def done(self):return self.l.finish_session(self.lid,dict(confirmed=True,binding_id='session-one'))
    def test_cross_day_restart_imports_both_takes_once_and_done_uses_log_order(self):
        first=self.media();self.restart();second=self.media('take-b')
        for p in first:os.utime(p,(time.time()-9,)*2)
        state=self.done();self.assertEqual(len(state['takes']),2);self.assertEqual(state['takes'][-1]['source_path'],str(second[0]))
        self.assertEqual(state['preferred_take'],state['takes'][-1]['id']);self.assertEqual(self.done(),state)
        self.assertEqual(self.handover()['imported'],0);self.assertEqual(self.l.binding()['prepared_at'],self.bound['prepared_at'])
        self.assertEqual(self.l.binding()['obs_log_cursor'],self.bound['obs_log_cursor']);self.assertEqual(len(self.l.binding()['obs_log_history']),2)
        self.assertTrue(all(p.exists() for p in first+second))
    def test_same_day_restart(self):
        self.media();self.restart('2026-10-17 12-00-00.txt');self.media('take-b',clock='12:01');self.assertEqual(len(self.done()['takes']),2)
    def test_multiple_restarts(self):
        self.media();self.restart();self.media('take-b');self.restart('2026-10-19 10-00-00.txt');self.media('take-c');self.assertEqual(len(self.done()['takes']),3)
    def test_midnight_in_one_log(self):
        self.media(clock='23:59');self.media('take-b',clock='00:01');self.assertEqual(len(self.done()['takes']),2)
    def test_empty_restart_log_is_uncertain(self):
        self.media();self.restart();self.log.write_text('');self.assertTrue(self.scan()['ambiguous'])
        with self.assertRaises(Conflict):self.done()
    def test_restart_wrong_scene_blocks_all_imports(self):
        self.media();self.restart(scene='Other');self.media('take-b');self.assertEqual(self.handover()['imported'],0)
        with self.assertRaises(Conflict):self.done()
    def test_matching_scene_reaffirmation_is_allowed(self):
        self.restart();self.append(f"10:00:02.000: User switched to scene '{m.SCENE}'\n");self.media();self.assertEqual(len(self.done()['takes']),1)
    def test_scene_change_during_recording_blocks(self):
        self.media(stop=False);self.append(f"11:01:00.500: User switched to scene '{m.SCENE}'\n");self.assertTrue(self.scan()['ambiguous'])
    def test_profile_or_collection_changes_block(self):
        for event in ["User switched to profile 'Other'","Changing scene collection"]:
            with self.subTest(event=event):self.append('11:00:01.000: '+event+'\n');self.assertTrue(self.scan()['ambiguous'])
    def test_missing_original_log_blocks(self):
        self.media();self.log.unlink();self.restart();self.assertFalse(self.scan()['valid'])
    def test_changed_original_prefix_blocks(self):
        self.media();self.log.write_text(self.log.read_text().replace('OBS startup','OBS changed'));self.assertFalse(self.scan()['valid'])
    def test_changed_previously_verified_new_log_blocks(self):
        self.restart();self.media();self.handover();self.log.write_text(self.log.read_text().replace('11:01:01','11:01:02'))
        self.assertFalse(self.scan()['valid'])
    def test_deleted_previously_verified_new_log_blocks(self):
        self.restart();self.media();self.handover();self.log.unlink();self.assertFalse(self.scan()['valid'])
    def test_status_polling_does_not_change_scan(self):
        self.media();a=self.scan();self.append('11:02:00.000: Websocket GetRecordStatus\n');self.assertEqual(a,self.scan())
    def test_active_or_disconnected_obs_blocks_handover_and_done(self):
        self.media()
        for status in [dict(connected=True,recording=True),dict(connected=False)]:
            with patch.object(self.l,'obs',return_value=status):
                self.assertEqual(self.handover()['imported'],0)
                with self.assertRaises(Conflict):self.done()
    def test_incomplete_take_after_restart_blocks_done(self):
        self.media();self.restart();self.media('take-b',stop=False)
        with self.assertRaisesRegex(Conflict,'not finished'):self.done()
    def test_stop_in_different_process_does_not_close_take(self):
        paths=self.media(stop=False);self.restart();self.append(f"11:01:01.000: [ffmpeg muxer: 'adv_file_output'] Output of file '{paths[-1]}' stopped\n")
        self.assertTrue(self.scan()['ambiguous']);self.assertTrue(self.scan()['incomplete'])
    def test_orphan_stop_blocks(self):
        self.append(f"11:01:01.000: [ffmpeg muxer: 'adv_file_output'] Output of file '{self.input/'orphan.mkv'}' stopped\n");self.assertTrue(self.scan()['ambiguous'])
    def test_duplicate_filename_start_blocks(self):
        paths=self.media();self.append(f"11:02:00.000: [ffmpeg muxer: 'adv_file_output'] Writing file '{paths[-1]}'...\n");self.assertTrue(self.scan()['ambiguous'])
    def test_events_before_preparation_block(self):
        self.media(clock='10:58');self.assertTrue(self.scan()['ambiguous'])
    def test_existing_mkv_excludes_later_remux(self):
        paths=self.media();self.bound['existing_paths']=[str(paths[1])];self.bind(self.bound);self.assertTrue(self.scan()['ambiguous'])
    def test_wrong_output_path_blocks(self):
        self.append(f"11:01:00.000: [ffmpeg muxer: 'adv_file_output'] Writing file '{self.folder/'other.mkv'}'...\n");self.assertTrue(self.scan()['ambiguous'])
    def test_unproven_new_file_never_adopted(self):
        self.media(proof=False);self.assertEqual(self.handover()['imported'],0)
        with self.assertRaises(Conflict):self.done()
    def test_pending_remux_blocks_done_then_resumes(self):
        paths=self.media();paths[0].unlink()
        with self.assertRaisesRegex(Conflict,'remux'):self.done()
        shutil.copyfile(self.fixture,paths[0]);os.utime(paths[0],(time.time()-15,)*2);self.assertEqual(len(self.done()['takes']),1)
    def test_missing_original_requires_review(self):
        paths=self.media();paths[1].unlink();self.assertEqual(self.handover()['status'],'proposed')
    def test_missing_newer_recording_does_not_select_older(self):
        self.media();self.handover();paths=self.media('take-b')
        for p in paths:p.unlink()
        with self.assertRaisesRegex(Conflict,'missing'):self.done()
    def test_recent_and_open_writer_not_imported(self):
        paths=self.media()
        with paths[1].open('ab'):self.assertEqual(self.handover()['imported'],0)
        os.utime(paths[0],None);self.assertEqual(self.handover()['imported'],0)
    def test_new_start_during_archive_hash_blocks_done(self):
        self.media();self.handover();original=m.digest
        def digest(p):
            value=original(p);self.append(f"11:02:00.000: [ffmpeg muxer: 'adv_file_output'] Writing file '{self.input/'pending.mkv'}'...\n");return value
        with patch.object(m,'digest',side_effect=digest),self.assertRaises(Conflict):self.done()
    def test_changed_evidence_before_import_blocks(self):
        paths=self.media();proof=next(iter(self.scan()['completed'].values()));self.append("11:02:00.000: User switched to scene 'Other'\n");p=paths[0];st=p.stat()
        with self.assertRaises(Conflict):self.l.import_take(self.lid,dict(path=str(p),size=st.st_size,mtime_ns=str(st.st_mtime_ns),confirmed_stopped=True,handover_evidence=proof))
    def test_manual_selection_preserved(self):
        self.media();self.handover();first=self.l.state(self.lid)['takes'][0];self.l.take_action(self.lid,dict(action='prefer',take_id=first['id']));self.restart();self.media('take-b');self.assertEqual(self.done()['preferred_take'],first['id'])
    def test_stale_binding_request_blocks(self):
        self.media();self.bind(dict(self.bound,id='session-two'))
        with self.assertRaises(Conflict):self.done()
    def test_uncertain_previous_session_cannot_switch(self):
        self.restart(scene='Other')
        with patch.object(m,'preparation_connection') as connect:
            with self.assertRaises(Conflict):self.l.prepare_lesson(self.other)
            connect.assert_not_called()
    def test_done_next_stores_boundary_and_does_not_adopt_previous(self):
        self.media();old=self.done()
        with patch.object(os,'startfile'),patch.object(self.l,'cues',return_value='script'):new=self.l.prepare_lesson(self.other)
        self.assertEqual(self.l.candidates(self.other),[]);self.assertEqual(self.l.state(self.lid),old)
        with self.l.store.connect() as c:archived=json.loads(c.execute('SELECT body FROM lesson_sessions WHERE id=?',('session-one',)).fetchone()['body'])
        self.assertEqual(archived['closed_log_cursor'],new['binding']['obs_log_cursor']);self.assertEqual(archived['prepared_ns'],self.bound['prepared_ns'])
    def test_historical_closed_boundary_excludes_next_lessons_recordings(self):
        self.media();self.restart();self.media('take-b');closed=dict(self.bound,closed_log_cursor=self.l.obs_log_cursor());self.media('other-lesson',clock='12:01')
        self.assertEqual(len(self.l.session_evidence(closed)['completed']),2)
    def test_historical_recovery_never_imports_next_lesson(self):
        paths=self.media();self.restart();paths+=self.media('take-b')
        closed=dict(self.bound,closed_log_cursor=self.l.obs_log_cursor(),closed_existing_paths=[str(p) for p in paths]);self.media('other-lesson',clock='12:01');self.bind(closed)
        self.assertEqual(len(self.done()['takes']),2)
    def test_wrong_startup_profile_blocks_even_matching_scene(self):
        self.restart();self.log.write_text('10:00:00.000: Command Line Arguments: --profile Other --scene '+m.SCENE+'\n'+self.log.read_text());self.media();self.assertTrue(self.scan()['ambiguous'])
    def test_changed_closed_boundary_blocks(self):
        self.media();closed=dict(self.bound,closed_log_cursor=self.l.obs_log_cursor());self.log.write_text('changed');self.assertFalse(self.l.session_evidence(closed)['valid'])
    def test_remux_manual_retry_is_single_take(self):
        paths=self.media();self.handover();p=paths[1];st=p.stat();self.l.import_take(self.lid,dict(path=str(p),size=st.st_size,mtime_ns=str(st.st_mtime_ns),confirmed_stopped=True));self.assertEqual(len(self.l.state(self.lid)['takes']),1)
    def test_same_lesson_reprepare_keeps_preparation(self):
        self.media();self.l.prepare_lesson(self.lid);self.assertEqual(self.l.binding(),self.bound)
    def test_partial_event_blocks_until_newline(self):
        paths=self.media(stop=False);self.append(f"11:01:01.000: [ffmpeg muxer: 'adv_file_output'] Output of file '{paths[-1]}' stopped")
        self.assertTrue(self.scan()['ambiguous']);self.append('\n');self.assertFalse(self.scan()['ambiguous'])
    def test_http_done_after_restart_and_readback(self):
        import threading,urllib.request,urllib.error
        from http.server import ThreadingHTTPServer
        from studio.server import Handler
        self.media();self.restart();self.media('take-b')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.lessons=self.l;server.store=self.l.store;server.token='isolated-test-token'
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(suffix,data=None):
            req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/lessons/{self.lid}'+suffix,data=None if data is None else json.dumps(data).encode(),headers={'X-DJ-Token':server.token,'Content-Type':'application/json'})
            with urllib.request.urlopen(req) as response:return json.load(response)
        try:
            result=request('/done',dict(confirmed=True,binding_id='session-one'));readback=request('')
            self.assertEqual(readback['recording'],result);self.assertEqual(len(result['takes']),2)
            self.assertEqual(request('/done',dict(confirmed=True,binding_id='session-one')),result)
            self.assertEqual(request('/handover',dict(binding_id='session-one'))['imported'],0)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SessionTests))
    (HERE/'test-results.json').write_text(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),real_obs_actions=0),indent=2))
    sys.exit(not result.wasSuccessful())
