import hashlib,json,shutil
from pathlib import Path
root=Path(__file__).resolve().parent;out=root/'reference_sources';out.mkdir(exist_ok=True)
old=Path(r'C:\Users\Anigma PC\Documents\Codex\2026-10-13\ok-x20\work')
v03=Path(r'E:\CareerOS\12_YouTube\08_promotion-rule\2026-10-14_edit_v03\Build scripts')
v05=Path(r'E:\CareerOS\10_Sales\VSL\VIDEO\08_V05_New_Edit\06_Tools')
paths=[old/'transcribe.py',old/'make_plan.py',old/'render_animations.py',v03/'analyze_audio.py',v03/'verify_composite_sync.py',v03/'build_v03.py',v05/'v05_sync_refine.py',v05/'v05_prepare_speech_maps.py',v05/'v05_render_all.py',v05/'v05_render_animation.py',Path(r'E:\Videos\Reusable-Video-Kit\assemble.py')]
records=[]
for p in paths:
    target=out/p.name;shutil.copy2(p,target);records.append(dict(original=str(p),copied=str(target),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),used_as='Read-only reference; not executed by app'))
(out/'PROVENANCE.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
print('Copied',len(records),'reference scripts; originals unchanged.')
