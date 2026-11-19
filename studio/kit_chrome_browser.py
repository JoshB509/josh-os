"""Concrete supported Kit browser reader; image insertion remains deliberately unavailable.

Uses the existing Claude-in-Chrome worker and raw hook receipts. Ordinary inspect
only reads. The separate inspect_image_menu method can focus verified existing text
and open its observed plus menu. Neither path takes over a locked draft or uploads.
"""
import json
import re
from pathlib import Path
import threading

from .kit_chrome_worker import KitChromeWorker,BOUNDARY_CONTROLS
from .kit_visual_browser import BrowserUnavailable, Snapshot
from .kit_visual_insertions import META
from .kit_draft_guard import private_draft, broadcast
from .kit_editor_observation import observed_body,tree_body
from .kit_image_recipe import paragraph_target,boundary_target,image_menu_target
from .store import now


def objects(receipt):
    for block in receipt.get('response',[]):
        if not isinstance(block,dict) or block.get('type')!='text':continue
        try:value=json.loads(block['text'])
        except (ValueError,KeyError):continue
        if isinstance(value,dict):yield value


def text_response(receipt):
    return '\n'.join(b.get('text','') for b in receipt.get('response',[]) if isinstance(b,dict) and b.get('type')=='text')


class KitChromeBrowser:
    def __init__(self,root,audit_root,read_broadcast=None,*,device_id,worker_factory=None):
        self.root=Path(root);self.audit_root=Path(audit_root);self.read_broadcast=read_broadcast
        self.device_id=device_id;self.worker_factory=worker_factory;self.lock=threading.Lock();self.last_blocker=''

    def readiness(self):
        return dict(available=False,methods=[],can_inspect=True,review_methods=['media_gallery'],
            reason=self.last_blocker or ('The dedicated reader uses supported Chrome tools and Kit structured-body reads. '
                'Paragraph focus and the plus menu with its Image entry have been observed without changing a draft. '
                'Upload and insertion are unavailable: Media Gallery/file-input, alt controls and saved image identity still need verification. '
                'Preparing a review reads the existing draft only.'))

    def _worker(self):
        if self.worker_factory:return self.worker_factory()
        from .agent import find_claude
        return KitChromeWorker(find_claude(self.root),self.root,self.audit_root)

    def _api(self,worker,bid):
        if self.read_broadcast is not None:return self.read_broadcast(bid)
        raw=worker.call('kit_get_broadcast',{'id':bid,'include':['content_tree']})
        return broadcast(raw.get('response'))

    def inspect(self,broadcast_id,*,reload):
        return self._inspect(broadcast_id,reload=reload,after_line=None)

    def inspect_image_menu(self,broadcast_id,*,after_line):
        """Read-only feasibility check, not insertion approval; never selects Image.

        The host binds the unique paragraph and adjacent hashes against the current
        clean body, opens only its plus menu, rechecks the API, and closes its tab.
        No route/tool grants this method as general Laia browser access.
        """
        if type(after_line) is not int or after_line<1:raise ValueError('Choose an existing nonblank paragraph boundary.')
        return self._inspect(broadcast_id,reload=True,after_line=after_line)

    def _inspect(self,broadcast_id,*,reload,after_line):
        if type(broadcast_id) is not int or broadcast_id<=0 or reload is not True:
            raise ValueError('Read back the exact existing draft after a fresh navigation.')
        if not self.lock.acquire(blocking=False):raise BrowserUnavailable('A dedicated Kit inspection is already running.')
        try:
            with self._worker() as worker:
                tab=None
                try:
                    api=self._api(worker,broadcast_id)
                    if not isinstance(api,dict) or api.get('id')!=broadcast_id or not private_draft(api):
                        raise BrowserUnavailable('Read-only Kit evidence must identify the exact private unsent draft before the editor is opened.')
                    tree_body(api)  # Fail before opening Chrome if structured body proof is unavailable.
                    worker.call('select_browser',{'deviceId':self.device_id})
                    context=worker.call('tabs_context_mcp',{'createIfEmpty':True})
                    groups=list(objects(context))
                    if len(groups)!=1 or len(groups[0].get('availableTabs',[]))!=1:
                        raise BrowserUnavailable('Expected one isolated session-owned inspection tab.')
                    tab=groups[0]['availableTabs'][0].get('tabId')
                    if type(tab) is not int:raise BrowserUnavailable('The browser did not identify its inspection tab.')
                    destination=f'https://app.kit.com/campaigns/{broadcast_id}/draft'
                    worker.call('navigate',{'tabId':tab,'url':destination})
                    match=None
                    for attempt in range(3):
                        page=worker.call('read_page',{'tabId':tab,'max_chars':50000});page_text=text_response(page)
                        if 'This broadcast is locked' in page_text:
                            raise BrowserUnavailable('Kit reports "This broadcast is locked" in another tab or window. This workflow never takes over editing. No upload or draft edit occurred.')
                        if '[BLOCKED:' in page_text:raise BrowserUnavailable('The supported editor read was denied. No alternate extraction will be attempted.')
                        match=re.search(r'textbox "Email content editor" \[(ref_\d+)\]',page_text)
                        if match:break
                    if not match:raise BrowserUnavailable('The exact Kit editor is absent or inaccessible.')
                    scoped=worker.call('read_page',{'tabId':tab,'ref_id':match.group(1),'max_chars':50000})
                    if '[BLOCKED:' in text_response(scoped):raise BrowserUnavailable('The supported body read was denied. No alternate extraction will be attempted.')
                    plain=worker.call('get_page_text',{'tabId':tab})
                    # Use only each tool's first content block; appended tab-context text is not body evidence.
                    try:body=observed_body(api,scoped['response'][0]['text'],plain['response'][0]['text'],broadcast_id)
                    except (IndexError,KeyError,TypeError):raise BrowserUnavailable('Incomplete supported editor observations.') from None
                    controls=None
                    if after_line is not None:
                        target=paragraph_target(api,scoped['response'][0]['text'],after_line)
                        worker.call('computer',dict(tabId=tab,action='left_click',ref=target['ref'],action_summary='Focus existing paragraph without editing'))
                        metadata=worker.call('javascript_tool',dict(tabId=tab,action='javascript_exec',text=BOUNDARY_CONTROLS))
                        worker.call('computer',dict(tabId=tab,action='left_click',coordinate=boundary_target(metadata),action_summary='Open observed paragraph insertion menu'))
                        menu=worker.call('read_page',dict(tabId=tab,max_chars=50000))
                        ref=image_menu_target(text_response(menu))
                        controls=dict(broadcast_id=broadcast_id,after_line=after_line,anchor=target,image_entry_ref=ref,
                                      menu_observed=True,media_gallery_observed=False,upload_available=False,
                                      evidence_session=str(worker.folder.name),body_proof=body['proof'])
                    api2=self._api(worker,broadcast_id)
                    keys=(*META,'content','content_tree','content_tree_is_clean','content_tree_warnings','email_template','theme','built_with_badge')
                    if not isinstance(api2,dict) or any(api2.get(k)!=api.get(k) for k in keys):
                        raise BrowserUnavailable('The Kit draft changed during inspection; review a new baseline.')
                    self.last_blocker=''
                    if any(k not in api for k in META):raise BrowserUnavailable('Required private draft metadata is missing from the read-only API response.')
                    if controls is not None:return controls
                    return Snapshot(str(worker.folder.name),now(),destination,True,{k:api[k] for k in META},body['html'],{},api,content_proof=body['proof'])
                finally:
                    # Close only the positively identified tab while its owning session is alive.
                    if tab is not None and not worker.closed:
                        try:worker.call('tabs_close_mcp',{'tabId':tab},timeout=45)
                        except Exception as exc:raise BrowserUnavailable('Owned inspection-tab cleanup could not be confirmed: '+str(exc)) from exc
        except (ValueError,OSError) as exc:
            self.last_blocker=str(exc);raise
        finally:self.lock.release()

    def insert_image(self,*args,**kwargs):
        raise BrowserUnavailable(self.readiness()['reason'])
