"""Local-only project app launcher. No external API clients."""
import argparse,ctypes,json,os,subprocess,sys,urllib.request,urllib.parse
from pathlib import Path
from studio.server import Server

def section_url(url,section):
    parts=urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme,parts.netloc,'/lessons' if section=='lessons' else '/',parts.query,''))

def focus_lessons():
    """Reuse the existing lesson window; do not navigate away from unsaved work."""
    if os.name!='nt':return False
    user=ctypes.WinDLL('user32',use_last_error=True)
    callback=ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_void_p)
    user.EnumWindows.argtypes=[callback,ctypes.c_void_p]
    user.GetWindowTextW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_int]
    user.ShowWindow.argtypes=[ctypes.c_void_p,ctypes.c_int]
    user.SetForegroundWindow.argtypes=[ctypes.c_void_p]
    found=[]
    @callback
    def visit(hwnd,_):
        text=ctypes.create_unicode_buffer(1024);user.GetWindowTextW(hwnd,text,1024)
        if text.value.startswith('Lessons') and 'Josh OS' in text.value:
            found.append(hwnd);return False
        return True
    user.EnumWindows(visit,None)
    if not found:return False
    user.ShowWindow(found[0],9);user.SetForegroundWindow(found[0]);return True

def browser(root,url,section=None,reuse=False):
    if section=='lessons' and reuse and focus_lessons():return
    url=section_url(url,section)
    paths=[Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe',Path(os.environ.get('LOCALAPPDATA',''))/'Google/Chrome/Application/chrome.exe',Path(os.environ.get('PROGRAMFILES(X86)','C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe']
    exe=next((p for p in paths if p.is_file()),None)
    if not exe:raise RuntimeError('An installed Chrome or Edge browser is required. No installation was attempted.')
    profile=root/'browser-profile';profile.mkdir(exist_ok=True)
    subprocess.Popen([str(exe),'--app='+url,'--user-data-dir='+str(profile),'--no-first-run','--disable-sync','--disable-background-networking','--disable-component-update','--disable-extensions','--window-size=1500,980'],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',default=str(Path(__file__).parent));p.add_argument('--data');p.add_argument('--port',type=int,default=0);p.add_argument('--no-browser',action='store_true');p.add_argument('--demo',action='store_true');p.add_argument('--section',choices=['lessons']);a=p.parse_args()
    root=Path(a.root).resolve();data=Path(a.data).resolve() if a.data else root/'Data';data.mkdir(parents=True,exist_ok=True)
    # One local server per persistent library. Lock disappears after process exit.
    lock=(data/'studio-corridorkey-v1.lock').open('a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        r=json.loads((data/'runtime.json').read_text());req=urllib.request.Request(r['url'].split('?')[0]+'api/state',headers={'X-DJ-Token':r['token']})
        with urllib.request.urlopen(req,timeout=5) as response:response.read()
        if not a.no_browser:browser(data,r['url'],a.section,reuse=True)
        return
    server=Server(root,data,a.port,a.demo);(data/'runtime.json').write_text(json.dumps(dict(url=server.url,token=server.token,pid=os.getpid(),app_generation='corridorkey-v1')),encoding='utf-8')
    if not a.no_browser:browser(data,server.url,a.section)
    try:server.serve_forever(poll_interval=.3)
    finally:server.server_close();lock.close()

if __name__=='__main__':
    try:main()
    except Exception:
        import traceback
        (Path(__file__).parent/'studio-launch-error.log').write_text(traceback.format_exc(),encoding='utf-8');raise
