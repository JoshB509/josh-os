"""Offline tests for project publishing and unlisted transport; no YouTube calls."""
import unittest
from unittest.mock import patch,Mock
from tests.test_youtube_publishing import PublishingServiceTests, remote
from studio.youtube_publishing import check_remote, PublishingError, YouTubePublisher
from studio.youtube_release import verify, ReleaseError
from studio.project_publishing import prepare

class UnlistedTests(PublishingServiceTests):
    def test_unlisted_has_no_schedule_and_verifies(self):
        source=dict(self.source,mode='unlisted',thumbnail_variants=[self.source['primary_thumbnail']])
        source.pop('local_time');source.pop('timezone');source.pop('utc_offset')
        job=self.service.prepare(source);p=job['packet']
        self.assertIsNone(p['schedule']);self.assertEqual(p['initial_privacy'],'unlisted');self.assertTrue(verify(p))
        v=remote(self.job['packet']);v['status']['privacyStatus']='unlisted';v['status'].pop('publishAt')
        self.assertTrue(check_remote(p,v)['ready'])
        v['status']['privacyStatus']='public'
        with self.assertRaises(PublishingError):check_remote(p,v,allow_published=True)
    def test_unlisted_upload_metadata(self):
        import json
        job=self.service.prepare(dict(self.source,mode='unlisted'))
        pub=YouTubePublisher(self.auth);pub.request=Mock(return_value=(200,{'Location':'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=x'},{}))
        pub.initiate(job['packet']);body=json.loads(pub.request.call_args.args[2])
        self.assertEqual(body['status']['privacyStatus'],'unlisted');self.assertNotIn('publishAt',body['status'])
    def test_comment_helper_with_real_publishing_service(self):
        from studio.youtube_comments import Comments
        from studio.youtube_release import digest
        text='Get the pack https://example.com/pack';vid='abcdefghijk'
        job=self.service.prepare(dict(self.source,pinned_comment=text))
        saved=self.service._load(job['id']);saved.update(state='published',video_id=vid,approval={'digest':job['packet']['packet_digest']})
        self.service._save(saved)
        self.assertNotIn('approval',self.service.get(job['id']))
        client=Mock();channel=self.source['channel_id'];client.channel.return_value={'id':channel}
        client.video.return_value={'id':vid,'snippet':{'channelId':channel},'status':{'privacyStatus':'public'}}
        client.video_threads.return_value={'items':[]}
        comment={'id':'comment1','snippet':{'textOriginal':text,'authorChannelId':{'value':channel}}}
        thread={'id':'thread1','snippet':{'videoId':vid,'topLevelComment':comment}}
        client.comment.return_value=thread;client.get_thread.return_value=thread
        comments=Comments(self.root,client,publishing=self.service);comments.expected_channel=channel
        link={'source_type':'pinned_comment','video_id':vid,'url':'https://example.com/pack'}
        result=comments.lead_magnet_comment(job['id'],vid,text,link)
        self.assertEqual(result['state'],'posted');self.assertEqual(result['comment_id'],'comment1')
        comments.lead_magnet_comment(job['id'],vid,text,link);client.comment.assert_called_once()

    def test_invalid_mode(self):
        with self.assertRaises(ReleaseError):self.service.prepare(dict(self.source,mode='public'))
    def test_project_revision_and_preview_digest_required(self):
        view={'revision':3,'preview_digest':'abc','blockers':{'unlisted':[]},'pinned_comment':'comment'}
        with patch('studio.project_publishing.preview',return_value=(view,{})):
            with self.assertRaises(ReleaseError):prepare(None,None,self.service,'p',{'revision':2,'preview_digest':'abc','mode':'unlisted'})
            with self.assertRaises(ReleaseError):prepare(None,None,self.service,'p',{'revision':3,'preview_digest':'changed','mode':'unlisted'})

if __name__=='__main__':unittest.main()
