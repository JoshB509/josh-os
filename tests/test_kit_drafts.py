"""Laia's Kit access: approved drafts only, never a date, schedule, send or publish.

Everything is mocked. No Kit call, no Claude call and no real broadcast is made by these tests.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from studio import kit_draft_guard, kit_drafts
from studio.agent import Agents
from studio.emails import Emails
from studio.kit import Kit, body_html
from studio.store import Store
from studio.team import Team

AUD = {'all': [{'type': 'segment', 'ids': [101]}]}


def confirmed(policy, **changes):
    write = policy['write']
    b = dict(write['input'], id=555, status='draft', send_at=None, published_at=None, public_url=None)
    b.pop('broadcast_id', None)
    b.update(changes)
    return dict(reply='Saved.', _kit_receipts=[
        dict(tool_name=write['tool'], tool_input=write['input'], tool_response={'broadcast': b}),
        dict(tool_name=kit_drafts.GET, tool_input={'broadcast_id': 555}, tool_response={'broadcast': b})])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'Data')
        self.emails = Emails(self.store, self.root)
        self.email = self.emails.create(dict(changes=dict(subject='Test subject', preview='Test preview', body='Line one.\nLine two.')))
        self.drafts = kit_drafts.KitDrafts(self.store, self.root)

    def live(self, on=True):
        (self.root / 'settings.json').write_text(json.dumps({'kit_drafts_live': on}), encoding='utf-8')

    def fresh(self):
        return self.emails.get(self.email['id'])


class NoSchedulingTests(Base):
    """The old route that scheduled Ready emails in Kit is off, even with Kit connected."""

    def test_ready_email_is_never_pushed_or_scheduled(self):
        e = self.emails.save(self.email['id'], dict(status='Ready', planned_date='2099-01-05', send_time='17:00'), self.email['revision'])
        calls = []
        kit = SimpleNamespace(connected=True, note=lambda **k: None, get=lambda kid: {'status': 'draft'},
                              push=lambda *a, **k: calls.append(a) or {}, request=lambda *a, **k: calls.append(a) or {})
        self.emails.kit = kit
        self.emails.kit_cycle()
        self.emails.kit_step(e)
        self.assertEqual(calls, [])
        self.assertEqual(self.fresh()['status'], 'Ready')
        with self.assertRaises(ValueError):
            self.emails.kit_push(self.email['id'])

    def test_api_key_client_refuses_to_write(self):
        k = Kit(self.root / 'Data')
        with patch.object(k, 'request', side_effect=AssertionError('no Kit request may be made')):
            with self.assertRaises(ValueError):
                k.push(self.fresh(), schedule=True)
            with self.assertRaises(ValueError):
                k.push(self.fresh(), schedule=False)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.want = dict(subject='S', preview_text='P', content='<p>C</p>', subscriber_filter=[AUD], public=False, description='Josh OS draft: S')
        self.policy = dict(reads=['Read'], write=dict(tool=kit_drafts.CREATE, input=self.want))

    def call(self, tool=kit_drafts.CREATE, **changes):
        return dict(tool_name=tool, tool_input=dict(self.want, **changes))

    def test_only_the_exact_approved_write_passes(self):
        self.assertTrue(kit_draft_guard.decision(self.call(), self.policy)[0])
        self.assertTrue(kit_draft_guard.decision(self.call(session_id='x', user_goal='draft_broadcast_email'), self.policy)[0])
        self.assertTrue(kit_draft_guard.decision(dict(tool_name='Read', tool_input={}), self.policy)[0])

    def test_dates_publishing_edits_and_other_tools_are_denied(self):
        for bad in (dict(send_at='2099-01-01T10:00:00Z'), dict(published_at='2099-01-01'), dict(public=True), dict(subject='Changed'),
                    dict(subscriber_filter=[{'all': [{'type': 'segment', 'ids': [999]}]}]), dict(content_tree=[{}]), dict(email_template_id=5)):
            self.assertFalse(kit_draft_guard.decision(self.call(**bad), self.policy)[0], bad)
        for tool in (kit_drafts.UPDATE, kit_drafts.KIT + 'delete_broadcast', kit_drafts.KIT + 'tag_subscriber', 'Bash', 'Write', ''):
            self.assertFalse(kit_draft_guard.decision(self.call(tool), self.policy)[0], tool)
        self.assertFalse(kit_draft_guard.decision(self.call(), self.policy, used=True)[0])
        for event, policy in ((None, self.policy), (self.call(), None), ({}, {})):
            self.assertFalse(kit_draft_guard.decision(event, policy)[0])

    def test_hook_allows_one_write_then_denies(self):
        with tempfile.TemporaryDirectory() as tmp:
            pol = Path(tmp) / 'kit-draft.json'
            pol.write_text(json.dumps(self.policy), encoding='utf-8')
            hook = [sys.executable, str(Path(kit_draft_guard.__file__)), str(pol)]
            run = lambda ev: json.loads(subprocess.run(hook, input=json.dumps(ev), capture_output=True, text=True).stdout)['hookSpecificOutput']['permissionDecision']
            self.assertEqual(run(self.call()), 'allow')
            self.assertEqual(run(self.call()), 'deny')
            pol.write_text('{broken', encoding='utf-8')
            self.assertEqual(run(dict(tool_name='Read', tool_input={})), 'deny')

    def test_update_requires_actual_read_of_the_exact_private_draft(self):
        policy = dict(reads=[kit_drafts.GET], write=dict(tool=kit_drafts.UPDATE, input=dict(self.want, broadcast_id=555)))
        event = dict(tool_name=kit_drafts.UPDATE, tool_input=policy['write']['input'])
        self.assertFalse(kit_draft_guard.decision(event, policy)[0])
        for status in ('scheduled', 'completed', 'sending'):
            receipt = dict(tool_name=kit_drafts.GET, tool_input={'broadcast_id': 555}, tool_response={
                'broadcast': dict(id=555, status=status, public=False, send_at=None)})
            self.assertFalse(kit_draft_guard.decision(event, policy, receipts=[receipt])[0])
        receipt['tool_response']['broadcast']['status'] = 'draft'
        self.assertTrue(kit_draft_guard.decision(event, policy, receipts=[receipt])[0])

    def test_even_a_policy_with_a_send_field_is_denied(self):
        policy = dict(self.policy, write=dict(tool=kit_drafts.CREATE, input=dict(self.want, send_at='2099-01-01')))
        self.assertFalse(kit_draft_guard.decision(self.call(send_at='2099-01-01'), policy)[0])

    def test_reference_resolves_to_exact_payload_and_preserves_raw_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            pol = Path(tmp) / 'policy.json'
            audit = Path(tmp) / 'audit.jsonl'
            reference = {'subject': 'opaque-random-run-reference'}
            want = dict(self.want, content='<p>First</p><p>\u200b</p><p>https://example.test/?id={{ subscriber.id }}</p>')
            policy = dict(self.policy, kind='kit_draft', reference_input=reference, audit_path=str(audit), run_id='test-run',
                          write=dict(tool=kit_drafts.CREATE, input=want))
            pol.write_text(json.dumps(policy), encoding='utf-8')
            def run(given):
                ev = dict(tool_name=kit_drafts.CREATE, tool_input=given, hook_event_name='PreToolUse')
                out = subprocess.run([sys.executable, kit_draft_guard.__file__, str(pol)], input=json.dumps(ev),
                                     capture_output=True, text=True)
                return json.loads(out.stdout)['hookSpecificOutput']
            denied = run(dict(want, content='wrong content'))
            self.assertEqual(denied['permissionDecision'], 'deny')
            self.assertFalse(pol.with_suffix('.used').exists())
            record = json.loads(audit.read_text(encoding='utf-8').splitlines()[0])
            self.assertFalse(record['allow'])
            self.assertFalse(record['reserved'])
            self.assertEqual(record['received_input']['content'], 'wrong content')
            allowed = run(reference)
            self.assertEqual(allowed['permissionDecision'], 'allow')
            self.assertEqual(allowed['updatedInput'], want)
            self.assertTrue(pol.with_suffix('.used').exists())
            self.assertEqual(run(reference)['permissionDecision'], 'deny')
            records = [json.loads(line) for line in audit.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(records[1]['received_input'], reference)
            self.assertEqual(records[1]['tool_input'], want)

    def test_hook_decodes_real_utf8_input_and_receipts_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            pol = Path(tmp) / 'policy.json'
            want = dict(self.want, content='<p>' + chr(0x200b) + '</p>')
            policy = dict(self.policy, reads=[kit_drafts.GET], write=dict(tool=kit_drafts.CREATE, input=want))
            pol.write_text(json.dumps(policy), encoding='utf-8')
            event = dict(hook_event_name='PreToolUse', tool_name=kit_drafts.CREATE, tool_input=want)
            out = subprocess.run([sys.executable, kit_draft_guard.__file__, str(pol)],
                                 input=json.dumps(event, ensure_ascii=False).encode('utf-8'), capture_output=True)
            self.assertEqual(json.loads(out.stdout)['hookSpecificOutput']['permissionDecision'], 'allow')
            event = dict(hook_event_name='PostToolUse', tool_name=kit_drafts.GET, tool_input={'id': 555},
                         tool_response={'broadcast': dict(want, id=555, status='draft', send_at=None)})
            subprocess.run([sys.executable, kit_draft_guard.__file__, str(pol)],
                           input=json.dumps(event, ensure_ascii=False).encode('utf-8'), capture_output=True, check=True)
            receipts = json.loads(pol.with_suffix('.receipts.json').read_text(encoding='utf-8'))
            self.assertEqual(kit_draft_guard.broadcast(receipts[0]['tool_response'])['content'], want['content'])
            self.assertEqual(kit_draft_guard.read_id(receipts[0]['tool_input']), 555)

    def test_unknown_or_modified_reference_cannot_change_approved_payload(self):
        policy = dict(self.policy, kind='kit_draft', reference_input={'subject': 'reference'})
        for given in ({'subject': 'wrong-reference'}, {'subject': 'reference', 'send_at': '2099-01-01'}):
            event, updated = kit_draft_guard.expand_reference(dict(tool_name=kit_drafts.CREATE, tool_input=given), policy)
            self.assertIsNone(updated)
            self.assertFalse(kit_draft_guard.decision(event, policy)[0])


class WorkflowTests(Base):
    def test_liquid_url_and_html_escaping(self):
        from html.parser import HTMLParser
        class Links(HTMLParser):
            def __init__(self):
                super().__init__(); self.hrefs = []
            def handle_starttag(self, tag, attrs):
                if tag == 'a': self.hrefs.append(dict(attrs)['href'])
        url = 'https://linklr.app/r/fxxvmuga?ksid={{ subscriber.id }}&x=1'
        rendered = body_html({'body': '<script>alert("x")</script> ' + url + ' end https://example.com/?a=1&b=2'})
        parser = Links(); parser.feed(rendered)
        self.assertEqual(parser.hrefs, [url, 'https://example.com/?a=1&b=2'])
        self.assertNotIn('<script>', rendered)
        self.assertIn('&amp;x=1', rendered)
        self.assertNotIn('&amp;amp;', rendered)

    def test_audience_rejects_silent_truncation_and_wrong_container(self):
        for rule in ({'all': [{}] * 21}, {'all': 'segment'}, {'all': {'type': 'segment', 'ids': [1]}}):
            with self.assertRaises(ValueError): kit_drafts.audience(rule)

    def test_cross_email_and_tampered_payload_cannot_be_approved(self):
        d = self.drafts.propose(self.fresh(), AUD)
        with self.assertRaises(ValueError):
            self.drafts.approve(d['id'], d['hash'], dict(self.fresh(), id='other-email'))
        d = self.drafts.propose(self.fresh(), AUD)
        d['payload']['subscriber_filter'] = [{'all': [{'type': 'segment', 'ids': [999]}]}]
        self.drafts._save(d)
        with self.assertRaises(ValueError): self.drafts.approve(d['id'], d['hash'], self.fresh())

    def test_explicit_single_draft_request_binds_origin_and_action_without_live_switch(self):
        d = self.drafts.propose(self.fresh(), AUD)
        approved = self.drafts.approve(d['id'], d['hash'], self.fresh(), origin={
            'channel': 'chatgpt', 'message_id': 'exact-message', 'actor_id': 'authenticated-josh'})
        request = approved['authorization']
        for changed in (dict(request, action='send'), dict(request, hash='other'),
                        dict(request, origin=dict(request['origin'], channel='discord'))):
            with self.assertRaises(ValueError):
                self.drafts.write(d['id'], self.fresh(), lambda *a, **k: self.fail('no run'), request=changed)
        def run(*a, **k): return 'ok', confirmed(k['guard'])
        out = self.drafts.write(d['id'], self.fresh(), run, request=request)
        self.assertEqual(out['status'], 'drafted')
        self.assertFalse(self.drafts.live())
        self.assertNotIn('send_at', out['payload'])
        with self.assertRaises(ValueError): self.drafts.write(d['id'], self.fresh(), run, request=request)

    def test_prompt_uses_reference_without_transcribing_approved_payload(self):
        self.live()
        e = self.fresh()
        self.emails.save(e['id'], dict(body='First paragraph.\n\nSecond paragraph.'), e['revision'])
        d = self.drafts.propose(self.fresh(), AUD)
        self.drafts.approve(d['id'], d['hash'], self.fresh())
        def run(system, *a, **k):
            self.assertNotIn(chr(0x200b), system)
            self.assertNotIn('First paragraph.', system)
            self.assertIn(k['guard']['reference_input']['subject'], system)
            self.assertIn(chr(0x200b), k['guard']['write']['input']['content'])
            return 'ok', confirmed(k['guard'])
        self.assertEqual(self.drafts.write(d['id'], self.fresh(), run)['status'], 'drafted')

    def test_model_success_without_receipts_is_uncertain_and_blocks_new_proposals(self):
        self.live()
        d = self.drafts.propose(self.fresh(), AUD)
        self.drafts.approve(d['id'], d['hash'], self.fresh())
        with self.assertRaises(ValueError):
            self.drafts.write(d['id'], self.fresh(), lambda *a, **k: ('ok', {
                'reply': 'Success!', 'broadcast_id': 555, 'confirm_url': 'https://app.kit.com/campaigns/555/draft'}))
        self.assertEqual(self.drafts.get(d['id'])['status'], 'uncertain')
        with self.assertRaises(ValueError): self.drafts.propose(self.fresh(), AUD)

    def test_readback_must_match_content_audience_and_unsent_state(self):
        for changes in ({'status': 'scheduled'}, {'send_at': '2099-01-01T10:00:00Z'}, {'public': True},
                        {'published_at': '2099-01-01'}, {'content': 'changed'}, {'subject': 'changed'},
                        {'subscriber_filter': []}):
            with self.subTest(changes=changes):
                self.setUp(); self.live()
                d = self.drafts.propose(self.fresh(), AUD)
                self.drafts.approve(d['id'], d['hash'], self.fresh())
                with self.assertRaises(ValueError):
                    self.drafts.write(d['id'], self.fresh(), lambda *a, **k: ('ok', confirmed(k['guard'], **changes)))
                self.assertEqual(self.drafts.get(d['id'])['status'], 'uncertain')

    def test_payload_is_built_from_the_saved_email_without_a_date(self):
        d = self.drafts.propose(self.fresh(), AUD)
        p = d['payload']
        self.assertEqual((p['subject'], p['preview_text'], p['public'], p['subscriber_filter']), ('Test subject', 'Test preview', False, [AUD]))
        self.assertEqual(set(p), {'subject', 'preview_text', 'content', 'subscriber_filter', 'public', 'description'})
        self.assertIn('<p>Line one.</p>', p['content'])
        for bad in ({}, {'all': [], 'any': []}, {'all': [{'type': 'list', 'ids': [1]}]}, {'all': [{'type': 'tag', 'ids': ['1']}]},
                    {'all': [{'type': 'tag', 'ids': [1]}], 'none': [{'type': 'tag', 'ids': [2]}]}, {'everyone': True}):
            with self.assertRaises(ValueError, msg=bad):
                self.drafts.propose(self.fresh(), bad)

    def test_approval_is_for_the_exact_version_and_edits_make_it_stale(self):
        d = self.drafts.propose(self.fresh(), AUD)
        with self.assertRaises(ValueError):
            self.drafts.approve(d['id'], 'wrong-hash', self.fresh())
        self.assertEqual(self.drafts.approve(d['id'], d['hash'], self.fresh())['status'], 'approved')
        e = self.fresh()
        self.emails.save(e['id'], dict(subject='Edited subject'), e['revision'])
        self.live()
        ran = []
        with self.assertRaises(ValueError) as cm:
            self.drafts.write(d['id'], self.fresh(), lambda *a, **k: ran.append(k))
        self.assertIn('approving again', str(cm.exception))
        self.assertEqual((ran, self.drafts.get(d['id'])['status']), ([], 'stale'))

    def test_nothing_is_written_until_josh_switches_it_on(self):
        d = self.drafts.propose(self.fresh(), AUD)
        self.drafts.approve(d['id'], d['hash'], self.fresh())
        ran = []
        with self.assertRaises(ValueError) as cm:
            self.drafts.write(d['id'], self.fresh(), lambda *a, **k: ran.append(k))
        self.assertIn("isn't switched on", str(cm.exception))
        self.assertEqual(ran, [])
        self.assertEqual(self.drafts.get(d['id'])['status'], 'approved')

    def test_unapproved_drafts_are_never_written(self):
        self.live()
        d = self.drafts.propose(self.fresh(), AUD)
        with self.assertRaises(ValueError):
            self.drafts.write(d['id'], self.fresh(), lambda *a, **k: self.fail('no run'))

    def test_live_write_uses_one_guarded_create_then_updates_only_its_own_draft(self):
        self.live()
        d = self.drafts.propose(self.fresh(), AUD)
        self.drafts.approve(d['id'], d['hash'], self.fresh())
        seen = []

        def run(system, prompt, schema, **k):
            seen.append(dict(k, system=system))
            return 'ok', confirmed(k['guard'])
        out = self.drafts.write(d['id'], self.fresh(), run)
        self.assertEqual((out['status'], out['broadcast_id']), ('drafted', 555))
        g = seen[0]['guard']
        self.assertEqual(g['write']['tool'], kit_drafts.CREATE)
        self.assertEqual(g['write']['input'], d['payload'])
        self.assertEqual(seen[0]['extra_tools'], (kit_drafts.GET, kit_drafts.CREATE))
        # A second approved version for the same email updates the draft Josh OS made, after checking it is still a draft.
        e = self.fresh()
        self.emails.save(e['id'], dict(subject='New subject'), e['revision'])
        d2 = self.drafts.propose(self.fresh(), AUD)
        self.assertEqual(d2['broadcast_id'], 555)
        self.drafts.approve(d2['id'], d2['hash'], self.fresh())
        self.drafts.write(d2['id'], self.fresh(), run)
        g = seen[1]['guard']
        self.assertEqual((g['write']['tool'], g['write']['input']['broadcast_id'], g['reads']), (kit_drafts.UPDATE, 555, [kit_drafts.GET]))
        self.assertIn('If its status is not "draft", do not update it', seen[1]['system'])
        self.assertIn('get_broadcast', seen[0]['system'])

    def test_unclear_outcome_is_never_retried(self):
        self.live()
        d = self.drafts.propose(self.fresh(), AUD)
        self.drafts.approve(d['id'], d['hash'], self.fresh())

        def boom(*a, **k):
            raise TimeoutError('no answer')
        with self.assertRaises(ValueError):
            self.drafts.write(d['id'], self.fresh(), boom)
        self.assertEqual(self.drafts.get(d['id'])['status'], 'uncertain')
        with self.assertRaises(ValueError):
            self.drafts.write(d['id'], self.fresh(), lambda *a, **k: self.fail('no retry'))

    def test_scheduled_or_sent_emails_are_left_alone(self):
        e = self.fresh()
        for kit_status in ('scheduled', 'sending', 'completed'):
            with self.assertRaises(ValueError):
                self.drafts.propose(dict(e, kit=dict(id=1, url='https://app.kit.com/x', status=kit_status)), AUD)
        with self.assertRaises(ValueError):
            self.drafts.propose(dict(e, status='Sent'), AUD)


class RunAndChatTests(Base):
    def test_proposal_failure_replaces_false_success_prose(self):
        agents = Agents(self.store, self.root, Team(self.store))
        agents.emails, agents.kit_drafts = self.emails, self.drafts
        agents.run = lambda *a, **k: ('I created it in Kit; approve and it sends in a minute.', {
            'kit_draft': {'email_id': self.email['id'], 'audience': {'all': [], 'none': []}}})
        r = agents.chat(dict(agent_id='laia', message='Prepare the draft'), [])
        self.assertIn("couldn't prepare", r['reply']['text'])
        self.assertNotIn('I created', r['reply']['text'])
        self.assertNotIn('minute', r['reply']['text'])

    def capture(self, **kw):
        agents = Agents(self.store, self.root, Team(self.store))
        got = {}

        def fake(cmd, **k):
            got['cmd'] = cmd
            i = cmd.index('--settings') if '--settings' in cmd else None
            if i is not None:
                hook = json.loads(cmd[i + 1])['hooks']['PreToolUse'][0]['hooks'][0]['command']
                got['policy'] = json.loads(Path(hook.rsplit(' ', 1)[1].strip('"')).read_text(encoding='utf-8'))
            return SimpleNamespace(stdout=json.dumps({'structured_output': {'reply': 'ok'}}), stderr='', returncode=0)
        with patch('studio.agent.find_claude', return_value='fake-claude.exe'), patch('studio.agent.subprocess.run', side_effect=fake):
            agents.run('system', 'prompt', **kw)
        return got

    def test_chats_can_never_use_kit_write_tools(self):
        cmd = self.capture(extra_tools=list(kit_drafts.KIT_WRITES[:3]))['cmd']
        denied = cmd[cmd.index('--disallowedTools') + 1].split(',')
        for t in (kit_drafts.CREATE, kit_drafts.UPDATE, kit_drafts.KIT + 'delete_broadcast', kit_drafts.KIT + 'unsubscribe'):
            self.assertIn(t, denied)

    def test_runner_persists_authoritative_reservation_state(self):
        policy = dict(kind='kit_draft', audit_id='00000000-0000-0000-0000-000000000001', reads=[],
                      write=dict(tool=kit_drafts.CREATE, input={'subject': 'S', 'public': False}))
        got = self.capture(extra_tools=[kit_drafts.CREATE], guard=policy, full=True)
        records = [json.loads(line) for line in Path(got['policy']['audit_path']).read_text(encoding='utf-8').splitlines()]
        self.assertEqual([r['event'] for r in records], ['RunStarted', 'RunCompleted'])
        self.assertFalse(records[-1]['reserved'])
        self.assertEqual(records[0]['run_id'], records[-1]['run_id'])

    def test_guarded_run_allows_only_its_one_write(self):
        policy = dict(reads=[], write=dict(tool=kit_drafts.CREATE, input={'subject': 'S', 'public': False}))
        got = self.capture(extra_tools=[kit_drafts.CREATE], guard=policy, full=True)
        cmd = got['cmd']
        denied = cmd[cmd.index('--disallowedTools') + 1].split(',')
        self.assertNotIn(kit_drafts.CREATE, denied)
        self.assertIn(kit_drafts.UPDATE, denied)
        self.assertEqual(cmd[cmd.index('--permission-mode') + 1], 'dontAsk')
        self.assertIn('kit_draft_guard.py', json.dumps(json.loads(cmd[cmd.index('--settings') + 1])))
        self.assertEqual(got['policy']['write'], policy['write'])

    def test_laia_proposes_and_others_cannot(self):
        agents = Agents(self.store, self.root, Team(self.store))
        agents.emails, agents.kit_drafts = self.emails, self.drafts
        seen = {}

        def run(system, prompt, schema, *a, **k):
            seen['schema'] = schema
            return 'ok', {'reply': 'Prepared.', 'kit_draft': {'email_id': self.email['id'], 'audience': AUD}}
        agents.run = run
        r = agents.chat(dict(agent_id='laia', message='Prepare the Kit draft for the segment 101'), [])
        self.assertIn('kit_draft', seen['schema']['properties'])
        self.assertIn('ready for your approval', r['reply']['text'])
        self.assertEqual([d['status'] for d in self.drafts.for_email(self.email['id'])], ['waiting'])
        agents.chat(dict(agent_id='sam', message='Prepare the Kit draft'), [])
        self.assertNotIn('kit_draft', seen['schema']['properties'])
        self.assertEqual(len(self.drafts.for_email(self.email['id'])), 1)


if __name__ == '__main__':
    unittest.main()
