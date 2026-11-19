import base64,json,mimetypes,os,secrets,subprocess,sys,threading,urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from . import __version__
from .store import Store,Conflict,STAGES
from .jobs import Jobs
from .writing import Writing
from .control import Control
from .writing_context import Context
from .inspiration import Inspiration
from .workflow import Workflow
from .lessons import Lessons
from .emails import Emails
from .tasks import Tasks
from .thumbnail_creator import ThumbnailCreator
from .admin import Admin
from .youtube_recording import Recording
from .project_folders import ProjectFolders
from .team import Team
from .results import VideoResults
from .people import People
from .learners import Learners
from .kit_drafts import KitDrafts
from .discord_bridge import Bridge as DiscordBridge
from .course_lessons import CourseLessons
from .carousels import Carousels
from .inspo import Inspo
from .feedback import Feedback
from .edit_review import EditReview,STANDIN
from .video_pieces import VideoPieces
from .video_thumbnails import VideoThumbnails
from .youtube_ideas import YouTubeIdeas
from .reply_queue import NotAllowed
from .scorecard import Scorecard
from .business import Business
from .youtube_publishing import PublishingService
from .edit_flow import EditFlow
from .phone import PhoneAccess
from .pairing import Pairing, PairingError, RateLimited, SESSION_TTL
from .agent import Agents,AgentError

def preview(data_root,file,width):
    import hashlib,io
    from PIL import Image
    st=file.stat();key=hashlib.sha1(f'{file.resolve()}|{st.st_size}|{st.st_mtime_ns}|{width}'.encode()).hexdigest()
    folder=Path(data_root)/'previews';folder.mkdir(exist_ok=True);out=folder/(key+'.jpg')
    if out.is_file():return out.read_bytes()
    with Image.open(file) as im:
        im.draft('RGB',(width,width*2))
        if im.mode in ('RGBA','LA','P'):
            im=im.convert('RGBA');bg=Image.new('RGB',im.size,(255,255,255));bg.paste(im,mask=im.split()[-1]);im=bg
        else:im=im.convert('RGB')
        im.thumbnail((width,width*4),Image.LANCZOS)
        buf=io.BytesIO();im.save(buf,'JPEG',quality=84,optimize=True,progressive=True)
    tmp=out.with_suffix('.tmp'+str(threading.get_ident()));tmp.write_bytes(buf.getvalue());tmp.replace(out)
    return buf.getvalue()

class Server(ThreadingHTTPServer):
    # Cloudflare fans out a page asset burst into concurrent origin connections.
    request_queue_size=128
    daemon_threads=True
    def __init__(self,root,data,port=0,demo=False,command=None,maintenance_hold_dispatch=False):
        self.maintenance_hold_dispatch=bool(maintenance_hold_dispatch)
        self.root=Path(root).resolve();self.store=Store(data);self.jobs=Jobs(self.store,self.root,command)
        self.inspiration=Inspiration(self.store,self.root);self.writing=Writing(self.store);self.control=Control(self.store,self.root,self.jobs)
        self.workflow=Workflow(self.store,self.root,self.jobs)
        self.lessons=Lessons(self.store,self.root) if (self.root/'Data/lesson-catalog.json').is_file() else None
        self.emails=Emails(self.store,self.root)
        self.tasks=Tasks(self.store)
        self.project_folders=ProjectFolders(self.store)
        self.youtube_recording=Recording(self.store,self.root)
        self.thumbnail_creator=ThumbnailCreator(self.store)
        self.team=Team(self.store);self.carousels=Carousels(self.store);self.inspo=Inspo(self.store);self.feedback=Feedback(self.store);self.agents=Agents(self.store,self.root,self.team);self.agents.feedback=self.feedback;self.agents.emails=self.emails;self.results=VideoResults(self.store);self.agents.results=self.results;self.people=People(self.store);self.agents.people=self.people;self.learners=Learners(self.store,self.agents);self.kit_drafts=KitDrafts(self.store,self.root);self.agents.kit_drafts=self.kit_drafts;self.courses=CourseLessons(self.store,self.root,self.lessons);self.courses.agents=self.agents;self.agents.courses=self.courses;self.edit_review=EditReview(self.store,self.project_folders);self.agents.edit_review=self.edit_review;self.pieces=VideoPieces(self.store,self.project_folders,self.youtube_recording,self.edit_review);self.video_thumbs=VideoThumbnails(self.store,self.project_folders,self.agents);self.agents.video_thumbs=self.video_thumbs;self.edit_flow=EditFlow(self.store,self.root,self.agents,self.project_folders,self.edit_review);self.agents.edit_flow=self.edit_flow;self.youtube_ideas=YouTubeIdeas(self.root);self.agents.youtube_ideas=self.youtube_ideas;self.inspiration.youtube_ideas=self.youtube_ideas;self.scorecard=Scorecard(self.store,self.root,self.store.root)
        from .kit_visual_host import wire as wire_kit_visuals
        self.kit_visual_review=wire_kit_visuals(self)
        from .kit_visual_dispatch import KitVisualDispatch
        self.agents.kit_visual_dispatch=KitVisualDispatch(self.kit_visual_review.service)
        self.kit_visual_review.dispatch=self.agents.kit_visual_dispatch
        from .local_asset_chat import LocalAssetChat
        self.local_asset_chat=LocalAssetChat(self.store.root/'local-assets')
        self.agents.local_asset_chat=self.local_asset_chat
        self.agents.learners=self.learners;self.agents.scorecard=self.scorecard
        self.publishing=PublishingService(self.store.root);self.agents.publishing=self.publishing
        from .youtube_manage import UpdateService
        self.video_updates=UpdateService(self.store.root);self.agents.video_updates=self.video_updates
        from .chat_upload_approval import ChatUploadApproval
        self.chat_upload_approval=ChatUploadApproval(self.publishing, lambda pid:self.store.get(pid)['revision'])
        self.publishing_nonces={};self.publishing_nonce_lock=threading.Lock()
        self.phone=PhoneAccess(self.root,self.store.root)
        self.token=self.phone.key or secrets.token_urlsafe(32)
        self.pairing=Pairing(self.store.root,self.token)
        if not port:port=self.phone.port
        self.admin=Admin(self.store.root,self.root)
        self.agents.admin=self.admin
        self.discord=DiscordBridge(self).start();self.video_thumbs.notify=self.discord.notify;self.agents.notify=self.discord.notify;self.agents.send_shortlist=lambda pid:self.discord.send_shortlist(pid,self.edit_review)
        threading.Thread(target=self.admin.loop,daemon=True).start()
        threading.Thread(target=self.kit_loop,daemon=True).start()
        # Max's daily business brief (07:00 Dubai) and nudges. See studio/business.py.
        self.business=Business(self.store,self.store.root,self.scorecard,self.tasks,self.discord,root=self.root);self.agents.business=self.business
        self.business.maintenance_hold_dispatch=self.maintenance_hold_dispatch
        if not self.maintenance_hold_dispatch:threading.Thread(target=self.business.loop,daemon=True).start()
        # The team's own Discord messages to Josh, Anouk's read-only inbox check and her proposed replies. See studio/notices.py.
        from .notices import wire as wire_notices
        self.notices,self.replies=wire_notices(self)
        # Max's daily check of the goals, calendar and outstanding work, nudging through the team's own message rules.
        from .oversight import Oversight
        self.oversight=Oversight(self.store,self.store.root,self.business,self.notices,self.agents,self.edit_flow,self.emails,self.courses,self.replies);self.agents.oversight=self.oversight
        if not self.maintenance_hold_dispatch:threading.Thread(target=self.oversight.loop,daemon=True,name='max-oversight').start()
        # Anouk's YouTube comment replies (forward only, off until Josh connects and switches them on). studio/youtube_comments.py
        from .youtube_comments import wire as wire_comments
        self.youtube_comments,self.youtube_comments_auth=wire_comments(self);self.agents.youtube_comments=self.youtube_comments
        # Anouk's quick review: email and comment replies in one queue, each sent only by Josh's click on that exact version. studio/reply_queue.py
        from .reply_queue import ReplyQueue
        self.reply_queue=ReplyQueue(self.replies,self.youtube_comments,self.agents.run)
        # Ready to film: one preparation shared by the Film button and Sam. See studio/filming.py.
        from .filming import Filming
        from .notices import busy_reasons
        self.filming=Filming(self.store,self.store.root,self.project_folders,self.pieces,self.youtube_recording,lambda:busy_reasons(self));self.agents.filming=self.filming
        # Saskia's approved standalone guides, delivered to the CareerOS Downloads folder in Drive. See studio/guide_delivery.py.
        from .guide_delivery import GuideDelivery
        self.guide_delivery=GuideDelivery(self.store,self.root);self.agents.guide_delivery=self.guide_delivery
        # What's really left before a video goes on YouTube, read from the existing workflow. See studio/release_ready.py.
        from .release_ready import Release
        # Optional Frame.io review for Tariq's exports. Never automatic, never public. See studio/frameio_review.py.
        from .frameio_review import FrameioReview
        from .archive import Archive
        self.frameio=FrameioReview(self.store,self.root,self.edit_flow);self.agents.frameio=self.frameio;self.archive=Archive(self.store,self.root,self.edit_flow,self.project_folders,self.frameio,self.agents);self.agents.archive=self.archive
        from .clean_edit import CleanEdit
        from .autocut_driver import Driver as AutoCutDriver
        self.clean_edit=CleanEdit(self.edit_flow.folder,driver_factory=lambda:AutoCutDriver());self.agents.clean_edit=self.clean_edit;self.edit_flow.clean_edit=self.clean_edit
        from .chat_edit_actions import ChatEditActions
        self.agents.chat_edit_actions=ChatEditActions(self.store,self.clean_edit,self.archive,flow=self.edit_flow)
        self.release=Release(self.store,self.project_folders,self.publishing.root,lambda:self.publishing.status()['jobs'],self.agents)
        # Sam's chapters and description from the final export (studio/chapters.py). Made after Josh approves the export.
        from .chapters import Chapters
        self.release.chapters=Chapters(self.store,self.edit_flow,self.release,self.agents)
        from .chat_release_actions import ChatReleaseActions
        self.agents.chat_release_actions=ChatReleaseActions(self.release)
        # Tariq's finish requests left as files (studio/edit_flow.py run_requests), picked up once Josh OS is up.
        threading.Thread(target=lambda:(__import__('time').sleep(20),self.edit_flow.run_requests()),daemon=True,name='tariq-requests').start()
        self.demo=demo;self.picker_lock=threading.Lock()
        super().__init__(('127.0.0.1',port),Handler)
        threading.Thread(target=self.project_folders.loop,daemon=True).start()
        # Evidence for review that this process loaded the approval-lineage fix: hashes of the loaded functions and a
        # self-check on a throwaway store. Read only. studio/lineage_diagnostic.py
        self.started_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat();self.lineage_diagnostic=None
        def _diag():
            from .lineage_diagnostic import report
            try:self.lineage_diagnostic=report(self.root,self.store.root,self.server_port,self.started_at)
            except Exception as e:self.lineage_diagnostic=dict(pid=__import__('os').getpid(),error=str(e)[:300])
        threading.Thread(target=_diag,daemon=True,name='lineage-diagnostic').start()
    def server_close(self):
        self.project_folders.stop.set()
        super().server_close()
    def kit_draft_action(self,eid,d):
        """Josh approves the exact draft Laia prepared, or asks Josh OS to save it in Kit. Never dates or sends."""
        email=next((e for e in self.emails.all() if e['id']==eid),None)
        if not email:raise KeyError('Email not found')
        if d.get('action')=='approve':return self.kit_drafts.approve(str(d.get('id','')),str(d.get('hash','')),email)
        if d.get('action')=='write':return self.kit_drafts.write(str(d.get('id','')),email,self.agents.run)
        raise ValueError('Unknown action')
    def kit_loop(self):
        """Every ten minutes, read Kit status. Never schedule Ready emails."""
        import time
        time.sleep(20)
        while True:
            try:self.emails.kit_cycle()
            except Exception as e:
                try:self.emails.kit.note(last_error=str(e)[:300])
                except Exception:pass
            time.sleep(600)
    def edit_flow_action(self,pid,action,d):
        """Tariq's editing workflow. Every action names the exact revision it acts on."""
        f=self.edit_flow
        if action=='sources':return f.register(pid,d['camera'],d['mic'],d.get('note',''))
        if action=='analyse':return f.analyse(pid,d['sources_id'],bool(d.get('editorial',True)))
        if action=='cancel':return f.cancel(pid)
        if action=='revise':return f.revise(pid,d['proposal_id'],d['digest'],d.get('changes') or [],d.get('words') or [],d.get('note',''))
        if action=='approve':return f.approve_script(pid,d['proposal_id'],d['digest'],d.get('note',''),auto=True)
        if action=='go':return f.go_ahead(pid, fresh=True) if d.get('fresh') is True else f.go_ahead(pid)
        if action=='build_again':return f.build_again(pid)
        if action=='anim_notes':return f.set_anim_notes(pid,d.get('text',''))
        if action=='slides':
            f.clean_gate(pid)
            name=self.store.get(pid)['name'];notes=(f.anim_notes(pid).get('text') or '').strip()
            ask='Make the animation slides and shot list for this video from its current script, following the video-editing-prep skill.'+(' Josh\'s notes for the animations are in your brief.' if notes else '')
            r=self.agents.start_deck_job('tariq',pid,name,dict(deck_change=ask),'')
            if r and r[0].get('action')=='failed':raise ValueError(r[0].get('error') or 'Tariq could not start the slides.')
            return dict(state='started')
        if action=='preview':return f.preview(pid,d['proposal_id'],d['event_id'],float(d.get('context_s',2)))
        if action=='premiere':return f.bridge_call('tq_probe',dict(master=d.get('master') or 'GREEN_MASTER'))
        if action=='build':
            check=f.bridge_call('tq_probe',dict(master=d.get('master') or 'GREEN_MASTER'))
            if not check.get('ok'):raise ValueError('Premiere is not ready for the cut: '+' '.join(check.get('notes') or ['see the check']))
            return f.build_cut(pid,d['proposal_id'],d['digest'],int(check['frame_ticks']),d.get('master'))
        if action=='proof':
            from .edit_flow import native_proof
            return native_proof(f,pid,d.get('param') or 'Choke',float(d.get('delta',4)))
        if action=='master':return f.make_master(pid,d['source'],d.get('reference'),bool(d.get('add_shadow')))
        if action=='native-test':
            from .edit_flow import native_test
            return native_test(f,pid,d['project'],d['source'],d.get('reference'),bool(d.get('add_shadow')))
        if action=='visuals':return f.visuals(pid,d['cut_id'],d.get('notes'),d.get('storyboard_digest'),bool(d.get('approve')),auto=True)
        if action=='final':return f.final(pid,d['visuals_id'],reuse_final_id=d.get('reuse_final_id'))
        raise KeyError('Unknown editing action')

    @property
    def url(self):return 'http://127.0.0.1:%d/?token=%s'%(self.server_port,self.token)

class Handler(BaseHTTPRequestHandler):
    server_version='DaJoshiResolve'
    def log_message(self,fmt,*args):pass
    def from_josh(self):
        # Edits typed in the app come from the browser; the team's own tasks call the API directly.
        return self.headers.get('Sec-Fetch-Site')=='same-origin' or bool(self.headers.get('Origin'))
    def authorized(self):
        self.device_authenticated=False
        local='127.0.0.1:'+str(self.server.server_port);phone=getattr(self.server,'phone',None) or PhoneAccess.off()
        if self.headers.get('Host') not in (local,*phone.hosts()):return False
        origin=self.headers.get('Origin')
        if origin and origin not in ('http://'+local,*phone.origins()):return False
        q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''))
        except Exception:pass
        if self.device_cookie_valid(cookie):
            # Paired sessions never fall back to a shared credential on the page.
            if origin and origin!=self.pairing_origin():return False
            if self.headers.get('Sec-Fetch-Site') not in (None,'none','same-origin'):return False
            if getattr(self,'command','GET') not in ('GET','HEAD'):
                if origin!=self.pairing_origin() or self.headers.get('Sec-Fetch-Site')!='same-origin':return False
            self.device_authenticated=True
            return True
        supplied=self.headers.get('X-DJ-Token') or q.get('token',[''])[0] or (cookie['dj_session'].value if 'dj_session' in cookie else '')
        return secrets.compare_digest(supplied,self.server.token)
    def pairing_origin(self):
        phone=getattr(self.server,'phone',None) or PhoneAccess.off()
        return 'https://'+phone.host if self.headers.get('Host') in phone.hosts() else None
    def device_cookie_valid(self,cookie):
        pairing=getattr(self.server,'pairing',None);origin=self.pairing_origin()
        if not pairing or not origin or '__Host-dj_device' not in cookie:return False
        try:return pairing.valid_session(cookie['__Host-dj_device'].value,origin)
        except Exception:return False
    def page_token(self):
        return '' if getattr(self,'device_authenticated',False) else self.server.token
    def publishing_browser(self, write=False):
        """Separate browser review gate; model tool calls never count as approval."""
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''))
        except Exception:return False
        legacy='dj_session' in cookie and secrets.compare_digest(cookie['dj_session'].value,self.server.token)
        if not legacy and (not getattr(self.server,'pairing',None) or not self.device_cookie_valid(cookie)):return False
        if self.headers.get('Sec-Fetch-Site')!='same-origin':return False
        if write:
            phone=getattr(self.server,'phone',None) or PhoneAccess.off()
            if self.headers.get('Origin') not in ('http://127.0.0.1:'+str(self.server.server_port),*phone.origins()):return False
        return True
    def publishing_review(self, job):
        import time
        result=dict(job)
        if self.publishing_browser():
            nonce=secrets.token_urlsafe(32)
            with self.server.publishing_nonce_lock:
                now=time.monotonic()
                self.server.publishing_nonces={k:v for k,v in self.server.publishing_nonces.items() if v[2]>now}
                self.server.publishing_nonces[nonce]=(job['id'],job['packet']['packet_digest'],now+900)
            result['review_nonce']=nonce
        return result
    def update_review(self, job):
        import time
        result=dict(job)
        if self.publishing_browser():
            nonce=secrets.token_urlsafe(32)
            with self.server.publishing_nonce_lock:
                now=time.monotonic()
                self.server.publishing_nonces={k:v for k,v in self.server.publishing_nonces.items() if v[2]>now}
                self.server.publishing_nonces[nonce]=('update:'+job['id'],job['digest'],now+900)
            result['review_nonce']=nonce
        return result
    def publishing_consume(self, data):
        import time
        if not self.publishing_browser(write=True):raise ValueError('Approve from the release review page in CareerOS.')
        with self.server.publishing_nonce_lock:
            item=self.server.publishing_nonces.pop(str(data.get('review_nonce','')),None)
        if not item or item[2]<time.monotonic() or item[:2]!=(data.get('job_id'),data.get('packet_digest')):
            raise ValueError('Review expired or changed. Open the release again before approving.')
    def send(self,data,status=200,mime='application/json',cookie=False,csp=None,cache=None,pairing_cookies=()):
        if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control',cache or 'no-store')
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',csp or "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; font-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        if cookie and not getattr(self,'device_authenticated',False):self.send_header('Set-Cookie','dj_session='+self.server.token+'; HttpOnly; SameSite=Strict; Path=/'+('; Secure' if self.pairing_origin() else ''))
        for value in pairing_cookies:self.send_header('Set-Cookie',value)
        self.end_headers();self.wfile.write(data)
    def pairing_route(self,method,path):
        """Public bootstrap only. No HTTP approval or request listing, even authenticated."""
        origin=self.pairing_origin()
        if not origin or (self.headers.get('Origin') and self.headers.get('Origin')!=origin):
            return self.send({'error':'Pairing is unavailable for this origin.'},403)
        if method=='GET' and path in ('/','/pairing.js'):
            # A chat/email link may navigate across sites to this inert public page.
            # No request, cookie or session is created until the strict same-origin POST.
            top_level_link=(path=='/' and self.headers.get('Sec-Fetch-Site') in ('cross-site','same-site')
                            and self.headers.get('Sec-Fetch-Mode')=='navigate'
                            and self.headers.get('Sec-Fetch-Dest')=='document')
            if self.headers.get('Sec-Fetch-Site') not in (None,'none','same-origin') and not top_level_link:
                return self.send({'error':'Open this address directly to pair.'},403)
            name='pairing.html' if path=='/' else 'pairing.js'
            return self.send((Path(__file__).parent/'web'/name).read_bytes(),mime='text/html; charset=utf-8' if path=='/' else 'text/javascript; charset=utf-8')
        if method!='POST' or path not in ('/api/pairing/request','/api/pairing/poll'):
            return self.send({'error':'Not found.'},404)
        if (self.headers.get('Origin')!=origin or self.headers.get('Sec-Fetch-Site')!='same-origin'
                or self.headers.get('Content-Type')!='application/json'
                or self.headers.get('Transfer-Encoding')):
            return self.send({'error':'Same-origin browser request required.'},403)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=512:raise PairingError('Invalid pairing request.')
            self.connection.settimeout(5)
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict):raise PairingError('Invalid pairing request.')
            if path=='/api/pairing/request':
                if data:raise PairingError('Invalid pairing request.')
                result,secret=self.server.pairing.issue(origin)
                return self.send(result,pairing_cookies=('__Host-dj_pair='+secret+'; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=300',))
            if set(data)!={'code'} or not isinstance(data['code'],str):raise PairingError('Invalid pairing request.')
            cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''))
            secret=cookie['__Host-dj_pair'].value if '__Host-dj_pair' in cookie else ''
            state,session=self.server.pairing.poll(data['code'],secret,origin)
            cookies=() if not session else (
                '__Host-dj_device='+session+'; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age='+str(SESSION_TTL),
                '__Host-dj_pair=; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            return self.send(dict(state=state),pairing_cookies=cookies)
        except RateLimited:
            return self.send({'error':'Too many requests. Wait a minute before trying again.'},429)
        except (ValueError,TypeError):
            return self.send({'error':'Invalid pairing request.'},400)
        except Exception:
            # Never emit DB paths, hashes, cookies or credential details in errors/logs.
            return self.send({'error':'Pairing is temporarily unavailable.'},503)
    def image(self,file,mime,cache):
        """Serve an image, or a small cached JPEG preview when ?w= is given."""
        q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        # No ?w= means the full image. Before 11 Nov 2026 a missing w became 64 here, so the team picture and every desk
        # scene were sent as 64-pixel previews and stretched across the page.
        w=q.get('w')
        try:width=max(64,min(1600,int(w[0]))) if w and w[0] not in ('','0') else 0
        except ValueError:width=0
        if width and mime.startswith('image/'):
            try:return self.send(preview(self.server.store.root,Path(file),width),mime='image/jpeg',cache=cache)
            except Exception:pass
        return self.send(Path(file).read_bytes(),mime=mime,cache=cache)
    def do_GET(self):self.route('GET')
    def do_POST(self):self.route('POST')
    def do_PUT(self):self.route('PUT')
    def route(self,method):
        path=urllib.parse.urlsplit(self.path).path;parts=path.strip('/').split('/');s=self.server
        if path.startswith('/api/pairing') or path=='/pairing.js':return self.pairing_route(method,path)
        if not self.authorized():
            if method=='GET' and path=='/' and self.pairing_origin():return self.pairing_route(method,path)
            return self.send({'error':'Open CareerOS from its desktop shortcut or pair this browser at the home page.'},403)
        try:
            if (path in ('/kit-visuals','/kit-visual-review.js','/kit-visual-review.css') or path.startswith('/api/kit-visual-review/')) and s.kit_visual_review.route(self,method,path):return
            from .lead_magnets import route as lead_magnets_route
            if lead_magnets_route(self,method,path):return
            if path.startswith('/api/local-assets/'):
                from .local_asset_chat import route as local_asset_route
                if local_asset_route(self,method,path):return
            if method=='GET':
                if path=='/publishing':
                    return self.send((Path(__file__).parent/'web/publishing.html').read_bytes(),mime='text/html; charset=utf-8',cookie=True)
                if path in ('/publishing.js','/publishing.css'):
                    return self.send((Path(__file__).parent/'web'/path[1:]).read_bytes(),mime='text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
                if path=='/api/publishing/status':return self.send(s.publishing.status())
                if path=='/publishing-updates':
                    return self.send((Path(__file__).parent/'web/publishing-updates.html').read_bytes(),mime='text/html; charset=utf-8',cookie=True)
                if path=='/publishing-updates.js':
                    return self.send((Path(__file__).parent/'web/publishing-updates.js').read_bytes(),mime='text/javascript; charset=utf-8')
                if path=='/api/publishing/updates':return self.send(s.video_updates.status())
                if len(parts)==4 and parts[:3]==['api','publishing','updates']:return self.send(self.update_review(s.video_updates.get(parts[3])))
                if len(parts)==6 and parts[:3]==['api','publishing','jobs'] and parts[4]=='thumbnail':
                    job=s.publishing.get(parts[3]);index=int(parts[5])
                    if index not in (0,1,2):raise ValueError('Unknown thumbnail')
                    asset=job['packet']['thumbnail_variants'][index]
                    from .youtube_release import file_identity
                    if file_identity(asset['path'])!=asset:raise ValueError('Thumbnail changed. Prepare and review again.')
                    file=Path(asset['path']);mime=mimetypes.guess_type(str(file))[0]
                    if mime not in ('image/png','image/jpeg'):raise ValueError('Unsupported preview image')
                    return self.send(file.read_bytes(),mime=mime)
                if len(parts)==4 and parts[:3]==['api','publishing','jobs']:
                    return self.send(self.publishing_review(s.publishing.get(parts[3])))
                if path in ('/project-folder-merge.js','/project-folders.js'):
                    return self.send((Path(__file__).parent/'web'/path[1:]).read_bytes(),mime='text/javascript; charset=utf-8')
                if path=='/youtube-recording.js':
                    return self.send((Path(__file__).parent/'web/youtube-recording.js').read_bytes(),mime='text/javascript; charset=utf-8')
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='recording':
                    return self.send(s.youtube_recording.state(parts[2]))
                if path in ('/thumbnail-creator.js','/thumbnail-creator.css'):
                    return self.send((Path(__file__).parent/'web'/path[1:]).read_bytes(),mime='text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
                if len(parts)==6 and parts[:2]==['api','projects'] and parts[3:5]==['thumbnail-creator','assets']:
                    file,mime=s.thumbnail_creator.source_asset(parts[2],parts[5])
                    return self.send(file.read_bytes(),mime=mime,cache='private, max-age=31536000, immutable')
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='thumbnail-creator':
                    return self.send(s.thumbnail_creator.workspace(parts[2]))
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='publish-preview':
                    from .project_publishing import preview
                    return self.send(preview(s.store,s.release,s.publishing,parts[2])[0])
                if path=='/admin':
                    text=(Path(__file__).parent/'web/admin.html').read_text(encoding='utf-8').replace('__TOKEN__',self.page_token())
                    policy="default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; font-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
                    return self.send(text.encode(),mime='text/html; charset=utf-8',cookie=True,csp=policy)
                if path in ('/admin.js','/admin.css','/brand-fonts.css','/brand.css'):
                    return self.send((Path(__file__).parent/'web'/path[1:]).read_bytes(),mime='text/css; charset=utf-8' if path.endswith('.css') else 'text/javascript; charset=utf-8')
                if path.startswith('/api/admin/'):
                    q={k:v[0] for k,v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
                    if path=='/api/admin/clients':return self.send(s.admin.clients_page())
                    if path=='/api/admin/client':return self.send(s.admin.client(q.get('id','')))
                    if path=='/api/admin/text':return self.send(s.admin.text(q.get('id',''),q.get('path','')))
                    if path=='/api/admin/customers':return self.send(s.admin.customers_page())
                    if path=='/api/admin/connections':return self.send(s.admin.connections())
                if path=='/tasks':
                    text=(Path(__file__).parent/'web/tasks.html').read_text(encoding='utf-8').replace('__TOKEN__',self.page_token())
                    return self.send(text.encode(),mime='text/html; charset=utf-8',cookie=True)
                if path in ('/tasks.js','/tasks.css','/task-links.js'):
                    return self.send((Path(__file__).parent/'web'/path[1:]).read_bytes(),mime='text/css; charset=utf-8' if path.endswith('.css') else 'text/javascript; charset=utf-8')
                if path=='/api/tasks':return self.send(s.tasks.all())
                if path=='/api/team':return self.send(s.team.all())
                if path.startswith('/team-office/'):
                    from .team import office_file,OFFICE_TYPES
                    f=office_file(path.rsplit('/',1)[1])
                    if not f:raise KeyError('No office image for that team member yet')
                    return self.image(f,OFFICE_TYPES[f.suffix.lower()],'private, max-age=86400')
                if path=='/team-group':
                    from .team import group_file,OFFICE_TYPES
                    f=group_file()
                    if not f:raise KeyError('No team group picture yet')
                    return self.image(f,OFFICE_TYPES[f.suffix.lower()],'private, max-age=86400')
                if len(parts)==3 and parts[:2]==['api','results']:return self.send(s.results.get(parts[2]))
                if path=='/api/people':return self.send(s.people.all())
                if path=='/api/learners':return self.send(s.learners.all())
                if len(parts)==4 and parts[:2]==['api','emails'] and parts[3]=='kit-draft':return self.send(dict(items=s.kit_drafts.for_email(parts[2]),live=s.kit_drafts.live()))
                if path=='/api/discord':return self.send(s.discord.status)
                if path=='/api/notices':return self.send(s.notices.view())
                if len(parts)==6 and parts[:2]==['api','projects'] and parts[3]=='frameio' and parts[5]=='status':
                    try:return self.send(s.frameio.status(parts[2],parts[4]),cache='no-store')
                    except KeyError:return self.send({'error':'Review not found.'},404)
                    except Exception:return self.send({'error':'Review status is temporarily unavailable.'},503)
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='frameio':return self.send(s.frameio.view(parts[2]))
                if path=='/api/frameio/destinations':return self.send(s.frameio.destinations())
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='archive':return self.send(s.archive.status(parts[2]))
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='clean-edit':return self.send(s.clean_edit.view(parts[2]))
                if len(parts)==6 and parts[:2]==['api','projects'] and parts[3:5]==['clean-edit','audio']:
                    return self.send(s.clean_edit.preview_file(parts[2],parts[5]).read_bytes(),mime='audio/wav',cache='private, max-age=3600')
                if path=='/api/archive/drives':return self.send(s.archive.drives())
                if path=='/api/oversight':return self.send(s.oversight.assess())
                if path=='/api/diagnostics/approval-lineage':return self.send(s.lineage_diagnostic or dict(pending=True))
                if path=='/api/youtube-comments':return self.send(dict(s.youtube_comments.status(),scopes=sorted(s.youtube_comments_auth.granted()),connection=getattr(s.youtube_comments_auth,'source','')))
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='release':return self.send(s.release.view(parts[2]))
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='film':return self.send(s.filming.latest(parts[2]) or {})
                if path=='/api/reply-queue':return self.send(s.reply_queue.view())
                if path=='/api/replies':return self.send(dict(items=[dict(r,offer=s.replies.offer(r['id'])['actions']) for r in s.replies.all()],configured=s.replies.configured(),actions_on=s.replies.action_config(),last=s.notices.state('mail_last_ok'),error=s.notices.state('mail_last_error')))
                if path=='/api/courses':return self.send(s.courses.all())
                if len(parts)==3 and parts[:2]==['api','courses']:return self.send(s.courses.get(parts[2]))
                if len(parts)==4 and parts[:2]==['api','courses'] and parts[3]=='cues':return self.send(dict(text=s.courses.cues(parts[2])))
                if path=='/api/agents/capabilities':
                    from .chat_capabilities import matrix
                    return self.send(matrix())
                if path=='/api/agents/status':return self.send(s.agents.status())
                if path=='/api/agents/thread':
                    q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    return self.send(s.agents.thread(q.get('project',[''])[0],q.get('agent',[''])[0]))
                if len(parts)==5 and parts[:2]==['api','carousels'] and parts[3]=='slide' and parts[4].isdigit():
                    item=s.carousels.get(parts[2])
                    if (item.get('design') or {}).get('override_version'):
                        from .carousel_overrides import read_slide
                        image,mime=read_slide(s.carousels,item,int(parts[4]))
                        return self.send(image,mime=mime,cache='private, no-store')
                    file,mime=s.carousels.image(parts[2],int(parts[4]))
                    return self.send(file.read_bytes(),mime=mime,cache='private, max-age=300')
                if path=='/api/inspo':return self.send(s.inspo.all())
                if len(parts)==5 and parts[:2]==['api','inspo'] and parts[3]=='slide' and parts[4].isdigit():
                    file,mime=s.inspo.image(parts[2],int(parts[4]))
                    return self.send(file.read_bytes(),mime=mime,cache='private, max-age=3600')
                if path=='/api/edit-review/decks':return self.send(s.edit_review.decks())
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='edit-flow':
                    from .native_background import readiness
                    v=s.edit_flow.view(parts[2]);v['processor']='autocut-v1';v['native_transcription']=readiness();v['suggest']=s.edit_flow.suggest(parts[2]);v['sources']=s.edit_flow.latest(parts[2],'edit_sources');v['cut_id']=(s.edit_flow.latest(parts[2],'edit_cut') or {}).get('id');v['visuals_id']=(s.edit_flow.latest(parts[2],'edit_visuals') or {}).get('id');return self.send(v)
                if len(parts)==6 and parts[:2]==['api','projects'] and parts[3:5]==['edit-flow','setup']:
                    f=s.edit_flow.setup_image(parts[2],parts[5]);return self.send(f.read_bytes(),mime='image/png' if f.suffix=='.png' else 'image/jpeg',cache='private, max-age=3600')
                if len(parts)==6 and parts[:2]==['api','projects'] and parts[3:5]==['edit-flow','audio']:
                    return self.send(s.edit_flow.preview_file(parts[2],parts[5]).read_bytes(),mime='audio/wav',cache='private, max-age=3600')
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='pieces':return self.send(s.pieces.status(parts[2]))
                if len(parts)==5 and parts[:2]==['api','projects'] and parts[3:5]==['recording','takes']:return self.send(s.pieces.takes(parts[2]))
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='edit-review':return self.send(s.edit_review.review(parts[2]))
                if len(parts)==6 and parts[:2]==['api','projects'] and parts[3:5]==['edit-review','img']:
                    return self.send(s.edit_review.image(parts[2],parts[5]).read_bytes(),mime='image/jpeg',cache='private, max-age=60')
                if path=='/edit-standin.webp':return self.send(STANDIN.read_bytes(),mime='image/webp',cache='private, max-age=86400')
                if path=='/api/carousel-jobs':
                    query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    try:return self.send(s.carousels.jobs.scoped(request_id=(query.get('request_id') or [''])[0]),cache='no-store')
                    except ValueError:return self.send({'error':'Choose one exact carousel request.'},400)
                    except Exception:return self.send({'error':'Carousel progress is unavailable.'},503)
                if len(parts)==3 and parts[:2]==['api','carousel-jobs']:
                    try:return self.send(s.carousels.jobs.status(parts[2]),cache='no-store')
                    except KeyError:return self.send({'error':'Carousel job not found.'},404)
                    except Exception:return self.send({'error':'Carousel progress is unavailable.'},503)
                if len(parts)==4 and parts[:2]==['api','carousels'] and parts[3]=='jobs':
                    try:return self.send(s.carousels.jobs.scoped(carousel_id=parts[2]),cache='no-store')
                    except ValueError:return self.send({'error':'Choose one exact carousel.'},400)
                    except Exception:return self.send({'error':'Carousel progress is unavailable.'},503)
                if path=='/api/carousels':return self.send(s.carousels.all())
                if path=='/api/phone':
                    if getattr(self,'device_authenticated',False):return self.send(dict(enabled=False,paired=True,error='This browser is paired. To connect another browser, open the same home address and request its own code.'))
                    return self.send(s.phone.status(s.token))
                if path=='/manifest.webmanifest':return self.send(s.phone.manifest(),mime='application/manifest+json')
                if path in ('/emails.js','/emails.css','/email-view.js','/email-flow.js'):
                    p=Path(__file__).parent/'web'/path[1:]
                    return self.send(p.read_bytes(),mime='text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
                if len(parts)==6 and parts[:2]==['api','emails'] and parts[3]=='visuals':
                    return self.send(s.emails.visuals.read_png(parts[2],parts[4],parts[5]),mime='image/png',cache='private, no-store')
                if path=='/api/emails':return self.send(s.emails.all())
                if path=='/api/kit':return self.send(s.emails.kit.status())
                if path=='/api/email-inspiration':
                    q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('q',[''])[0]
                    return self.send(s.emails.library(q))
                if len(parts)==3 and parts[:2]==['api','email-inspiration']:return self.send(s.emails.reference(urllib.parse.unquote(parts[2])))
                if len(parts)==4 and parts[:2]==['api','emails'] and parts[3]=='history':return self.send(s.emails.history(parts[2]))
                if len(parts)==3 and parts[:2]==['api','emails']:return self.send(s.emails.get(parts[2]))
                if path=='/lessons':
                    text=(Path(__file__).parent/'web'/'lessons.html').read_text(encoding='utf-8').replace('__TOKEN__',self.page_token())
                    return self.send(text.encode(),mime='text/html; charset=utf-8',cookie=True)
                if path in ['/lessons.js','/lessons.css','/notion.css']:
                    p=Path(__file__).parent/'web'/path[1:];return self.send(p.read_bytes(),mime='text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
                if path.startswith('/api/lessons'):
                    if not s.lessons:raise ValueError('Lesson mapping is not installed; recording in OBS remains available')
                    if path=='/api/lessons':return self.send(s.lessons.catalog())
                    if path=='/api/lessons/recovery':return self.send(s.lessons.recovery())
                    if path=='/api/lessons/obs':return self.send(s.lessons.obs())
                    if path=='/api/lessons/recordings':return self.send(s.lessons.candidates())
                    if len(parts)==4 and parts[3]=='recordings':return self.send(s.lessons.candidates(parts[2]))
                    if len(parts)==3:return self.send(s.lessons.detail(parts[2]))
                if path=='/':
                    text=(Path(__file__).parent/'web'/'index.html').read_text(encoding='utf-8').replace('__TOKEN__',self.page_token())
                    text=text.replace('<nav aria-label="Workflows">','<nav aria-label="Workflows"><a class="nav" href="/publishing">Publish to YouTube</a>')
                    text=text.replace('<link rel="stylesheet" href="/brand-fonts.css">','<link rel="stylesheet" href="/thumbnail-creator.css"><link rel="stylesheet" href="/brand-fonts.css">').replace('</body>','<script src="/thumbnail-creator.js"></script></body>')
                    return self.send(text.encode(),mime='text/html; charset=utf-8',cookie=True)
                if path in ['/team.js','/team.css','/tokens.css','/system.css','/shell.js','/carousels.js','/inspo.js','/dock.js','/edit-review.js','/edit-review.css','/edit-flow.js','/edit-flow.css','/clean-cut.js','/clean-cut.css','/scorecard.js','/scorecard.css','/video-page.js','/video-page.css','/editing.js','/desk.js','/screens.js','/youtube.js','/studio.js','/results.js','/results.css','/success.js','/success.css','/courses.js','/chat-images.js','/courses.css','/youtube.css','/mobile.js','/autosave.js','/calendar-sync.js','/app.js','/writer.js','/keying.js','/planning.js','/planning.css','/style.css','/workflow.js','/workflow.css','/notion.js','/notion.css']:
                    p=Path(__file__).parent/'web'/path[1:];return self.send(p.read_bytes(),mime='text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
                if path=='/icon.png':return self.send((s.root/'CareerOS.png').read_bytes(),mime='image/png')   # the CareerOS mark (16 Nov 2026); DaJoshiResolve.png is kept
                if path in ('/inspiration/creatorhooks','/inspiration/creatorhooks.library.json'):
                    name='creatorhooks-library.html' if path.endswith('creatorhooks') else 'creatorhooks.library.json'
                    file=s.root/'References'/'CreatorHooks'/name
                    if not file.is_file():raise KeyError('Inspiration library has not been installed')
                    policy="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'none'; connect-src 'none'; font-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
                    return self.send(file.read_bytes(),mime='text/html; charset=utf-8' if name.endswith('.html') else 'application/json',csp=policy)
                if path.startswith('/inspiration/youtube-ideas/'):
                    import re
                    name=path.rsplit('/',1)[1]
                    if not re.fullmatch(r'[A-Za-z0-9_-]{11}\.jpg',name):raise KeyError('Unknown saved video thumbnail')
                    file=s.youtube_ideas.thumbs/name
                    if not file.is_file():raise KeyError('Saved video thumbnail not downloaded')
                    return self.image(file,'image/jpeg','private, max-age=604800')
                if path.startswith('/inspiration/creatorhooks-thumbs/'):
                    import re
                    name=path.rsplit('/',1)[1]
                    if not re.fullmatch(r'[A-Za-z0-9_-]{11}\.jpg',name):raise KeyError('Unknown Creator Hooks thumbnail')
                    file=s.root/'References'/'CreatorHooks'/'thumbs'/name
                    if not file.is_file():raise KeyError('Creator Hooks thumbnail not downloaded')
                    mime='image/png' if file.read_bytes()[:4]==b'\x89PNG' else 'image/jpeg'
                    return self.image(file,mime,'private, max-age=604800')
                if path.startswith('/inspiration/thumbnail-formats/'):
                    name=path[len('/inspiration/thumbnail-formats/'):]
                    if not name:name='index.html'
                    allowed={'index.html','README.txt','data/library.json','data/adaptation-lineage.json','data/channel-title-evidence.json','data/original-concepts.json','data/title-reference-registry.json'}
                    allowed.update('crops/example-%03d.png'%i for i in range(1,112))
                    allowed.update('screenshots/%02d.png'%i for i in range(1,28))
                    if name not in allowed:raise KeyError('Unknown thumbnail reference asset')
                    library=(s.root/'References'/'ThumbnailFormats').resolve()
                    file=(library/name).resolve()
                    if not file.is_relative_to(library) or not file.is_file():raise KeyError('Thumbnail Formats has not been installed')
                    mime={'.html':'text/html; charset=utf-8','.png':'image/png','.json':'application/json','.txt':'text/plain; charset=utf-8'}[file.suffix]
                    policy="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self'; connect-src 'none'; font-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
                    if file.suffix=='.png':return self.image(file,mime,'private, max-age=86400')
                    return self.send(file.read_bytes(),mime=mime,csp=policy)
                if path=='/api/inspiration':return self.send(s.inspiration.catalog())
                if path=='/api/business':return self.send(dict(preview=s.business.build(),outbox=s.business.outbox(),settings=s.business.settings()))
                if path=='/api/scorecard':
                    v=s.scorecard.view()
                    import datetime as _dt
                    old=not v['snapshot'] or (_dt.datetime.now(_dt.timezone.utc)-_dt.datetime.fromisoformat(v['freshness']['fetched_at'])).total_seconds()>3*3600
                    if old and not v['refreshing']:s.scorecard.refresh();v['refreshing']=True
                    return self.send(v)
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='workflow':return self.send(s.workflow.status(parts[2]))
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='handoff':return self.send(s.workflow.handoff(parts[2]))
                if len(parts)==6 and parts[:2]==['api','projects'] and parts[3]=='workflow-file':
                    a=s.workflow.get(parts[2],parts[4]);name=parts[5]
                    if a['kind']!='clean' or name not in a['payload']['hashes']:raise KeyError('Unknown workflow file')
                    if s.workflow.health(s.store.get(parts[2]),a):raise Conflict('Artifact is stale; inspect workflow status')
                    file=Path(a['payload']['folder'])/name
                    return self.send(file.read_bytes(),mime='application/xml' if name.endswith('.xml') else 'application/json')
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='inspiration':return self.send(s.inspiration.links(parts[2]))
                if path=='/api/project-status':return self.send([s.control.status(p['id']) for p in s.store.projects() if not p['archived']])
                if len(parts)==5 and parts[:2]==['api','jobs'] and parts[3]=='asset':
                    j=s.store.job(parts[2]);allowed={'source.png':('keying/source.png','image/png'),'keying.mp4':('KEYING_PREVIEW.mp4','video/mp4'),'review.mp4':('REVIEW_PREVIEW.mp4','video/mp4'),'audio.wav':('CLEAN_AUDIO.wav','audio/wav'),'joins.json':('audio_joins.json','application/json')}
                    if not j or parts[4] not in allowed:raise KeyError('Preview not found')
                    rel,mime=allowed[parts[4]];file=Path(j['path'])/rel
                    if not file.is_file():raise KeyError('Preview is not ready')
                    size=file.stat().st_size;start=0;end=size-1;status=200
                    byte_range=self.headers.get('Range')
                    if byte_range:
                        import re
                        match=re.fullmatch(r'bytes=(\d+)-(\d*)',byte_range)
                        if not match:return self.send({'error':'Unsupported byte range'},416)
                        start=int(match[1]);end=min(end,int(match[2])) if match[2] else end;status=206
                        if not 0<=start<=end<size:return self.send({'error':'Range outside preview'},416)
                    self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
                    if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                    self.end_headers()
                    with file.open('rb') as stream:
                        stream.seek(start);remaining=end-start+1
                        while remaining:
                            block=stream.read(min(262144,remaining))
                            if not block:break
                            self.wfile.write(block);remaining-=len(block)
                    return
                if path=='/api/state':return self.send(dict(version=__version__,projects=[s.project_folders.present(p) for p in s.store.projects()],jobs=s.jobs.all(),stages=STAGES,demo=s.demo,data_path=str(s.store.root),engine='Local editor with script alignment and validated XML export',maintenance_hold_dispatch=s.maintenance_hold_dispatch))
                if len(parts)==2 and parts[0]=='assets':
                    p,mime=s.store.asset(parts[1]);return self.image(p,mime,'private, max-age=31536000, immutable')
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='export':return self.send(s.store.get(parts[2]))
                if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='writing':
                    workspace=s.writing.workspace(parts[2])
                    try:workspace['guidance']=Context(s.root).catalog()
                    except (OSError,ValueError,KeyError):workspace['guidance']=dict(sources=[],pass_configuration_status='unavailable',message='Writing reference package is unavailable; scripts and version history remain usable')
                    return self.send(workspace)
                if len(parts)==5 and parts[:2]==['api','projects'] and parts[3]=='script-versions':return self.send(s.writing.version(parts[2],parts[4]))
                raise KeyError('Not found')
            length=int(self.headers.get('Content-Length','0'))
            if length<=0 or length>29*1024*1024:raise ValueError('Request is empty or too large')
            data=json.loads(self.rfile.read(length))
            if method=='POST' and path=='/api/publishing/updates/prepare':
                return self.send(s.video_updates.prepare(data.get('video_id'),data.get('changes'),by='Josh' if self.from_josh() else 'sam',note=data.get('note','')))
            if method=='POST' and path=='/api/publishing/updates/approve':
                # Josh's approval: only from the review page, with the one-time review code for this exact version
                self.publishing_consume(dict(review_nonce=data.get('review_nonce'),job_id='update:'+str(data.get('update_id')),packet_digest=data.get('digest')))
                job=s.video_updates.approve(data['update_id'],data['digest'])
                threading.Thread(target=s.video_updates.apply,args=(job['id'],),daemon=True,name='video-update').start()
                return self.send(job)
            if method=='POST' and path=='/api/publishing/updates/apply':
                if not self.publishing_browser(write=True):raise ValueError('Retry from the update review page in CareerOS.')
                return self.send(s.video_updates.apply(data['update_id']))
            if method=='POST' and path=='/api/publishing/updates/verify':
                return self.send(s.video_updates.verify_chrome(data['update_id'],by='Josh' if self.from_josh() else 'sam'))
            if method=='POST' and path=='/api/publishing/updates/live-check':
                return self.send(s.video_updates.live_check(data.get('video_id')))
            if method=='POST' and len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='publish-preview':
                from .project_publishing import prepare
                return self.send(self.publishing_review(prepare(s.store,s.release,s.publishing,parts[2],data)))
            if method=='POST' and path=='/api/publishing/prepare':
                return self.send(s.publishing.prepare(data))
            if method=='POST' and path in ('/api/publishing/approve','/api/publishing/resume'):
                job=s.publishing.get(data['job_id'])
                packet=job.get('packet') or {}
                if packet.get('project_id') and s.store.get(packet['project_id'])['revision']!=packet.get('project_revision'):
                    raise ValueError('The video changed. Reopen Publish and review it again.')
                self.publishing_consume(data)
                if path.endswith('/approve'):
                    return self.send(s.publishing.approve_and_start(data['job_id'],data['packet_digest']))
                job=s.publishing.get(data['job_id'])
                if job['packet']['packet_digest']!=data['packet_digest']:raise ValueError('Release changed. Review it again.')
                return self.send(s.publishing.resume(data['job_id']))
            if path=='/api/youtube-recording/lock' and method=='POST':
                out=s.youtube_recording.invoke()
                if not out['take'].get('kept_in_place') and not out['take'].get('reused'):s.filming.restore_default()
                return self.send(out)
            if len(parts)==5 and parts[:2]==['api','projects'] and parts[3]=='recording' and method=='POST':
                if parts[4]=='active':return self.send(s.youtube_recording.activate(parts[2],data['revision'],data['folder']))
                if parts[4]=='source':return self.send(s.youtube_recording.select(parts[2],data['revision'],data['path']))
            if len(parts)==5 and parts[:2]==['api','projects'] and parts[3]=='thumbnail-creator' and method=='POST':
                pid,action=parts[2],parts[4];creator=s.thumbnail_creator
                if action=='save':return self.send(creator.save(pid,data))
                if action=='asset':return self.send(creator.add_asset(pid,data))
                if action=='preview':return self.send({'png':base64.b64encode(creator.render(pid,data['layout'])[0]).decode(),'bounds':creator.geometry(pid,data['layout'])})
                if action=='export':return self.send(creator.export(pid,data['layouts']),mime='application/zip')
            if len(parts)==4 and parts[:2]==['api','emails'] and parts[3]=='visuals' and method=='POST':
                if set(data)!={'revision','visuals'}:raise ValueError('Provide revision and visuals only.')
                return self.send(s.emails.visuals.generate(dict(data,email_id=parts[2])))
            if path=='/api/emails' and method=='POST':return self.send(s.emails.create(data))
            if path=='/api/emails/import' and method=='POST':return self.send(s.emails.import_records(data))
            if path=='/api/emails/kit-sync' and method=='POST':return self.send(s.emails.sync_kit(data))
            if path.startswith('/api/admin/') and method=='POST':
                if path=='/api/admin/sync':return self.send(s.admin.sync())
                if path=='/api/admin/connect':return self.send(s.admin.connect(data))
                if path=='/api/admin/open':return self.send(s.admin.open(data['id'],data.get('path','')))
                if path=='/api/admin/update':return self.send(s.admin.update(data['id'],data.get('changes',{})))
                if path=='/api/admin/create-folder':return self.send(s.admin.create_folder(data['id']))
            if path=='/api/tasks' and method=='POST':return self.send(s.tasks.create(data))
            if len(parts)==3 and parts[:2]==['api','team'] and method=='PUT':return self.send(s.team.save(parts[2],data['changes'],data['revision']))
            if path=='/api/team/learnings' and method=='POST':return self.send(s.team.learn(data))
            if path=='/api/notices/settings' and method=='POST':s.notices.set(data.get('changes') or {});return self.send(s.notices.view())
            if len(parts)>=4 and parts[:2]==['api','reply-queue'] and method=='POST':
                # Only Josh's own click in the app sends, edits or dismisses. The team's runs can't.
                if not self.from_josh():raise NotAllowed('Only Josh can do this, from the app.')
                kind,rid=parts[2],parts[3]
                if len(parts)==5 and parts[4]=='send':return self.send(s.reply_queue.send(kind,rid,str(data.get('digest') or '')))
                if len(parts)==4 and data.get('dismiss'):return self.send(s.reply_queue.dismiss(kind,rid))
                if len(parts)==4:return self.send(s.reply_queue.edit(kind,rid,str(data.get('text') or '')))
                raise KeyError('Unknown reply action')
            if len(parts)==3 and parts[:2]==['api','replies'] and method=='POST':return self.send(s.replies.update(parts[2],data.get('changes') or {}))
            # Josh approves one exact version of one Gmail action (the hash he was shown), then Josh OS does it. See studio/replies.py.
            if len(parts)==4 and parts[:2]==['api','replies'] and parts[3]=='approve' and method=='POST':
                a=s.replies.approve(parts[2],str(data.get('action') or ''),str(data.get('digest') or ''),by='josh',via='joshos',said='Approved on the Replies page')
                return self.send(s.replies.execute(a['id'],s.agents.run))
            if path=='/api/people' and method=='POST':return self.send(s.people.create(data))
            if path=='/api/learners/lookup' and method=='POST':return self.send(s.learners.lookup(data))
            if len(parts)==4 and parts[:2]==['api','emails'] and parts[3]=='kit-draft' and method=='POST':return self.send(s.kit_draft_action(parts[2],data))
            if len(parts)==3 and parts[:2]==['api','people'] and method=='PUT':return self.send(s.people.save(parts[2],data['changes'],data['revision'],data.get('add_history') or []))
            if len(parts)==3 and parts[:2]==['api','results'] and method=='PUT':return self.send(s.results.save(parts[2],data['changes'],data['revision']))
            if path=='/api/courses/settings' and method=='POST':return self.send(s.courses.set_publish_root(data.get('publish_root','')))
            if len(parts)==3 and parts[:2]==['api','courses'] and method=='PUT':return self.send(s.courses.save(parts[2],data['changes'],data['revision']))
            if len(parts)==4 and parts[:2]==['api','courses'] and method=='POST':
                lid,act=parts[2],parts[3]
                if act=='stage':return self.send(s.courses.move(lid,data.get('stage',''),data.get('revision')))
                if act=='approve':return self.send(s.courses.approve(lid,data.get('revision')))
                if act=='work':return self.send(s.courses.start(lid,data.get('task',''),str(data.get('note') or '')))
                if act=='check':return self.send(s.courses.check_slides(lid))
                if act=='cues':return self.send(s.courses.save_cues(lid,data.get('text','')))
                if act=='publish':return self.send(s.courses.publish(lid))
                if act=='live':return self.send(s.courses.mark_live(lid,data.get('revision')))
                if act=='open':return self.send(s.courses.open_path(lid,data.get('kind','')))
            if path=='/api/agents/reflect' and method=='POST':
                try:return self.send(s.agents.reflect(data,s.carousels.all()['items'],s.carousels,s.inspo))
                except AgentError as e:return self.send({'error':str(e)},503)
            if path=='/api/scorecard/refresh' and method=='POST':return self.send(s.scorecard.refresh())
            if path=='/api/agents/chat' and method=='POST':
                from .owner_release_chat import browser_chat
                exact_release=browser_chat(self,data)
                if exact_release is not None:return self.send(exact_release)
                try:return self.send(s.agents.chat(data,s.carousels.all()['items'],s.carousels,s.inspo))
                except AgentError as e:return self.send({'error':str(e)},503)
            if path=='/api/agents/ask' and method=='POST':
                try:return self.send(s.agents.ask(data,s.carousels.all()['items'],s.carousels,s.inspo))
                except AgentError as e:return self.send({'error':str(e)},503)
            if len(parts)==4 and parts[:3]==['api','team','learnings'] and method=='POST' and data.get('delete') is True:return self.send(s.team.forget(parts[3]))
            if path=='/api/inspo' and method=='POST':return self.send(s.inspo.create(data))
            if path=='/api/inspo/import' and method=='POST':return self.send(s.inspo.import_folder(data['folder'],data['creator'],data.get('checked_at','')))
            if len(parts)==3 and parts[:2]==['api','inspo'] and method=='PUT':return self.send(s.inspo.save(parts[2],data['changes'],data['revision']))
            if len(parts)==4 and parts[:2]==['api','carousels'] and parts[3]=='design' and method=='POST':return self.send(s.carousels.make_design(parts[2]))
            if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='edit-review' and method=='POST':
                return self.send(s.edit_review.render(parts[2],[int(n) for n in data.get('only') or []] or None))
            if len(parts)==5 and parts[:2]==['api','projects'] and parts[3]=='edit-flow' and method=='POST':
                return self.send(s.edit_flow_action(parts[2],parts[4],data))
            if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='edit-setup' and method=='POST':return self.send(s.edit_review.setup(parts[2]))
            if len(parts)==5 and parts[:2]==['api','projects'] and parts[3:5]==['recording','start'] and method=='POST':return self.send(s.pieces.start(parts[2]))
            if len(parts)==5 and parts[:2]==['api','projects'] and parts[3:5]==['recording','done'] and method=='POST':
                out=s.pieces.recorded(parts[2],data.get('path',''))
                if not out['take'].get('kept_in_place') and not out['take'].get('reused'):s.filming.restore_default()
                return self.send(out)
            if len(parts)==5 and parts[:2]==['api','projects'] and parts[3]=='frameio' and method=='POST':
                pid,act=parts[2],parts[4]
                if act=='send':s.frameio.send(pid,dict(by='Josh',said='Chose Send to Frame.io on the video page',via='joshos'),data.get('path'))
                elif act=='feedback':s.frameio.import_feedback(s.frameio.latest(pid)['id'])
                elif act=='check':s.frameio.check_destination(s.frameio.latest(pid)['id'])
                else:raise KeyError('Unknown Frame.io step')
                return self.send(s.frameio.view(pid))
            if len(parts)==5 and parts[:2]==['api','projects'] and parts[3]=='archive' and method=='POST':
                pid,act=parts[2],parts[4]
                if act=='prepare':s.archive.prepare(pid)
                elif act=='public':s.archive.record_public(pid,data.get('video_id'),by='sam')
                elif act=='review':return self.send(s.archive.cleanup_review(pid))
                elif act in('copy','verify'):
                    role=data.get('role') if data.get('role') in('passport','backup') else 'passport'
                    fn=s.archive.copy if act=='copy' else s.archive.verify
                    threading.Thread(target=fn,args=(pid,role),daemon=True,name='archive-'+act).start()
                else:raise KeyError('Unknown archive step')
                return self.send(s.archive.status(pid))
            if len(parts)==5 and parts[:2]==['api','projects'] and parts[3]=='clean-edit' and method=='POST':
                pid,act=parts[2],parts[4];ce=s.clean_edit
                who='Josh' if self.from_josh() else 'tariq'
                if act in('remove','restore'):return self.send(ce.edit(pid,act,data.get('words') or [],who))
                if act=='undo':return self.send(ce.undo(pid))
                if act=='preview':return self.send(ce.preview(pid,str(data.get('word') or ''),data.get('context') or 2))
                if act=='clean_transcript':return self.send(ce.clean_transcript(pid))
                if act=='final_transcript':return self.send(ce.final_transcript(pid,str(data.get('sequence') or '')))
                if act=='approve_final':return self.send(ce.approve_final(pid,str(data.get('digest') or ''),'Josh',self.from_josh()))
                if act=='handoff':return self.send(ce.sam_handoff(pid))
                if act=='cleanup':return self.send(ce.cleanup(pid))          # proposals and a review pack only; nothing is cut
                if act=='decide':return self.send(ce.decide(pid,str(data.get('key') or ''),str(data.get('decision') or ''),'Josh',self.from_josh(),str(data.get('revision') or '') or None))
                if act=='remove_span':
                    if not self.from_josh():raise ValueError('Only Josh removes a sound by its times.')
                    return self.send(ce.edit_span(pid,data.get('start'),data.get('end'),'Josh'))
                if act=='release':return self.send(ce.record_release(pid,data.get('state'),data.get('video_id'),'sam' if not self.from_josh() else 'Josh'))
                if act=='build':
                    threading.Thread(target=ce.build,args=(pid,),daemon=True,name='clean-edit-build').start()
                    return self.send(dict(ce.view(pid),building=True))
                if act=='approve':return self.send(ce.approve(pid,'Josh',self.from_josh(),data.get('sequence')))
                raise KeyError('Unknown clean edit step')
            if path=='/api/archive/drive' and method=='POST':return self.send(s.archive.set_drive(data.get('role'),data.get('volume_id'),data.get('serial')))
            if path=='/api/archive/channel' and method=='POST':return self.send(s.archive.set_channel(data.get('channel_id')))
            if path=='/api/frameio/destination' and method=='POST':return self.send(s.frameio.set_destination(data.get('destination') or {}))
            if len(parts)==5 and parts[:2]==['api','projects'] and parts[3]=='release' and method=='POST':
                pid,act=parts[2],parts[4]
                if act=='approve-export':s.release.approve_export(pid,data.get('path'),said='Approved on the video page')
                elif act=='approve-description':s.release.approve_description(pid,str(data.get('digest') or ''),said='Approved on the video page')
                elif act=='approve-cta':s.release.approve_cta(pid,str(data.get('digest') or ''),said='Approved on the video page')
                elif act=='publish-time':s.release.set_publish_time(pid,data.get('time'),data.get('tz'))
                elif act=='draft':s.release.draft_packaging(pid)
                elif act=='chapters':s.release.chapters.prepare(pid,preview=not bool((s.store.get(pid).get('release') or {}).get('export_approval')))
                else:raise KeyError('Unknown release step')
                return self.send(dict(release=s.release.view(pid),project=s.project_folders.present(s.store.get(pid))))
            if len(parts)==5 and parts[:2]==['api','projects'] and parts[3:5]==['film','prompter'] and method=='POST':return self.send(s.filming.check_prompter(parts[2]))
            if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='film' and method=='POST':return self.send(s.filming.prepare(pid=parts[2],via='joshos',force=bool(data.get('force'))))
            if path=='/api/carousels' and method=='POST':return self.send(s.carousels.create(data))
            if len(parts)==3 and parts[:2]==['api','carousels'] and method=='PUT':
                before=s.carousels.get(parts[2]);saved=s.carousels.save(parts[2],data['changes'],data['revision'])
                if self.from_josh():s.feedback.record('carousel',before,saved)
                return self.send(saved)
            if len(parts)==3 and parts[:2]==['api','tasks'] and method=='PUT':return self.send(s.tasks.save(parts[2],data['changes'],data['revision']))
            if path=='/api/kit/connect' and method=='POST':
                r=s.emails.kit.connect(data)
                if r['connected']:threading.Thread(target=s.emails.kit_cycle,daemon=True).start()
                return self.send(r)
            if path=='/api/kit/disconnect' and method=='POST':return self.send(s.emails.kit.disconnect())
            if path=='/api/kit/sync' and method=='POST':return self.send(s.emails.kit_cycle())
            if len(parts)==4 and parts[:2]==['api','emails'] and parts[3]=='kit' and method=='POST':return self.send(s.emails.kit_push(parts[2]))
            if len(parts)==4 and parts[:3]==['api','calendar','emails'] and method=='PUT':
                if set(data['changes'])!={'planned_date'}:raise ValueError('Calendar changes only the local date')
                return self.send(s.emails.save(parts[3],data['changes'],data['revision'],sync_remote=False))
            if len(parts)==3 and parts[:2]==['api','emails'] and method=='PUT':
                before=s.emails.get(parts[2]);saved=s.emails.save(parts[2],data['changes'],data['revision'])
                if self.from_josh():s.feedback.record('email',before,saved)
                return self.send(saved)
            if path.startswith('/api/lessons/') and method=='POST':
                if not s.lessons:raise ValueError('Lesson mapping is not installed')
                if path=='/api/lessons/prepare':return self.send(s.lessons.obs(prepare=True))
                if path=='/api/lessons/prompter':return self.send(s.lessons.prompter())
                if len(parts)==4:
                    lid,action=parts[2:]
                    if action=='select':return self.send(s.lessons.select(lid))
                    if action=='prepare':return self.send(s.lessons.prepare_lesson(lid))
                    if action=='written':return self.send(s.lessons.written_lesson(lid,data))
                    if action=='delivery-plan':return self.send(s.lessons.delivery_plan(lid))
                    if action=='use-latest':return self.send(s.lessons.use_latest(lid))
                    if action=='release':return self.send(s.lessons.release_session(lid,data.get('binding_id'),'left_recording_view'))
                    if action=='done':return self.send(s.lessons.finish_session(lid,data))
                    if action=='handover':return self.send(s.lessons.handover(lid,data.get('binding_id')))
                    if action=='import':return self.send(s.lessons.import_take(lid,data))
                    if action=='take':return self.send(s.lessons.take_action(lid,data))
                    if action=='plan':return self.send(s.lessons.plan(lid,data.get('date','')))
                    if action=='open':return self.send(s.lessons.open_source(lid,data['key']))
            if len(parts)==5 and parts[:2]==['api','projects'] and parts[3]=='workflow':
                pid,action=parts[2],parts[4];w=s.workflow
                if action=='pass1':return self.send(w.register_pass1(pid,data['revision'],data['report']))
                if action=='import-cut':return self.send(w.import_cut(pid,data['revision'],data['job_id']))
                if action=='start-cut':return self.send(w.launch(pid,data['revision'],'silence'))
                if action=='render-cut':return self.send(w.launch(pid,data['revision'],'review',data['artifact_id'],data['digest'],data.get('review_fade_ms',0)))
                if action=='import-map':return self.send(w.import_map(pid,data['revision'],data['mapping'],data['source_identities'],data['note']))
                if action=='reject':return self.send(w.revise_cut(pid,data['revision'],data['artifact_id'],data['digest'],data['decisions']))
                if action=='approve':return self.send(w.approve(pid,data['revision'],data['artifact_id'],data['digest'],data['checks'],data['note']))
            if path=='/api/inspiration/link' and method=='POST':return self.send(s.inspiration.link(data))
            if path=='/api/script-import' and method=='POST':return self.send(s.writing.import_script(data))
            if path=='/api/script-proposals' and method=='POST':return self.send(s.writing.propose(data))
            if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='writing-context':return self.send(s.writing.context(s.root,parts[2],data['revision'],data['start'],data['end'],data['rhetorical_job'],data.get('pass_id'),data.get('scope','selection')))
            if len(parts)==5 and parts[:2]==['api','projects'] and parts[3]=='script-proposals':return self.send(s.writing.resolve_proposal(parts[2],parts[4],data['accept']))

            if len(parts)==4 and parts[:2]==['api','projects']:
                if parts[3]=='script-approve':return self.send(s.writing.approve(parts[2],data['version_id'],data['revision'],data['note']))
                if parts[3]=='script-restore':return self.send(s.writing.restore(parts[2],data['version_id'],data['revision']))
            if path=='/api/projects' and method=='POST':return self.send(s.project_folders.present(s.store.create(data['name'],data.get('id'))))
            if len(parts)==3 and parts[:2]==['api','projects'] and method=='PUT':
                before=s.store.get(parts[2]);saved=s.store.save(parts[2],data['changes'],data['revision'])
                if self.from_josh():s.feedback.record('project',before,saved)
                if saved.get('stage')!=before.get('stage') and saved.get('stage') in ('Ready to record','Recording','Recorded','Editing'):
                    # Once a video is being recorded, Tariq's edit folders are made the way Josh lays them out.
                    try:s.edit_review.setup(parts[2])
                    except (KeyError,ValueError,OSError):pass
                return self.send(s.project_folders.present(saved))
            if path=='/api/images' and method=='POST':
                raw=base64.b64decode(data['data'],validate=True);return self.send(s.store.import_image(data['name'],raw))
            if len(parts)==4 and parts[:2]==['api','projects'] and parts[3] in ['edit','keying-preview']:return self.send(s.jobs.start(parts[2],data.get('revision'),keying_only=parts[3]=='keying-preview'))
            if len(parts)==4 and parts[:2]==['api','jobs']:
                jid,action=parts[2:];j=s.store.job(jid)
                if not j:raise KeyError('Run not found')
                if action=='cancel':return self.send(s.jobs.cancel(jid))
                if action=='resume':return self.send(s.jobs.resume(jid))
                if action=='open':
                    p=Path(j['path'])
                    if not p.is_dir():raise ValueError('Run folder has not been created yet')
                    subprocess.Popen(['explorer.exe',str(p)]);return self.send({'ok':True})
            if path=='/api/classic':
                p=s.root/'DaJoshiResolve.Editor.exe'
                if not p.is_file():raise ValueError('Classic editor is not deployed here')
                subprocess.Popen([str(p)],cwd=s.root);return self.send({'ok':True})
            if path=='/api/pick':
                kind=data.get('kind','video')
                if kind not in ['video','audio','reference','shots','deck','transcript','folder']:raise ValueError('Unknown file field')
                if not s.picker_lock.acquire(False):raise Conflict('A file chooser is already open')
                try:
                    settings=json.loads((s.root/'settings.json').read_text(encoding='utf-8-sig'))
                    result=subprocess.run([settings['python'],str(Path(__file__).parent/'picker.py'),kind],capture_output=True,text=True,encoding='utf-8',timeout=600,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    if result.returncode:raise ValueError('File chooser failed. You can paste the full path instead.')
                    return self.send({'path':result.stdout.strip()})
                finally:s.picker_lock.release()
            raise KeyError('Not found')
        except (BrokenPipeError,ConnectionAbortedError,ConnectionResetError):pass
        except Conflict as e:self.send({'error':str(e)},409)
        except NotAllowed as e:self.send({'error':str(e)},403)
        except (ValueError,KeyError,TypeError) as e:self.send({'error':str(e)},400)
        except Exception as e:
            import traceback
            (s.store.root/'server-error.log').open('a',encoding='utf-8').write(traceback.format_exc()+'\n')
            self.send({'error':str(e)},500)

