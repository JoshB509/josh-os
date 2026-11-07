import unittest,tempfile,json,subprocess,sys
from pathlib import Path
from unittest.mock import patch
import worker
from dajoshi.media import check,Cancelled

class WorkflowTests(unittest.TestCase):
    def test_run_lock_released_and_parallel_worker_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            with worker.job_lock(d):
                with self.assertRaisesRegex(RuntimeError,'active worker'):
                    with worker.job_lock(d):pass
            with worker.job_lock(d):pass
    def test_cancellation_before_work(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'CANCEL').touch()
            with self.assertRaises(Cancelled):check(d)
    def test_recording_guard_blocks(self):
        class Result:returncode=0;stdout='1';stderr=''
        with patch('subprocess.run',return_value=Result()):
            with self.assertRaisesRegex(RuntimeError,'OBS is open'):worker.guard_recording()
    def test_guard_fails_closed(self):
        class Result:returncode=1;stdout='';stderr='error'
        with patch('subprocess.run',return_value=Result()):
            with self.assertRaisesRegex(RuntimeError,'Cannot check'):worker.guard_recording()
    def test_cue_phrase_ambiguity_is_not_guessed(self):
        from dajoshi.timing import build_map
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'shots.json';p.write_text(json.dumps([dict(phrase='Hello',source_end=5,layout='full')]))
            with self.assertRaisesRegex(ValueError,'exactly once'):worker.load_shots(p,[dict(word='Hello',start=1),dict(word='Hello',start=3)],0,build_map([[0,6]],25,0,6,6),None,[])
    def test_existing_job_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'request.json';p.write_text('{}')
            run=subprocess.run([sys.executable,str(Path(worker.__file__)),'--job',d,'--request',str(p)],capture_output=True)
            self.assertNotEqual(run.returncode,0);self.assertEqual(p.read_text(),'{}')

if __name__=='__main__':unittest.main()
