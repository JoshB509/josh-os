"""Evidence that the running Josh OS loaded the approval-lineage fix (13 Nov 2026, for the independent review).

Read only. It touches no credentials, no real messages, no Gmail and no settings. When the server starts it:
- fingerprints the functions it actually loaded (studio/approval_lineage.py Lineage.resolve, _resolve, _delivered_after,
  begin, finish and replies.Replies.natural_from_chat), by hashing their compiled bytecode, constants and names. That is
  what runs, not what is on disk, so a later edit to the file doesn't change it;
- runs the race checks against a throwaway store in a temporary folder: "send it" while another message is going out
  must be refused at once (not after waiting), and a message Discord created after Josh's must never be answered;
- writes the result, with this process's own id, start time and port, to Data/diagnostics/approval-lineage.json. The
  server also serves it at /api/diagnostics/approval-lineage, which needs Josh's session like every other /api route.
"""
import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

FUNCTIONS = (('approval_lineage', 'Lineage', 'resolve'), ('approval_lineage', 'Lineage', '_resolve'),
             ('approval_lineage', 'Lineage', '_delivered_after'), ('approval_lineage', 'Lineage', 'begin'),
             ('approval_lineage', 'Lineage', 'finish'), ('replies', 'Replies', 'natural_from_chat'),
             # Tariq's cut-edge safety (13 Nov 2026 review): edges stay in their own word-free gap, the final removed range is
             # checked again after grid snapping, and every new edge must be quiet.
             ('clean_transcript', 'CleanTranscript', 'keeps'), ('clean_transcript', 'CleanTranscript', '_quiet'),
             ('clean_transcript', 'CleanTranscript', '_quiet_edge'), ('clean_transcript', 'CleanTranscript', '_check_final'),
             ('clean_transcript', 'CleanTranscript', '_on_grid'))


def _code_digest(code):
    """A stable hash of a code object: its bytecode, constants (nested code included), names and variables."""
    h = hashlib.sha256()
    h.update(code.co_code)
    for c in code.co_consts:
        h.update((_code_digest(c) if hasattr(c, 'co_code') else repr(c)).encode('utf-8'))
    h.update(repr((code.co_names, code.co_varnames, code.co_argcount)).encode('utf-8'))
    return h.hexdigest()


def fingerprints(modules=None):
    """{module.Class.function: sha256} for the functions as loaded in this process."""
    import importlib
    out = {}
    for mod, cls, fn in FUNCTIONS:
        m = (modules or {}).get(mod) or importlib.import_module('studio.' + mod)
        out[f'{mod}.{cls}.{fn}'] = _code_digest(getattr(getattr(m, cls), fn).__code__)
    return out


def self_check():
    """Run the race checks on the loaded classes against a throwaway store. Returns dict(passed, checks)."""
    from .approval_lineage import Lineage
    from .store import Store
    checks = {}
    with tempfile.TemporaryDirectory() as d:
        lin = Lineage(Store(Path(d) / 'Data'))
        conv, offer = 'discord:dm:42', [dict(proposal_id='p-a')]
        a = lin.record('anouk', conv, ['600000000001'], offer, owner_id='42', channel_id='9')
        b = lin.begin('anouk', conv, [dict(proposal_id='p-b')], owner_id='42', channel_id='9')
        t0 = time.monotonic()
        rec, why = lin.resolve('anouk', conv, author_id='42', channel_id='9', inbound_id='600000000005')
        took = time.monotonic() - t0
        checks['send_it_while_another_message_is_going_out'] = dict(expected='busy, at once', got=why, bound=rec is not None,
                                                                    seconds=round(took, 3), ok=why == 'busy' and rec is None and took < 0.5)
        lin.finish(b, ['600000000009'], 'sent')
        rec, why = lin.resolve('anouk', conv, author_id='42', channel_id='9', inbound_id='600000000005')
        checks['message_created_after_josh_is_never_answered'] = dict(expected='arrived_after', got=why, bound=rec is not None,
                                                                      ok=why == 'arrived_after' and rec is None)
        rec, why = lin.resolve('anouk', conv, reply_to='600000000001', author_id='42', channel_id='9', inbound_id='600000000005')
        checks['a_reply_to_the_earlier_message_binds_only_it'] = dict(expected=a['id'], got=(rec or {}).get('id') or why,
                                                                      ok=rec is not None and rec['id'] == a['id'])
        c = lin.record('anouk', conv, ['600000000020', '600000000030'], offer, owner_id='42', channel_id='9')
        rec, why = lin.resolve('anouk', conv, reply_to='600000000020', author_id='42', channel_id='9', inbound_id='600000000025')
        checks['every_part_must_precede_josh'] = dict(expected='arrived_after', got=why, ok=why == 'arrived_after' and rec is None)
        rec, why = lin.resolve('anouk', conv, author_id='43', channel_id='9', inbound_id='600000000040')
        checks['another_owner_is_refused'] = dict(expected='other_owner', got=why, ok=why == 'other_owner' and rec is None)
        rec, why = lin.resolve('anouk', conv, author_id='42', channel_id='9', inbound_id='')
        checks['no_inbound_id_binds_nothing'] = dict(expected='no_conversation', got=why, ok=rec is None)
        rec, why = lin.resolve('anouk', conv, author_id='42', channel_id='9', inbound_id='600000000040')
        checks['latest_message_before_josh_is_answered'] = dict(expected=c['id'], got=(rec or {}).get('id') or why,
                                                                ok=rec is not None and rec['id'] == c['id'])
    return dict(passed=all(x['ok'] for x in checks.values()), checks=checks)


def labelled_link_capability():
    """Evidence from loaded code only; no email save, connector call or external write."""
    from .agent import Agents, EMAIL_PROPS
    from .emails import Emails, FIELDS
    from .kit import body_html, link_spans
    label = "see what's inside"
    url = 'https://example.test/?subscriber={{ subscriber.id }}'
    html = body_html(dict(body="You can see what's inside here.", links=[dict(label=label, href=url)]))
    from html.parser import HTMLParser
    class Anchor(HTMLParser):
        def __init__(self):
            super().__init__(); self.hrefs = []; self.words = []; self.inside = False
        def handle_starttag(self, tag, attrs):
            if tag == 'a': self.inside = True; self.hrefs.append(dict(attrs).get('href'))
        def handle_endtag(self, tag):
            if tag == 'a': self.inside = False
        def handle_data(self, data):
            if self.inside: self.words.append(data)
    parsed = Anchor(); parsed.feed(html)
    checks = dict(save_accepts_links='links' in FIELDS,
                  authoring_links_array=EMAIL_PROPS['emails']['items']['properties'].get('links', {}).get('type') == 'array',
                  renderer_preserves_label_and_liquid=parsed.hrefs == [url] and ''.join(parsed.words) == label)
    functions = {'agent.Agents.apply_emails': Agents.apply_emails, 'emails.Emails.save': Emails.save,
                 'kit.body_html': body_html, 'kit.link_spans': link_spans}
    return dict(version=1, passed=all(checks.values()), checks=checks,
                loaded_function_sha256={name: _code_digest(fn.__code__) for name, fn in functions.items()})


def gmail_writes_switched_on(root):
    """Whether Gmail writes are on in settings.json, read only. Never changed here."""
    try:
        return bool((json.loads((Path(root) / 'settings.json').read_text(encoding='utf-8-sig')).get('gmail_actions') or {}).get('enabled'))
    except (OSError, ValueError):
        return None


def report(root, data, port=None, started_at=None):
    """The evidence record for this process. Writes Data/diagnostics/approval-lineage.json and returns it."""
    body = dict(pid=os.getpid(), parent_pid=os.getppid(), process_started_at=started_at, port=port,
                checked_at=datetime.now(timezone.utc).isoformat(), loaded_function_sha256=fingerprints(),
                self_check=self_check(), labelled_links=labelled_link_capability(), gmail_writes_on=gmail_writes_switched_on(root),
                note='Hashes are of the compiled functions this process loaded. Compare with tests/lineage_fingerprint.py run on the source.')
    out = Path(data) / 'diagnostics'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'approval-lineage.json').write_text(json.dumps(body, indent=1), encoding='utf-8')
    return body
