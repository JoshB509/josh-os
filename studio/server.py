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
from .carousels import Carousels
from .phone import PhoneAccess

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
    daemon_threads=True
    def __init__(self,root,data,port=0,demo=False,command=None):
        self.root=Path(root).resolve();self.store=Store(data);self.jobs=Jobs(self.store,self.root,command)
        self.inspiration=Inspiration(self.store,self.root);self.writing=Writing(self.store);self.control=Control(self.store,self.root,self.jobs)
        self.workflow=Workflow(self.store,self.root,self.jobs)
        self.lessons=Lessons(self.store,self.root) if (self.root/'Data/lesson-catalog.json').is_file() else None
        self.emails=Emails(self.store,self.root)
        self.tasks=Tasks(self.store)
        self.project_folders=ProjectFolders(self.store)
        self.youtube_recording=Recording(self.store,self.root)
        self.thumbnail_creator=ThumbnailCreator(self.store)
        self.team=Team(self.store);self.carousels=Carousels(self.store)
        self.phone=PhoneAccess(self.root,self.store.root)
        if not port:port=self.phone.port
        self.admin=Admin(self.store.root,self.root)
        threading.Thread(target=self.admin.loop,daemon=True).start()
        threading.Thread(target=self.kit_loop,daemon=True).start()
        self.token=self.phone.key or secrets.token_urlsafe(32);self.demo=demo;self.picker_lock=threading.Lock()
        super().__init__(('127.0.0.1',port),Handler)
        threading.Thread(target=self.project_folders.loop,daemon=True).start()
    def server_close(self):
        self.project_folders.stop.set()
        super().server_close()
    def kit_loop(self):
        """Every ten minutes, check Kit for sent emails and schedule anything marked Ready."""
        import time
        time.sleep(20)
        while True:
            try:self.emails.kit_cycle()
            except Exception as e:
                try:self.emails.kit.note(last_error=str(e)[:300])
                except Exception:pass
            time.sleep(600)
    @property
    def url(self):return 'http://127.0.0.1:%d/?token=%s'%(self.server_port,self.token)

class Handler(BaseHTTPRequestHandler):
    server_version='DaJoshiResolve'
    def log_message(self,fmt,*args):pass
    def authorized(self):
        local='127.0.0.1:'+str(self.server.server_port);phone=getattr(self.server,'phone',None) or PhoneAccess.off()
        if self.headers.get('Host') not in (local,*phone.hosts()):return False
        origin=self.headers.get('Origin')
        if origin and origin not in ('http://'+local,*phone.origins()):return False
        q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''))
        except Exception:pass
        supplied=self.headers.get('X-DJ-Token') or q.get('token',[''])[0] or (cookie['dj_session'].value if 'dj_session' in cookie else '')
        return secrets.compare_digest(supplied,self.server.token)
    def send(self,data,status=200,mime='application/json',cookie=False,csp=None,cache=None):
        if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control',cache or 'no-store')
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',csp or "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; font-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        if cookie:self.send_header('Set-Cookie','dj_session='+self.server.token+'; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers();self.wfile.write(data)
    def image(self,file,mime,cache):
        """Serve an image, or a small cached JPEG preview when ?w= is given."""
        q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        try:width=max(64,min(1600,int(q.get('w',['0'])[0])))
        except ValueError:width=0
        if width and mime.startswith('image/'):
            try:return self.send(preview(self.server.store.root,Path(file),width),mime='image/jpeg',cache=cache)
            except Exception:pass
        return self.send(Path(file).read_bytes(),mime=mime,cache=cache)
    def do_GET(self):self.route('GET')
    def do_POST(self):self.route('POST')
    def do_PUT(self):self.route('PUT')
    def route(self,method):
        if not self.authorized():return self.send({'error':'Open Josh OS from its desktop shortcut.'},403)
        path=urllib.parse.urlsplit(self.path).path;parts=path.strip('/').split('/');s=self.server
        try:
            if method=='GET':
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
                if path=='/admin':
                    text=(Path(__file__).parent/'web/admin.html').read_text(encoding='utf-8').replace('__TOKEN__',s.token)
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
                    text=(Path(__file__).parent/'web/tasks.html').read_text(encoding='utf-8').replace('__TOKEN__',s.token)
                    return self.send(text.encode(),mime='text/html; charset=utf-8',cookie=True)
                if path in ('/tasks.js','/tasks.css','/task-links.js'):
                    return self.send((Path(__file__).parent/'web'/path[1:]).read_bytes(),mime='text/css; charset=utf-8' if path.endswith('.css') else 'text/javascript; charset=utf-8')
                if path=='/api/tasks':return self.send(s.tasks.all())
                if path=='/api/team':return self.send(s.team.all())
                if path=='/api/carousels':return self.send(s.carousels.all())
                if path=='/api/phone':return self.send(s.phone.status(s.token))
                if path=='/manifest.webmanifest':return self.send(s.phone.manifest(),mime='application/manifest+json')
                if path in ('/emails.js','/emails.css','/email-view.js'):
                    p=Path(__file__).parent/'web'/path[1:]
                    return self.send(p.read_bytes(),mime='text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
                if path=='/api/emails':return self.send(s.emails.all())
                if path=='/api/kit':return self.send(s.emails.kit.status())
                if path=='/api/email-inspiration':
                    q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('q',[''])[0]
                    return self.send(s.emails.library(q))
                if len(parts)==3 and parts[:2]==['api','email-inspiration']:return self.send(s.emails.reference(urllib.parse.unquote(parts[2])))
                if len(parts)==4 and parts[:2]==['api','emails'] and parts[3]=='history':return self.send(s.emails.history(parts[2]))
                if len(parts)==3 and parts[:2]==['api','emails']:return self.send(s.emails.get(parts[2]))
                if path=='/lessons':
                    text=(Path(__file__).parent/'web'/'lessons.html').read_text(encoding='utf-8').replace('__TOKEN__',s.token)
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
                    text=(Path(__file__).parent/'web'/'index.html').read_text(encoding='utf-8').replace('__TOKEN__',s.token)
                    text=text.replace('<link rel="stylesheet" href="/brand-fonts.css">','<link rel="stylesheet" href="/thumbnail-creator.css"><link rel="stylesheet" href="/brand-fonts.css">').replace('</body>','<script src="/thumbnail-creator.js"></script></body>')
                    return self.send(text.encode(),mime='text/html; charset=utf-8',cookie=True)
                if path in ['/team.js','/team.css','/tokens.css','/carousels.js','/editing.js','/desk.js','/mobile.js','/autosave.js','/calendar-sync.js','/app.js','/writer.js','/keying.js','/planning.js','/planning.css','/style.css','/workflow.js','/workflow.css','/notion.js','/notion.css']:
                    p=Path(__file__).parent/'web'/path[1:];return self.send(p.read_bytes(),mime='text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
                if path=='/icon.png':return self.send((s.root/'DaJoshiResolve.png').read_bytes(),mime='image/png')
                if path in ('/inspiration/creatorhooks','/inspiration/creatorhooks.library.json'):
                    name='creatorhooks-library.html' if path.endswith('creatorhooks') else 'creatorhooks.library.json'
                    file=s.root/'References'/'CreatorHooks'/name
                    if not file.is_file():raise KeyError('Inspiration library has not been installed')
                    policy="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'none'; connect-src 'none'; font-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
                    return self.send(file.read_bytes(),mime='text/html; charset=utf-8' if name.endswith('.html') else 'application/json',csp=policy)
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
                if path=='/api/state':return self.send(dict(version=__version__,projects=[s.project_folders.present(p) for p in s.store.projects()],jobs=s.jobs.all(),stages=STAGES,demo=s.demo,data_path=str(s.store.root),engine='Local editor with script alignment and validated XML export'))
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
            if path=='/api/youtube-recording/lock' and method=='POST':return self.send(s.youtube_recording.invoke())
            if len(parts)==5 and parts[:2]==['api','projects'] and parts[3]=='recording' and method=='POST':
                if parts[4]=='active':return self.send(s.youtube_recording.activate(parts[2],data['revision'],data['folder']))
                if parts[4]=='source':return self.send(s.youtube_recording.select(parts[2],data['revision'],data['path']))
            if len(parts)==5 and parts[:2]==['api','projects'] and parts[3]=='thumbnail-creator' and method=='POST':
                pid,action=parts[2],parts[4];creator=s.thumbnail_creator
                if action=='save':return self.send(creator.save(pid,data))
                if action=='asset':return self.send(creator.add_asset(pid,data))
                if action=='preview':return self.send({'png':base64.b64encode(creator.render(pid,data['layout'])[0]).decode(),'bounds':creator.geometry(pid,data['layout'])})
                if action=='export':return self.send(creator.export(pid,data['layouts']),mime='application/zip')
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
            if len(parts)==4 and parts[:3]==['api','team','learnings'] and method=='POST' and data.get('delete') is True:return self.send(s.team.forget(parts[3]))
            if path=='/api/carousels' and method=='POST':return self.send(s.carousels.create(data))
            if len(parts)==3 and parts[:2]==['api','carousels'] and method=='PUT':return self.send(s.carousels.save(parts[2],data['changes'],data['revision']))
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
            if len(parts)==3 and parts[:2]==['api','emails'] and method=='PUT':return self.send(s.emails.save(parts[2],data['changes'],data['revision']))
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
            if len(parts)==3 and parts[:2]==['api','projects'] and method=='PUT':return self.send(s.project_folders.present(s.store.save(parts[2],data['changes'],data['revision'])))
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
        except (ValueError,KeyError,TypeError) as e:self.send({'error':str(e)},400)
        except Exception as e:
            import traceback
            (s.store.root/'server-error.log').open('a',encoding='utf-8').write(traceback.format_exc()+'\n')
            self.send({'error':str(e)},500)

