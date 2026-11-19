import tempfile
import unittest
from unittest.mock import Mock
from contextlib import nullcontext
from studio.youtube_release import digest
from studio.youtube_pin_worker import YouTubePinWorker, PinUnavailable
from studio.kit_chrome_gate import decide,record_receipt

class PinGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.channel='UC'+'a'*22;self.video='abcdefghijk';self.text='Approved comment'
        p={'channel_id':self.channel,'pinned_comment':self.text};p['packet_digest']=digest(p)
        self.job={'packet':p,'approval':{'digest':p['packet_digest']},'video_id':self.video,'state':'published'}
        self.pub=Mock();self.pub._load.return_value=self.job;self.pub.locked.side_effect=lambda _:nullcontext()
        self.comments=Mock();self.comments.expected_channel=self.channel
        self.record={'state':'posted','packet_digest':p['packet_digest'],'comment_id':'comment1'}
        self.comments._read.return_value={self.video:self.record}
        client=self.comments.client;client.channel.return_value={'id':self.channel}
        client.video.return_value={'id':self.video,'snippet':{'channelId':self.channel},'status':{'privacyStatus':'public'}}
        comment={'id':'comment1','snippet':{'textOriginal':self.text,'authorChannelId':{'value':self.channel}}}
        client.get_comment.return_value=comment;client.video_threads.return_value={'items':[{'snippet':{'topLevelComment':comment}}]}
        self.worker=YouTubePinWorker(self.pub,self.comments,self.tmp.name)
    def test_binding_is_exact_and_pin_unavailable(self):
        binding=self.worker.bind('job');self.assertIn('lc=comment1',binding['url'])
        self.assertFalse(self.worker.capability()['available'])
        with self.assertRaises(PinUnavailable):self.worker.pin()
        self.comments.client.comment.assert_not_called()
    def test_uncertain_private_wrong_text_rejected(self):
        self.record['state']='uncertain'
        with self.assertRaises(PinUnavailable):self.worker.bind('job')
        self.record['state']='posted';self.comments.client.video.return_value['status']['privacyStatus']='private'
        with self.assertRaises(PinUnavailable):self.worker.bind('job')
        self.comments.client.video.return_value['status']['privacyStatus']='public'
        self.comments.client.get_comment.return_value['snippet']['textOriginal']='changed'
        with self.assertRaises(PinUnavailable):self.worker.bind('job')
    def test_generic_actions_and_other_urls_rejected(self):
        self.worker.bind('job')
        for tool,args in [('computer',{'tabId':1,'action':'left_click','ref':'ref_1'}),('javascript_tool',{'tabId':1,'text':'alert(1)'}),('navigate',{'tabId':1,'url':'https://evil.invalid'})]:
            with self.assertRaises(PinUnavailable):self.worker.permit_read(tool,args,tab_id=1)
    def test_exact_hook_receipt_only_once(self):
        self.worker.bind('job');p=self.worker.permit_read('read_page',{'tabId':1},tab_id=1)
        e={'tool_name':p['tool'],'tool_input':p['args'],'tool_use_id':'call1'}
        self.assertTrue(decide(self.worker.folder,e));self.assertFalse(decide(self.worker.folder,e))
        self.assertTrue(record_receipt(self.worker.folder,dict(e,tool_response=[{'text':'observed page'}])))
        self.assertEqual(self.worker.receipt(p['id'])['tool_use_id'],'call1')
        with self.assertRaises(ValueError):self.worker.receipt(p['id'])

if __name__=='__main__':unittest.main()
