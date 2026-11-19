"""Fixed offline Pillow worker for local assets; never a browser or model tool.

Invoked by LocalAssets in an isolated Python subprocess with a fixed job file.
Only generated PNG input filenames are read. No networking, shell, SVG/HTML,
plugins, user fonts, dynamic imports, publishing or application-store access.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys

from PIL import Image, ImageDraw, ImageFont, ImageOps

if __package__:
    from . import local_asset_spec as spec
else:
    # -I excludes the script directory. Add only this host-owned module directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import local_asset_spec as spec


FONT_NAMES = {
    ('sans', False): ('arial.ttf', 'DejaVuSans.ttf'),
    ('sans', True): ('arialbd.ttf', 'DejaVuSans-Bold.ttf'),
    ('serif', False): ('georgia.ttf', 'DejaVuSerif.ttf'),
    ('serif', True): ('georgiab.ttf', 'DejaVuSerif-Bold.ttf'),
    ('mono', False): ('consola.ttf', 'DejaVuSansMono.ttf'),
    ('mono', True): ('consolab.ttf', 'DejaVuSansMono-Bold.ttf'),
}


def font_file(family, bold):
    names = FONT_NAMES[(family, bold)]
    roots = [Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts',
             Path('/usr/share/fonts/truetype/dejavu'), Path('/usr/share/fonts/dejavu')]
    for root in roots:
        for name in names:
            file = root / name
            if file.is_file():
                return file
    raise ValueError('A configured local font is unavailable: ' + family)


def wrapped(text, font, width):
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for word in paragraph.split():
            candidate = line + (' ' if line else '') + word
            if font.getlength(candidate) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            line = ''
            for char in word:
                if line and font.getlength(line + char) > width:
                    lines.append(line)
                    line = ''
                line += char
        lines.append(line)
    return lines


def fit_text(layer, fonts):
    file = font_file(layer['font'], layer['bold'])
    fonts[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
    best = None
    lo, hi = layer['min_size'], layer['size']
    while lo <= hi:
        size = (lo + hi) // 2
        font = ImageFont.truetype(str(file), size)
        lines = wrapped(layer['text'], font, layer['width'])
        step = round(size * 1.3)
        fits = len(lines) * step <= layer['height'] and all(font.getlength(x) <= layer['width'] for x in lines)
        if fits:
            best = font, lines, step
            lo = size + 1
        else:
            hi = size - 1
    if best is None:
        raise ValueError('Text does not fit its box at the minimum font size.')
    return best


def draw_text(draw, layer, fonts):
    font, lines, step = fit_text(layer, fonts)
    for n, line in enumerate(lines):
        used = font.getlength(line)
        shift = 0 if layer['align'] == 'left' else (layer['width'] - used) / (2 if layer['align'] == 'center' else 1)
        top = font.getbbox(line or 'Ag')[1]
        draw.text((layer['x'] + shift, layer['y'] + n * step - top), line, font=font, fill=layer['color'])


def render_scene(scene, inputs, fonts):
    canvas = Image.new('RGBA', (scene['width'], scene['height']), scene['background'])
    layers = list(scene['layers'])
    if scene['branding']:
        margin = max(8, min(scene['width'], scene['height']) // 30)
        height = min(48, max(20, scene['height'] // 12))
        layers.append(dict(kind='text', opacity=1, x=margin, y=scene['height'] - height - margin,
            width=scene['width'] - 2 * margin, height=height, text=scene['branding']['text'],
            size=min(24, height // 2), min_size=8, font='sans', bold=False,
            color=scene['branding']['color'], align='right'))
    for layer in layers:
        overlay = Image.new('RGBA', canvas.size)
        draw = ImageDraw.Draw(overlay)
        kind = layer['kind']
        if kind in ('rect', 'ellipse'):
            box = (layer['x'], layer['y'], layer['x'] + layer['width'] - 1, layer['y'] + layer['height'] - 1)
            kwargs = dict(fill=layer['fill'], outline=layer['stroke'] if layer['stroke_width'] else None,
                          width=layer['stroke_width'])
            if kind == 'rect':
                draw.rounded_rectangle(box, radius=layer['radius'], **kwargs)
            else:
                draw.ellipse(box, **kwargs)
        elif kind == 'line':
            if layer['stroke_width']:
                draw.line([tuple(x) for x in layer['points']], fill=layer['stroke'], width=layer['stroke_width'], joint='curve')
        elif kind == 'polygon':
            points = [tuple(x) for x in layer['points']]
            draw.polygon(points, fill=layer['fill'])
            if layer['stroke_width']:
                draw.line(points + points[:1], fill=layer['stroke'], width=layer['stroke_width'], joint='curve')
        elif kind == 'text':
            draw_text(draw, layer, fonts)
        else:
            with Image.open(io.BytesIO(inputs[layer['asset_id']])) as picture:
                picture = picture.convert('RGBA')
                size = (layer['width'], layer['height'])
                picture = (ImageOps.fit if layer['fit'] == 'cover' else ImageOps.contain)(picture, size, Image.Resampling.LANCZOS)
                x = layer['x'] + (layer['width'] - picture.width) // 2
                y = layer['y'] + (layer['height'] - picture.height) // 2
                overlay.alpha_composite(picture, (x, y))
        if layer['opacity'] != 1:
            overlay.putalpha(overlay.getchannel('A').point(lambda x: round(x * layer['opacity'])))
        canvas = Image.alpha_composite(canvas, overlay)
    data = io.BytesIO()
    canvas.save(data, format='PNG')
    return data.getvalue()


def read_input(folder, entry):
    spec.fields(entry, {'file', 'sha256'}, {'file', 'sha256'}, 'worker input')
    name = entry['file']
    if not isinstance(name, str) or not re.fullmatch(r'input_[0-9]{2}\.png', name):
        raise ValueError('Invalid input filename.')
    path = folder / name
    if path.resolve() != path or path.is_symlink():
        raise ValueError('Input links are not supported.')
    with path.open('rb') as handle:
        raw = handle.read(spec.MAX_INPUT_BYTES + 1)
    if len(raw) > spec.MAX_INPUT_BYTES or hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ValueError('Input hash or size mismatch.')
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('Source image must be PNG.')
    with Image.open(io.BytesIO(raw)) as im:
        if im.format != 'PNG' or im.width * im.height > spec.MAX_PIXELS or getattr(im, 'n_frames', 1) != 1:
            raise ValueError('Invalid source PNG.')
        im.verify()
    return raw


def main(job_path):
    path = Path(job_path).resolve()
    folder = path.parent
    with path.open('rb') as handle:
        raw = handle.read(spec.MAX_MANIFEST_BYTES + 1)
    if len(raw) > spec.MAX_MANIFEST_BYTES:
        raise ValueError('Job is too large.')
    job = json.loads(raw)
    spec.fields(job, {'visuals', 'inputs', 'renderer'}, {'visuals', 'inputs', 'renderer'}, 'render job')
    if job['renderer'] != spec.RENDERER_VERSION:
        raise ValueError('Unknown renderer version.')
    scenes = spec.validate_request({'visuals': job['visuals']})
    expected = {x['asset_id'] for s in scenes for x in s['layers'] if x['kind'] == 'image'}
    if not isinstance(job['inputs'], dict) or set(job['inputs']) != expected:
        raise ValueError('Unexpected worker inputs.')
    inputs = {key: read_input(folder, val) for key, val in job['inputs'].items()}
    if sum(map(len, inputs.values())) > spec.MAX_TOTAL_INPUT_BYTES:
        raise ValueError('Inputs exceed the byte budget.')
    fonts, total = {}, 0
    for index, scene in enumerate(scenes):
        png = render_scene(scene, inputs, fonts)
        total += len(png)
        if len(png) > spec.MAX_PNG_BYTES or total > spec.MAX_TOTAL_PNG_BYTES:
            raise ValueError('Rendered PNGs exceed the byte budget.')
        with (folder / f'render_{index:02d}.png').open('xb') as handle:
            handle.write(png)
    with (folder / 'render_metadata.json').open('x', encoding='utf-8') as handle:
        json.dump({'renderer': spec.RENDERER_VERSION, 'fonts': fonts}, handle, sort_keys=True)


if __name__ == '__main__':
    main(sys.argv[1])
