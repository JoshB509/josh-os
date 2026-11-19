"""Real HTTPS browser pairing against an isolated handler; no live app services."""
from http.server import ThreadingHTTPServer
from pathlib import Path
import secrets
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest

from playwright.sync_api import expect, sync_playwright
from studio.pairing import Pairing
from studio.phone import PhoneAccess
from studio.server import Handler


class FixtureHandler(Handler):
    def route(self, method):
        if method=='GET' and self.path=='/external-link':
            origin='https://'+self.server.phone.host
            return self.send(('<title>External chat fixture</title><a href="'+origin+'/">Open JoshOS</a>').encode(),
                             mime='text/html',csp="default-src 'self'; frame-src "+origin+"; connect-src "+origin)
        if method == 'GET' and self.path == '/' and self.authorized():
            return self.send(b'<title>Paired fixture</title><p>This isolated browser is authenticated.</p>',
                             mime='text/html', cookie=True)
        return super().route(method)


class PairingBrowserTests(unittest.TestCase):
    def test_real_https_cookie_binding_and_automatic_pairing(self):
        openssl = shutil.which('openssl') or r'C:\Program Files\Git\usr\bin\openssl.exe'
        if not Path(openssl).is_file():self.skipTest('OpenSSL is needed for the temporary HTTPS certificate.')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run([openssl,'req','-x509','-newkey','rsa:2048','-nodes',
                            '-keyout',str(root/'key.pem'),'-out',str(root/'cert.pem'),
                            '-days','1','-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1'],
                           capture_output=True,check=True,timeout=30)
            server = ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler)
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(root/'cert.pem',root/'key.pem')
            server.socket = tls.wrap_socket(server.socket,server_side=True)
            server.token = secrets.token_urlsafe(32)
            server.phone = PhoneAccess.off()
            # This fixture uses an ephemeral port; production PhoneAccess validates a DNS hostname.
            server.phone.host = '127.0.0.1:'+str(server.server_port)
            server.pairing = Pairing(root,server.token)
            origin = 'https://'+server.phone.host
            thread = threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                with sync_playwright() as p:
                    browser=p.chromium.launch(headless=True,channel='chrome')
                    try:
                        owner=browser.new_context(ignore_https_errors=True,viewport={'width':390,'height':844})
                        stranger=browser.new_context(ignore_https_errors=True)
                        page=owner.new_page()
                        page.goto('https://localhost:'+str(server.server_port)+'/external-link')
                        # Real external-site iframe and POST are refused without creating state.
                        with page.expect_response(lambda r:r.url==origin+'/') as iframe_response:
                            page.evaluate("url => { const frame=document.createElement('iframe'); frame.src=url; document.body.append(frame); }",origin+'/')
                        self.assertEqual(iframe_response.value.status,403)
                        with page.expect_response(lambda r:r.url==origin+'/api/pairing/request') as post_response:
                            page.evaluate("url => fetch(url,{method:'POST',mode:'no-cors',body:'{}'}).then(()=>null)",origin+'/api/pairing/request')
                        self.assertEqual(post_response.value.status,403)
                        self.assertEqual(server.pairing.pending(),[])
                        # This actual anchor click emits cross-site/navigate/document.
                        with page.expect_response(lambda r:r.url==origin+'/' and r.request.is_navigation_request()) as landing:
                            page.get_by_role('link',name='Open JoshOS').click()
                        self.assertEqual(landing.value.status,200)
                        fetch_headers=landing.value.request.all_headers()
                        self.assertEqual(fetch_headers.get('sec-fetch-site'),'cross-site')
                        self.assertEqual(fetch_headers.get('sec-fetch-mode'),'navigate')
                        self.assertEqual(fetch_headers.get('sec-fetch-dest'),'document')
                        expect(page.get_by_role('button',name='Request a pairing code')).to_be_visible()
                        self.assertEqual(owner.cookies(),[])
                        self.assertEqual(server.pairing.pending(),[])
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
                        page.locator('#request').click()
                        expect(page.locator('#code')).to_have_text(re.compile(r'^[A-F0-9]{4}(?:-[A-F0-9]{4}){2}$'))
                        code=page.locator('#code').inner_text()
                        self.assertEqual(page.evaluate('document.cookie'),'')
                        self.assertNotIn(server.token,page.content())
                        other=stranger.new_page();other.goto(origin)
                        result=other.evaluate("""async code => (await fetch('/api/pairing/poll', {
                            method:'POST',headers:{'Content-Type':'application/json'},
                            body:JSON.stringify({code})})).json()""",code)
                        self.assertEqual(result,{'state':'unavailable'})
                        server.pairing.approve(code)
                        expect(page).to_have_title('Paired fixture',timeout=15000)
                        cookies=owner.cookies()
                        device=next(c for c in cookies if c['name']=='__Host-dj_device')
                        self.assertTrue(device['secure']);self.assertTrue(device['httpOnly'])
                        self.assertEqual(device['sameSite'],'Strict')
                        self.assertFalse(any(c['name'] in ('__Host-dj_pair','dj_session') for c in cookies))
                        self.assertEqual(page.evaluate('document.cookie'),'')
                        self.assertNotIn(device['value'],page.content())
                        self.assertNotIn(server.token,page.content())
                        self.assertEqual(other.evaluate("async () => (await fetch('/tasks')).status"),403)
                    finally:browser.close()
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
