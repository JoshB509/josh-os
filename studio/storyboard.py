"""Storyboard cards for Tariq's shot shortlist: one portrait image per segment, made for reading on a phone.

Layout (Josh's sketch, revised 11 Nov): one line of tiny grey metadata at the top edge, the finished shot as large as the
width allows at the upper left, up to three of the real build pictures stacked on the right, and the segment's script
underneath in smaller type with each press's words marked and numbered to match the thumbnails. The source file names
stay in the message text, not on the picture. A talking head segment has no frame rendered for review, so its card says
that instead of showing one.

Everything on the card comes from the shortlist (studio/shot_review.build): the pictures render_edit_review.py already
took, in press order, and the shot list's own script text. Nothing is drawn that isn't in those.
"""
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, PAD = 1080, 24
INK, MUTED, LINE, PAPER, GOLD, GOLD_T = '#15171C', '#6C6F77', '#E4E4E1', '#FFFFFF', '#F5C21B', '#FDF1C7'
FONTS = Path('C:/Windows/Fonts')
TOP = 44                            # the one line of grey metadata sits in this strip at the top edge
GAP = 12                            # between the finished shot and the build column, and between thumbnails
MAIN_W, MAIN_H = 768, 432           # 16:9, the finished shot, as big as the width allows beside the column
THUMB_H = (MAIN_H - 2 * GAP) // 3   # three stacked to the main picture's height
THUMB_W = W - 2 * PAD - MAIN_W - GAP
BODY, LINE_H = 34, 48               # script size: smaller than the pictures, still readable when the card fills a phone
TYPE_NAMES = {'pip': 'picture in picture', 'half': 'half and half', 'full': 'full screen', 'talk': 'talking head'}


def font(size, bold=False):
    for name in (('segoeuib.ttf', 'seguisb.ttf') if bold else ('segoeui.ttf',)):
        f = FONTS / name
        if f.is_file():
            return ImageFont.truetype(str(f), size)
    return ImageFont.load_default(size)


def pick_builds(paths):
    """Up to three build pictures, in press order. With more than three: the first, the middle and the last."""
    n = len(paths)
    if n <= 3:
        return list(range(n))
    return sorted({0, n // 2, n - 1})


def fit(img, w, h):
    """Scale to fit inside w by h without cropping, centred on paper."""
    img = img.convert('RGB')
    img.thumbnail((w, h))
    box = Image.new('RGB', (w, h), PAPER)
    box.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
    return box


def pieces(item):
    """The script as drawable pieces: (text, press number or None, bold, space before). Pieces split only where a
    found cue starts or ends, or where the shot list itself marks **bold**, so the words and punctuation stay exactly
    as written. The ** marks are drawn as bold rather than shown."""
    script = item['script']
    cue = [None] * len(script)
    for p in sorted(item['presses'], key=lambda p: p['press'], reverse=True):
        if p['found']:
            for i in range(p['start'], p['end']):
                cue[i] = p['press']
    bold, out, gap = False, [], True
    i = 0
    while i < len(script):
        if script.startswith('**', i):
            bold = not bold
            i += 2
            continue
        if script[i].isspace():
            gap = True
            i += 1
            continue
        start = j = i
        while j < len(script) and not script[j].isspace() and not script.startswith('**', j) and cue[j] == cue[start]:
            j += 1
        out.append((script[start:j], cue[start], bold, gap))
        gap = False
        i = j
    return out


def layout(parts, f, fb, width):
    """Lines of (text, press, bold, x, w), wrapping only at spaces, so a word never breaks across lines."""
    words, cur = [], []
    for t, k, bold, sp in parts:
        if sp and cur:
            words.append(cur)
            cur = []
        cur.append((t, k, bold))
    if cur:
        words.append(cur)
    lines, line, x = [], [], 0.0
    space = f.getlength(' ')
    for word in words:
        w = sum((fb if bold else f).getlength(t) for t, _, bold in word)
        if line and x + w > width:
            lines.append(line)
            line, x = [], 0.0
        for t, k, bold in word:
            tw = (fb if bold else f).getlength(t)
            line.append((t, k, bold, x, tw))
            x += tw
        x += space
    if line:
        lines.append(line)
    return lines


def plain_lines(text, f, width):
    out = []
    for ln in layout([(w, None, False, True) for w in text.split()], f, f, width):
        out.append(' '.join(t for t, *_ in ln))
    return out


def card(item, position, total, title, source, out, test=False):
    """Draw one segment's card to `out` (PNG). Returns the path. `source` stays in the message text, not on the card."""
    f_meta, f_body, f_bold, f_badge, f_note = font(22), font(BODY), font(BODY, True), font(20, True), font(22)
    body_lines = layout(pieces(item), f_body, f_bold, W - 2 * PAD)
    lost = [p for p in item['presses'] if not p['found']]
    notes = []
    if lost:
        notes.append('Not found in the script, so not marked: ' + '; '.join(f'press {p["press"]} "{p["words"]}"' for p in lost) + '.')
    if item.get('missing') and item.get('kind') != 'talk':
        notes.append(item['missing'])
    note_lines = [ln for n in notes for ln in plain_lines(n, f_note, W - 2 * PAD)]
    body_top = TOP + MAIN_H + 22
    H = body_top + len(body_lines) * LINE_H + (10 + len(note_lines) * 30 if note_lines else 0) + PAD
    img = Image.new('RGB', (W, H), PAPER)
    d = ImageDraw.Draw(img)

    # One line of tiny grey metadata, close to the top edge. A long video title is shortened with an ellipsis.
    kind = 'Talking head' if item.get('kind') == 'talk' else f"Animation {item['n']}, {TYPE_NAMES.get(item['type'], item['type'])}"
    meta = f"Shot {position + 1} of {total}. {kind}" + (f", at {item['time']}" if item.get('time') else '') + '.'
    name = ('Test review. ' if test else '') + title
    room = W - 2 * PAD - f_meta.getlength(meta) - 24
    if f_meta.getlength(name) > room:
        while f_meta.getlength(name + '…') > room and len(name) > 1:
            name = name[:-1]
        name = name.rstrip(' (') + '…' if name else ''
    d.text((PAD, 10), meta, font=f_meta, fill=MUTED)
    d.text((W - PAD, 10), name, font=f_meta, fill=MUTED, anchor='ra')

    # The finished shot, as large as the width allows. A missing picture is said, not drawn.
    main = item.get('picture') if item.get('kind') != 'talk' else ''
    if main and Path(main).is_file():
        img.paste(fit(Image.open(main), MAIN_W, MAIN_H), (PAD, TOP))
        d.rectangle([PAD, TOP, PAD + MAIN_W - 1, TOP + MAIN_H - 1], outline=LINE, width=2)
    else:
        d.rectangle([PAD, TOP, PAD + MAIN_W - 1, TOP + MAIN_H - 1], fill='#F5F5F4', outline=LINE, width=2)
        msg = ('No frame from this video is rendered for talking head.' if item.get('kind') == 'talk'
               else item.get('missing') or 'No finished picture.')
        for i, ln in enumerate(plain_lines(msg, f_note, MAIN_W - 60)):
            d.text((PAD + 30, TOP + MAIN_H // 2 - 30 + i * 32), ln, font=f_note, fill=MUTED)

    # Up to three real build pictures, stacked on the right, numbered by press.
    x = PAD + MAIN_W + GAP
    builds = item.get('build_pictures') or []
    # A single build is the finished shot itself, so it isn't repeated as a thumbnail.
    for row, idx in enumerate(pick_builds(builds) if len(builds) > 1 else []):
        y = TOP + row * (THUMB_H + GAP)
        img.paste(fit(Image.open(builds[idx]), THUMB_W, THUMB_H), (x, y))
        last = idx == len(builds) - 1
        d.rectangle([x, y, x + THUMB_W - 1, y + THUMB_H - 1], outline=GOLD if last else LINE, width=3 if last else 2)
        d.ellipse([x + 6, y + 6, x + 34, y + 34], fill=GOLD)
        d.text((x + 20, y + 20), str(item.get('first_press', 1) + idx), font=f_badge, fill=INK, anchor='mm')
    if len(builds) > 3:
        d.text((x + THUMB_W, TOP + MAIN_H + 2), f'{len(builds)} builds, 3 shown', font=font(18), fill=MUTED, anchor='ra')
    elif item.get('kind') != 'talk' and len(builds) == 1:
        d.text((x, TOP + 4), 'Builds in one press.', font=font(20), fill=MUTED)

    # The script underneath. Each found cue is one gold band, numbered at its start to match the thumbnails.
    y = body_top
    for ln in body_lines:
        bands = []
        for t, k, bold, bx, bw in ln:
            if k:
                if bands and bands[-1][0] == k and bx - bands[-1][2] < 16:
                    bands[-1][2] = bx + bw
                else:
                    bands.append([k, bx, bx + bw])
        for k, bx0, bx1 in bands:
            d.rounded_rectangle([PAD + bx0 - 4, y + 3, PAD + bx1 + 4, y + LINE_H - 3], radius=7, fill=GOLD_T)
        for t, k, bold, bx, bw in ln:
            d.text((PAD + bx, y), t, font=f_bold if bold else f_body, fill=INK)
        y += LINE_H
    seen, y = set(), body_top
    for ln in body_lines:
        for t, k, bold, bx, bw in ln:
            if k and k not in seen:
                seen.add(k)
                d.ellipse([PAD + bx - 15, y - 8, PAD + bx + 9, y + 16], fill=GOLD)
                d.text((PAD + bx - 3, y + 4), str(k), font=font(17, True), fill=INK, anchor='mm')
        y += LINE_H
    y += 10
    for ln in note_lines:
        d.text((PAD, y), ln, font=f_note, fill=MUTED)
        y += 30
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out, 'PNG', optimize=True)
    return str(out)
