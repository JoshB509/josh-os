"""No external calls: local SQLite status fixtures, fake dispatch and route handlers."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import urllib.parse
from unittest.mock import Mock, patch

from studio.frameio_progress import present_review, safe_view_url, send_from_chat
from studio.frameio_review import FrameioReview
from studio.store import Store

NOW = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def review(**changes):
    item = dict(id='review-1', project_id='project-1', state='putting', updated_at=NOW.isoformat(),
        created_at=NOW.isoformat(), reason='Sent 1 of 4 parts (25%).',
        export=dict(path='E:/private/export.mp4', name='private-name.mp4', size=400, duration=60),
        progress=dict(parts=4, parts_done=1, bytes_sent=100, bytes_total=400, at=NOW.isoformat(),
                      presigned_url='https://storage.invalid/?secret=token', plan_from='private log'),
        frameio=dict(file_id='file-1', view_url='https://next.frame.io/review/file-1'),
        history=[dict(raw='secret')], destination=dict(token='never emit'))
    item.update(changes)
    return item


class PresentationTests(unittest.TestCase):
    def show(self, item):
        return present_review(item, at=NOW)

    def test_local_state_mapping_and_verified_completion(self):
        for raw, expected in [('staged', 'queued'), ('checked_absent', 'queued'), ('uploading', 'uploading'),
                              ('putting', 'uploading'), ('processing', 'processing'), ('uncertain', 'blocked'),
                              ('blocked', 'blocked'), ('unknown', 'blocked'), ('ready', 'blocked')]:
            with self.subTest(raw=raw):
                result = self.show(review(state=raw, reason=''))
                self.assertEqual(result['state'], expected)
                self.assertEqual(result['terminal'], expected == 'blocked')
                self.assertNotIn('view_url', result)
        item = review(state='ready', frameio=dict(file_id='file-1', upload_complete=True, playable=True,
            file_size=400, duration=60, view_url='https://next.frame.io/review/file-1'))
        self.assertEqual(self.show(item)['state'], 'complete')
        self.assertEqual(self.show(item)['view_url'], item['frameio']['view_url'])
        for key, bad in [('upload_complete', False), ('playable', 1), ('file_size', 399), ('duration', 100), ('file_id', '')]:
            wrong = deepcopy(item); wrong['frameio'][key] = bad
            self.assertEqual(self.show(wrong)['state'], 'blocked')
            self.assertNotIn('view_url', self.show(wrong))

    def test_staged_and_stopped_part_uploads_are_blocked(self):
        self.assertEqual(self.show(review(state='staged', reason='Connection not checked'))['state'], 'blocked')
        for reason in ['The export file changed or is missing', 'Part 2 could not be read in full',
                       'Part 2 of 4 did not go through. Resume sends the rest to the same URLs.']:
            self.assertEqual(self.show(review(reason=reason))['state'], 'blocked')

    def test_missing_old_or_future_timestamps_are_stalled_never_complete(self):
        for stamp in [None, 'nonsense', (NOW-timedelta(minutes=6)).isoformat(), (NOW+timedelta(hours=1)).isoformat()]:
            item = review(updated_at=stamp, created_at=stamp, progress={})
            result = self.show(item)
            self.assertEqual(result['state'], 'stalled')
            self.assertFalse(result['terminal'])
        item = review(progress=dict(parts=4, parts_done=1, at=(NOW-timedelta(minutes=6)).isoformat()))
        self.assertEqual(self.show(item)['state'], 'stalled')

    def test_only_valid_counts_are_exposed_and_100_percent_is_not_completion(self):
        result = self.show(review())
        self.assertEqual(result['progress'], dict(parts=4, parts_done=1, bytes_sent=100, bytes_total=400))
        for value in [-1, True, '100', 401, float('inf'), 2**60]:
            item = review(progress=dict(bytes_sent=value, bytes_total=400))
            self.assertEqual(self.show(item)['progress'], {})
        for total in [0, -1, True, 399, '400']:
            self.assertEqual(self.show(review(progress=dict(bytes_sent=0, bytes_total=total)))['progress'], {})
        result = self.show(review(state='processing', progress=dict(bytes_sent=400, bytes_total=400)))
        self.assertEqual(result['state'], 'processing')
        self.assertFalse(result['terminal'])

    def test_allowlist_never_exposes_paths_raw_errors_tokens_or_upload_urls(self):
        item = review(reason='E:/private/token https://storage.invalid/?X-Amz-Signature=secret')
        result = self.show(item)
        self.assertEqual(set(result), {'kind','project_id','review_id','title','state','terminal','message','updated_at','progress','action'})
        blob = json.dumps(result)
        for secret in ['private', 'secret', 'token', 'presigned', 'plan_from', 'X-Amz', 'storage.invalid']:
            self.assertNotIn(secret, blob)
        self.assertEqual(item, review(reason=item['reason']))

    def test_view_url_https_exact_host_and_no_credentials_query_fragment(self):
        for url in ['https://app.frame.io/projects/abc', 'https://next.frame.io/review/abc']:
            self.assertEqual(safe_view_url(url), url)
        for url in ['http://next.frame.io/x', 'javascript:alert(1)', 'https://next.frame.io.evil.test/x',
                    'https://next.frame.io@evil.test/x', 'https://user@next.frame.io/x', 'https://next.frame.io:444/x',
                    'https://next.frame.io/x?X-Amz-Signature=secret', 'https://next.frame.io/x#secret',
                    'https://next.frame.io/\nsecret', 'https://next.frame.io\\@evil.test/x', 'https://evil.test/x',
                    'https://next.frame.io./x', 'https://next.frame.io:bad/x', None]:
            self.assertEqual(safe_view_url(url), '')


class StatusAccessorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name)/'Data')
        self.auth = Mock(side_effect=AssertionError('External auth must never run'))
        self.runner = Mock(side_effect=AssertionError('External tools must never run'))
        self.service = FrameioReview(self.store, Path(self.temp.name), auth=self.auth, runner=self.runner)
        self.one = review()
        with self.store.connect() as c:
            for row in [self.one, review(id='review-2', state='ready')]:
                c.execute('INSERT INTO frameio_reviews VALUES(?,?,?,?)', (row['id'], row['project_id'], row['id'], json.dumps(row)))

    def test_exact_review_does_not_follow_latest_or_call_external_checks(self):
        with self.store.connect() as c: before = list(c.iterdump())
        with patch.object(self.service, 'view', side_effect=AssertionError), patch.object(self.service, 'latest', side_effect=AssertionError):
            result = self.service.status('project-1', 'review-1')
        self.assertEqual(result['review_id'], 'review-1')
        self.assertEqual(result['progress']['bytes_sent'], 100)
        self.auth.assert_not_called(); self.runner.assert_not_called()
        with self.store.connect() as c: self.assertEqual(list(c.iterdump()), before)

    def test_missing_cross_project_and_mismatched_body_identity_are_rejected(self):
        for pid, rid in [('project-2','review-1'), ('project-1','missing'), ('../escape','review-1')]:
            with self.assertRaises(KeyError): self.service.status(pid,rid)
        with self.store.connect() as c:
            c.execute('UPDATE frameio_reviews SET body=? WHERE id=?', (json.dumps(review(project_id='project-2')), 'review-1'))
        with self.assertRaises(KeyError): self.service.status('project-1','review-1')


class DispatchTests(unittest.TestCase):
    def test_success_receipt_reads_exact_id_not_latest_and_repeat_is_same_review(self):
        snapshot = present_review(review(), at=NOW)
        service = SimpleNamespace(send=Mock(return_value=review()), status=Mock(return_value=snapshot))
        for _ in range(2): self.assertEqual(send_from_chat(service,'project-1',dict(said='Send it')),snapshot)
        service.status.assert_called_with('project-1','review-1')

    def test_staging_failure_or_empty_request_never_retains_uploading_claim_or_secrets(self):
        service = SimpleNamespace(send=Mock(side_effect=ValueError('E:/private/file.mp4?token=SECRET')), status=Mock())
        result = send_from_chat(service,'project-1',dict(said='Send it'))
        self.assertEqual((result['state'],result['action'],result['review_id']),('blocked','failed',''))
        self.assertNotIn('SECRET', json.dumps(result)); service.status.assert_not_called()
        service.send.reset_mock()
        send_from_chat(service,'project-1',dict(said=''))
        service.send.assert_not_called()

    def test_send_recorded_but_status_failure_preserves_id_without_claiming_success(self):
        service = SimpleNamespace(send=Mock(return_value=review()), status=Mock(side_effect=OSError('private database path')))
        result = send_from_chat(service,'project-1',dict(said='Send it'))
        self.assertEqual((result['review_id'],result['state'],result['terminal']),('review-1','stalled',False))
        self.assertNotIn('database', json.dumps(result))

    def test_agent_integration_replaces_model_claim_and_saves_host_receipt(self):
        # Execute the actual small dispatch block, avoiding all model and editing setup.
        tree = ast.parse((ROOT/'studio/agent.py').read_text(encoding='utf-8'))
        node = next(n for n in ast.walk(tree) if isinstance(n,ast.If) and 'send_from_chat' in ast.unparse(n)
                    and ast.unparse(n.test).startswith("fio is not None and action.get('frameio') == 'send'"))
        receipt = dict(kind='frameio', review_id='', state='blocked', message='Could not confirm request.')
        namespace = dict(__name__='studio.agent',__package__='studio',fio=object(),fio_pid='project-1',
            action={'frameio':'send'},message='Send it',via='',saved=[],reply='Uploading now!')
        with patch('studio.frameio_progress.send_from_chat',return_value=receipt):
            exec(compile(ast.Module(body=[node],type_ignores=[]),'<agent frameio block>','exec'),namespace)
        self.assertEqual(namespace['reply'],receipt['message'])
        self.assertEqual(namespace['saved'],[receipt])


class RouteTests(unittest.TestCase):
    def handler(self, authorized=True):
        # Compile the real route method alone: importing server also imports the
        # optional video decoder. This feature does not need that dependency.
        tree=ast.parse((ROOT/'studio/server.py').read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Handler')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='route')
        namespace=dict(__name__='studio.server',__package__='studio',urllib=urllib)
        exec(compile(ast.Module(body=[method],type_ignores=[]),'<actual route>','exec'),namespace)
        Handler=type('Handler',(),{'route':namespace['route']})
        handler = object.__new__(Handler)
        handler.path='/api/projects/project-1/frameio/review-1/status'
        handler.authorized=lambda: authorized
        handler.send=Mock(side_effect=lambda body,status=200,**kw:(status,body,kw))
        handler.server=SimpleNamespace(frameio=SimpleNamespace(status=Mock(return_value={'review_id':'review-1'})))
        return handler

    def test_status_route_requires_existing_auth_and_calls_only_exact_accessor(self):
        handler=self.handler(False)
        self.assertEqual(handler.route('GET')[0],403)
        handler.server.frameio.status.assert_not_called()
        handler=self.handler()
        result=handler.route('GET')
        self.assertEqual(result,(200,{'review_id':'review-1'},{'cache':'no-store'}))
        handler.server.frameio.status.assert_called_once_with('project-1','review-1')

    def test_status_errors_are_generic_and_never_echo_paths(self):
        for error,code in [(KeyError('secret path'),404),(RuntimeError('token=secret'),503)]:
            handler=self.handler();handler.server.frameio.status.side_effect=error
            result=handler.route('GET')
            self.assertEqual(result[0],code)
            self.assertNotIn('secret',json.dumps(result))

    def test_upload_and_lineage_methods_are_byte_equivalent_ast(self):
        before=ROOT.parent/'before/studio/frameio_review.py'
        if not before.exists(): self.skipTest('preimage available in staging bundle only')
        def methods(path):
            tree=ast.parse(path.read_text(encoding='utf-8'))
            cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='FrameioReview')
            return {n.name:ast.dump(n,include_attributes=False) for n in cls.body if isinstance(n,ast.FunctionDef)}
        original,current=methods(before),methods(ROOT/'studio/frameio_review.py')
        self.assertEqual(set(current)-set(original),{'status'})
        for name,value in original.items(): self.assertEqual(current[name],value,name)


class SavedChatTests(unittest.TestCase):
    def setUp(self):
        from studio.agent import Agents
        from studio.team import Team
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'Data')
        self.project=self.store.create('Fixture review video')
        self.agents=Agents(self.store,self.root,Team(self.store))
        self.agents.training=lambda owner:('',[])
        self.agents.edit_review=SimpleNamespace(project_folder=lambda pid:None)
        self.record=review(project_id=self.project['id'])
        self.agents.frameio=SimpleNamespace(context=lambda pid:'',send=Mock(return_value=self.record),
            status=Mock(return_value=present_review(self.record,at=NOW)))
        self.agents.run=lambda *a,**kw:('Uploading now!',dict(reply='Uploading now!',edit={'frameio':'send'}))

    def ask(self):
        return self.agents.chat(dict(agent_id='tariq',message='Send this video to Frame.io',
            context=dict(kind='project',id=self.project['id'])),[])['reply']

    def test_host_receipt_survives_saved_thread_reopen(self):
        from studio.agent import Agents,DESK
        result=self.ask()
        self.assertNotIn('Uploading now!',result['text'])
        self.assertEqual(result['saved'][0]['review_id'],'review-1')
        reopened=Agents(self.store,self.root,self.agents.team).thread(DESK,'tariq')
        self.assertEqual(reopened[-1]['saved'],result['saved'])

    def test_failed_stage_is_persisted_without_model_uploading_claim(self):
        from studio.agent import DESK
        self.agents.frameio.send.side_effect=ValueError('private secret filename')
        result=self.ask()
        self.assertEqual(result['saved'][0]['state'],'blocked')
        self.assertNotIn('Uploading now!',result['text'])
        self.assertNotIn('secret',json.dumps(result))
        self.assertEqual(self.agents.thread(DESK,'tariq')[-1]['saved'],result['saved'])


if __name__=='__main__': unittest.main()
