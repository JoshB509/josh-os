"""Bounded, renderer-independent static scenes. No paths, HTML, code or URLs.

Templates choose defaults only: callers can override canvas, palette and every
layer. This first dialect uses shapes/text/raster layers; raw SVG is deliberately
not accepted. A future SVG adapter must preserve the same resource boundary.
"""
import json
import math
import re

MAX_BATCH = 16
MAX_PIXELS = 8_000_000
MAX_BATCH_PIXELS = 32_000_000
MAX_LAYERS = 128
MAX_BATCH_LAYERS = 512
MAX_REQUEST_BYTES = 256_000
MAX_INPUTS = 16
MAX_INPUT_BYTES = 8_000_000
MAX_TOTAL_INPUT_BYTES = 32_000_000
MAX_PNG_BYTES = 10_000_000
MAX_TOTAL_PNG_BYTES = 64_000_000
MAX_MANIFEST_BYTES = 512_000
SCHEMA_VERSION = 1
RENDERER_VERSION = 'local-pillow-scenes-v1'
PALETTES = {
    'paper': ('#F6F5F1', '#15171C'),
    'plain': ('#FFFFFF', '#15171C'),
    'dark': ('#15171C', '#FFFFFF'),
}
ASSET_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}')
HASH = re.compile(r'[0-9a-f]{64}')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def fields(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - set(allowed) or not set(required) <= set(value):
        raise ValueError('Invalid ' + label + ' fields.')


def number(value, lo, hi, label, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not lo <= value <= hi or (type(value) is float and not math.isfinite(value)):
        raise ValueError('Invalid ' + label + '.')
    return value


def text(value, limit, label, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()) or any(ord(c) < 32 and c != '\n' for c in value):
        raise ValueError('Invalid ' + label + '.')
    return value


def color(value):
    if value == 'transparent':
        return '#00000000'
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?', value):
        raise ValueError('Use a six/eight-digit hex color or transparent.')
    return value.upper()


def layer(item, width, height, ink):
    common = {'kind', 'opacity'}
    box = {'x', 'y', 'width', 'height'}
    extra = {
        'rect': box | {'fill', 'stroke', 'stroke_width', 'radius'},
        'ellipse': box | {'fill', 'stroke', 'stroke_width'},
        'line': {'points', 'stroke', 'stroke_width'},
        'polygon': {'points', 'fill', 'stroke', 'stroke_width'},
        'text': box | {'text', 'size', 'min_size', 'font', 'bold', 'color', 'align'},
        'image': box | {'asset_id', 'fit'},
    }
    if not isinstance(item, dict) or item.get('kind') not in extra:
        raise ValueError('Unsupported layer kind.')
    kind = item['kind']
    required = {'kind'} | (box if kind not in ('line', 'polygon') else {'points'})
    required |= {'text'} if kind == 'text' else {'asset_id'} if kind == 'image' else set()
    fields(item, common | extra[kind], required, 'layer')
    out = dict(kind=kind, opacity=number(item.get('opacity', 1), 0, 1, 'opacity'))
    if kind not in ('line', 'polygon'):
        for key, lo, hi in (('x', -width, width * 2), ('y', -height, height * 2),
                            ('width', 1, width * 2), ('height', 1, height * 2)):
            out[key] = number(item[key], lo, hi, key, integer=True)
        if out['width'] * out['height'] > MAX_PIXELS:
            raise ValueError('Layer exceeds the pixel budget.')
    else:
        points = item['points']
        if not isinstance(points, list) or not (2 if kind == 'line' else 3) <= len(points) <= 256:
            raise ValueError('Use a bounded list of points.')
        out['points'] = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError('Invalid point.')
            out['points'].append([number(point[0], -width, width * 2, 'point x', True),
                                  number(point[1], -height, height * 2, 'point y', True)])
    if kind in ('rect', 'ellipse', 'line', 'polygon'):
        out.update(stroke=color(item.get('stroke', ink if kind == 'line' else 'transparent')),
                   stroke_width=number(item.get('stroke_width', 3 if kind == 'line' else 0), 0, 64, 'stroke width', True))
        if kind != 'line':
            out['fill'] = color(item.get('fill', ink))
        if kind == 'rect':
            out['radius'] = number(item.get('radius', 0), 0, min(out['width'], out['height']) // 2, 'radius', True)
    elif kind == 'text':
        font = item.get('font', 'sans')
        align = item.get('align', 'left')
        bold = item.get('bold', False)
        if font not in ('sans', 'serif', 'mono') or align not in ('left', 'center', 'right') or type(bold) is not bool:
            raise ValueError('Invalid text font or alignment.')
        size = number(item.get('size', 40), 8, 256, 'font size', True)
        out.update(text=text(item['text'], 2000, 'layer text'), size=size,
                   min_size=number(item.get('min_size', min(size, 16)), 8, size, 'minimum font size', True),
                   font=font, bold=bold, color=color(item.get('color', ink)), align=align)
    else:
        aid, fit = item['asset_id'], item.get('fit', 'contain')
        if not isinstance(aid, str) or not ASSET_ID.fullmatch(aid) or fit not in ('contain', 'cover'):
            raise ValueError('Use an authorized asset id and contain/cover fit.')
        out.update(asset_id=aid, fit=fit)
    return out


def visual(spec):
    fields(spec, {'title', 'alt', 'width', 'height', 'template', 'background', 'branding', 'layers'},
           {'alt', 'layers'}, 'visual')
    template = spec.get('template', 'paper')
    if not isinstance(template, str) or template not in PALETTES:
        raise ValueError('Unknown template.')
    background, ink = PALETTES[template]
    width = number(spec.get('width', 1200), 64, 4096, 'canvas width', True)
    height = number(spec.get('height', 675), 64, 4096, 'canvas height', True)
    if width * height > MAX_PIXELS:
        raise ValueError('Canvas exceeds the pixel budget.')
    layers = spec['layers']
    if not isinstance(layers, list) or not 1 <= len(layers) <= MAX_LAYERS:
        raise ValueError('Use 1-128 layers.')
    out = dict(title=text(spec.get('title', ''), 120, 'title', True),
               alt=text(spec['alt'], 1000, 'alt text'), width=width, height=height,
               template=template, background=color(spec.get('background', background)),
               branding=None, layers=[layer(x, width, height, ink) for x in layers])
    brand = spec.get('branding')
    if brand is not None:
        fields(brand, {'text', 'color'}, {'text'}, 'branding')
        out['branding'] = dict(text=text(brand['text'], 80, 'branding text'), color=color(brand.get('color', ink)))
    if sum(len(x.get('text', '')) for x in out['layers']) > 12_000:
        raise ValueError('Visual has too much text.')
    return out


def validate_request(request):
    fields(request, {'visuals'}, {'visuals'}, 'creation request')
    try:
        size = len(canonical(request))
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ValueError('Request must be finite JSON data.') from None
    if size > MAX_REQUEST_BYTES:
        raise ValueError('Creation request is too large.')
    items = request['visuals']
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH:
        raise ValueError('Create between one and sixteen visuals.')
    result = [visual(item) for item in items]
    if sum(x['width'] * x['height'] for x in result) > MAX_BATCH_PIXELS:
        raise ValueError('Batch exceeds the pixel budget.')
    if sum(len(x['layers']) for x in result) > MAX_BATCH_LAYERS:
        raise ValueError('Batch has too many layers.')
    if len({x['asset_id'] for s in result for x in s['layers'] if x['kind'] == 'image'}) > MAX_INPUTS:
        raise ValueError('Batch has too many source images.')
    return result


# Deliberately separate from email/carousel schemas. Enforce validate_request at
# dispatch even if a caller ignores this model-facing schema.
LAYER_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['kind'],
    'properties': {
        'kind': {'type': 'string', 'enum': ['rect', 'ellipse', 'line', 'polygon', 'text', 'image']},
        'opacity': {'type': 'number', 'minimum': 0, 'maximum': 1},
        **{k: {'type': 'integer'} for k in ('x', 'y', 'width', 'height', 'radius', 'stroke_width', 'size', 'min_size')},
        **{k: {'type': 'string'} for k in ('fill', 'stroke', 'color')},
        'text': {'type': 'string', 'maxLength': 2000}, 'bold': {'type': 'boolean'},
        'font': {'type': 'string', 'enum': ['sans', 'serif', 'mono']},
        'align': {'type': 'string', 'enum': ['left', 'center', 'right']},
        'asset_id': {'type': 'string', 'maxLength': 80},
        'fit': {'type': 'string', 'enum': ['contain', 'cover']},
        'points': {'type': 'array', 'minItems': 2, 'maxItems': 256, 'items': {
            'type': 'array', 'minItems': 2, 'maxItems': 2, 'items': {'type': 'integer'}}},
    },
}
CREATE_LOCAL_ASSETS_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['visuals'],
    'description': 'Create local PNG previews only. No attachment, send, upload or publication. '
        'Each visual has alt, optional title/width/height/template/background/branding, and 1-128 layers. '
        'Kinds: rect, ellipse, line, polygon, text, image. Boxes use integer x,y,width,height; '
        'line/polygon use points. Text uses text,size,min_size,font(sans/serif/mono),bold,color,align. '
        'Shapes use fill/stroke/stroke_width; rect also radius. Images use an authorized asset_id and fit. '
        'Colors are #RRGGBB/#RRGGBBAA or transparent. Templates paper/plain/dark set overridable defaults. '
        'Branding is null or {text,color}. Never supply paths, URLs, owner, HTML, SVG or code.',
    'properties': {'visuals': {'type': 'array', 'minItems': 1, 'maxItems': MAX_BATCH,
        'items': {'type': 'object', 'additionalProperties': False, 'required': ['alt', 'layers'],
            'properties': {
                'title': {'type': 'string', 'maxLength': 120}, 'alt': {'type': 'string', 'maxLength': 1000},
                'width': {'type': 'integer', 'minimum': 64, 'maximum': 4096},
                'height': {'type': 'integer', 'minimum': 64, 'maximum': 4096},
                'template': {'type': 'string', 'enum': list(PALETTES)}, 'background': {'type': 'string'},
                'branding': {'type': ['object', 'null'], 'additionalProperties': False,
                    'properties': {'text': {'type': 'string'}, 'color': {'type': 'string'}}, 'required': ['text']},
                'layers': {'type': 'array', 'minItems': 1, 'maxItems': MAX_LAYERS,
                    'items': LAYER_SCHEMA},
            }}}},
}
