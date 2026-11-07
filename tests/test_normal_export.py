import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import worker
from dajoshi.export_clock import probe_clock
from dajoshi.interchange import validate
from dajoshi.media import probe
from studio.jobs import Jobs
from studio.store import Store


class NormalExportTests(unittest.TestCase):
    def test_storytelling_nominal_clock(self):
        clock,report=probe_clock('14882500/496083','30','30',992.166)
        self.assertEqual(clock,'30');self.assertLess(report['maximum_change_frames'],0.05)

    def test_no_blind_rounding_or_conflicting_metadata(self):
        self.assertEqual(probe_clock('2999/100','30','30',1000)[0],'2999/100')
        self.assertEqual(probe_clock('14882500/496083','30','25',992.166)[0],'14882500/496083')

    def test_ntsc_and_integer_rates_preserved(self):
        for fps in ('24','25','50','60','24000/1001','30000/1001','60000/1001'):
            self.assertEqual(probe_clock(fps,fps,fps,1000)[0],fps)

    def test_probe_uses_clock_and_preserves_evidence(self):
        from types import SimpleNamespace as NS
        from fractions import Fraction
        v=NS(average_rate=Fraction(14882500,496083),base_rate=Fraction(30),guessed_rate=Fraction(30),width=3840,height=2160)
        a=NS(rate=48000,channels=2)
        class Container:
            duration=992166000
            streams=NS(video=[v],audio=[a])
            def __enter__(self):return self
            def __exit__(self,*args):pass
        with patch('dajoshi.media.av.open',return_value=Container()):result=probe('fixture.mp4')
        self.assertEqual(result['fps'],'30');self.assertEqual(result['clock']['reported_average_fps'],'14882500/496083')

    def test_studio_requests_timeline_only(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            root=Path(d);store=Store(root/'Data');p=store.create('Fixture');media=root/'source.mp4';media.write_bytes(b'fixture')
            p=store.save(p['id'],{'media':{'video':str(media)}},p['revision'])
            jobs=Jobs(store,root)
            with patch.object(jobs,'_spawn'):j=jobs.start(p['id'])
            self.assertFalse(json.loads(Path(j['request']).read_text())['preview'])

    def test_normal_worker_exports_without_preview_even_legacy_true(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            root=Path(d);job=root/'run';job.mkdir();source=root/'original.mp4';source.write_bytes(b'fixture')
            transcript=root/'transcript.json';transcript.write_text(json.dumps([{'words':[{'word':'Hello','start':1.1,'end':1.4,'probability':.99}]}]))
            cfg={'video':str(source),'transcript':str(transcript),'preview':True}
            worker.write(job/'request.json',cfg)
            info=dict(path=str(source),fps='30',duration=10,width=3840,height=2160,sample_rate=48000,channels=2)
            with patch.object(worker,'guard_recording'),patch.object(worker,'probe',return_value=info),patch.object(worker,'decode',return_value=np.zeros(160000)),patch.object(worker,'plan',return_value=([[1,3]],[],[],{})),patch.object(worker,'preview',side_effect=AssertionError('Full preview must not run')),patch.object(worker,'transcribe',side_effect=AssertionError('Use provided transcript')):
                worker.run_job(job)
            self.assertTrue(validate((job/'EDITABLE_DRAFT.xml').read_bytes())['dtd_valid'])
            self.assertFalse(worker.read(job/'status.json')['preview_created'])

    def test_unsupported_rate_stops_before_decode(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            root=Path(d);job=root/'run';job.mkdir();source=root/'original.mp4';source.write_bytes(b'fixture');worker.write(job/'request.json',{'video':str(source)})
            info=dict(path=str(source),fps='2999/100',duration=10,channels=2)
            with patch.object(worker,'guard_recording'),patch.object(worker,'probe',return_value=info),patch.object(worker,'decode',side_effect=AssertionError('Do not decode unsupported input')):
                with self.assertRaisesRegex(ValueError,'cannot represent'):worker.run_job(job)


if __name__=='__main__':unittest.main(verbosity=2)
