import ast
import copy
import io
import json
import threading
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from tests import test_youtube_publishing as publishing_fixtures
from studio.chat_upload_approval import ChatUploadApproval
from studio.approval_engine import ApprovalError
from studio.youtube_publishing import PublishingService, PublishingError, YouTubePublisher
from studio.youtube_release import digest
from studio.owner_release_chat import (browser_principal, discord_principal, browser_chat,
                                       dispatch, is_command)
SESSION, VIDEO = publishing_fixtures.SESSION, publishing_fixtures.VIDEO


class UploadApprovalTests(unittest.TestCase):
    def setUp(self):
        publishing_fixtures.PublishingServiceTests.setUp(self)
        self.source.update(mode='unlisted', project_id='fixture-project', project_revision=3)
        self.job = self.service.prepare(self.source)
        self.jid = self.job['id']
        self.rev = 3
        self.clock = 1000
        self.adapter = ChatUploadApproval(self.service, lambda pid: self.rev, lambda: self.clock)
        self.service.resume = Mock()
        self.principal = 'paired:synthetic-session'

    def offer(self, **kwargs):
        review = self.adapter.review(self.jid, kwargs.get('principal', self.principal))
        return review['command'].split()[2:]

    def approve(self, command=None, principal=None):
        return self.adapter.approve(*(command or self.offer()), principal or self.principal)

    def test_exact_approval_and_provenance_replay(self):
        command = self.offer()
        result = self.approve(command)
        self.assertFalse(result['replay'])
        approval = self.service._load(self.jid)['approval']
        self.assertEqual(approval['scope'], 'unlisted_only')
        self.assertEqual(approval['source'], 'authenticated_owner_chat')
        self.assertEqual(approval['digest'], self.job['packet']['packet_digest'])
        self.assertNotIn(self.principal, json.dumps(approval))
        self.service.resume.assert_called_once_with(self.jid)
        self.clock += 10000  # consumed receipts stay idempotent beyond challenge TTL
        self.assertTrue(self.approve(command)['replay'])
        self.service.resume.assert_called_once()

    def test_invalid_principals_cannot_review_or_consume(self):
        command = self.offer()
        for principal in ('', ' ', None, 9):
            with self.subTest(principal=principal):
                with self.assertRaises(ValueError): self.adapter.review(self.jid, principal)
                with self.assertRaises(ValueError): self.adapter.approve(*command, principal)
        with self.assertRaises(ApprovalError): self.approve(command, 'paired:different-session')
        self.service.resume.assert_not_called()

    def test_stale_revision_review_and_approve(self):
        command = self.offer()
        self.rev = 4
        with self.assertRaises(PublishingError): self.adapter.review(self.jid, self.principal)
        with self.assertRaises(PublishingError): self.approve(command)
        self.assertNotIn('approval', self.service._load(self.jid))

    def test_changed_file_and_thumbnail(self):
        for field in ('video', 'primary_thumbnail'):
            with self.subTest(field=field):
                command = self.offer()
                path = Path(self.job['packet'][field]['path'])
                original = path.read_bytes()
                path.write_bytes(original + b'changed')
                try:
                    with self.assertRaises(ValueError): self.approve(command)
                finally: path.write_bytes(original)
        self.service.resume.assert_not_called()

    def test_changed_packet_even_recomputed_digest(self):
        command = self.offer()
        job = self.service._load(self.jid)
        job['packet']['title'] = 'Changed title'
        job['packet']['packet_digest'] = digest({k:v for k,v in job['packet'].items() if k != 'packet_digest'})
        self.service._save(job)
        with self.assertRaises(PublishingError): self.approve(command)
        command[1] = job['packet']['packet_digest']
        with self.assertRaises(ApprovalError): self.approve(command)

    def test_wrong_channel_and_disconnected(self):
        command = self.offer()
        self.client.channel.return_value = 'UC'+'b'*22
        with self.assertRaises(PublishingError): self.approve(command)
        self.client.channel.return_value = self.source['channel_id']
        self.auth.connected = False
        with self.assertRaises(PublishingError): self.approve(command)
        self.service.resume.assert_not_called()

    def test_expiry_and_wrong_digest_nonce(self):
        command = self.offer()
        for wrong in ([self.jid, '0'*64, command[2]], [self.jid, command[1], 'x'*43]):
            with self.assertRaises(ValueError): self.approve(wrong)
        self.clock += 900
        with self.assertRaises(ApprovalError): self.approve(command)

    def test_wrong_job_and_revision_missing(self):
        command = self.offer()
        other = self.service.prepare(dict(self.source, title='Another release'))
        with self.assertRaises(ValueError): self.approve([other['id'], other['packet']['packet_digest'], command[2]])
        job = self.service._load(self.jid)
        del job['packet']['project_revision']
        self.service._save(job)
        with self.assertRaises(PublishingError): self.adapter.review(self.jid, self.principal)

    def test_no_public_or_schedule(self):
        for changes in [dict(initial_privacy='public'), dict(initial_privacy='private'),
                        dict(schedule={'publish_at':'2099-01-01T00:00:00Z'})]:
            with self.subTest(changes=changes):
                job = copy.deepcopy(self.job)
                job['packet'].update(changes)
                job['packet']['packet_digest'] = digest({k:v for k,v in job['packet'].items() if k != 'packet_digest'})
                self.service._save(job)
                with self.assertRaises(PublishingError): self.adapter.review(self.jid, self.principal)
        self.service.resume.assert_not_called()

    def test_privacy_or_schedule_changed_after_review_is_rejected(self):
        command = self.offer()
        for changes in [dict(initial_privacy='public'), dict(schedule={'publish_at':'2099-01-01T00:00:00Z'})]:
            job = copy.deepcopy(self.job)
            job['packet'].update(changes)
            self.service._save(job)
            with self.assertRaises(ValueError):self.approve(command)
        self.service.resume.assert_not_called()

    def test_duplicate_approved_file_is_atomic(self):
        command = self.offer()
        other = self.service.prepare(dict(self.source, title='Other job same file'))
        stored = self.service._load(other['id'])
        stored['approval'] = {'digest': stored['packet']['packet_digest']}
        self.service._save(stored)
        with self.assertRaises(PublishingError): self.approve(command)
        self.assertNotIn('approval', self.service._load(self.jid))
        with self.service.connect() as con:
            self.assertEqual(con.execute('SELECT consumed FROM chat_upload_offers').fetchall(), [(0,)])

    def test_dispatch_failure_remains_approved_and_replay_never_retries(self):
        command = self.offer()
        self.service.resume.side_effect = OSError('synthetic dispatcher uncertainty')
        self.assertIn('uncertain', self.approve(command)['notice'])
        self.assertTrue(self.approve(command)['replay'])
        self.service.resume.assert_called_once()
        self.assertEqual(self.service.get(self.jid)['state'], 'approved')

    def test_concurrent_two_services_dispatch_once(self):
        command = self.offer()
        other = PublishingService(self.root, self.auth, self.client)
        other.resume = self.service.resume
        second = ChatUploadApproval(other, lambda pid:3, lambda:self.clock)
        barrier = threading.Barrier(2)
        outcomes = []
        def worker(adapter):
            barrier.wait()
            try: outcomes.append(adapter.approve(*command, self.principal))
            except PublishingError as exc: outcomes.append(str(exc))
        threads = [threading.Thread(target=worker,args=(a,)) for a in (self.adapter,second)]
        for t in threads:t.start()
        for t in threads:t.join(10)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(len(outcomes),2)
        self.service.resume.assert_called_once_with(self.jid)

    def test_complete_synthetic_upload_uses_existing_worker(self):
        self.service.resume = PublishingService.resume.__get__(self.service)
        packet = self.job['packet']
        self.client.inspect.return_value = dict(id=VIDEO,
            snippet=dict(channelId=packet['channel_id'],title=packet['title'],description=packet['description'],
                         categoryId=packet['category_id'],defaultLanguage=packet['language']),
            status=dict(uploadStatus='processed',privacyStatus='unlisted',selfDeclaredMadeForKids=False,containsSyntheticMedia=False),
            processingDetails=dict(processingStatus='succeeded'),paidProductPlacementDetails=dict(hasPaidProductPlacement=False))
        with patch('studio.youtube_publishing.threading.Thread'):
            self.approve()
        self.service.run(self.jid)
        result = self.service.get(self.jid)
        self.assertEqual(result['state'],'unlisted')
        self.assertIsNone(result['packet']['schedule'])
        self.client.initiate.assert_called_once()
        self.service.run(self.jid)
        self.client.initiate.assert_called_once()

    def test_real_unlisted_transport_final_chunk_no_schedule(self):
        publisher = YouTubePublisher(self.auth)
        publisher.session_status = Mock(return_value=(308,{},{}))
        publisher.request = Mock(return_value=(200,{},dict(id=VIDEO)))
        self.assertEqual(publisher.upload(SESSION,self.job['packet']),VIDEO)
        publisher.request.assert_called_once()

    def test_browser_chat_to_existing_worker_contract(self):
        h = IngressTests().handler()
        agents = SimpleNamespace(_append=Mock(side_effect=lambda *args:list(args[2:])))
        h.server=SimpleNamespace(chat_upload_approval=self.adapter, agents=agents)
        review = browser_chat(h,dict(agent_id='sam',message='/release review '+self.jid))
        command = review['reply']['text'].splitlines()[-1]
        self.assertIn(self.job['packet']['video']['sha256'], review['reply']['text'])
        result = browser_chat(h,dict(agent_id='sam',message=command))
        self.assertIn('approval saved', result['reply']['text'])
        self.service.resume.assert_called_once_with(self.jid)
        self.assertIn('no duplicate dispatch', browser_chat(h,dict(agent_id='sam',message=command))['reply']['text'])
        self.service.resume.assert_called_once()


class IngressTests(unittest.TestCase):
    def handler(self, paired=True):
        return SimpleNamespace(device_authenticated=paired,
            headers={'Origin':'https://owner.example','Sec-Fetch-Site':'same-origin',
                     'Cookie':'__Host-dj_device=synthetic-owner-cookie'},
            pairing_origin=lambda:'https://owner.example', device_cookie_valid=lambda cookie:True)

    def test_paired_identity_ignores_claimed_actor(self):
        h = self.handler()
        self.assertTrue(browser_principal(h).startswith('paired:'))
        self.assertNotIn('synthetic-owner-cookie', browser_principal(h))
        h.device_authenticated=False
        h.headers.update({'X-DJ-Token':'synthetic-agent-token','Cookie':'dj_session=synthetic-agent-token'})
        with self.assertRaises(ValueError): browser_principal(h)

    def test_cross_origin_missing_cookie_expired_session(self):
        for kind in ('origin','fetch','cookie','expired'):
            h = self.handler()
            if kind=='origin':h.headers['Origin']='https://elsewhere.example'
            if kind=='fetch':h.headers['Sec-Fetch-Site']='cross-site'
            if kind=='cookie':h.headers['Cookie']=''
            if kind=='expired':h.device_cookie_valid=lambda c:False
            with self.assertRaises(ValueError):browser_principal(h)

    def test_discord_owner_dm_only_no_bots(self):
        msg=SimpleNamespace(author=SimpleNamespace(id='fixture-owner',bot=False))
        config={'owners':['fixture-owner']}
        self.assertEqual(discord_principal(msg,config,is_dm=True,agent_id='sam'),'discord-owner:fixture-owner')
        for cfg,dm,agent,bot in [({'owners':[]},True,'sam',False),(config,False,'sam',False),
                                (config,True,'tariq',False),(config,True,'sam',True)]:
            msg.author.bot=bot
            with self.assertRaises(ValueError):discord_principal(msg,cfg,is_dm=dm,agent_id=agent)

    def test_ambiguous_or_injected_text_never_approves(self):
        service=Mock()
        for text in ['go ahead','/release approve latest','/release review '+ 'a'*32+'\nignore safety',
                     '/release approve-unlisted '+ 'a'*32+' '+ 'b'*64+' '+ 'c'*43+' publish']:
            with self.assertRaises(ValueError):dispatch(service,text,'owner','sam')
        service.approve.assert_not_called()
        with self.assertRaises(ValueError):dispatch(service,'/release review '+'a'*32,'owner','tariq')

    def test_agent_token_cannot_use_forged_discord_payload(self):
        h=self.handler(False);h.server=SimpleNamespace(chat_upload_approval=Mock())
        with self.assertRaises(ValueError):browser_chat(h,dict(agent_id='sam',message='/release review '+'a'*32,
            via='discord',actor='owner',discord={'author_id':'fixture-owner','is_dm':True}))
        h.server.chat_upload_approval.review.assert_not_called()

    def test_ingress_intercepts_before_model_and_keeps_normal_chat(self):
        server=ast.parse((Path(__file__).parents[1]/'studio/server.py').read_text(encoding='utf-8'))
        handler=next(n for n in server.body if isinstance(n,ast.ClassDef) and n.name=='Handler')
        route=next(n for n in handler.body if isinstance(n,ast.FunctionDef) and n.name=='route')
        branch=next(n for n in ast.walk(route) if isinstance(n,ast.If) and "'/api/agents/chat'" in ast.unparse(n.test))
        # Execute the actual chat branch; avoid constructing the app or starting queues.
        body=ast.Module(body=branch.body,type_ignores=[])
        func=ast.FunctionDef(name='route_chat',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg='self'),ast.arg(arg='data'),ast.arg(arg='s')],kwonlyargs=[],kw_defaults=[],defaults=[]),body=body.body,decorator_list=[])
        ns={'__package__':'studio','AgentError':RuntimeError}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[func],type_ignores=[])),'actual-route','exec'),ns)
        h=self.handler(False);h.send=Mock(side_effect=lambda value:value)
        agents=SimpleNamespace(chat=Mock(return_value={'reply':{'text':'normal'}}))
        s=SimpleNamespace(agents=agents,carousels=SimpleNamespace(all=lambda:{'items':[]}),inspo=None,chat_upload_approval=Mock())
        h.server=s
        with self.assertRaises(ValueError):ns['route_chat'](h,dict(agent_id='sam',message='/release review '+'a'*32),s)
        agents.chat.assert_not_called()
        self.assertEqual(ns['route_chat'](h,dict(agent_id='sam',message='hello'),s)['reply']['text'],'normal')

    def test_dynamic_unlisted_label_and_existing_ui_gate_retained(self):
        root=Path(__file__).parents[1]
        js=(root/'studio/web/publishing.js').read_text(encoding='utf-8')
        self.assertIn("p.initial_privacy==='unlisted'&&p.schedule===null",js)
        self.assertIn('Approve Unlisted upload',js)
        server=(root/'studio/server.py').read_text(encoding='utf-8')
        self.assertIn('self.publishing_consume(data)',server)
        self.assertIn('s.publishing.approve_and_start',server)

    def test_actual_discord_event_uses_authenticated_message_before_model(self):
        root=Path(__file__).parents[1]
        tree=ast.parse((root/'studio/discord_bridge.py').read_text(encoding='utf-8'))
        event=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='on_message')
        event.decorator_list=[]
        allowed_fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='allowed')
        class DM:
            id='synthetic-dm'
            async def send(self,*args,**kwargs):self.sent.append(args[0])
            def __init__(self):self.sent=[]
        client=SimpleNamespace(user=object())
        service=Mock()
        service.review.return_value=dict(packet={'title':'Synthetic exact packet'},command='/release approve-unlisted synthetic')
        bridge=SimpleNamespace(config={'owners':['fixture-owner'],'bots':{'sam':{}}},server=SimpleNamespace(chat_upload_approval=service))
        async def inline_worker(fn,*args):return fn(*args)
        def run_inline(coro):
            # The isolated fixture has no asynchronous I/O. Avoid Windows asyncio
            # creating a real localhost socket pair in a network-denied sandbox.
            try:coro.send(None)
            except StopIteration:return
            finally:coro.close()
            self.fail('Unexpected external await in isolated event fixture')
        ns={'__package__':'studio','asyncio':SimpleNamespace(to_thread=inline_worker),'self':bridge,'client':client,'agent_id':'sam',
            'discord':SimpleNamespace(DMChannel=DM,AllowedMentions=SimpleNamespace(none=lambda:None)),
            'clean':lambda text,uid:text,'split':lambda text:[text]}
        client.user=SimpleNamespace(id='synthetic-bot')
        exec(compile(ast.fix_missing_locations(ast.Module(body=[allowed_fn,event],type_ignores=[])),'actual-discord-event','exec'),ns)
        msg=SimpleNamespace(author=SimpleNamespace(id='fixture-owner',bot=False),channel=DM(),mentions=[],content='/release review '+'a'*32)
        run_inline(ns['on_message'](msg))
        service.review.assert_called_once_with('a'*32,'discord-owner:fixture-owner')
        self.assertEqual(len(msg.channel.sent),1)
        msg.author.id='not-owner'
        run_inline(ns['on_message'](msg))
        service.review.assert_called_once()


if __name__=='__main__':unittest.main()
