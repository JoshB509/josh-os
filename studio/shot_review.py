"""Tariq's shot shortlist: the video as a run of segments, in script order, sent as ONE Discord message that Josh pages
through with Previous and Next (each press edits that same message).

A segment is exactly one part of EditReview's reading of the shot list: a whole talking head passage, or a whole stretch
on one animation (picture in picture, half and half or full screen) shown fully built, however many presses build it.
Segments are never merged or split beyond what the shot list says, and together they cover the whole script.

It is built only from what Tariq already made for the video, read through EditReview:
- the newest slides deck and shot list in the video's folder (their exact file names and content hashes are kept, so a
  review always says which version it shows and can be checked again before it goes out);
- the pictures render_edit_review.py already took, one per press. The last one is the shot fully built, so that is the
  only picture sent. Nothing is rendered or changed here;
- the shot list's own script text for that segment, unchanged, with the words of each "Press N on ..." marked where
  they actually appear in it;
- for talking head, no picture: the footage isn't rendered for review, so the segment says so rather than showing a
  stand-in.

Nothing is guessed. A press whose words can't be found in the script is listed as not found and isn't marked. An
animation with no picture, or whose pictures were taken from an older deck, says so instead of showing one.
"""
import difflib
import hashlib
import re

from .edit_review import _clean, parse_scenes

TYPE_NAMES = {'pip': 'picture in picture', 'half': 'half and half', 'full': 'full screen'}


def locate(script, words):
    """(start, end) of the press words in the script, matching every word in order, ignoring case and punctuation."""
    i = script.lower().find(words.lower().strip())
    if i >= 0 and words.strip():
        return i, i + len(words.strip())
    tokens = re.findall(r"[a-z0-9']+", words.lower())
    if not tokens:
        return None
    m = re.compile(r'\W+'.join(re.escape(t) for t in tokens), re.I).search(script)
    return (m.start(), m.end()) if m else None


def marked(script, spans, mark=('**', '**')):
    """The script with each found press marked. Overlapping spans are merged; the words themselves are unchanged."""
    out, last = [], 0
    for s, e in sorted(merge(spans)):
        out += [script[last:s], mark[0], script[s:e], mark[1]]
        last = e
    return ''.join(out + [script[last:]])


def merge(spans):
    spans = sorted(spans)
    out = []
    for s, e in spans:
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def build(er, pid):
    """The shortlist for one video, with its exact source version."""
    slides, shots = er.deck_files(pid)
    if not slides or not shots:
        raise ValueError("This video doesn't have both a slides deck and a shot list yet, so there's no shortlist to send.")
    html = slides.read_text(encoding='utf-8')
    md = shots.read_text(encoding='utf-8')
    review = er.review(pid)
    out_dir = er.out_folder(pid)
    source = dict(project_id=pid, slides=slides.name, slides_hash=hashlib.sha1(html.encode('utf-8')).hexdigest()[:12],
                  shot_list=shots.name, shot_list_hash=hashlib.sha1(md.encode('utf-8')).hexdigest()[:12],
                  rendered=review.get('rendered'), stale=bool(review.get('stale')), folder=str(slides.parent))
    scenes = {s['n']: s for s in parse_scenes(html)}
    items, section = [], ''
    for p in review['parts']:
        if p['kind'] == 'section':
            section = p.get('title') or ''
            continue
        script = p['script']
        if p['kind'] == 'talk':
            items.append(dict(kind='talk', n=None, time=p.get('time') or '', type='talk', desc='', section=section, script=script,
                              presses=[], marked=script, picture='', picture_name='', standin=False, build_pictures=[],
                              missing='No frame from this video is rendered for talking head, so none is shown.'))
            continue
        presses, spans = [], []
        for x in p.get('presses') or []:
            at = locate(script, x['words'])
            presses.append(dict(press=x['press'], words=x['words'], found=bool(at), start=at[0] if at else None, end=at[1] if at else None))
            if at:
                spans.append(at)
        images = p.get('images') or []
        picture = str(out_dir / images[-1]) if images and (out_dir / images[-1]).is_file() else ''
        missing = ('' if picture else 'The pictures were taken from an older version of the deck, so none is shown.' if source['stale']
                   else 'No picture has been taken for this animation yet.' if not source['rendered'] else 'The finished picture for this animation is missing.')
        items.append(dict(kind='animation', n=p['n'], time=p.get('time') or '', type=p.get('type') or scenes.get(p['n'], {}).get('type', 'full'),
                          desc=p.get('desc') or '', section=section, script=script, presses=presses, marked=marked(script, spans),
                          picture=picture, picture_name=images[-1] if picture else '', standin=False, missing=missing,
                          builds=len(images), first_press=p.get('first_press', 1),
                          build_pictures=[str(out_dir / x) for x in images if (out_dir / x).is_file()] if picture else []))
    if not items:
        raise ValueError('The shot list has no script rows, so there are no shots to send.')
    source['coverage'] = coverage(md, items)
    return dict(source=source, items=items, name=er.store.get(pid).get('name', ''))


def script_rows(md):
    """The script text of every shot list row, in order, read the same way EditReview reads it."""
    rows = []
    for line in md.splitlines():
        if not line.startswith('|') or line.startswith('|---') or line.startswith('| Script'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) < 2:
            continue
        if re.match(r'\*\*(\d+)\s*·\s*(.+?)\*\*$', cells[0]) and not cells[1].replace('**', '').strip():
            continue
        tc = re.match(r'(\d+:\d{2})\s*·\s*(.*)', cells[0])
        rows.append(_clean(tc.group(2)) if tc else _clean(cells[0]))
    return rows


def coverage(md, items):
    """How much of the shot list's script the segments carry, word for word and in order."""
    words = lambda t: re.findall(r"[a-z0-9']+", t.lower())
    full, ours = words(' '.join(script_rows(md))), words(' '.join(i['script'] for i in items))
    matched = sum(b.size for b in difflib.SequenceMatcher(None, full, ours, autojunk=False).get_matching_blocks())
    return dict(script_words=len(full), segment_words=len(ours), matched=matched, complete=full == ours)


def same_source(er, pid, source):
    """Whether the deck and shot list on disk are still exactly the version a shortlist was built from."""
    slides, shots = er.deck_files(pid)
    if not slides or not shots:
        return False
    return (slides.name == source['slides'] and shots.name == source['shot_list']
            and hashlib.sha1(slides.read_text(encoding='utf-8').encode('utf-8')).hexdigest()[:12] == source['slides_hash']
            and hashlib.sha1(shots.read_text(encoding='utf-8').encode('utf-8')).hexdigest()[:12] == source['shot_list_hash'])
