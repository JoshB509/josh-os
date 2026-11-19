"""Offline publishing tests. All Google calls mocked; fixtures use temporary files."""
import copy
import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image
from studio.youtube_publishing import (PublishingService, PublishingError, YouTubePublisher,
    check_remote, safe_session)
from studio.youtube_release import ReleaseError

SESSION = 'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=fixture-session'
VIDEO = 'abcdefghijk'


def remote(packet, processing='succeeded'):
    return dict(id=VIDEO, snippet=dict(channelId=packet['channel_id'],title=packet['title'],
        description=packet['description'],categoryId=packet['category_id'],defaultLanguage=packet['language']),
        status=dict(uploadStatus='processed' if processing=='succeeded' else 'uploaded', privacyStatus='private',
            publishAt=packet['schedule']['publish_at'], selfDeclaredMadeForKids=packet['made_for_kids'],
            containsSyntheticMedia=packet['contains_synthetic_media']),
        processingDetails=dict(processingStatus=processing),
        paidProductPlacementDetails=dict(hasPaidProductPlacement=packet['contains_paid_promotion']))


class PublishingServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'final.mp4').write_bytes(b'offline-fixture-not-real-media')
        for name, color in [('a','red'),('b','green'),('c','blue')]:
            Image.new('RGB',(10,10),color).save(self.root/(name+'.png'))
        self.source = dict(channel_id='UC'+'a'*22, title='Approved title',description='Approved description',
            category_id='27',language='en',made_for_kids=False,contains_paid_promotion=False,
            contains_synthetic_media=False,video=str(self.root/'final.mp4'),
            primary_thumbnail=str(self.root/'a.png'),thumbnail_variants=[str(self.root/(n+'.png')) for n in 'abc'],
            local_time='2099-01-15T18:00',timezone='Europe/London',utc_offset='+00:00')
        self.auth = Mock(connected=True)
        self.client = Mock()
        self.client.channel.return_value = self.source['channel_id']
        self.client.initiate.return_value = SESSION
        self.client.upload.return_value = VIDEO
        self.service = PublishingService(self.root, self.auth, self.client)
        self.job = self.service.prepare(self.source)
        self.client.inspect.return_value = remote(self.job['packet'])

    def approve(self):
        with patch('studio.youtube_publishing.threading.Thread'):
            self.service.approve_and_start(self.job['id'],self.job['packet']['packet_digest'])

    def test_prepare_is_idempotent_and_network_free(self):
        second = self.service.prepare(self.source)
        self.assertEqual(second['id'],self.job['id'])
        self.assertEqual(second['state'],'needs_review')
        self.assertEqual(self.client.mock_calls,[])

    def test_missing_approval_does_not_upload(self):
        with self.assertRaises(PublishingError): self.service.resume(self.job['id'])
        self.service.run(self.job['id'])
        self.client.channel.assert_not_called()
        self.client.initiate.assert_not_called()

    def test_wrong_digest_and_disconnected_rejected(self):
        with self.assertRaises(ReleaseError): self.service.approve_and_start(self.job['id'],'wrong')
        self.auth.connected=False
        with self.assertRaises(PublishingError): self.approve()
        self.assertNotIn('approval',self.service._load(self.job['id']))

    def test_wrong_channel_never_initializes(self):
        self.approve()
        self.client.channel.return_value='UC'+'b'*22
        self.service.run(self.job['id'])
        self.assertEqual(self.service.get(self.job['id'])['state'],'blocked')
        self.client.initiate.assert_not_called()

    def test_changed_file_never_initializes(self):
        self.approve()
        Path(self.source['video']).write_bytes(b'changed')
        self.service.run(self.job['id'])
        self.assertEqual(self.service.get(self.job['id'])['state'],'blocked')
        self.client.initiate.assert_not_called()

    def test_success_replay_does_not_reupload_or_thumbnail_again(self):
        self.approve()
        self.service.run(self.job['id'])
        self.service.run(self.job['id'])
        job=self.service.get(self.job['id'])
        self.assertEqual(job['state'],'scheduled')
        self.assertEqual(job['video_id'],VIDEO)
        self.client.initiate.assert_called_once()
        self.client.upload.assert_called_once()
        self.client.thumbnail.assert_called_once()
        self.assertNotIn('session',job)
        self.assertNotIn('approval',job)
        self.assertNotIn('fixture-session',self.service.db.read_bytes().decode('latin1'))

    def test_initialization_failure_never_blindly_restarts(self):
        self.approve()
        self.client.initiate.side_effect=PublishingError('Uncertain initialization')
        self.service.run(self.job['id'])
        self.service.run(self.job['id'])
        self.assertEqual(self.service.get(self.job['id'])['state'],'initialization_uncertain')
        self.client.initiate.assert_called_once()

    def test_upload_uncertainty_recovers_same_encrypted_session(self):
        self.approve()
        self.client.upload.side_effect=[PublishingError('Uncertain upload'),VIDEO]
        self.service.run(self.job['id'])
        self.assertEqual(self.service.get(self.job['id'])['state'],'blocked')
        self.service.run(self.job['id'])
        self.assertEqual(self.service.get(self.job['id'])['state'],'scheduled')
        self.client.initiate.assert_called_once()
        self.assertEqual(self.client.upload.call_count,2)
        self.assertTrue(all(call.args[0]==SESSION for call in self.client.upload.call_args_list))

    def test_thumbnail_failure_recovers_existing_video_id(self):
        self.approve()
        self.client.thumbnail.side_effect=[PublishingError('Thumbnail failed'),None]
        self.service.run(self.job['id'])
        first=self.service.get(self.job['id'])
        self.assertEqual(first['video_id'],VIDEO)
        self.assertFalse(first['thumbnail_set'])
        self.service.run(self.job['id'])
        self.assertEqual(self.service.get(self.job['id'])['state'],'scheduled')
        self.client.initiate.assert_called_once()
        self.client.upload.assert_called_once()
        self.assertEqual(self.client.thumbnail.call_count,2)

    def test_processing_is_not_claimed_ready(self):
        self.approve()
        self.client.inspect.side_effect=[remote(self.job['packet'],'processing'),remote(self.job['packet'])]
        self.service.run(self.job['id'])
        first=self.service.get(self.job['id'])
        self.assertEqual(first['state'],'processing')
        self.assertFalse(first['verification']['ready'])
        self.service.run(self.job['id'])
        self.assertEqual(self.service.get(self.job['id'])['state'],'scheduled')
        self.client.upload.assert_called_once()

    def test_processing_failure_retains_id(self):
        self.approve()
        self.client.inspect.return_value=remote(self.job['packet'],'failed')
        self.service.run(self.job['id'])
        job=self.service.get(self.job['id'])
        self.assertEqual(job['state'],'blocked')
        self.assertEqual(job['video_id'],VIDEO)

    def test_encryption_failure_after_initialization_is_uncertain(self):
        self.approve()
        with patch('studio.youtube_publishing.protect',side_effect=RuntimeError('fixture storage failure')):
            self.service.run(self.job['id'])
        self.assertEqual(self.service.get(self.job['id'])['state'],'initialization_uncertain')
        self.service.run(self.job['id'])
        self.client.initiate.assert_called_once()
        self.client.upload.assert_not_called()

    def test_new_packet_cannot_duplicate_approved_video(self):
        self.approve()
        changed=dict(self.source,title='Different title')
        with self.assertRaises(PublishingError): self.service.prepare(changed)

    def test_prepared_second_packet_cannot_be_approved_after_first(self):
        second=self.service.prepare(dict(self.source,title='Different title'))
        self.approve()
        with patch('studio.youtube_publishing.threading.Thread'):
            with self.assertRaises(PublishingError):
                self.service.approve_and_start(second['id'],second['packet']['packet_digest'])
        self.assertNotIn('approval',self.service._load(second['id']))

    def test_malformed_upload_id_not_used_for_thumbnail(self):
        self.approve()
        self.client.upload.return_value='../invalid'
        self.service.run(self.job['id'])
        self.service.run(self.job['id'])
        self.assertEqual(self.service.get(self.job['id'])['state'],'blocked')
        self.client.thumbnail.assert_not_called()

    def test_past_release_reconciles_public_read_only_without_claiming_schedule(self):
        self.approve()
        self.client.thumbnail.side_effect=PublishingError('Thumbnail pending')
        self.service.run(self.job['id'])
        video=remote(self.job['packet'])
        video['status']['privacyStatus']='public'
        del video['status']['publishAt']
        self.client.inspect.return_value=video
        with patch('studio.youtube_publishing.datetime',wraps=datetime) as clock:
            clock.now.return_value=datetime(2100,1,1,tzinfo=timezone.utc)
            self.service.run(self.job['id'])
        job=self.service.get(self.job['id'])
        self.assertEqual(job['state'],'published')
        self.assertTrue(job['verification']['published'])
        self.assertFalse(job['verification']['schedule_verified'])
        self.assertFalse(job['thumbnail_set'])
        self.client.initiate.assert_called_once()
        self.client.upload.assert_called_once()
        self.client.thumbnail.assert_called_once()

    def test_past_release_still_private_is_not_claimed_published(self):
        self.approve()
        self.client.inspect.return_value=remote(self.job['packet'],'processing')
        self.service.run(self.job['id'])
        with patch('studio.youtube_publishing.datetime',wraps=datetime) as clock:
            clock.now.return_value=datetime(2100,1,1,tzinfo=timezone.utc)
            self.service.run(self.job['id'])
        job=self.service.get(self.job['id'])
        self.assertEqual(job['state'],'processing')
        self.assertFalse(job['verification']['published'])
        self.client.upload.assert_called_once()
        self.client.thumbnail.assert_called_once()

    def test_past_release_reconciliation_checks_channel(self):
        self.approve()
        self.service.run(self.job['id'])
        self.client.channel.return_value='UC'+'b'*22
        calls=self.client.inspect.call_count
        with patch('studio.youtube_publishing.datetime',wraps=datetime) as clock:
            clock.now.return_value=datetime(2100,1,1,tzinfo=timezone.utc)
            self.service.run(self.job['id'])
        self.assertEqual(self.service.get(self.job['id'])['state'],'blocked')
        self.assertEqual(self.client.inspect.call_count,calls)
        self.client.upload.assert_called_once()

    def test_packet_change_after_approval_rejected(self):
        self.approve()
        job=self.service._load(self.job['id'])
        job['packet']['title']='Changed'
        self.service._save(job)
        self.service.run(job['id'])
        self.client.initiate.assert_not_called()


class PublisherTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'video.mp4'
        self.path.write_bytes(b'abcdefgh')
        self.packet=dict(video=dict(path=str(self.path),size=8,sha256=hashlib.sha256(b'abcdefgh').hexdigest()),schedule=dict(publish_at='2099-01-15T18:00:00Z'))
        self.client=YouTubePublisher(Mock(),opener=Mock())

    def test_session_rejects_external_or_insecure_urls(self):
        for url in ('http://www.googleapis.com/upload/youtube/v3/videos',
            'https://evil.example/upload/youtube/v3/videos','https://www.googleapis.com.evil.example/upload/youtube/v3/videos',
            'https://user@www.googleapis.com/upload/youtube/v3/videos',
            'https://www.googleapis.com:8443/upload/youtube/v3/videos',
            'https://www.googleapis.com/youtube/v3/videos','https://www.googleapis.com/upload/youtube/v3/videos#fragment'):
            with self.subTest(url=url),self.assertRaises(PublishingError): safe_session(url)
        self.assertEqual(safe_session(SESSION),SESSION)

    def test_offsets_are_strict(self):
        self.assertEqual(self.client.offset({}),0)
        self.assertEqual(self.client.offset({'Range':'bytes=0-3'}),4)
        for value in ('bytes=1-3','bytes=0--1','garbage','bytes 0-3/8'):
            with self.subTest(value=value),self.assertRaises(PublishingError): self.client.offset({'Range':value})

    def test_completed_session_does_not_send_media(self):
        self.client.session_status=Mock(return_value=(200,{},dict(id=VIDEO)))
        self.client.request=Mock()
        self.assertEqual(self.client.upload(SESSION,self.packet),VIDEO)
        self.client.request.assert_not_called()

    def test_resume_upload_sends_only_remaining_bytes(self):
        self.client.session_status=Mock(return_value=(308,{'Range':'bytes=0-3'},{}))
        self.client.request=Mock(return_value=(201,{},dict(id=VIDEO)))
        self.assertEqual(self.client.upload(SESSION,self.packet),VIDEO)
        args=self.client.request.call_args.args
        self.assertEqual(args[2],b'efgh')
        self.assertEqual(args[3]['Content-Range'],'bytes 4-7/8')

    def test_modified_file_withholds_final_chunk(self):
        self.path.write_bytes(b'abcdFAIL')
        self.client.session_status=Mock(return_value=(308,{'Range':'bytes=0-3'},{}))
        self.client.request=Mock()
        with self.assertRaises(PublishingError): self.client.upload(SESSION,self.packet)
        self.client.request.assert_not_called()

    def test_past_release_withholds_final_chunk(self):
        self.packet['schedule']['publish_at']='2000-01-15T18:00:00Z'
        self.client.session_status=Mock(return_value=(308,{},{}))
        self.client.request=Mock()
        with self.assertRaises(PublishingError): self.client.upload(SESSION,self.packet)
        self.client.request.assert_not_called()

    def test_bad_offset_or_completion_fails_closed(self):
        for response in ((308,{'Range':'bytes=0-99'},{}),(202,{},{})):
            self.client.session_status=Mock(return_value=response)
            self.client.request=Mock()
            with self.assertRaises(PublishingError): self.client.upload(SESSION,self.packet)
            self.client.request.assert_not_called()


class RemoteVerificationTests(unittest.TestCase):
    def setUp(self):
        self.packet=dict(channel_id='UC'+'a'*22,title='Title',description='Description',category_id='27',
            language='en',made_for_kids=False,contains_synthetic_media=False,contains_paid_promotion=False,
            schedule=dict(publish_at='2099-01-15T18:00:00Z'))

    def test_exact_remote_passes(self):
        self.assertTrue(check_remote(self.packet,remote(self.packet))['ready'])

    def test_metadata_privacy_schedule_disclosures_checked(self):
        changes=[('snippet','channelId','wrong'),('snippet','title','wrong'),
            ('snippet','description','wrong'),('status','privacyStatus','public'),
            ('status','publishAt','2099-01-15T19:00:00Z'),('status','selfDeclaredMadeForKids',True),
            ('status','containsSyntheticMedia',True),('paidProductPlacementDetails','hasPaidProductPlacement',True),
            ('status','uploadStatus','rejected'),('processingDetails','processingStatus','terminated')]
        for section,key,value in changes:
            with self.subTest(section=section,key=key):
                video=remote(self.packet)
                video[section][key]=value
                with self.assertRaises(PublishingError): check_remote(self.packet,video)

    def test_public_reconciliation_does_not_invent_timestamp_verification(self):
        video=remote(self.packet)
        video['status']['privacyStatus']='public'
        del video['status']['publishAt']
        with self.assertRaises(PublishingError): check_remote(self.packet,video)
        result=check_remote(self.packet,video,allow_published=True)
        self.assertTrue(result['published'])
        self.assertFalse(result['schedule_verified'])
        video['status']['selfDeclaredMadeForKids']=True
        with self.assertRaises(PublishingError): check_remote(self.packet,video,allow_published=True)

    def test_unlisted_not_mistaken_for_published(self):
        video=remote(self.packet)
        video['status']['privacyStatus']='unlisted'
        with self.assertRaises(PublishingError): check_remote(self.packet,video,allow_published=True)

    def test_unknown_processing_not_ready(self):
        video=remote(self.packet,'unknown')
        self.assertFalse(check_remote(self.packet,video)['ready'])


if __name__=='__main__': unittest.main()





