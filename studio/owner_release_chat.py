"""Trusted ingress for deterministic release commands. Never called by a model.

Browser identity comes from an already-paired, currently valid owner session;
legacy shared API tokens do not qualify. Discord identity comes directly from
the verified Gateway event and existing owner configuration, only in a DM.
"""
import hashlib
import json
import re
import uuid
from http.cookies import SimpleCookie
from .youtube_publishing import utcnow

PREFIX = '/release'
REVIEW = re.compile(r'/release review ([a-f0-9]{32})')
APPROVE = re.compile(r'/release approve-unlisted ([a-f0-9]{32}) ([a-f0-9]{64}) ([A-Za-z0-9_-]{43})')


def is_command(message):
    return isinstance(message, str) and bool(re.match(r'^/release(?:\s|$)', message.strip(), re.IGNORECASE))


def browser_principal(handler):
    if not getattr(handler, 'device_authenticated', False):
        raise ValueError('Use your already-paired CareerOS browser or your verified owner DM to Sam. Agent/API-token requests cannot approve.')
    if (handler.headers.get('Origin') != handler.pairing_origin()
            or handler.headers.get('Sec-Fetch-Site') != 'same-origin'):
        raise ValueError('Release approval must originate in the paired chat.')
    cookie = SimpleCookie()
    cookie.load(handler.headers.get('Cookie', ''))
    if '__Host-dj_device' not in cookie or not handler.device_cookie_valid(cookie):
        raise ValueError('Owner browser session expired. Sign in through the existing pairing flow.')
    return 'paired:' + hashlib.sha256(cookie['__Host-dj_device'].value.encode()).hexdigest()


def discord_principal(msg, config, *, is_dm, agent_id):
    if (agent_id != 'sam' or not is_dm or msg.author.bot
            or str(msg.author.id) not in config.get('owners', [])):
        raise ValueError('Only the configured owner in a direct message to Sam can approve an Unlisted release.')
    return 'discord-owner:' + str(msg.author.id)


def dispatch(approvals, message, principal, agent_id):
    if agent_id != 'sam':
        raise ValueError('Send this exact release command to Sam.')
    text = message.strip()
    match = REVIEW.fullmatch(text)
    if match:
        result = approvals.review(match[1], principal)
        p = result['packet']
        # Show the complete packet so all approved fields are actually reviewable.
        return ('Exact Unlisted release. No public publishing or schedule is authorized.\n'
                + json.dumps(p, indent=2, ensure_ascii=False) + '\n\n'
                + 'To approve this exact version within 15 minutes, send this command from the same chat session:\n'
                + result['command'])
    match = APPROVE.fullmatch(text)
    if match:
        result = approvals.approve(match[1], match[2], match[3], principal)
        job = result['job']
        return ('Existing approval; no duplicate dispatch. ' if result['replay'] else 'Exact Unlisted approval saved. ') + (
            'Job ' + job['id'] + ': ' + job['state'] + '. '
            + ('Video: ' + job['url'] if job.get('url') else 'No verified YouTube URL yet.')
            + (' ' + result['notice'] if result.get('notice') else '')
            + ' Public publishing, scheduling, comments and A/B testing are not executed by this approval command.')
    raise ValueError('Use /release review <exact job ID>, then copy its exact approve-unlisted command. An ambiguous go-ahead cannot authorize uploading.')


def browser_chat(handler, data):
    if not is_command(data.get('message')):
        return None
    principal = browser_principal(handler)
    # Do not inspect any client-supplied actor/via/discord metadata.
    text = dispatch(handler.server.chat_upload_approval, data['message'], principal, data.get('agent_id'))
    at = utcnow()
    mine = dict(id=str(uuid.uuid4()), role='josh', text=data['message'], at=at)
    reply = dict(id=str(uuid.uuid4()), role='agent', text=text, at=at, conversation='joshos:desk')
    messages = handler.server.agents._append('desk', 'sam', mine, reply)
    return dict(messages=messages, reply=reply)
