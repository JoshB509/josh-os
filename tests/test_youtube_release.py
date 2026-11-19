import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from studio.youtube_release import ReleaseError, prepare, verify, schedule

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name in ('final.mp4', 'a.jpg', 'b.jpg', 'c.jpg'):
            (root / name).write_bytes(name.encode())
        self.source = dict(channel_id='UC' + 'a' * 22, title='Approved title', description='Approved description',
            category_id='27', language='en', made_for_kids=False, contains_paid_promotion=False,
            contains_synthetic_media=False, video=str(root / 'final.mp4'), primary_thumbnail=str(root / 'a.jpg'),
            thumbnail_variants=[str(root / n) for n in ('a.jpg', 'b.jpg', 'c.jpg')],
            local_time='2027-01-15T18:00', timezone='Europe/London', utc_offset='+00:00')
    def test_packet_is_not_approval(self):
        packet = prepare(self.source, NOW)
        self.assertEqual(packet['state'], 'needs_review')
        self.assertEqual(packet['initial_privacy'], 'private')
        self.assertTrue(verify(packet, NOW))
    def test_content_change_invalidates(self):
        packet = prepare(self.source, NOW)
        packet['title'] = 'Changed'
        with self.assertRaises(ReleaseError): verify(packet, NOW)
    def test_asset_change_invalidates(self):
        packet = prepare(self.source, NOW)
        Path(self.source['video']).write_bytes(b'changed')
        with self.assertRaises(ReleaseError): verify(packet, NOW)
    def test_no_inferred_disclosures(self):
        del self.source['made_for_kids']
        with self.assertRaises(ReleaseError): prepare(self.source, NOW)
    def test_missing_clock_time(self):
        with self.assertRaises(ReleaseError): schedule('2027-01-15', 'Europe/London', '+00:00', NOW)
    def test_nonexistent_dst_time(self):
        with self.assertRaises(ReleaseError): schedule('2027-03-28T01:30', 'Europe/London', '+00:00', NOW)
    def test_ambiguous_dst_time_explicit_offsets(self):
        first = schedule('2027-10-31T01:30', 'Europe/London', '+01:00', NOW)
        second = schedule('2027-10-31T01:30', 'Europe/London', '+00:00', NOW)
        self.assertNotEqual(first['publish_at'], second['publish_at'])
    def test_past_schedule(self):
        with self.assertRaises(ReleaseError): schedule('2025-01-15T18:00', 'Europe/London', '+00:00', NOW)
    def test_description_limit_in_bytes(self):
        self.source['description'] = '\u00e9' * 2501
        with self.assertRaises(ReleaseError): prepare(self.source, NOW)
    def test_duplicate_variants(self):
        self.source['thumbnail_variants'][1] = self.source['thumbnail_variants'][0]
        with self.assertRaises(ReleaseError): prepare(self.source, NOW)
    def test_wrong_offset(self):
        with self.assertRaises(ReleaseError): schedule('2027-07-15T18:00', 'Europe/London', '+00:00', NOW)

if __name__ == '__main__': unittest.main()
