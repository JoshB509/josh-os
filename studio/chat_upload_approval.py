"""Exact Unlisted approval, called only by authenticated chat ingress adapters.

Never expose this object as an agent tool. A model's actor/via fields are not
identity. The adapters in owner_release_chat.py establish the principal.
"""
import hashlib
import json
import re
import time
from .approval_engine import ApprovalEngine, canonical_json
from .youtube_publishing import PublishingError, utcnow
from .youtube_release import verify


class ChatUploadApproval:
    def __init__(self, service, revision_reader, clock=time.time):
        self.service = service
        self.revision_reader = revision_reader
        self.engine = ApprovalEngine(service.db, clock=clock)

    def _validate(self, job):
        p = job['packet']
        if p.get('initial_privacy') != 'unlisted' or p.get('schedule') is not None:
            raise PublishingError('Chat approval is for Unlisted uploads only, never public or scheduled releases.')
        if not p.get('project_id') or type(p.get('project_revision')) is not int:
            raise PublishingError('Prepare a release bound to the exact video card revision first.')
        if self.revision_reader(p['project_id']) != p['project_revision']:
            raise PublishingError('The video card changed. Prepare and review the exact new release.')
        verify(p)
        if not self.service.auth.connected:
            raise PublishingError('Publishing permissions are not connected.')
        if self.service.client.channel() != p['channel_id']:
            raise PublishingError('Connected YouTube channel differs from this packet.')
        return p

    @staticmethod
    def _action(job):
        p = job['packet']
        # Include the full packet digest in the opaque job binding: title, copy,
        # disclosures, thumbnail hashes and every other packet field are covered.
        return dict(job_id=job['id'] + ':' + p['packet_digest'],
                    project_id=p['project_id'], revision=p['project_revision'],
                    channel_id=p['channel_id'], file_sha256=p['video']['sha256'],
                    visibility='unlisted', schedule=None)

    def review(self, jid, principal):
        if not isinstance(principal, str) or not principal.strip():
            raise PublishingError('Authenticated owner identity is required.')
        with self.service.locked(jid):
            job = self.service._load(jid)
            if job['state'] != 'needs_review' or job.get('approval') or job.get('video_id'):
                raise PublishingError('This job already has an outcome or approval. Inspect its saved status; do not create another upload.')
            self._validate(job)
            nonce, action_digest = self.engine.issue(principal, self._action(job))
        # The command contains the immutable packet digest, not a fuzzy title.
        return dict(packet=job['packet'], command='/release approve-unlisted ' + jid + ' ' +
                    job['packet']['packet_digest'] + ' ' + nonce,
                    challenge_digest=action_digest, expires_in_seconds=900)

    def approve(self, jid, packet_digest, nonce, principal):
        if not isinstance(principal, str) or not principal.strip():
            raise PublishingError('Authenticated owner identity is required.')
        if not re.fullmatch(r'[a-f0-9]{64}', packet_digest):
            raise PublishingError('Use the exact confirmation command from the release review.')
        with self.service.locked(jid):
            job = self.service._load(jid)
            if job['packet']['packet_digest'] != packet_digest:
                raise PublishingError('The exact release packet changed.')
            action = self._action(job)
            action_digest = hashlib.sha256(canonical_json(action).encode()).hexdigest()

            def validate(saved):
                if saved != action:
                    raise PublishingError('Confirmation belongs to a different job or release.')
                if job['state'] != 'needs_review' or job.get('approval') or job.get('video_id') or job.get('session'):
                    raise PublishingError('This job has already started; inspect the saved outcome.')
                self._validate(job)

            def commit(con, saved):
                # Same database transaction as the one-use claim. No crash window
                # between consuming consent and persisting the approved job.
                for (raw,) in con.execute('SELECT body FROM jobs WHERE id<>?', (jid,)):
                    other = json.loads(raw)
                    if (other.get('approval') and other['packet']['channel_id'] == saved['channel_id']
                            and other['packet']['video']['sha256'] == saved['file_sha256']):
                        raise PublishingError('This file already has an approved upload job: ' + other['id'])
                job['approval'] = dict(digest=packet_digest, at=utcnow(), source='authenticated_owner_chat',
                                       principal_hash=hashlib.sha256(principal.encode()).hexdigest(),
                                       scope='unlisted_only', challenge_hash=hashlib.sha256(nonce.encode()).hexdigest())
                job['state'] = 'approved'
                job['updated_at'] = utcnow()
                con.execute('UPDATE jobs SET body=? WHERE id=?', (json.dumps(job), jid))
                return dict(job_id=jid, packet_digest=packet_digest, state='approved', scope='unlisted_only')

            result = self.engine.consume(nonce, action_digest, principal, validate, commit)
        if not result.replay:
            # If dispatch is uncertain, approval/consumption remain saved. A repeated
            # chat command returns state only; recovery requires explicit app review.
            try:
                self.service.resume(jid)
            except Exception:
                return dict(job=self.service.get(jid), replay=False,
                            notice='Approval saved; worker dispatch was uncertain. Inspect this job in the release page before recovery.')
        return dict(job=self.service.get(jid), replay=result.replay)
