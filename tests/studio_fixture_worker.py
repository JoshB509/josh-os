"""Lifecycle test double only. Never used by the installed application."""
import argparse,json,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--request');p.add_argument('--job');p.add_argument('--resume',action='store_true');a=p.parse_args();job=Path(a.job)
if not a.resume:
    job.mkdir(parents=True);(job/'request.json').write_bytes(Path(a.request).read_bytes())
elif (job/'CANCEL').exists():(job/'CANCEL').unlink()
def status(state,message):(job/'status.json').write_text(json.dumps(dict(state=state,message=message)))
status('running','Test worker running')
for _ in range(200):
    if (job/'CANCEL').exists():status('cancelled','Test worker cancelled');break
    time.sleep(.05)
else:status('complete','Test worker finished')
