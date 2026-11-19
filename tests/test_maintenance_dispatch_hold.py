"""Isolated startup/queue preservation tests: no live app, credentials or sends."""
import ast
import contextlib
import hashlib
import json
import random
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT=Path(__file__).parents[1]


def load_class(file,name,ns,methods=None):
    tree=ast.parse((ROOT/file).read_text(encoding='utf-8'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
    if methods is not None:cls.body=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in methods]
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),str(file),'exec'),ns)
    return ns[name]


class TempStore:
    def __init__(self,root):self.root=root;self.db=root/'synthetic.sqlite'
    @contextlib.contextmanager
    def connect(self):
        con=sqlite3.connect(self.db);con.row_factory=sqlite3.Row
        try:
            with con:yield con
        finally:con.close()


class NoticePreservation(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name);self.store=TempStore(self.root)
        ns=dict(json=json,random=random,threading=threading,Path=Path,_time=SimpleNamespace(sleep=Mock()),
                now=lambda:'2026-01-01T12:00:00+00:00',datetime=datetime,UTC=timezone.utc,
                DEFAULTS={'enabled':True,'paused_until':'','muted':[],'banter':True},NAMES={})
        self.Cls=load_class('studio/notices.py','Notices',ns)
        self.bridge=Mock();self.busy=Mock()
        self.n=self.Cls(self.store,self.root,self.bridge,self.busy,maintenance_hold_dispatch=True)
        rows=[('a',{'state':'queued','agent_id':'anouk','created_at':'2000-01-01T00:00:00+00:00','attempts':0,'history':[]}),
              ('b',{'state':'failed','agent_id':'anouk','attempts':1,'history':['synthetic prior failure']}),
              ('c',{'state':'sending','agent_id':'sam','attempts':1,'history':[]})]
        with self.store.connect() as c:
            c.executemany('INSERT INTO notices VALUES(?,?)',[(k,json.dumps(v,separators=(',',':'))) for k,v in rows])
        (self.n.dir/'settings.json').write_text('{"enabled": false, "muted": ["anouk"]}')
        self.before=self.snapshot()

    def snapshot(self):
        with self.store.connect() as c:rows=[tuple(r) for r in c.execute('SELECT key,body FROM notices ORDER BY key')]
        return rows,(self.n.dir/'settings.json').read_bytes()

    def test_tick_does_not_age_skip_mutate_claim_or_send(self):
        self.n._tick=Mock(side_effect=AssertionError('must not age or scan during maintenance'))
        for _ in range(3):self.assertIn('Maintenance hold',self.n.tick())
        self.assertEqual(self.snapshot(),self.before)
        self.n._tick.assert_not_called();self.busy.assert_not_called();self.bridge.send_dm.assert_not_called()

    def test_direct_send_also_holds_before_claiming(self):
        self.assertIn('nothing claimed',self.n.send('anouk',[{'key':'a'}],{},datetime.now(timezone.utc)))
        self.assertEqual(self.snapshot(),self.before)
        self.bridge.send_dm.assert_not_called()

    def test_restart_in_hold_preserves_even_uncertain_sending_row_exactly(self):
        again=self.Cls(self.store,self.root,self.bridge,self.busy,maintenance_hold_dispatch=True)
        self.assertIn('Maintenance hold',again.tick())
        self.assertEqual(self.snapshot(),self.before)

    def test_old_default_behavior_and_explicit_rollback_boundary(self):
        # Normal mode still performs the pre-existing in-flight recovery. It is NOT
        # a safe rollback while a maintenance hold is required.
        normal=self.Cls(self.store,self.root,self.bridge,self.busy)
        with self.store.connect() as c:
            self.assertEqual(json.loads(c.execute("SELECT body FROM notices WHERE key='c'").fetchone()[0])['state'],'uncertain')
        normal._tick=Mock(return_value='normal dispatcher')
        self.assertEqual(normal.tick(),'normal dispatcher')
        normal._tick.assert_called_once()

    def test_status_reports_hold_without_toggling_settings(self):
        self.n.gate=Mock(side_effect=AssertionError('normal gate not needed during hold'))
        # Avoid formatting the deliberately synthetic muted agent in this fixture.
        self.n.settings=lambda:{'muted':[],'banter':True}
        self.assertIn('queued rows are preserved',self.n.status_line())
        self.assertEqual(self.snapshot(),self.before)


class StartupHold(unittest.TestCase):
    def test_discord_start_returns_before_import_or_thread_or_flush(self):
        thread=Mock(side_effect=AssertionError('no Discord thread during hold'))
        cls=load_class('studio/discord_bridge.py','Bridge',{'threading':SimpleNamespace(Thread=thread)},['start'])
        b=cls();b.server=SimpleNamespace(maintenance_hold_dispatch=True)
        self.assertIs(b.start(),b)
        thread.assert_not_called()

    def test_business_tick_returns_before_due_claim_or_external_work(self):
        cls=load_class('studio/business.py','Business',{},['tick'])
        b=cls();b.maintenance_hold_dispatch=True
        b.due=Mock(side_effect=AssertionError('must not inspect/claim outbox'))
        self.assertEqual(b.tick(),{'state':'maintenance_hold'})
        b.due.assert_not_called()

    def test_thread_start_branches_remain_off_only_during_hold(self):
        tests=[('studio/notices.py','n.loop','n'),('studio/youtube_comments.py','loop','server')]
        for file,target,obj in tests:
            tree=ast.parse((ROOT/file).read_text(encoding='utf-8'))
            node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and 'maintenance_hold_dispatch' in ast.unparse(n.test)
                      and any('threading.Thread' in ast.unparse(x) and ('target='+target) in ast.unparse(x).replace(' ','') for x in n.body))
            for held in (True,False):
                start=Mock();thread=Mock(return_value=SimpleNamespace(start=start))
                ns={obj:SimpleNamespace(maintenance_hold_dispatch=held,loop=Mock()),'threading':SimpleNamespace(Thread=thread),'loop':Mock()}
                exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),file,'exec'),ns)
                self.assertEqual(start.call_count,0 if held else 1)

    def test_cli_guard_is_explicit_default_off_no_auth_or_network_config(self):
        code=(ROOT/'studio_app.py').read_text(encoding='utf-8')
        self.assertIn("'--maintenance-hold-dispatch',action='store_true'",code)
        self.assertIn('maintenance_hold_dispatch=a.maintenance_hold_dispatch',code)
        server=(ROOT/'studio/server.py').read_text(encoding='utf-8')
        self.assertIn('maintenance_hold_dispatch=False',server)
        self.assertIn('maintenance_hold_dispatch=s.maintenance_hold_dispatch',server)
        self.assertIn('if not self.maintenance_hold_dispatch:threading.Thread(target=self.business.loop',server)
        self.assertIn('if not self.maintenance_hold_dispatch:threading.Thread(target=self.oversight.loop',server)

    def test_browser_chat_and_approval_handler_not_disabled_by_hold(self):
        # No transport hold in the owner browser route. Its authentication is
        # exercised independently by test_chat_upload_approval, including end to end.
        code=(ROOT/'studio/owner_release_chat.py').read_text(encoding='utf-8')
        self.assertNotIn('maintenance_hold_dispatch',code)
        server=(ROOT/'studio/server.py').read_text(encoding='utf-8')
        self.assertIn('exact_release=browser_chat(self,data)',server)


if __name__=='__main__':unittest.main()
