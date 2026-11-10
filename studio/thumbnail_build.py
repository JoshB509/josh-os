"""Build one YouTube thumbnail with Josh's approved scripts. Run only by Josh OS:

    python thumbnail_build.py <job.json>

job.json is written by video_thumbnails.py after it has checked every value. It holds
{"format": "middle" | "close", "spec": {...}, "out": "<png path inside the video's thumbnails folder>"}.

This file is the whole boundary for what a thumbnail job can run. It imports one approved
build script from the thumbnail tools folder, points that script's file paths at the assets on
this PC (the cloud workspace paths in the scripts don't exist here), and calls its build().
When the spec carries a text style (font, colour, size and so on), the background and pose still
come from the approved scripts and the words are drawn by styled() below, with the same layers
as the house look. Fonts are chosen by name from FONTS, never by path. Nothing else is run,
nothing is read from the job but plain values, every value is checked again here, and the output
path is checked again before anything is written.
"""
import json
import re
import sys
from pathlib import Path

TOOLS = Path(r'E:\CareerOS\12_YouTube\_thumbnail-system\tools')
TEMPLATE = Path(r'C:\Users\Anigma PC\Documents\Codex\2026-10-24\task\Josh Photoshop Template')
FONT = Path(r'C:\Users\Anigma PC\Downloads\Josh Thumbnail Font Kit v2\Josh Thumbnail Font Kit v2\Font\JoshThumbnailV2-BoldItalic.ttf')
YOUTUBE = Path(r'E:\CareerOS\12_YouTube')
# format -> the approved script's module name in TOOLS
FORMATS = {'middle': 'oneline', 'close': 'close_unit'}
BACKGROUNDS = {
    'seated': TEMPLATE / 'final-scenes' / 'Scene Assets' / 'Backgrounds' / '01-seated-office.png',
    'standing': TEMPLATE / 'final-scenes' / 'Scene Assets' / 'Backgrounds' / '02-standing-office.png',
    'close': TEMPLATE / 'final-scenes' / 'Scene Assets' / 'Backgrounds' / '03-closeup-office.png',
}

USER_FONTS = Path(r'C:\Users\Anigma PC\AppData\Local\Microsoft\Windows\Fonts')
WIN_FONTS = Path(r'C:\Windows\Fonts')
CUT_FONTS = Path(r'E:\Videos\DaJoshiCut\templates\fonts')
# The fonts a thumbnail can use, by name. Weights run light to heavy, and the last one is the default.
# A font whose file is missing is refused with its path, never swapped for another.
FONTS = {
    'josh': dict(label='Josh Thumbnail V2, the house font (bold italic)', weights={'bold': FONT}),
    'anton': dict(label='Anton, tall condensed caps', weights={'regular': CUT_FONTS / 'Anton-Regular.woff2'}),
    'impact': dict(label='Impact, heavy condensed', weights={'regular': WIN_FONTS / 'impact.ttf'}),
    'gotham': dict(label='Gotham, clean geometric', weights={'book': WIN_FONTS / 'Gotham-Book.otf', 'black': WIN_FONTS / 'Gotham-Black.otf'}),
    'franie': dict(label='Franie, round geometric', weights={
        'regular': USER_FONTS / 'Franie-Regular.ttf', 'semibold': USER_FONTS / 'Franie-SemiBold.ttf', 'bold': USER_FONTS / 'Franie-Bold.ttf',
        'extrabold': USER_FONTS / 'Franie-ExtraBold.ttf', 'black': USER_FONTS / 'Franie-Black.ttf'}),
    'chivo': dict(label='Chivo Black, wide and punchy', weights={'black': USER_FONTS / 'Chivo-Black.ttf'}),
    'arial': dict(label='Arial, plain', weights={'bold': WIN_FONTS / 'arialbd.ttf', 'black': WIN_FONTS / 'ariblk.ttf'}),
    'segoe': dict(label='Segoe UI, plain modern', weights={'bold': WIN_FONTS / 'segoeuib.ttf', 'black': WIN_FONTS / 'seguibl.ttf'}),
    'poppins': dict(label='Poppins, friendly rounded', weights={'semibold': CUT_FONTS / 'Poppins-SemiBold.woff2', 'extrabold': CUT_FONTS / 'Poppins-ExtraBold.woff2'}),
    'manrope': dict(label='Manrope, the Josh OS font', weights={'bold': USER_FONTS / 'Manrope-Bold.ttf', 'extrabold': USER_FONTS / 'Manrope-ExtraBold.ttf'}),
    'oswald': dict(label='Oswald, narrow', weights={'regular': USER_FONTS / 'Oswald-Regular.ttf'}),
    'tall-king': dict(label='Tall King, very tall and narrow', weights={'black': USER_FONTS / 'Tall King-Black.otf'}),
    'marker': dict(label='Street Marker, hand drawn', weights={'regular': USER_FONTS / 'Street Marker.otf'}),
    'fraunces': dict(label='Fraunces, bold serif', weights={'semibold': CUT_FONTS / 'Fraunces-SemiBold.woff2', 'extrabold': CUT_FONTS / 'Fraunces-ExtraBold.woff2'}),
}
COLOURS = {
    'white': '#FFFDF5', 'yellow': '#F4CF30', 'bright-yellow': '#FFE500', 'gold': '#F5C21B', 'red': '#E2584C',
    'green': '#43B37A', 'black': '#0C0D10', 'orange': '#FF8A1F', 'blue': '#3D8BFF',
}
# name: (default, low, high)
STYLE_NUMBERS = {'size': (200, 60, 360), 'outline': (0, 0, 14), 'bolden': (0, 0, 6)}
WORDS_RE = r"[\w ',.!?&%$£€:+\-]+"
LINE_GAP = 0.92      # baseline to baseline, as a share of the font size (same as the close format)
MIN_SIZE = 60
EDGE = 40            # words stay this far inside the frame


def colour(value):
    v = str(value or '').strip()
    v = COLOURS.get(v.lower(), v)
    if not re.fullmatch(r'#[0-9A-Fa-f]{6}', v):
        raise ValueError(f"Colour must be one of {', '.join(COLOURS)} or a hex code like #FFE500.")
    return v.upper()


def font_file(name, weight):
    entry = FONTS.get(name)
    if not entry:
        raise ValueError(f"Font must be one of {', '.join(FONTS)}.")
    path = entry['weights'].get(weight)
    if not path:
        raise ValueError(f"{name} comes in {', '.join(entry['weights'])}, not {weight}.")
    if not path.is_file():
        raise ValueError(f"The {name} {weight} font file is missing from this PC. It should be at {path}.")
    return path


def check_style(style):
    """A text style, checked. Used by video_thumbnails.py before the job and again here before drawing."""
    if not isinstance(style, dict):
        raise ValueError('The style must be a set of settings.')
    out = {}
    name = str(style.get('font') or 'josh')
    if name not in FONTS:
        raise ValueError(f"Font must be one of {', '.join(FONTS)}.")
    out['font'] = name
    out['weight'] = str(style.get('weight') or list(FONTS[name]['weights'])[-1])
    font_file(name, out['weight'])
    for key, (default, low, high) in STYLE_NUMBERS.items():
        v = style.get(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not low <= v <= high:
            raise ValueError(f'{key} must be a number between {low} and {high}.')
        out[key] = v
    out['color'] = colour(style.get('color', 'white'))
    out['bar'] = style.get('bar', out['color'] == COLOURS['white'])
    out['bar_color'] = colour(style.get('bar_color', 'yellow'))
    out['outline_color'] = colour(style.get('outline_color', 'black'))
    out['shadow'] = style.get('shadow', True)
    for key in ('bar', 'shadow'):
        if not isinstance(out[key], bool):
            raise ValueError(f'{key} must be true or false.')
    out['case'] = style.get('case', 'as-is')
    if out['case'] not in ('as-is', 'upper'):
        raise ValueError('case must be as-is or upper.')
    out['fit'] = style.get('fit', 'size')
    if out['fit'] not in ('size', 'fill'):
        raise ValueError('fit must be size or fill.')
    if out['bar'] and out['bar_color'] == out['color']:
        raise ValueError('The words and the bar behind them can\'t be the same colour.')
    return out


def rgb(hex_code):
    return tuple(int(hex_code[i:i + 2], 16) for i in (1, 3, 5))


def layout(lines, style, fmt, box):
    """Pick the font size: the asked size (or the size that fills the bar), shrunk until every line fits
    inside the box and the frame. Returns (font, size, baselines). Refuses words that can't fit at the smallest size."""
    from PIL import ImageFont
    path = str(font_file(style['font'], style['weight']))
    grow = style['bolden'] + style['outline']
    widest = box['right'] - box['left']

    def measure(size):
        f = ImageFont.truetype(path, round(size))
        widths = [f.getbbox(t, anchor='ls', stroke_width=grow)[2] - f.getbbox(t, anchor='ls', stroke_width=grow)[0] for t in lines]
        asc = -min(f.getbbox(t, anchor='ls', stroke_width=grow)[1] for t in lines)
        desc = max(f.getbbox(t, anchor='ls', stroke_width=grow)[3] for t in lines)
        return f, widths, asc, desc

    size = style['size']
    if style['fit'] == 'fill':
        f, widths, _, _ = measure(200)
        size = min(STYLE_NUMBERS['size'][2], 200 * (widest - (54 if style['bar'] else 0)) / max(widths))
    while True:
        f, widths, asc, desc = measure(size)
        gap = size * LINE_GAP
        if fmt == 'middle':      # lines stack up from the baseline
            baselines = [box['baseline'] - (len(lines) - 1 - i) * gap for i in range(len(lines))]
        else:                    # lines run down from the first baseline
            baselines = [box['baseline'] + i * gap for i in range(len(lines))]
        pad = 27 if style['bar'] else 0
        fits = (max(widths) + 2 * pad <= widest and baselines[0] - asc >= EDGE
                and baselines[-1] + max(desc, (0.144 * size) if style['bar'] else 0) <= 1080 - EDGE / 2)
        if fits:
            return f, round(size, 1), baselines
        if size <= MIN_SIZE:
            raise ValueError('These words are too long to fit even at the smallest size. Use fewer words.')
        size = max(MIN_SIZE, size * 0.97)


def styled(fmt, spec, out):
    """The house layers (dark glow, optional bar, drop shadow, words) with the chosen font, colours and size."""
    from PIL import Image, ImageDraw
    import thumb
    import oneline
    W, H = oneline.W, oneline.H
    style = check_style(spec['style'])
    lines = spec['lines']
    canvas = Image.open(thumb.BG[spec['scene']]).convert('RGBA').resize((W, H), Image.LANCZOS)
    p = spec['pose']
    if fmt == 'middle':
        pose_log = thumb.place_pose(canvas, p['path'], p['face_cx'], head_top=p['head_top'], body_bottom=p['body_bottom'], desk_top=p['desk_top'])
        box = dict(left=spec['bar_left'], right=spec['bar_right'], baseline=spec['baseline'], cx=(spec['bar_left'] + spec['bar_right']) / 2)
    else:
        pose_log = thumb.place_pose(canvas, p['path'], p['face_cx'], p['face_cy'], p['face_w'])
        box = dict(left=1297 - 560, right=min(1880, 1297 + 560), baseline=470, cx=1297)
    canvas.alpha_composite(thumb.bottom_shade(spec.get('shade_start', 400)))
    f, size, baselines = layout(lines, style, fmt, box)

    words = Image.new('L', (W, H), 0)
    edge = Image.new('L', (W, H), 0)
    bars = []
    for text, base in zip(lines, baselines):
        l, _, r, _ = f.getbbox(text, anchor='ls', stroke_width=style['bolden'])
        x = box['cx'] - (l + r) / 2
        ImageDraw.Draw(words).text((x, base), text, font=f, fill=255, anchor='ls', stroke_width=style['bolden'], stroke_fill=255)
        if style['outline']:
            w = style['bolden'] + style['outline']
            ImageDraw.Draw(edge).text((x, base), text, font=f, fill=255, anchor='ls', stroke_width=w, stroke_fill=255)
        bars.append((round(box['cx'] - (r - l) / 2 - 27), round(base - oneline.BAR_UP * size), round(box['cx'] + (r - l) / 2 + 27),
                     round(base + oneline.BAR_DOWN * size)))
    ink = edge if style['outline'] else words
    if style['shadow']:
        from PIL import ImageFilter
        glow = ink.filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.GaussianBlur(34)).point(lambda v: round(v * 0.55))
        canvas.alpha_composite(oneline.solid(glow, (0, 0, 0)))
    if style['bar']:
        bl = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        for b in bars:
            ImageDraw.Draw(bl).rectangle(b, fill=rgb(style['bar_color']) + (255,))
        canvas.alpha_composite(bl)
    if style['shadow']:
        oneline.drop_shadow(canvas, ink)
    if style['outline']:
        canvas.alpha_composite(oneline.solid(edge, rgb(style['outline_color'])))
    canvas.alpha_composite(oneline.solid(words, rgb(style['color'])))
    canvas.convert('RGB').save(out, optimize=True)
    return dict(pose=pose_log, size=size, asked=style['size'] if style['fit'] == 'size' else None, ink=words.getbbox(), bars=bars if style['bar'] else [])


def main(job_path):
    job = json.loads(Path(job_path).read_text(encoding='utf-8'))
    fmt = job['format']
    module = FORMATS[fmt]
    out = Path(job['out']).resolve()
    # The output must be a new PNG in a video's own thumbnails folder.
    if out.suffix != '.png' or out.parent.name != 'thumbnails' or out.parent.parent.parent != YOUTUBE.resolve() or out.exists():
        raise SystemExit('Refused: the output must be a new .png in a video\'s thumbnails folder.')
    pose = Path(job['spec']['pose']['path']).resolve()
    if not pose.is_relative_to((TEMPLATE / 'Pose Catalogue').resolve()) or pose.suffix.lower() != '.png' or not pose.is_file():
        raise SystemExit('Refused: the pose must be a cutout from the Pose Catalogue.')
    if job['spec']['scene'] not in BACKGROUNDS:
        raise SystemExit('Refused: unknown scene.')
    style = job['spec'].get('style')
    if style is not None:
        lines = job['spec'].get('lines')
        if not isinstance(lines, list) or not 1 <= len(lines) <= 2 or not all(
                isinstance(t, str) and 0 < len(t) <= 40 and re.fullmatch(WORDS_RE, t) for t in lines):
            raise SystemExit('Refused: the words must be one or two plain lines.')
        try:
            check_style(style)
        except ValueError as e:
            raise SystemExit(f'Refused: {e}')
    sys.path.insert(0, str(TOOLS))
    import cv2
    import thumb
    thumb.U = str(TEMPLATE)
    thumb.BG = {k: str(v) for k, v in BACKGROUNDS.items()}
    thumb.FONT = str(FONT)
    thumb._casc = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    import oneline
    oneline.FONT = str(FONT)
    if style is not None:
        try:
            result = styled(fmt, job['spec'], str(out))
        except ValueError as e:
            raise SystemExit(str(e))
    else:
        builder = __import__(module)
        builder.FONT = str(FONT)
        result = builder.build(job['spec'], str(out))
    print(json.dumps(dict(out=str(out), result=result), default=str))


if __name__ == '__main__':
    main(sys.argv[1])
