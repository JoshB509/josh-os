import copy
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import sqlite3
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from studio.store import Store
from studio.lead_magnets import LeadMagnets
from studio.agent import Agents
from studio.team import Team

ROOT = Path(__file__).resolve().parents[1]


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'Data')
        self.registry = LeadMagnets(self.store, self.root)
        self.record = dict(id='stable-source-id', name='A guide', type='lead_magnet', landing_url='https://example.org/guide', contents='A worksheet')

    def test_idempotent_three_way_merge_preserves_edits_and_missing_records(self):
        self.registry.merge([self.record, dict(id='other', name='Other', type='lead_magnet')])
        with self.store.connect() as c:
            local = dict(self.record, name='Josh’s name', contents='', notes='Keep this')
            c.execute('UPDATE lead_magnets SET body=? WHERE id=?', (json.dumps(local), self.record['id']))
        incoming = dict(self.record, name='Source rename', contents='New contents', landing_url='https://example.org/new', audience='Managers')
        self.registry.merge([incoming])
        once = self.registry.all()
        self.registry.merge([incoming])
        self.assertEqual(self.registry.all(), once)
        actual = next(r for r in once if r['id'] == self.record['id'])
        self.assertEqual(actual['name'], local['name'])
        self.assertEqual(actual['contents'], '')
        self.assertEqual(actual['notes'], 'Keep this')
        self.assertEqual(actual['landing_url'], incoming['landing_url'])
        self.assertEqual(actual['audience'], 'Managers')
        self.assertEqual(len(once), 2)

    def test_filters_types_and_rejects_invalid_batch_atomically(self):
        self.registry.merge([dict(id='sales', name='Sales', type='sales_page'), self.record])
        self.assertEqual(len(self.registry.all()), 1)
        with self.assertRaises(ValueError):
            self.registry.merge([dict(self.record, id='good'), dict(self.record, id='bad', landing_url='javascript:alert(1)')])
        self.assertEqual(len(self.registry.all()), 1)
        with self.assertRaises(ValueError):
            self.registry.merge([self.record, self.record])

    def test_download_requires_verification_for_exact_url(self):
        item = dict(self.record, download_url='https://example.org/file.pdf')
        self.registry.merge([item])
        self.assertNotIn('download_url', self.registry.directory()['magnets'][0])
        item['download_verification'] = dict(status='verified', checked_at='2026-11-16', url=item['download_url'])
        self.registry.merge([item])
        self.assertEqual(self.registry.directory()['magnets'][0]['download_url'], item['download_url'])
        item['download_url'] = 'https://example.org/other.pdf'
        self.registry.merge([item])
        self.assertNotIn('download_url', self.registry.directory()['magnets'][0])

    def test_real_seed_has_stable_ids_and_leaves_video_briefs_alone(self):
        video = self.store.create('Existing video')
        registry = LeadMagnets(self.store, ROOT)
        one = registry.all()
        self.assertEqual(len(one), 16)
        self.assertEqual(len({r['id'] for r in one}), 16)
        self.assertTrue(all(r['id'] == r['source_id'] and r['source'] == 'linklr' for r in one))
        self.assertEqual(registry.all(), one)
        self.assertEqual(self.store.get(video['id']), video)

    def test_common_runner_reads_current_registry_for_every_agent(self):
        team = Team(self.store)
        agents = Agents(self.store, self.root, team)
        agents.lead_magnets.merge([self.record])
        captured = []
        def fake_run(cmd, **kwargs):
            captured.append(Path(cmd[cmd.index('--append-system-prompt-file') + 1]).read_text(encoding='utf-8'))
            return SimpleNamespace(stdout=json.dumps({'structured_output': {'reply': 'OK'}}), stderr='', returncode=0)
        with patch('studio.agent.find_claude', return_value='fixture-claude.exe'), patch('studio.agent.subprocess.run', side_effect=fake_run):
            for agent in team.all()['agents']:
                agents.run('You are ' + agent['name'], 'Read the registry')
            agents.lead_magnets.merge([dict(self.record, contents='Fresh verified content')])
            agents.run('A later job', 'Read it again')
        self.assertTrue(all('stable-source-id' in s and 'Shared lead-magnet registry' in s for s in captured))
        self.assertIn('Fresh verified content', captured[-1])

    def write_source(self, value):
        self.registry.source.parent.mkdir(exist_ok=True)
        self.registry.source.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')

    def test_bad_source_retains_last_good_rows_and_local_edits(self):
        self.write_source(dict(schema_version=1, magnets=[self.record]))
        self.registry.all()
        with self.store.connect() as c:
            local = dict(self.record, name='Josh edit', contents='')
            c.execute('UPDATE lead_magnets SET body=? WHERE id=?', (json.dumps(local), self.record['id']))
        bad_sources = [
            '{partial-json-sensitive-text', [], {}, dict(schema_version=1, magnets={}),
            dict(schema_version=1, magnets=[None]),
            dict(schema_version=1, magnets=[self.record, self.record]),
            dict(schema_version=1, magnets=[dict(self.record, id='new-first'), dict(self.record, landing_url='javascript:secret')]),
            dict(schema_version=1, magnets=[dict(self.record, aliases='not-a-list')]),
            dict(schema_version=1, magnets=[dict(self.record, download_verification='not-an-object')]),
        ]
        for value in bad_sources:
            with self.subTest(source=value):
                self.write_source(value)
                self.registry._source_issue = None
                with self.assertLogs('studio.lead_magnets', level='WARNING') as logs:
                    self.assertEqual(self.registry.all(), [local])
                self.assertNotIn('sensitive-text', ''.join(logs.output))
                self.assertNotIn('javascript:secret', ''.join(logs.output))
                with self.store.connect() as c:
                    row = c.execute('SELECT source_body FROM lead_magnets').fetchone()
                    self.assertEqual(json.loads(row['source_body']), self.record)
        self.write_source(dict(schema_version=1, magnets=[dict(self.record, audience='A recovered update')]))
        recovered = self.registry.all()[0]
        self.assertEqual(recovered['name'], 'Josh edit')
        self.assertEqual(recovered['contents'], '')
        self.assertEqual(recovered['audience'], 'A recovered update')

    def test_source_read_errors_are_empty_safe_without_last_good_rows(self):
        for error in (FileNotFoundError(), PermissionError(), UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid')):
            with self.subTest(error=type(error).__name__), patch.object(Path, 'read_text', side_effect=error):
                with self.assertLogs('studio.lead_magnets', level='WARNING'):
                    self.assertEqual(self.registry.directory(), {'magnets': []})
                self.assertTrue(self.registry.agent_context().endswith('[]'))

    def test_invalid_source_does_not_stop_agent_runner(self):
        agents = Agents(self.store, self.root, Team(self.store))
        self.write_source('{partial-json')
        captured = []
        def fake_run(cmd, **kwargs):
            captured.append(Path(cmd[cmd.index('--append-system-prompt-file') + 1]).read_text(encoding='utf-8'))
            return SimpleNamespace(stdout=json.dumps({'structured_output': {'reply': 'Still usable'}}), stderr='', returncode=0)
        with patch('studio.agent.find_claude', return_value='fixture-claude.exe'), patch('studio.agent.subprocess.run', side_effect=fake_run):
            with self.assertLogs('studio.lead_magnets', level='WARNING'):
                self.assertEqual(agents.run('Generic job', 'Unrelated request')[0], 'Still usable')
            self.assertTrue(captured[-1].endswith('[]'))
            agents.lead_magnets.merge([self.record])
            self.assertEqual(agents.run('Generic job', 'Another request')[0], 'Still usable')
            self.assertIn(self.record['id'], captured[-1])

    def test_database_failures_are_not_hidden_as_source_errors(self):
        self.write_source(dict(schema_version=1, magnets=[self.record]))
        with patch.object(self.store, 'connect', side_effect=sqlite3.OperationalError('fixture database failure')):
            with self.assertRaises(sqlite3.OperationalError):
                self.registry.all()
        self.registry.merge([self.record])
        with self.store.connect() as c:
            c.execute('UPDATE lead_magnets SET body=?', ('{corrupt-db-row',))
        with self.assertRaises(json.JSONDecodeError):
            self.registry.all()


class HttpTests(unittest.TestCase):
    def test_existing_auth_gate_protects_directory_api_and_assets(self):
        # Real Handler, isolated Store, no Server constructor or background services.
        from studio.server import Handler
        with tempfile.TemporaryDirectory() as tmp:
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.token = 'fixture-only'
            server.agents = SimpleNamespace(lead_magnets=LeadMagnets(Store(Path(tmp) / 'Data'), ROOT))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for path in ('/lead-magnets', '/api/lead-magnets', '/lead-magnets.js', '/lead-magnets.css'):
                    url = f'http://127.0.0.1:{server.server_port}' + path
                    with self.assertRaises(HTTPError) as error:
                        urlopen(url)
                    self.assertEqual(error.exception.code, 403)
                    with urlopen(Request(url, headers={'X-DJ-Token': server.token})) as r:
                        self.assertEqual(r.status, 200)
                        body = r.read()
                        if path == '/api/lead-magnets':
                            self.assertEqual(len(json.loads(body)['magnets']), 16)
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(url, headers={'X-DJ-Token': server.token, 'Origin': 'https://untrusted.example'}))
                    self.assertEqual(error.exception.code, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
