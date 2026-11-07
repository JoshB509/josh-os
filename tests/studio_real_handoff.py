"""Real installed-worker handoff smoke: starts once, immediately cancels safely."""
import json,sys,time,uuid,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from studio.store import Store
from studio.jobs import Jobs
root=Path(__file__).resolve().parents[1];app=Path(r'E:\Videos\DaJoshiResolve');out=root/'Evidence'/'Studio';out.mkdir(parents=True,exist_ok=True)
store=Store(out/('handoff-data-'+uuid.uuid4().hex[:8]));p=store.create('Scheduler handoff smoke — isolated test')
cfg=json.loads((app/'Inputs'/'Storytelling-2.0-json-20261014-v2'/'request.json').read_text())
p=store.save(p['id'],{'media':{k:cfg[k] for k in ['video','shots','deck','transcript']}},p['revision'])
jobs=Jobs(store,app);j=jobs.start(p['id']);again=jobs.start(p['id']);assert j['id']==again['id'];jobs.cancel(j['id']);jobs.cancel(j['id'])
jobs.processes[j['id']].wait(timeout=40);time.sleep(.3);result=jobs.status(store.job(j['id']))
assert result['status']['state']=='cancelled',result
installed=hashlib.sha256()
for f in [app/'worker.py']+sorted((app/'dajoshi').glob('*.py')):installed.update(f.read_bytes())
old=json.loads((app/'Runs'/'Storytelling-2.0-native-test-20261014'/'pipeline_identity.json').read_text())['code_sha256'];assert installed.hexdigest()==old
report=dict(passed=True,worker=str(app/'worker.py'),run=result,duplicate_start_same_run=True,immediate_cancel_passed=True,editing_code_unchanged=True,full_media_edit_intentionally_not_run=True)
(out/'real-handoff-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
