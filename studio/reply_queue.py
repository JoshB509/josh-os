"""Anouk's quick review: every proposed email and YouTube comment reply in one queue (16 Nov 2026).

Each item shows what Josh is answering, the earlier messages in the thread when they were saved, exactly where the reply
goes (the mailbox and recipients, or a public reply under one comment on one video) and the full reply text. "Send this
reply" carries the hash of exactly that version and destination:
- email: studio/replies.py binds the action, mailbox, recipients, subject, body, thread and revision. The approval is
  saved before anything happens, made by one guarded call, and then read back from Gmail (get_message, read only). It
  only shows as sent and checked when the read-back matches.
- comment: studio/youtube_comments.py binds the text, channel, video and parent comment. It is marked sending under a
  lock before the call and read back from YouTube afterwards.
Any edit makes a new version and a new hash, so an older approval no longer matches. A second click finds the reply
already taken. An unclear result is shown as unclear and never retried on its own. Nothing here sends without that click.
"""
from . import gmail_access

OUTCOMES = ('sending', 'replied', 'unverified', 'uncertain')


class NotAllowed(Exception):
    """A send, edit or dismissal that didn't come from Josh's own click in the app."""


class ReplyQueue:
    def __init__(self, replies, comments=None, run=None):
        self.replies, self.comments, self.run = replies, comments, run

    # Reading ---------------------------------------------------------------------------------------------------------
    def email_item(self, r):
        offer = self.replies.offer(r['id'])
        send = offer['actions'].get('send')
        last = next((a for a in offer['history'] if a['action'] == 'send'), None)
        item = dict(kind='email', id=r['id'], state=r['state'], who=r.get('from_name') or r.get('from_email') or '',
                    original=dict(from_name=r.get('from_name', ''), from_email=r.get('from_email', ''), subject=r.get('subject', ''),
                                  at=r.get('received_at', ''), text=r.get('original_text') or '', summary=r.get('summary', ''),
                                  needs=r.get('needs', '')),
                    thread=r.get('thread') or [], link=r.get('link', ''), reply=r.get('proposed_reply', ''),
                    version=r.get('revision', 1), digest=None, destination=None, available=False,
                    why="Gmail reading isn't set up on this PC, so a send can't be bound to a mailbox.",
                    outcome=self._email_outcome(last))
        if send:
            inp = send['input']
            item.update(reply=inp['body'], digest=send['digest'], available=send['available'], why=send['why'],
                        destination=dict(kind='email', account=send['account'], to=inp['to'], cc=inp.get('cc') or [],
                                         subject=inp['subject'], thread_id=inp.get('replyThreadId') or ''))
        return item

    @staticmethod
    def _email_outcome(a):
        if not a:
            return None
        ok = a['state'] == 'done' and (a.get('verification') or {}).get('ok')
        return dict(state='replied' if ok else ('sent_unchecked' if a['state'] == 'done' else a['state']), reason=a.get('reason', ''),
                    at=a.get('done_at') or a.get('updated_at'), view_url=a.get('view_url', ''))

    def comment_item(self, it):
        c = self.comments
        dest = c.destination(it)
        connected = c.client is not None
        return dict(kind='comment', id=it['id'], state=it.get('state'), who=it.get('author') or '',
                    original=dict(author=it.get('author') or '', text=it.get('text') or '', at=it.get('published_at') or '',
                                  video_id=it.get('video_id') or ''),
                    thread=it.get('thread') or [], link=dest['url'], reply=it.get('reply') or '', version=it.get('revision', 1),
                    digest=c.digest_for(it) if it.get('reply') else None, destination=dest,
                    available=connected and bool(it.get('reply')) and it.get('state') == 'needs_josh',
                    why='' if connected and it.get('reply') else ("YouTube isn't connected for comment replies." if not connected else
                                                                   'There is no proposed reply yet. Write one first.'),
                    outcome=dict(state=it['state'], reason=it.get('reason', ''), at=it.get('sent_at') or it.get('approved_at'),
                                 view_url=dest['url']) if it.get('state') in OUTCOMES else None)

    def view(self):
        waiting, done = [], []
        for r in self.replies.all():
            if r['state'] in ('proposed', 'drafted'):
                waiting.append(self.email_item(r))
            elif r['state'] == 'handled' and any(a['action'] == 'send' for a in self.replies.actions(r['id'])):
                done.append(self.email_item(r))
        if self.comments is not None:
            for it in self.comments.items().values():
                if it.get('state') == 'needs_josh':
                    waiting.append(self.comment_item(it))
                elif it.get('state') in OUTCOMES:
                    done.append(self.comment_item(it))
        key = lambda x: x['original'].get('at') or ''  # noqa: E731
        return dict(waiting=sorted(waiting, key=key), done=sorted(done, key=lambda x: (x['outcome'] or {}).get('at') or '', reverse=True)[:20],
                    email_on=bool(self.replies.action_config() and 'send' in self.replies.action_config()['actions']),
                    comments_on=self.comments is not None and self.comments.client is not None)

    # Sending -----------------------------------------------------------------------------------------------------------
    def send(self, kind, rid, digest, by='Josh'):
        """Send one reply, the exact version Josh saw. Returns the item afterwards, with its outcome."""
        if not str(digest or '').strip():
            raise ValueError('This reply has no approvable version yet, so nothing was sent.')
        if kind == 'email':
            if self.run is None:
                raise ValueError("Josh OS can't reach Gmail from here, so nothing was sent.")
            a = self.replies.approve(rid, 'send', digest, by=by, via='quick-review', said='Send this reply, on the quick review')
            a = self.replies.execute(a['id'], self.run)
            if a['state'] == 'done':
                self.replies.verify(a['id'], self.run)
            return self.email_item(self.replies.get(rid))
        if kind == 'comment':
            if self.comments is None:
                raise ValueError("YouTube comment replies aren't set up, so nothing was posted.")
            it = self.comments.send_reply(rid, digest, by=by, via='quick-review')
            return self.comment_item(it)
        raise ValueError('Unknown kind of reply.')

    def edit(self, kind, rid, text, by='Josh'):
        if kind == 'email':
            return self.email_item(self.replies.update(rid, dict(proposed_reply=text), by=by.lower()))
        if kind == 'comment' and self.comments is not None:
            return self.comment_item(self.comments.edit_reply(rid, text, by=by))
        raise ValueError('Unknown kind of reply.')

    def dismiss(self, kind, rid, by='Josh'):
        if kind == 'email':
            return self.email_item(self.replies.update(rid, dict(state='dismissed'), by=by.lower()))
        if kind == 'comment' and self.comments is not None:
            return self.comment_item(self.comments.dismiss(rid, by=by))
        raise ValueError('Unknown kind of reply.')


assert gmail_access.ACTION_TOOLS['send']   # the queue only ever sends, it never drafts or does anything else
