"""Mock-browser smoke test. No app server, OAuth, real assets or upload calls."""
import json
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright

class PublishingBrowserTests(unittest.TestCase):
    def test_review_and_confirm(self):
        root=Path(__file__).resolve().parents[1]/'studio'/'web'
        requests=[]
        packet=dict(channel_id='UC-fixture',video={'path':'fixture.mp4','size':10,'sha256':'fake'},title='Review fixture',description='Full approved description\nLink and notes',category_id='27',language='en',made_for_kids=False,contains_paid_promotion=False,contains_synthetic_media=False,primary_thumbnail={'path':'a.jpg','sha256':'a'},thumbnail_variants=[{'path':n+'.jpg','sha256':n} for n in 'abc'],schedule={'local_time':'2027-01-15T18:00','timezone':'Europe/London','publish_at':'2027-01-15T18:00:00Z'},initial_privacy='private',packet_digest='fixture-digest')
        job=dict(id='fixture',packet=packet,state='needs_review',review_nonce='fixture-nonce')
        def route(r):
            url=r.request.url.split('publishing.test',1)[1].split('?',1)[0]
            if url=='/api/publishing/status':value={'connected':True,'jobs':[job]}
            elif url=='/api/publishing/jobs/fixture':value=job
            elif url=='/api/publishing/approve':
                requests.append(r.request.post_data_json)
                job['state']='uploading';value=job
            elif '/thumbnail/' in url:
                r.fulfill(status=200,content_type='image/svg+xml',body='<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="#406f64"/></svg>');return
            else:
                name='publishing.html' if url=='/publishing' else url.lstrip('/')
                file=root/name
                if not file.is_file():r.fulfill(status=404,body='');return
                mime='text/html' if name.endswith('.html') else 'text/css' if name.endswith('.css') else 'text/javascript'
                r.fulfill(status=200,content_type=mime,body=file.read_text(encoding='utf-8-sig'));return
            r.fulfill(status=200,content_type='application/json',body=json.dumps(value))
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,channel="chrome")
            try:
                page=browser.new_page(viewport={'width':1280,'height':900})
                page.route('**/*',route)
                page.goto('http://publishing.test/publishing#fixture')
                page.locator('#job-state').filter(has_text='needs_review').wait_for()
                self.assertTrue(page.locator('#approve').is_disabled())
                self.assertIn('Full approved description',page.locator('#details').inner_text())
                self.assertEqual(page.locator('#details img').count(),3)
                page.locator('#consent').check()
                self.assertTrue(page.locator('#approve').is_enabled())
                page.locator('#approve').click()
                page.locator('#job-state').filter(has_text='uploading').wait_for()
                self.assertEqual(len(requests),1)
                self.assertEqual(requests[0]['packet_digest'],'fixture-digest')
                self.assertTrue(page.locator('#approve').is_disabled())
                page.locator('#refresh').click()
                self.assertEqual(len(requests),1)
                job['state']='scheduled'
                page.locator('#refresh').click()
                page.locator('#job-state').filter(has_text='scheduled').wait_for()
                self.assertTrue(page.locator('#resume').is_disabled())
                page.locator('#consent').check()
                self.assertTrue(page.locator('#resume').is_enabled())
                job['state']='published'
                page.locator('#refresh').click()
                page.locator('#job-state').filter(has_text='published').wait_for()
                page.locator('#consent').check()
                self.assertTrue(page.locator('#resume').is_disabled())
                self.assertIn('Published, but primary thumbnail was not confirmed',page.locator('#thumbnail-warning').inner_text())
                self.assertIn('does not authorize or start a native A/B test',page.locator('#review > .warning').inner_text())
                page.set_viewport_size({'width':390,'height':844})
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
            finally:browser.close()

if __name__=='__main__':unittest.main()
