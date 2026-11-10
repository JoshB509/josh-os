"""Tariq's approved animation deck onto the final assembly, automatically.

The deck and shot list in the video's folder (the ones Josh reviewed and approved as the storyboard) give each
animation's presses as quoted words. Each press is found in the approved transcript, mapped through the cut's
own map to its place in the edit, rendered locally from the deck with the engine's renderer (ProRes with alpha
for PiP and half and half, so the keyed picture shows through), and placed by the Josh Cut Pro panel on V5.
PiP and half and half shots get a copy of the GREEN_MASTER piece underneath on V4, never the raw camera file.

Rendering runs here with Playwright and FFmpeg. No model call, no upload.
"""
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

from dajoshi import edit_plan as ep

CUT_HELPER = Path(r'E:\Videos\DaJoshiCut\helper')
RENDER = CUT_HELPER / 'render.py'
ALPHA_TYPES = {'pip', 'half'}
TRACK_CAMERA, TRACK_ANIMATION = 3, 4  # V4 and V5, as Josh Cut Pro uses them


def norm(t):
    return re.sub(r"[^a-z0-9']+", '', t.lower())


def tokens(text):
    return [x for x in (norm(w) for w in str(text).split()) if x]


def locate(stream, phrase, start=0, need=6, window=600, stop=None):
    """Index in stream (kept words) where phrase starts, searching forward from start within a window.

    The shot list quotes the script, and the transcript can differ a little ("you on" against "you're on"), so the
    first few words are compared loosely: the first close match wins, else the best one if it is close enough."""
    from difflib import SequenceMatcher
    want = tokens(phrase)[:need]
    if not want:
        return None
    toks = [norm(w['text']) for w in stream]
    end = min(len(stream), start + window, stop if stop is not None else len(stream))
    target = ' '.join(want)
    best, best_i = 0.0, None
    for i in range(max(0, start), max(start, end - 1) + 1):
        if not toks[i:i + 1] or SequenceMatcher(None, toks[i], want[0]).ratio() < 0.6 and SequenceMatcher(None, toks[i], want[min(1, len(want) - 1)]).ratio() < 0.6:
            continue
        r = SequenceMatcher(None, ' '.join(toks[i:i + len(want)]), target).ratio()
        if r >= 0.85:
            return i
        if r > best:
            best, best_i = r, i
    return best_i if best >= 0.7 else None


def plan_shots(words, removals, pieces, parts, scenes, frame_ticks, sr=16000):
    """Each press as a shot in output frames. Returns (shots, problems)."""
    kept = [w for w in words if sum(max(0, min(b, w['end']) - max(a, w['start'])) for a, b in removals) < 0.5 * (w['end'] - w['start'])]
    tick = lambda w: ep.map_word(pieces, w['start'], sr)
    total = max([p['out_end'] for p in pieces] or [0])
    problems, cursor, starts = [], 0, []
    for p in parts:
        if p['kind'] == 'section':
            starts.append(None)
            continue
        i = locate(kept, p.get('script', ''), cursor)
        starts.append(i)
        if i is not None:
            cursor = i + 1
    shots = []
    for k, p in enumerate(parts):
        if p['kind'] != 'animation':
            continue
        sc = scenes.get(p['n'])
        if not sc:
            problems.append(f"Animation {p['n']} is in the shot list but not in the slides.")
            continue
        nxt = next((starts[j] for j in range(k + 1, len(parts)) if starts[j] is not None), None)
        end_t = tick(kept[nxt]) if nxt is not None else total
        base = starts[k] if starts[k] is not None else 0
        presses = []
        for pr in p.get('presses') or []:
            i = locate(kept, pr['words'], base, stop=nxt)
            if i is None or (nxt is not None and i >= nxt):
                problems.append(f"Animation {p['n']} press {pr['press']}: \"{pr['words']}\" is not in the approved script, so it was left out.")
                continue
            presses.append((pr['press'], tick(kept[i])))
        presses = [(n, t) for n, t in presses if t is not None]
        for j, (n, t) in enumerate(presses):
            hi = presses[j + 1][1] if j + 1 < len(presses) else end_t
            a, b = round(t / frame_ticks), round(hi / frame_ticks)
            if shots and a < shots[-1]['end_f']:
                a = shots[-1]['end_f']
            if b - a < 2:
                continue
            beat = sc['start'] + n - 1
            if beat > sc['end']:
                problems.append(f"Animation {p['n']} press {n} is past the last press in its slides.")
                continue
            # An animation that comes back after a cut to talking head picks up where it left off: its first press
            # this time builds on the presses already shown, not on a blank scene.
            shots.append(dict(animation=p['n'], press=n, type=sc['type'], beat=beat, previous=beat - 1 if j or n > 1 else None,
                              start_f=a, end_f=b, alpha=sc['type'] in ALPHA_TYPES))
    return shots, problems


def fps_of(frame_ticks):
    r = Fraction(ep.TICKS_PER_SECOND, int(frame_ticks))
    return r


def render_shots(shots, deck, out_dir, frame_ticks, scale, ffmpeg, job_dir, log=print):
    """Render every press that isn't already rendered. Cached by deck content, press and length."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fps = fps_of(frame_ticks)
    deck_hash = hashlib.sha256(Path(deck).read_bytes()).hexdigest()[:16]
    contract = dict(motion_frames=round(2.5 * float(fps)), handle_frames=round(5 * float(fps)), scene_selector='.scene', active_selector='.scene.on')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    for n, s in enumerate(shots, 1):
        frames = s['end_f'] - s['start_f']
        hole = camera_hole(s['type']) if s['alpha'] else None
        key = hashlib.sha1(json.dumps([deck_hash, s['beat'], s['previous'], frames, str(fps), s['alpha'], scale, hole]).encode()).hexdigest()[:12]
        out = out_dir / f"anim{s['animation']:02d}-press{s['press']:02d}-{key}{'.mov' if s['alpha'] else '.mp4'}"
        s['asset'] = str(out)
        if out.is_file():
            continue
        log(f"Rendering animation {s['animation']}, press {s['press']} ({n} of {len(shots)})")
        req = Path(job_dir) / f"render-{n:03d}.json"
        req.write_text(json.dumps(dict(deck=str(Path(deck).resolve()), beat=s['beat'], previous=s['previous'], frames=frames, fps=str(fps),
                                       out=str(out), alpha=s['alpha'], scale=scale, job=str(job_dir), ffmpeg=ffmpeg, contract=contract,
                                       hole=hole, border=s['type'] == 'pip')), encoding='utf-8')
        run = subprocess.run([sys.executable, str(RENDER), str(req)], capture_output=True, text=True, encoding='utf-8', timeout=1800, env=env,
                             cwd=str(job_dir), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if run.returncode != 0 or not out.is_file():
            raise ValueError(f"Animation {s['animation']} press {s['press']} would not render: " +
                             ((run.stderr or run.stdout).strip().splitlines() or ['no details'])[-1][:300])
        if hole:
            window_open(out, hole, s)
    return shots


def window_open(out, hole, s):
    """The camera window must really be see-through. Checked on the last rendered frame, since FFmpeg reads
    ProRes 4444 alpha unreliably. A deck that paints over the window would hide Josh in Premiere."""
    from PIL import Image
    report = json.loads(Path(out).with_suffix('.json').read_text(encoding='utf-8'))
    frames = sorted(Path(report['frame_directory']).glob('*.png'))
    if not frames:
        return
    im = Image.open(frames[-1]).convert('RGBA')
    k = im.size[0] / 1920.0
    x, y, w, h = hole
    spots = [(x + w * fx, y + h * fy) for fx in (0.2, 0.5, 0.8) for fy in (0.2, 0.5, 0.8)]
    if any(im.getpixel((int(px * k), int(py * k)))[3] > 8 for px, py in spots):
        raise ValueError(f"Animation {s['animation']} press {s['press']} covers the camera window, so Josh would not show in it.")


def camera_hole(kind):
    """The window the camera copy shows through, in the deck's 1920 by 1080 layout. It is the same box the framing
    below fills, and the same place Josh's house deck puts its #pip and #half placeholders."""
    sys.path.insert(0, str(CUT_HELPER))
    try:
        import style as cut_style
        return cut_style.camera_box(kind, {})
    finally:
        sys.path.remove(str(CUT_HELPER))


def camera_framing(kind, video_w, video_h, frame_h):
    """The PiP and half and half framing Josh Cut Pro uses, so the keyed picture fills the hole in the animation."""
    sys.path.insert(0, str(CUT_HELPER))
    try:
        import pip as cut_pip
        import style as cut_style
        box = cut_style.camera_box(kind, {})
        f = cut_pip.framing(dict(cut_pip.DEFAULT, box=box, border=0), video_w, video_h)
    finally:
        sys.path.remove(str(CUT_HELPER))
    f['scale'] *= frame_h / 1080.0
    return f


def layer_result(shots, frame_ticks, camera_path, video_size, frame_size):
    fps = fps_of(frame_ticks)
    layers = []
    for s in shots:
        n = s['end_f'] - s['start_f']
        layers.append(dict(track=TRACK_ANIMATION, kind='animation', path=s['asset'], start_f=s['start_f'], in_f=0, out_f=n,
                           name=f"Animation {s['animation']} press {s['press']}"))
        if s['type'] in ALPHA_TYPES:
            layers.append(dict(track=TRACK_CAMERA, kind='camera_copy', path=camera_path, start_f=s['start_f'], in_f=0, out_f=n,
                               framing=camera_framing(s['type'], video_size[0], video_size[1], frame_size[1]), hole=camera_hole(s['type']),
                               name=f"Picture for animation {s['animation']}"))
    return dict(layers=layers, fps_num=fps.numerator, fps_den=fps.denominator)
