"""Tariq builds the Premiere timeline himself, from the raw files.

He makes his own project in the video's video\\Tariq folder and builds GREEN_MASTER there: the background on V1, a
shadow copy of Josh on V2 and the keyed presenter on V3, with every effect setting copied from a reference project
Josh keyed by hand (his look). Josh's own projects are only ever read through copies.

The work in Premiere is a list of small fixed steps (panel command tq_ops). The panel refuses them outside Tariq's
own projects. Masks cannot be made through Adobe's plugin API, so they are not copied.
"""
import json
import shutil
from pathlib import Path

from .store import now

REFERENCE = Path(r'C:\Users\Anigma PC\Desktop\Tariq-Editing-Handover-2026-11-10\reference-projects\Data storytelling.prproj')
BACKGROUND = Path(r'E:\CareerOS\12_YouTube\Backgrounds\Josh-Video-Background-Office-4K.png')
MASTER = 'GREEN_MASTER'
# Effects every new clip already has. Their settings are copied, the effect is not added again.
BUILT_IN = ('AE.ADBE Motion', 'AE.ADBE Opacity', 'AE.ADBE Time Remapping')


class MasterError(ValueError):
    pass


def ops(flow, steps, timeout=1800):
    r = flow.bridge_call('tq_ops', dict(ops=steps), timeout=timeout)
    if not r.get('ok'):
        bad = (r.get('steps') or [{}])[-1]
        raise MasterError(f"Premiere refused step {bad.get('n')} ({bad.get('op')}): {bad.get('error')}")
    return [s.get('result') for s in r['steps']]


def roles(info):
    """Which video track is the background, the shadow copy and the keyed presenter, from their effects."""
    first = {}
    for it in info['items']:
        if it['kind'] == 'v' and it['track'] not in first:
            first[it['track']] = it
    def has(it, word):
        return any(word in (e['match'] + ' ' + e['name']).lower() for e in it['effects'])
    keyed = [t for t, it in first.items() if has(it, 'ultra key')]
    shadow = [t for t in keyed if has(first[t], 'drop shadow')]
    presenter = [t for t in keyed if t not in shadow]
    out = dict(presenter=first[max(presenter)] if presenter else None, shadow=first[max(shadow)] if shadow else None)
    rest = [t for t in first if t not in keyed]
    out['background'] = first[min(rest)] if rest else None
    return out


def read_look(flow, reference=REFERENCE, sequence=None, folder=None):
    """Josh's look from a project he keyed by hand: every effect setting on the background, shadow and presenter.
    Read from a copy, so the reference itself is never opened."""
    reference = Path(reference)
    stamp = now().replace(':', '').replace('-', '').replace('.', '')[:15]
    folder = Path(folder or flow.data / 'look') / 'native-test' / stamp
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / (reference.stem + ' - look copy.prproj')
    shutil.copy2(reference, copy)
    from .edit_flow import wake_premiere, _close_copy
    wake_premiere(flow)
    ops(flow, [dict(op='open_project', path=str(copy))])
    try:
        names = ops(flow, [dict(op='project')])[0]['sequences']
        best = None
        for name in ([sequence] if sequence else names):
            info = ops(flow, [dict(op='info', seq=name)])[0]
            r = roles(info)
            if r['presenter'] and (best is None or (r['shadow'] and not best[1]['shadow'])):
                best = (info, r)
            if best and best[1]['shadow'] and sequence is None:
                break
        if not best:
            raise MasterError('No sequence in ' + reference.name + ' has a keyed presenter to copy the look from.')
    finally:
        try:
            _close_copy(flow, copy)
        except Exception:  # noqa: BLE001 - a copy left open is harmless
            pass
    info, r = best
    look = dict(reference=str(reference), sequence=info['name'], frame=info['frame'], read=now(),
                presenter=r['presenter']['effects'], shadow=(r['shadow'] or {}).get('effects'),
                background=(r['background'] or {}).get('effects'), background_path=(r['background'] or {}).get('path'),
                note='Masks are not copied. Adobe\'s plugin API cannot make them.')
    (flow.data / 'look.json').write_text(json.dumps(look, indent=1), encoding='utf-8')
    return look


def _type(v):
    """The setting type and the value to send. Premiere reads points back as {'value': [x, y]}."""
    if isinstance(v, bool):
        return 'bool', v
    if isinstance(v, (int, float)):
        return 'number', v
    if isinstance(v, dict) and 'value' in v and 'color' not in v:
        v = v['value']
    if isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
        return 'point', v
    if isinstance(v, dict) and isinstance(v.get('color'), list):
        return 'color', v['color']
    return None, None


FILL = 'CAMERA_FILLED'  # the recording with everything outside Josh's mask painted key green


def key_color(look):
    for e in look.get('presenter') or []:
        if 'Ultra Key' in e['match']:
            for p in e['params']:
                if isinstance(p.get('value'), dict) and isinstance(p['value'].get('color'), list):
                    return p['value']['color'][:3]
    return [0.33, 0.61, 0.29]


# Josh's standard mask: an outline around him in fractions of the camera frame, from the one he drew on Exec
# questions, carried down to the bottom so his hands stay in. It sits clear of him and inside the green screen, so a
# camera that sits slightly differently still fits. look.json can hold its own 'mask' to change it.
MASK = [[0.14, 1.0], [0.14, 0.64], [0.17, 0.56], [0.30, 0.31], [0.47, 0.15], [0.55, 0.16], [0.71, 0.25], [0.78, 0.48], [0.78, 1.0]]


def mask_fill(camera, out, color, mask=None, feather=0.012):
    """The mask drawn as a PNG the size of the recording: key green outside it, clear inside, with a soft edge.
    Painted green, the room outside keys out with the screen."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(camera))
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if not W or not H:
        raise MasterError('Could not read the size of the recording.')
    m = np.zeros((H, W), np.uint8)
    cv2.fillPoly(m, [np.array([[x * (W - 1), y * (H - 1)] for x, y in (mask or MASK)], np.int32)], 255)
    m = cv2.GaussianBlur(m, (0, 0), max(1, feather * W))
    b, g, r = [int(round(c * 255)) for c in reversed(color[:3])]
    png = np.dstack([np.full((H, W), b, np.uint8), np.full((H, W), g, np.uint8), np.full((H, W), r, np.uint8), 255 - m])
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), png)
    return dict(path=str(out), mask=mask or MASK)


def josh_x(camera, mask=None, samples=6):
    """Where Josh sits across the recording, 0 to 1: the middle of what isn't green inside his mask, between head
    height and mid chest, over a few small frames. His hands and the desk are left out so they don't pull it."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(camera))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w, h = 480, 270
    poly = np.zeros((h, w), np.uint8)
    cv2.fillPoly(poly, [np.array([[x * (w - 1), y * (h - 1)] for x, y in (mask or MASK)], np.int32)], 1)
    poly[:int(0.12 * h)] = 0
    poly[int(0.55 * h):] = 0
    xs = []
    for i in range(samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * (i + 0.5) / samples))
        ok, f = cap.read()
        if not ok:
            continue
        f = cv2.resize(f, (w, h), interpolation=cv2.INTER_AREA).astype(np.int16)
        green = (f[..., 1] - f[..., 2] > 35) & (f[..., 1] - f[..., 0] > 35) & (f[..., 1] > 70)
        cols = np.nonzero((~green) & (poly > 0))[1]
        if cols.size > 0.01 * w * h:
            xs.append(float(np.median(cols)) / (w - 1))
    cap.release()
    return float(np.median(xs)) if xs else None


def centred(look, x):
    """Josh's look with his Position moved so he sits in the middle of the frame. The shadow moves by the same amount."""
    look = json.loads(json.dumps(look))
    def motion(effects):
        for e in effects or []:
            if e['match'] == 'AE.ADBE Motion':
                return e
    pm = motion(look.get('presenter'))
    if x is None or not pm:
        return look, None
    pos = next(p for p in pm['params'] if p.get('name') == 'Position')
    scale = next((p['value'] for p in pm['params'] if p.get('name') == 'Scale'), 100) / 100.0
    old = (pos['value'].get('value') if isinstance(pos['value'], dict) else pos['value'])[:]
    dx = (0.5 - (x - 0.5) * scale) - old[0]
    for e in (pm, motion(look.get('shadow'))):
        if e:
            p = next(p for p in e['params'] if p.get('name') == 'Position')
            v = p['value'].get('value') if isinstance(p['value'], dict) else p['value']
            p['value'] = {'value': [v[0] + dx, v[1]]}
    return look, round(dx, 4)


def copy_steps(seq, track, have, want):
    """Steps that give the clip on a track the effects and settings in want. have is what the clip has now."""
    steps, count = [], {}
    pool = [e['match'] for e in have]
    for e in want or []:
        nth = count.get(e['match'], 0)
        count[e['match']] = nth + 1
        if pool.count(e['match']) <= nth:
            if e['match'] in BUILT_IN:
                continue
            steps.append(dict(op='add_effect', seq=seq, kind='v', track=track, index=0, effect=e['match']))
            pool.append(e['match'])
        for i, p in enumerate(e['params']):
            t, v = _type(p['value'])
            if t is None:
                continue
            steps.append(dict(op='set', seq=seq, kind='v', track=track, index=0, effect=e['match'], nth=nth, param_index=i,
                              value=v, type=t, may_fail=True, report=False))
    return steps


def project_path(flow, pid):
    base = Path(flow.folders.path(pid))
    title = ''.join(c for c in str(flow.store.get(pid).get('name') or 'video') if c.isalnum() or c in ' -_').strip()[:60] or 'video'
    return base / 'video' / 'Tariq' / f'{title} - Tariq.prproj'


def open_project(flow, pid):
    """Premiere ready with Tariq's project for this video open. Returns its path, or None when it isn't made yet."""
    from .edit_flow import wake_premiere
    wake_premiere(flow)
    path = project_path(flow, pid)
    if not path.is_file():
        return None
    now_open = (flow.bridge_call('tq_probe', dict(master=MASTER)) or {}).get('project') or ''
    if now_open.replace('\\\\?\\', '').lower() != str(path).lower():
        ops(flow, [dict(op='open_project', path=str(path))])
    return path


def build_master(flow, pid, camera, mic, look=None, background=None, say=print):
    """Tariq's project and GREEN_MASTER, built from the raw files with Josh's look. Safe to run again: it never
    overwrites, and an existing master is left as it is."""
    from .edit_flow import wake_premiere
    look = look or json.loads((flow.data / 'look.json').read_text(encoding='utf-8'))
    background = Path(background or look.get('background_path') or BACKGROUND)
    path = project_path(flow, pid)
    say('Opening Premiere')
    wake_premiere(flow)
    if path.is_file():
        open_project(flow, pid)
        if MASTER in ops(flow, [dict(op='project')])[0]['sequences']:
            return dict(project=str(path), master=MASTER, state='exists')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        say('Making Tariq\'s Premiere project')
        ops(flow, [dict(op='create_project', path=str(path))])
        if MASTER in ops(flow, [dict(op='project')])[0]['sequences']:
            return dict(project=str(path), master=MASTER, state='exists')
    fill = mask_fill(camera, path.parent / (path.stem + ' - mask fill.png'), key_color(look), look.get('mask'))
    say('Bringing in the camera, the mic and the background')
    ops(flow, [dict(op='import', paths=[str(camera), str(mic), str(background), fill['path']])])
    # the mask: the recording with the room outside it painted key green, as a nest
    info = ops(flow, [dict(op='seq_from_media', name=FILL, media=str(camera))])[0]
    cam = next(i for i in info['items'] if i['kind'] == 'v')
    steps = [dict(op='remove_track', seq=FILL, kind='a', track=t) for t in sorted({i['track'] for i in info['items'] if i['kind'] == 'a'})]
    steps += [dict(op='place', seq=FILL, media=fill['path'], at='0', v=cam['track'] + 1, a=0),
              dict(op='set_end', seq=FILL, kind='v', track=cam['track'] + 1, index=0, end=cam['end'])]
    ops(flow, steps)
    info = ops(flow, [dict(op='seq_from_media', name=MASTER, media=str(camera))])[0]
    say('Laying out GREEN_MASTER')
    cam = next(i for i in info['items'] if i['kind'] == 'v')
    steps = [dict(op='remove_track', seq=MASTER, kind='a', track=t) for t in sorted({i['track'] for i in info['items'] if i['kind'] == 'a'})]
    steps += [dict(op='remove', seq=MASTER, kind='v', track=cam['track'], index=0),
              dict(op='place_sequence', seq=MASTER, source=FILL, at='0', v=2, a=0),
              dict(op='place_sequence', seq=MASTER, source=FILL, at='0', v=1, a=0),
              dict(op='place', seq=MASTER, media=str(background), at='0', v=0, a=0),
              dict(op='set_end', seq=MASTER, kind='v', track=0, index=0, end=cam['end'], may_fail=True),
              dict(op='remove_track', seq=MASTER, kind='a', track=0, may_fail=True),
              dict(op='remove_track', seq=MASTER, kind='a', track=1, may_fail=True),
              dict(op='info', seq=MASTER)]
    laid = ops(flow, steps)[-1]
    first = {}
    for it in laid['items']:
        if it['kind'] == 'v':
            first.setdefault(it['track'], it)
    if not all(t in first for t in (0, 1, 2)):
        raise MasterError('GREEN_MASTER did not get the background, shadow and presenter on V1 to V3. Premiere reported tracks '
                          + ', '.join('V%d' % (t + 1) for t in sorted(first)) + '.')
    say('Keying you and copying your look')
    look, moved = centred(look, josh_x(camera, look.get('mask')))
    steps = copy_steps(MASTER, 2, first[2]['effects'], look['presenter'])
    steps += copy_steps(MASTER, 1, first[1]['effects'], look.get('shadow') or look['presenter'])
    steps += copy_steps(MASTER, 0, first[0]['effects'], look.get('background'))
    steps += [dict(op='save'), dict(op='info', seq=MASTER)]
    res = flow.bridge_call('tq_ops', dict(ops=steps), timeout=1800)
    if not res.get('ok'):
        bad = res['steps'][-1]
        raise MasterError(f"Premiere refused step {bad.get('n')} ({bad.get('op')}): {bad.get('error')}")
    failed = [s for s in res['steps'] if not s['ok']]
    final = res['steps'][-1]['result']
    check = roles(final)
    problems = []
    if not check['presenter']:
        problems.append('V3 has no Ultra Key, so you are not keyed.')
    if look.get('shadow') and not check['shadow']:
        problems.append('V2 has no Drop Shadow, so the shadow copy is missing.')
    if any(i['kind'] == 'a' for i in final['items']):
        problems.append('GREEN_MASTER still has sound on it.')
    bg_end = max((int(i['end']) for i in final['items'] if i['kind'] == 'v' and i['track'] == 0), default=0)
    if bg_end < int(cam['end']) - int(final['frame_ticks']):
        problems.append('The background stops before the recording ends.')
    return dict(project=str(path), master=MASTER, state='built' if not problems else 'problems', problems=problems,
                nest=FILL, mask=fill['mask'], centred_by=moved,
                settings_failed=[dict(step=s['n'], error=s.get('error')) for s in failed][:40], settings=len(steps) - 2,
                tracks=final['video_tracks'], note='Your mask is drawn from fixed coordinates (look.json can change them) and painted key green in ' + FILL + '.')
