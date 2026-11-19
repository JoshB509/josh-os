"""Host-only bridge to the installed supported Chrome file_upload tool.

Not activated as the production Kit adapter. Requires an approved/reserved
KitVisualInsertions job, an isolated directory of exact copies, and fresh owned-tab
observations. Read permission is limited to this CLI session and those copies. Upload
is not insertion or a completion receipt; the UI adapter must verify those later.
"""
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

from .kit_chrome_worker import KitChromeWorker
from .kit_visual_browser import BrowserUnavailable
from .kit_visual_insertions import digest


def stage_session_assets(service,packet_id):
    """Make independent hash-named copies for one approved job, never arbitrary paths."""
    row=service.get(packet_id);service._check_approval(row);_,pngs=service._check_packet(row)
    uuid.UUID(row['id'])
    if row['digest']!=digest(row['packet']) or not re.fullmatch('[a-f0-9]{64}',row['digest']):raise ValueError('The approved job changed.')
    root=Path(service.store.root).resolve();folder=root/'kit-visual-session-assets'/row['id']/row['digest']/'files'
    if not folder.resolve().is_relative_to(root):raise ValueError('Session assets must remain in the local workspace.')
    folder.mkdir(parents=True,exist_ok=True)
    expected=set()
    for asset,png in zip(row['packet']['assets'],pngs,strict=True):
        sha=asset['visual']['png_hash'];name=sha+'.png';expected.add(name)
        if not re.fullmatch('[a-f0-9]{64}',sha) or hashlib.sha256(png).hexdigest()!=sha:raise ValueError('Original PNG differs from approval.')
        file=folder/name
        try:
            with file.open('xb') as out:out.write(png)
        except FileExistsError:
            if file.is_symlink() or file.resolve().parent!=folder.resolve() or file.stat().st_nlink!=1 or file.read_bytes()!=png:raise ValueError('Existing session copy differs from approval.') from None
    if {p.name for p in folder.iterdir()}!=expected:raise ValueError('Unexpected file in this job\'s session asset directory.')
    service._check_packet(row)
    return folder.resolve()


class KitUploadWorker(KitChromeWorker):
    def __init__(self,*args,service,packet_id,shared_directory,sharing_evidence_id,**kwargs):
        super().__init__(*args,**kwargs)
        self.service=service;self.packet_id=packet_id
        self.shared_directory=Path(shared_directory).resolve(strict=True)
        if not self.shared_directory.is_dir() or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',sharing_evidence_id):
            raise ValueError('A host-approved, already session-shared directory and evidence identity are required.')
        self.sharing_evidence_id=sharing_evidence_id;self.upload_permit=None
        # The owner-approved upload already identifies these bytes. Admit only
        # their isolated copies for this one CLI session; never global settings.
        row=self.service.get(self.packet_id);self.service._check_approval(row);self.service._check_packet(row)
        if digest(row['packet'])!=row['digest']:raise ValueError('The approved packet changed.')
        self.admitted={str(self.shared_directory/(a['visual']['png_hash']+'.png')):a['visual']['png_hash'] for a in row['packet']['assets']}
        if {p.name for p in self.shared_directory.iterdir()}!={Path(p).name for p in self.admitted}:
            raise ValueError('The session asset directory must contain only this job\'s exact PNG copies.')
        for name,sha in self.admitted.items():
            p=Path(name)
            if p.is_symlink() or p.resolve(strict=True).parent!=self.shared_directory or p.stat().st_nlink!=1 or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:
                raise ValueError('Each admitted file must be an independent exact PNG copy.')
        self.cwd=str(self.shared_directory)
        (self.folder/'admitted-pngs.json').write_text(json.dumps(self.admitted,sort_keys=True),encoding='utf8')

    def hook_path(self):return Path(__file__).with_name('kit_chrome_upload_guard.py')
    def builtin_tools(self):return 'ToolSearch,Read'
    def extra_allow_rules(self):return ['Read(./'+Path(p).name+')' for p in self.admitted]
    def extra_cli_args(self):return ['--add-dir',str(self.shared_directory)]
    def session_settings(self):return {'permissions':{'allow':self.extra_allow_rules()}}

    def extra_tools(self):return ('file_upload',)

    def mutation_rules(self):
        return ('No shell, credentials, draft text edits, forms, scheduling or sending actions. '
                'The sole upload exception is one exact host-permitted file_upload and the exact admitted PNG Read needed for it, with its supplied '
                'shared PNG path, fresh file-input ref and session-owned tab. Do not click Choose File '
                'or open native dialogs. Never retry an upload, select gallery assets or insert blocks. ')

    def _receipt(self,cid,tool,tab):
        """Only a completed raw receipt already produced by this worker is evidence."""
        if not re.fullmatch(r'[a-f0-9]{32}',str(cid)):raise BrowserUnavailable('Invalid observation identity.')
        call=next((c for c in self.calls if c['id']==cid),None)
        if not call or call['tool']!=tool or call['args'].get('tabId')!=tab:raise BrowserUnavailable('Observation is not from this owned tab/session.')
        file=self.folder/(cid+'.receipt.json')
        if not file.is_file() or time.time()-file.stat().st_mtime>30:raise BrowserUnavailable('Observe the file input and exact draft again.')
        data=json.loads(file.read_text(encoding='utf8'))
        if data.get('policy_id')!=cid or data.get('args')!=call['args'] or not data.get('tool','').endswith('__'+tool):raise BrowserUnavailable('Observation identity changed.')
        try:text=data['response'][0]['text']
        except (KeyError,IndexError,TypeError):raise BrowserUnavailable('Incomplete supported observation.') from None
        if not isinstance(text,str) or any(s in text for s in ('[BLOCKED:','[TRUNCATED','This broadcast is locked')):
            raise BrowserUnavailable('A complete, unlocked supported observation is required.')
        return text,call

    def _check_job(self,command,png):
        row=self.service.get(self.packet_id);packet=row['packet']
        self.service._check_approval(row);self.service._check_packet(row)
        if row['state']!='insertion_uncertain' or digest(packet)!=row['digest']:
            raise BrowserUnavailable('The exact approved job must be reserved before uploading.')
        if command.method not in ('media_gallery','media_gallery_auto') or (command.packet_id,command.packet_digest,command.broadcast_id,command.destination,command.method)!=(
                row['id'],row['digest'],packet['broadcast_id'],packet['destination'],packet['method']):
            raise BrowserUnavailable('Upload belongs to another approved job or draft.')
        if type(command.ordinal) is not int or not 0<=command.ordinal<len(packet['assets']):raise BrowserUnavailable('Invalid image ordinal.')
        asset=packet['assets'][command.ordinal];visual=asset['visual']
        if command.png_hash!=visual['png_hash'] or command.alt!=visual['alt'] or command.anchor!=asset['anchor']:
            raise BrowserUnavailable('Image identity, alt text or placement changed.')
        if not isinstance(png,bytes) or not 0<len(png)<10_000_000 or hashlib.sha256(png).hexdigest()!=command.png_hash:
            raise BrowserUnavailable('Only the exact original PNG below ten megabytes can be uploaded.')
        return row

    def upload_exact(self,command,png,*,tab_id,page_receipt_id,identity_receipt_id,before_mutation,snapshot):
        """Issue the real file_upload call once; never assert upload/insert success.

        before_mutation is the existing service's fresh private-draft/source guard.
        The caller supplies its fresh Snapshot, not model prose. The independently
        checked worker receipts bind the file input and destination to this tab.
        The worker admits only the exact hash-named copy through documented
        session-only Read rules. Merely passing an arbitrary path grants no access.
        """
        self._check_job(command,png)
        if tab_id not in self.owned_tabs or len(self.owned_tabs)!=1:raise BrowserUnavailable('Use one dedicated session-owned tab.')
        page,pcall=self._receipt(page_receipt_id,'read_page',tab_id)
        identity,icall=self._receipt(identity_receipt_id,'get_page_text',tab_id)
        if not re.match(r'\ATitle: [^\n]*\nURL: '+re.escape(command.destination)+r'(?:\r?\n|\Z)',identity):raise BrowserUnavailable('The observed tab is not the exact draft.')
        if not re.match(r'\A\s*dialog "Media Gallery" \[ref_\d+\]\s*\n',page):raise BrowserUnavailable('Read the scoped Media Gallery dialog before uploading.')
        refs=re.findall(r'^\s*button "Choose File" \[(ref_\d+)\] type="file"\s*$',page,re.M)
        if len(refs)!=1:raise BrowserUnavailable('The observed Choose File input is absent or ambiguous.')
        # No intervening navigation/click may make the observed reference stale.
        start=min(self.calls.index(pcall),self.calls.index(icall))
        if any(c['tool'] not in ('read_page','get_page_text','kit_get_broadcast') for c in self.calls[start:]):
            raise BrowserUnavailable('The tab changed after its upload observation.')
        file=self.shared_directory/(command.png_hash+'.png')
        if file.is_symlink() or file.resolve(strict=True).parent!=self.shared_directory or file.read_bytes()!=png:
            raise BrowserUnavailable('The session-shared original PNG is missing or changed.')
        before_mutation('upload_insert' if command.method=='media_gallery_auto' else 'upload',snapshot)
        self._check_job(command,png)
        # Durable single-attempt marker is job/ordinal-scoped, across new workers.
        attempts=Path(self.service.store.root)/'kit-browser-upload-attempts';attempts.mkdir(exist_ok=True)
        marker=attempts/(digest([command.packet_id,command.packet_digest,command.ordinal])+'.json')
        args=dict(paths=[str(file)],ref=refs[0],tabId=tab_id)
        attempt=dict(state='reserved',packet_id=command.packet_id,packet_digest=command.packet_digest,
                     ordinal=command.ordinal,png_hash=command.png_hash,sharing_evidence_id=self.sharing_evidence_id)
        with marker.open('x',encoding='utf8') as f:
            json.dump(attempt,f)
        self.upload_permit=dict(args=args,expires=time.monotonic()+10,command=command,png=png,file=file)
        try:
            receipt=self.call('file_upload',args)
            # Raw transport evidence only. No URL/resource-id guessed from prose.
            marker.write_text(json.dumps(dict(attempt,state='attempted_requires_readback',receipt_id=receipt['policy_id'])),encoding='utf8')
            return receipt
        finally:self.upload_permit=None

    def check_extra_call(self,tool,args):
        permit=self.upload_permit;self.upload_permit=None
        if tool!='file_upload' or not permit or permit['args']!=args or time.monotonic()>permit['expires']:
            return False
        self._check_job(permit['command'],permit['png'])
        if permit['file'].read_bytes()!=permit['png']:raise BrowserUnavailable('Shared original changed immediately before upload.')
        return True
