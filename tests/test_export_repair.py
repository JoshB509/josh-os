import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dajoshi.export_clock import normalize_saved
from dajoshi.timing import build_map
from dajoshi.export import export_xml
from dajoshi.interchange import validate


def fixture(fps='14882500/496083'):
    m=build_map([[1,3],[4,6]],fps,0,10,10)
    info={'path':'original.mp4','fps':fps,'duration':10,'width':3840,'height':2160,'sample_rate':48000,'channels':2}
    m.update(source_video=info.copy(),source_audio=info.copy(),sync={'verified':True,'offset_seconds':0})
    return m


class RepairTests(unittest.TestCase):
    def test_nominal_mapping_preserves_frames_and_audio(self):
        original=fixture();before=copy.deepcopy(original);result=normalize_saved(original,'30')
        self.assertEqual(original,before)
        self.assertEqual(result['duration_frames'],original['duration_frames'])
        for s,old in zip(result['spans'],original['spans']):
            for key in ('source_in_frame','source_out_frame','out_in_frame','out_out_frame'):self.assertEqual(s[key],old[key])
            self.assertAlmostEqual(s['audio_in_seconds'],s['source_in_frame']/30)

    def test_exact_fractional_clock(self):
        self.assertEqual(normalize_saved(fixture('30000/1001'),'30000/1001')['fps'],'30000/1001')

    def test_not_fixed_to_thirty(self):
        self.assertEqual(normalize_saved(fixture('24'),'24')['fps'],'24')

    def test_reject_meaningful_rate_change(self):
        with self.assertRaisesRegex(ValueError,'0.05 frame'):normalize_saved(fixture('30000/1001'),'30')

    def test_reject_incomplete_ranges(self):
        m=fixture();m['spans'][1]['out_in_frame']+=1
        with self.assertRaisesRegex(ValueError,'inconsistent'):normalize_saved(m,'30')

    def test_reject_audio_disagreement(self):
        m=fixture();m['spans'][0]['audio_in_seconds']+=0.1
        with self.assertRaisesRegex(ValueError,'audio timing'):normalize_saved(m,'30')

    def test_reject_unsupported_clock(self):
        with self.assertRaisesRegex(ValueError,'representable'):normalize_saved(fixture(),'2999/100')

    def test_xml_schema_links_bounds_and_cached_assets(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            job=Path(temp);source=job/'original.mp4';asset=job/'beat.mp4';source.write_bytes(b'fixture');asset.write_bytes(b'fixture')
            m=normalize_saved(fixture(),'30')
            for k in ('source_video','source_audio'):m[k]['path']=str(source)
            n=m['duration_frames'];shots=[{'layout':'pip','asset':str(asset),'out_start':0,'out_end':n,'beat':1}]
            info={'path':str(asset),'fps':'30','duration':35,'duration_frames':1050,'width':1920,'height':1080,'channels':0,'sample_rate':None}
            with patch('dajoshi.export.probe',side_effect=AssertionError('Must not open animation files')):
                xml=export_xml(job,m,m['source_video'],m['source_audio'],shots,[],{str(asset):info})
            data=xml.read_bytes();result=validate(data)
            self.assertTrue(result['dtd_valid']);self.assertEqual(result['file_count'],2)
            self.assertNotIn(b'numOutputChannels',data);self.assertIn(b'<channelcount>2</channelcount>',data)
            self.assertFalse((job/'REVIEW_PREVIEW.mp4').exists())


if __name__=='__main__':unittest.main(verbosity=2)
