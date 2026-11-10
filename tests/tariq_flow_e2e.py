"""End-to-end check of Tariq's editing workflow on a real recording, in a throwaway Josh OS data folder.

    python tests/tariq_flow_e2e.py <camera> <mic> [out_dir]

Runs the real local path: fingerprints, decode, three-point sync, local faster-whisper transcription (CPU),
measurement, then Josh's review actions (a word removal, a restore, a pause shortened, a note), approval
gating, preview WAVs and the native cut plan. No editorial model call and no Premiere. Writes a receipt.
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.store import Store, Conflict  # noqa: E402
from studio.edit_flow import EditFlow  # noqa: E402


def main(camera, mic, out_dir=None):
    tmp = Path(tempfile.mkdtemp(prefix='tariq-e2e-'))
    receipt = dict(camera=camera, mic=mic, data=str(tmp), steps=[])
    log = lambda name, **kw: (receipt['steps'].append(dict(step=name, **kw)), print(name, json.dumps(kw)[:300], flush=True))
    try:
        store = Store(tmp)
        p = store.create('Tariq e2e test')
        flow = EditFlow(store, ROOT)
        # share the real decode/transcript cache so a rerun is quick (cache keys are content hashes)
        cache = ROOT / 'Data' / 'edit_flow' / 'cache'
        cache.mkdir(parents=True, exist_ok=True)
        (flow.data / 'cache').mkdir(exist_ok=True)
        flow._cache = lambda: cache
        src = flow.register(p['id'], camera, mic)
        log('sources', id=src['id'], fps=src['payload']['headers']['camera']['fps'], mic_rate=src['payload']['headers']['mic']['sample_rate'])
        t0 = time.time()
        flow.analyse(p['id'], src['id'], editorial=False)
        while True:
            time.sleep(5)
            pr = flow.progress(p['id'])
            if pr and pr['state'] != 'running':
                break
        log('analyse', state=pr['state'], message=pr['message'], seconds=round(time.time() - t0))
        if pr['state'] != 'complete':
            raise SystemExit('analysis failed: ' + pr['message'])
        v = flow.view(p['id'])
        log('view', summary=v['summary'], sync=dict(verified=v['sync'].get('verified'), offset=v['sync'].get('offset_seconds')),
            asr=v['asr'], errors=v['errors'], cards=len(v['cards']))
        prop = v['proposal']
        # stale approval is refused
        try:
            flow.approve_script(p['id'], prop['id'], 'wrong-digest')
            log('stale_approval', refused=False)
        except Conflict:
            log('stale_approval', refused=True)
        # Josh removes three words, shortens the first long pause, and adds a note
        words = [i for i in v['transcript'] if i['type'] == 'word']
        pause = next((i for i in v['transcript'] if i['type'] == 'event' and i['kind'] == 'pause' and i['review']), None)
        sel = [w['id'] for w in words[40:43]]
        changes = [dict(event_id=pause['id'], action='shorten', target_ms=400)] if pause else []
        v2 = flow.revise(p['id'], prop['id'], prop['digest'], changes, [dict(ids=sel, action='remove')], 'Test note from the e2e check')
        removed = [i['id'] for i in v2['transcript'] if i['type'] == 'word' and i['state'] == 'removed']
        log('revise', revision=v2['proposal']['revision'], words_removed=all(x in removed for x in sel),
            pause=next((i for i in v2['transcript'] if pause and i['id'] == pause['id']), None), removed_s=v2['summary']['removed_s'])
        # the old revision can no longer be changed or approved
        try:
            flow.revise(p['id'], prop['id'], prop['digest'], [], [], 'x')
            log('old_revision_locked', locked=False)
        except Conflict:
            log('old_revision_locked', locked=True)
        # restore the words: a new revision, rebuilt from the source, nothing reinserted into a cut
        v3 = flow.revise(p['id'], v2['proposal']['id'], v2['proposal']['digest'], [], [dict(ids=sel, action='default')])
        log('restore', revision=v3['proposal']['revision'],
            words_back=all(i['state'] == 'kept' for i in v3['transcript'] if i['type'] == 'word' and i['id'] in sel))
        # preview for the first card
        card = v3['cards'][0] if v3['cards'] else None
        if card:
            pv = flow.preview(p['id'], v3['proposal']['id'], card['event_id'], 2)
            pv2 = flow.preview(p['id'], v3['proposal']['id'], card['event_id'], 2)
            f = flow.preview_file(p['id'], pv['proposed'])
            log('preview', card=card['label'], original=pv['original'], proposed=pv['proposed'], bytes=f.stat().st_size, cached=pv == pv2)
        # cut plan refused before approval
        try:
            flow.cut_plan(p['id'], v3['proposal']['id'], v3['proposal']['digest'], 8467191533)
            log('plan_before_approval', refused=False)
        except Conflict:
            log('plan_before_approval', refused=True)
        if not v3['errors']:
            flow.approve_script(p['id'], v3['proposal']['id'], v3['proposal']['digest'], 'e2e approval')
            plan = flow.cut_plan(p['id'], v3['proposal']['id'], v3['proposal']['digest'], 8467191533)
            m = plan['map']
            src_pieces = [x for x in m['pieces'] if x['kind'] == 'source']
            contiguous = all(a['out_end'] == b['out_start'] for a, b in zip(m['pieces'], m['pieces'][1:]))
            log('cut_plan', pieces=len(src_pieces), seconds=round(m['duration_ticks'] / m['ticks_per_second'], 3),
                contiguous=contiguous, max_rounding_ms=round(m['max_rounding_ticks'] / m['ticks_per_second'] * 1000, 2),
                mic_minus_camera_s=m['mic_minus_camera_ticks'] / m['ticks_per_second'])
            res = flow.build_cut(p['id'], v3['proposal']['id'], v3['proposal']['digest'], 8467191533)
            log('build_without_premiere', state=res['payload']['state'], error=res['payload'].get('error'))
        else:
            log('approval_blocked', errors=v3['errors'])
        log('status', stages=flow.status(p['id']))
    finally:
        out = Path(out_dir) if out_dir else ROOT / 'tests' / 'fixtures' / 'tariq_edit'
        out.mkdir(parents=True, exist_ok=True)
        (out / f"flow_e2e_{Path(mic).stem[:20]}.json").write_text(json.dumps(receipt, indent=1, default=str), encoding='utf-8')
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main(*sys.argv[1:])
