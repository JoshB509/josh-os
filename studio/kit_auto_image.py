"""Kit-specific auto-inserting upload recipe; not activated by the server.

Controls match the owner-verified Kit editor. Host observers must provide raw
same-session evidence; this module never accepts agent selectors or browser jobs.
Live loaded-image observation remains a separately required production dependency.
"""
import time
import struct
from datetime import datetime,timezone

from .kit_chrome_upload import KitUploadWorker
from .kit_chrome_worker import BOUNDARY_CONTROLS
from .kit_image_recipe import paragraph_target,image_menu_target
from .kit_editor_observation import tree_body
from .kit_visual_insertions import Document,META,digest,image_node
from .kit_visual_browser import BrowserUnavailable,UploadReceipt
from .kit_draft_guard import private_draft
import re


def unique_control(page,role,label,suffix=''):
    if any(x in page for x in ('[BLOCKED:','[TRUNCATED','This broadcast is locked')):
        raise BrowserUnavailable('The current control observation is unavailable or locked.')
    refs=re.findall(r'^\s*'+role+' "'+re.escape(label)+r'" \[(ref_\d+)\]'+suffix+r'\s*$',page,re.M)
    if len(refs)!=1:raise BrowserUnavailable('The exact '+label+' control is absent or ambiguous.')
    return refs[0]


class KitAutoImageWorker(KitUploadWorker):
    """One approved image's exact controls, with immutable single-call hooks."""
    def __init__(self,*args,**kw):
        super().__init__(*args,**kw);self.recipe=None;self.control_permit=None

    def extra_tools(self):return (*super().extra_tools(),'form_input')

    def mutation_rules(self):
        return ('Only host-bound steps for one approved PNG: existing paragraph focus, End then ArrowDown, '
                'observed plus/Image menus, one auto-inserting file_upload, caption off, Accessibility and exact alt. '
                'No other keys, text, links, forms, sends, schedules, takeovers, credentials or retries. ')

    def begin_image(self,command,png,tab):
        if self.recipe and self.recipe['step']!='done':raise BrowserUnavailable('The prior image is incomplete; never retry it.')
        if command.method!='media_gallery_auto' or self.owned_tabs!={tab}:raise BrowserUnavailable('Use the reviewed auto-insert method and one owned tab.')
        self._check_job(command,png)
        self.recipe=dict(command=command,png=png,tab=tab,step='focus')

    def take_bound_control(self,tool,args):
        permit=self.control_permit;self.control_permit=None
        if not permit:return False
        if permit['tool']!=tool or permit['args']!=args or time.monotonic()>permit['expires']:return False
        self._check_job(self.recipe['command'],self.recipe['png'])
        return self.owned_tabs=={args.get('tabId')}

    def control(self,step,*,page_id=None,api=None):
        r=self.recipe
        if not r or r['step']!=step:raise BrowserUnavailable('Image controls must execute once in the approved order.')
        self._check_job(r['command'],r['png']);tab=r['tab'];tool='computer'
        page=self._receipt(page_id,'read_page',tab)[0] if page_id else None
        args=dict(tabId=tab,action='left_click')
        next_step={'focus':'end','end':'blank','blank':'plus','plus':'image','image':'upload','caption':'accessibility','accessibility':'alt','alt':'done'}[step]
        if step=='focus':
            target=paragraph_target(api,page,r['command'].anchor['after_line']+r['command'].ordinal)
            if (target['left_hash'],target['right_hash'])!=(r['command'].anchor['left_hash'],r['command'].anchor['right_hash']):
                raise BrowserUnavailable('The observed paragraph boundary differs from approval.')
            following=api['content_tree'][target['after_line']:target['after_line']+1]
            if following!=[dict(type='paragraph',children=[dict(text='')])] and following!=[dict(type='paragraph',children=[dict(text='\u200b')])]:
                raise BrowserUnavailable('End/ArrowDown requires the observed empty paragraph immediately after this anchor.')
            args.update(ref=target['ref'],action_summary='Focus existing paragraph without editing')
        elif step in ('end','blank'):
            args=dict(tabId=tab,action='key',text='End' if step=='end' else 'ArrowDown',action_summary='Move within the approved paragraph boundary')
        elif step=='plus':
            if tab not in self.boundary_caps or time.monotonic()-self.boundary_caps[tab]['at']>30:raise BrowserUnavailable('Observe the exact plus control after positioning the caret.')
            args.update(coordinate=self.boundary_caps[tab]['coordinate'],action_summary='Open observed paragraph insertion menu')
        elif step=='image':args.update(ref=image_menu_target(page),action_summary='Open observed Media Gallery without selecting an image')
        elif step=='caption':
            ref=unique_control(page,'switch','Show caption',r'(?: \[(?:checked|unchecked)\])?')
            line=next(x for x in page.splitlines() if '['+ref+']' in x)
            if '[checked]' not in line:
                if '[unchecked]' not in line:raise BrowserUnavailable('Caption state is not explicit.')
                r['step']=next_step;return None
            args.update(ref=ref,action_summary='Turn off caption on the approved inserted image')
        elif step=='accessibility':
            if 'textbox "Describe this image for screen readers"' in page:
                unique_control(page,'textbox','Describe this image for screen readers');r['step']=next_step;return None
            args.update(ref=unique_control(page,'button','Accessibility'),action_summary='Expand accessibility for the approved inserted image')
        elif step=='alt':
            tool='form_input';args=dict(tabId=tab,ref=unique_control(page,'textbox','Describe this image for screen readers'),
                                       value=r['command'].alt,action_summary='Set the exact approved image alternative text')
        # Consume state before sending: an uncertain call cannot be repeated.
        r['step']='uncertain';self.control_permit=dict(tool=tool,args=args,expires=time.monotonic()+10)
        try:result=self.call(tool,args)
        finally:self.control_permit=None
        r['step']=next_step;return result

    def upload_auto(self,*,page_id,identity_id,before_mutation,snapshot):
        r=self.recipe
        if not r or r['step']!='upload':raise BrowserUnavailable('The exact image upload step is not available.')
        r['step']='uncertain'
        receipt=self.upload_exact(r['command'],r['png'],tab_id=r['tab'],page_receipt_id=page_id,identity_receipt_id=identity_id,
                                  before_mutation=before_mutation,snapshot=snapshot)
        r['step']='caption';return receipt


class KitAutoImageRecipe:
    """Concrete control order with exact intermediate API/source checks.

    observe(worker, tab, command, reload=...) is privileged host code. It must
    return a fresh Snapshot, raw page/editor/identity receipt IDs, and positively
    observed selected-image/load details. Until the production observer exists,
    this recipe is usable only by isolated fixtures; it is never auto-enabled.
    """
    def __init__(self,service,observe):self.service=service;self.observe=observe;self.captures=set()

    def checked(self,worker,tab,command,*,pending=False,reload=False):
        row=worker._check_job(command,worker.recipe['png']);packet=row['packet']
        o=self.observe(worker,tab,command,reload=reload);snap=o['snapshot'];api=snap.api_broadcast
        # Raw same-session evidence must identify the exact owned draft.
        identity,_=worker._receipt(o['identity_id'],'get_page_text',tab)
        if not re.match(r'\ATitle: [^\n]*\nURL: '+re.escape(command.destination)+r'\n',identity):
            raise BrowserUnavailable('The current observation is not the exact owned draft.')
        page,_=worker._receipt(o['page_id'],'read_page',tab)
        if 'This broadcast is locked' in page:raise BrowserUnavailable('Never take over a locked broadcast.')
        if reload and not re.search(r'^\s*(?:generic|status) "Saved" \[ref_\d+\]',page,re.M):
            raise BrowserUnavailable('A fresh Saved label is required after reload.')
        if not isinstance(api,dict) or not private_draft(api) or {k:api.get(k) for k in META}!=packet['baseline']['metadata']:
            raise BrowserUnavailable('Private draft metadata or audience changed.')
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(snap.captured_at)).total_seconds()
        if not -5<=age<=60 or not snap.capture_id or snap.url!=command.destination or (reload and not snap.reloaded):
            raise BrowserUnavailable('The image observation is stale or not reloaded.')
        if snap.capture_id in self.captures:raise BrowserUnavailable('A fresh observation is required before every image control.')
        self.captures.add(snap.capture_id)
        template_keys=('email_template','theme','built_with_badge')
        template=digest({k:api.get(k) for k in template_keys}) if any(k in api for k in template_keys) else None
        if template!=packet['baseline']['template_hash'] or (snap.content_proof or {}).get('footer_sha256')!=packet['baseline']['footer_hash']:
            raise BrowserUnavailable('The existing template or footer changed.')
        if not pending:
            self.service._matches(packet,snap,self.service._receipts(command.packet_id),command.ordinal,reload=False)
            return o
        body=tree_body(api,allow_pending_caption=True);nodes=Document(body['html']).nodes
        at=command.anchor['after_line']+command.ordinal
        if not 0<=at<len(nodes):raise BrowserUnavailable('The inserted image is missing at its approved boundary.')
        image=image_node(nodes[at]);selected=o.get('selected_image',{})
        width,height=struct.unpack('>II',worker.recipe['png'][16:24])
        if (selected.get('src')!=image['src'] or selected.get('alt')!=image['alt'] or selected.get('url')!=''
                or selected.get('loaded') is not True or selected.get('width')!=width
                or selected.get('height')!=height):
            raise BrowserUnavailable('Image selection, loaded dimensions, alt or empty URL is not verified.')
        images=body['images']
        if len(images)!=command.ordinal+1 or any(i['showCaption'] for i in images[:-1]):raise BrowserUnavailable('Image count or existing captions changed.')
        current=images[command.ordinal]
        if current['src']!=image['src'] or current['alt'] not in ('',command.alt):raise BrowserUnavailable('The new image identity or alt differs.')
        # Compare every other node to baseline plus already verified images.
        if Document(snap.content_html).nodes!=nodes:raise BrowserUnavailable('Browser and structured image body disagree.')
        remaining=nodes[:at]+nodes[at+1:]
        expected=[];receipts=self.service._receipts(command.packet_id)
        for boundary in range(len(packet['baseline']['document'])+1):
            for ordinal,asset in enumerate(packet['assets'][:command.ordinal]):
                if asset['anchor']['after_line']==boundary:
                    expected.append(('image',ordinal))
            if boundary<len(packet['baseline']['document']):expected.append(('text',packet['baseline']['document'][boundary]))
        if len(remaining)!=len(expected):raise BrowserUnavailable('Duplicate or unrelated content appeared.')
        for node,(kind,want) in zip(remaining,expected):
            if kind=='text':
                if node!=want:raise BrowserUnavailable('Body wording, links or placement changed.')
            else:
                attrs=image_node(node)
                if attrs['alt']!=packet['assets'][want]['visual']['alt'] or snap.image_resources.get(attrs['src'])!=receipts[want]['resource_id']:
                    raise BrowserUnavailable('A previously verified image changed.')
        if len(images)!=command.ordinal+1:raise BrowserUnavailable('Unexpected image count.')
        o['new_image']=current;return o

    def run(self,worker,tab,command,png,before_mutation,record_upload):
        worker.begin_image(command,png,tab)
        o=self.checked(worker,tab,command)
        worker.control('focus',page_id=o['editor_id'],api=o['snapshot'].api_broadcast)
        worker.control('end');worker.control('blank')
        worker.call('javascript_tool',dict(tabId=tab,action='javascript_exec',text=BOUNDARY_CONTROLS))
        worker.control('plus')
        page=worker.call('read_page',dict(tabId=tab,max_chars=50000));worker.control('image',page_id=page['policy_id'])
        o=self.checked(worker,tab,command)
        upload=worker.upload_auto(page_id=o['gallery_id'],identity_id=o['identity_id'],before_mutation=before_mutation,snapshot=o['snapshot'])
        o=self.checked(worker,tab,command,pending=True);im=o['new_image']
        record_upload(UploadReceipt(command.packet_id,command.ordinal,command.png_hash,im['resource_id'],im['src'],command.method,
                                    upload['policy_id']))
        worker.control('caption',page_id=o['page_id'])
        o=self.checked(worker,tab,command,pending=True)
        if o['new_image']['showCaption']:raise BrowserUnavailable('Caption did not turn off; no retry.')
        worker.control('accessibility',page_id=o['page_id'])
        o=self.checked(worker,tab,command,pending=True)
        worker.control('alt',page_id=o['page_id'])
        # Autosave, never a schedule/publish/send control. Read-only polling belongs
        # to the observer; it may not repeat any mutating control.
        o=self.checked(worker,tab,command,pending=True,reload=True)
        if not o.get('saved') or o['new_image']['showCaption'] or o['new_image']['alt']!=command.alt:
            raise BrowserUnavailable('The exact image settings did not persist after reload.')
        self.service._matches(self.service.get(command.packet_id)['packet'],o['snapshot'],self.service._receipts(command.packet_id),command.ordinal+1)
        return o['snapshot']
