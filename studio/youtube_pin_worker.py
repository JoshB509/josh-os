"""Offline-stage YouTube pin worker gate. No process, polling or pin clicks.

A live observed comment/menu/confirmation recipe is required before mutation can
be implemented. This module deliberately cannot mark a comment pinned.
"""
import json
import re
import time
import uuid
from pathlib import Path
from .kit_chrome_gate import publish_policy, retire_policy, fingerprint, checked_policy
from .youtube_release import digest

PREFIX='mcp__claude-in-chrome__'

class PinUnavailable(ValueError): pass

class YouTubePinWorker:
    def __init__(self, publishing, comments, audit_root):
        self.publishing=publishing;self.comments=comments;self.audit_root=Path(audit_root)
        self.binding=None;self.folder=None

    def bind(self, job_id):
        """Read-only remote identity checks and exact local approval; no browser call."""
        with self.publishing.locked(job_id):
            job=self.publishing._load(job_id);packet=job.get('packet') or {}
            sha=digest({k:v for k,v in packet.items() if k!='packet_digest'})
            if packet.get('packet_digest')!=sha or (job.get('approval') or {}).get('digest')!=sha:
                raise PinUnavailable('The exact release is not approved.')
            vid=job.get('video_id')
            if job.get('state')!='published' or not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid or ''):
                raise PinUnavailable('The approved video is not confirmed public.')
            rec=self.comments._read('lead-magnet.json',{}).get(vid) or {}
            if rec.get('state')!='posted' or rec.get('packet_digest')!=sha or not rec.get('comment_id'):
                raise PinUnavailable('No verified posted comment is available to pin.')
            client=self.comments.client
            if client is None:raise PinUnavailable('Comments connection is unavailable.')
            channel=packet['channel_id'];video=client.video(vid) or {}
            if (client.channel() or {}).get('id')!=channel or channel!=self.comments.expected_channel:
                raise PinUnavailable('The connected channel does not match.')
            if video.get('id')!=vid or (video.get('snippet') or {}).get('channelId')!=channel or (video.get('status') or {}).get('privacyStatus')!='public':
                raise PinUnavailable('The remote video does not match the public release.')
            comment=client.get_comment(rec['comment_id']) or {};sn=comment.get('snippet') or {}
            if comment.get('id')!=rec['comment_id'] or sn.get('textOriginal')!=packet.get('pinned_comment') or (sn.get('authorChannelId') or {}).get('value')!=channel:
                raise PinUnavailable('The remote comment does not match the approved comment.')
            # Video-scoped list binds the comment to this video; comment resources omit videoId.
            page=None;found=False
            for _ in range(5):
                listing=client.video_threads(vid,page)
                found=any(((t.get('snippet') or {}).get('topLevelComment') or {}).get('id')==rec['comment_id'] for t in listing.get('items') or [])
                if found:break
                page=listing.get('nextPageToken')
                if not page:break
            if not found:raise PinUnavailable('The exact comment was not found on this video.')
            self.binding=dict(job_id=job_id,video_id=vid,comment_id=rec['comment_id'],packet_digest=sha,
                text=packet['pinned_comment'],channel_id=channel,
                url='https://www.youtube.com/watch?v='+vid+'&lc='+rec['comment_id'])
            return dict(self.binding)

    def capability(self):
        return dict(available=False,stage='offline_preparation_only',
            reason='No live observed YouTube comment menu and Pin confirmation recipe has been validated.')

    def permit_read(self, tool, args, *, tab_id):
        """Stage one exact fixed-target read for a supported transport. Does not execute it.
        Host supplies its positively owned tab. No JavaScript, clicks, typing or keys.
        """
        if not self.binding:raise PinUnavailable('Bind an approved, verified posted comment first.')
        if type(tab_id) is not int or args.get('tabId')!=tab_id:raise PinUnavailable('Exact owned tab required.')
        allowed=(tool=='navigate' and args=={'tabId':tab_id,'url':self.binding['url']}
            or tool=='read_page' and args=={'tabId':tab_id}
            or tool=='computer' and args=={'tabId':tab_id,'action':'screenshot'})
        if not allowed:raise PinUnavailable('Only exact YouTube target navigation, reading and screenshots are staged.')
        if self.folder is None:
            self.folder=self.audit_root/uuid.uuid4().hex;self.folder.mkdir(parents=True)
        policy=dict(id=uuid.uuid4().hex,tool=PREFIX+tool,args=args,expires=time.time()+60,binding_sha256=fingerprint(self.binding))
        publish_policy(self.folder,policy)
        return policy

    def receipt(self, policy_id):
        """Accept immutable hook-produced receipts only, never model descriptions."""
        if self.folder is None:raise PinUnavailable('No exact-call permit exists.')
        policy=checked_policy(self.folder)
        if policy['id']!=policy_id or policy['binding_sha256']!=fingerprint(self.binding):raise PinUnavailable('Target changed.')
        receipt=json.loads((self.folder/(policy_id+'.receipt.json')).read_text(encoding='utf-8'))
        claim=json.loads((self.folder/(policy_id+'.used')).read_text(encoding='utf-8'))
        complete=json.loads((self.folder/(policy_id+'.receipt-complete')).read_text(encoding='utf-8'))
        if (receipt.get('policy_id')!=policy_id or receipt.get('tool_use_id')!=claim.get('tool_use_id')
                or complete.get('tool_use_id')!=claim.get('tool_use_id') or receipt.get('tool')!=policy['tool']
                or receipt.get('args')!=policy['args'] or claim.get('policy_sha256')!=fingerprint(policy)):
            raise PinUnavailable('Receipt does not match the immutable call.')
        retire_policy(self.folder,policy_id)
        return receipt

    def pin(self):
        raise PinUnavailable(self.capability()['reason'])
