
import json
import unittest
from pathlib import Path
from test_video_workflow_browser import VideoWorkflow, release, WEB

class PublishPanel(unittest.TestCase):
    setUpClass = classmethod(VideoWorkflow.setUpClass.__func__)
    tearDownClass = classmethod(VideoWorkflow.tearDownClass.__func__)
    setUp = VideoWorkflow.setUp
    open = VideoWorkflow.open
    click = VideoWorkflow.click
    posts = VideoWorkflow.posts
    def test_publish_panel(self):
        self.open(self.confirmed)
        page=self.page
        self.assertEqual(page.locator('.pk-still,.pk-pub').count(),0)
        self.assertNotIn('Sam drafts it', page.locator('.pk').inner_text())
        value=dict(project_id=self.confirmed,revision=self.store.get(self.confirmed)['revision'],preview_digest='fingerprint',
          title='The confirmed title',description='',pinned_comment='',thumbnail_url='',publish_date='',publish_time='',timezone='Asia/Dubai',
          completed_edit=False,connected=False,blockers=dict(unlisted=['No edit'],schedule=['No edit']),disclosures={},comment_delivery='manual')
        requests=[]
        def preview(route):
            if route.request.method=='POST':
                requests.append(route.request.post_data_json)
                return route.fulfill(status=200,content_type='application/json',body=json.dumps(dict(id='job',review_nonce='nonce',packet=dict(title=value['title'],description=value['description'],pinned_comment=value['pinned_comment'],packet_digest='packet'))))
            route.fulfill(status=200,content_type='application/json',body=json.dumps(value))
        page.route('**/publish-preview',preview)
        approvals=[]
        def approve(route):
            approvals.append(route.request.post_data_json)
            route.fulfill(status=200,content_type='application/json',body='{"state":"uploading"}')
        page.route('**/api/publishing/approve',approve)
        page.click('[data-vp-publish]')
        page.wait_for_selector('.vp-publish-dialog .missing')
        self.assertEqual(page.locator('.vp-publish-dialog .missing').count(),4)
        self.assertEqual(page.locator('[data-vp-publish-mode]').count(),2)
        self.assertTrue(page.locator('[data-vp-publish-mode="unlisted"]').is_disabled())
        self.assertEqual(requests,[])
        page.keyboard.press('Escape')
        self.assertEqual(page.locator('.vp-publish-dialog').count(),0)
        self.assertEqual(approvals,[])
        value.update(title='The confirmed title',description='Real description',pinned_comment='Get the pack',thumbnail_url='/assets/'+self.shot,
          publish_date='2027-01-15',publish_time='17:00',completed_edit=True,connected=True,blockers=dict(unlisted=[],schedule=[]))
        page.click('[data-vp-publish]')
        page.wait_for_selector('[data-disclosure]')
        for select in page.locator('[data-disclosure]').all():select.select_option('false')
        self.assertTrue(page.locator('[data-vp-publish-mode="unlisted"]').is_enabled())
        page.screenshot(path=str(Path(__file__).resolve().parents[1]/'Evidence'/'publish-panel-review.png'))
        page.set_viewport_size(dict(width=390,height=844))
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
        page.click('[data-vp-publish-mode="unlisted"]')
        page.wait_for_function("document.querySelector('[data-vp-publish-notice]').textContent==='Upload requested.'")
        self.assertEqual(len(requests),1);self.assertEqual(len(approvals),1)
        self.assertEqual(requests[0]['mode'],'unlisted')
        self.assertEqual(requests[0]['preview_digest'],'fingerprint')
        self.assertTrue(page.locator('[data-vp-publish-mode="unlisted"]').is_disabled())
        page.click('[data-vp-publish-close]')
        self.assertEqual(page.locator('.vp-publish-dialog').count(),0)
        self.assertEqual(self.errors,[])

if __name__=='__main__':unittest.main()

