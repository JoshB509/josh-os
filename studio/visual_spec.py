"""Bounded static visual specifications, reusable without granting file or network access."""
import math
import re
import xml.etree.ElementTree as ET

MAX_VISUALS = 16
MAX_PIXELS = 8_000_000
MAX_BATCH_PIXELS = 40_000_000
ET.register_namespace('', 'http://www.w3.org/2000/svg')
TAGS = set('svg g defs rect circle ellipse line polyline polygon path text tspan title desc marker linearGradient radialGradient stop clipPath'.split())
ATTRS = set('id viewBox width height x y x1 x2 y1 y2 cx cy r rx ry d points fill stroke stroke-width stroke-linecap stroke-linejoin stroke-dasharray opacity fill-opacity stroke-opacity transform font-family font-size font-weight text-anchor dominant-baseline dx dy marker-start marker-mid marker-end markerWidth markerHeight refX refY orient markerUnits offset stop-color stop-opacity gradientUnits gradientTransform spreadMethod clip-path preserveAspectRatio'.split())


def safe_svg(value):
    if not isinstance(value, str) or len(value.encode('utf-8')) > 200_000 or '<!' in value or '<?' in value:
        raise ValueError('Use a static SVG drawing under 200 KB without declarations or entities.')
    try:
        root = ET.fromstring(value)
    except ET.ParseError:
        raise ValueError('The SVG drawing is not valid XML.') from None
    def tag(name):
        return name.removeprefix('{http://www.w3.org/2000/svg}')
    if tag(root.tag) != 'svg':
        raise ValueError('The drawing must be an SVG.')
    nodes = list(root.iter())
    if len(nodes) > 2000:
        raise ValueError('Keep the SVG drawing under 2000 elements.')
    ids, refs = set(), set()
    def inspect(node, depth=0):
        if depth > 32:
            raise ValueError('SVG groups are nested too deeply.')
        if tag(node.tag) not in TAGS:
            raise ValueError('Only static SVG shapes and text are supported.')
        for key, val in node.attrib.items():
            if key not in ATTRS or len(val) > 20000:
                raise ValueError('Unsupported SVG attribute: ' + key[:60])
            if any(c in val for c in ('\\', '<', '>', '&', ';', '{', '}')) or re.search(r'(?i)(https?:|file:|data:|javascript:|//|@|expression)', val):
                raise ValueError('SVG external references and CSS are not allowed.')
            if key in ('fill', 'stroke', 'clip-path', 'marker-start', 'marker-mid', 'marker-end'):
                if not re.fullmatch(r'(?:#[0-9a-fA-F]{3,8}|[A-Za-z]+|(?:rgb|rgba)\([0-9.,% ]+\)|url\(#[A-Za-z_][\w-]*\))', val):
                    raise ValueError('SVG paint references must be local fragments or colors.')
                if val.startswith('url(#'):
                    refs.add(val[5:-1])
            elif re.search('url', val, re.I):
                raise ValueError('SVG references are only allowed in paint attributes.')
            if key == 'id':
                if not re.fullmatch('[A-Za-z_][\\w-]*', val) or val in ids:
                    raise ValueError('SVG ids must be unique local names.')
                ids.add(val)
            if key == 'viewBox':
                try:
                    box = [float(n) for n in re.split('[ ,]+', val.strip())]
                except ValueError:
                    raise ValueError('Invalid SVG viewBox.') from None
                if len(box) != 4 or not all(math.isfinite(n) and abs(n) <= 100000 for n in box) or min(box[2:]) <= 0:
                    raise ValueError('Invalid SVG viewBox.')
        # References from definitions can form recursive paints/markers. Keep definitions nonrecursive.
        if tag(node.tag) in ('defs', 'marker', 'clipPath', 'linearGradient', 'radialGradient'):
            if any('url(' in v for child in node.iter() for v in child.attrib.values()):
                raise ValueError('SVG definitions cannot reference other definitions.')
        for child in node:
            inspect(child, depth + 1)
    inspect(root)
    if refs - ids:
        raise ValueError('An SVG fragment reference is missing its definition.')
    return ET.tostring(root, encoding='unicode')


def normalize_visual(item):
    allowed = {'lines', 'svg', 'width', 'height', 'aspect_ratio', 'template', 'layout', 'footer', 'badge', 'note', 'background', 'alt', 'after_line'}
    if not isinstance(item, dict) or set(item) - allowed:
        raise ValueError('Use a visual specification only; file paths, HTML and unknown options are not accepted.')
    template = item.get('template', 'paper')
    if template not in ('paper', 'slide-card', 'plain'):
        raise ValueError('Unknown visual starting template.')
    layout = item.get('layout', 'standalone' if template == 'plain' else 'card' if template == 'slide-card' else 'text')
    if layout not in ('text', 'card', 'standalone'):
        raise ValueError('Choose text, card or standalone layout.')
    width = item.get('width', 1600 if template == 'slide-card' else 1080)
    height = item.get('height', 1040 if template == 'slide-card' else 1080)
    for dimension in (width, height):
        if type(dimension) is not int or not 320 <= dimension <= 4096:
            raise ValueError('Visual dimensions must be integers between 320 and 4096 pixels.')
    aspect = item.get('aspect_ratio')
    if aspect is not None:
        if not isinstance(aspect, str) or not re.fullmatch(r'[1-9][0-9]{0,3}:[1-9][0-9]{0,3}', aspect):
            raise ValueError('Use an aspect ratio such as 16:9.')
        a, b = map(int, aspect.split(':'))
        if 'height' not in item:
            height = round(width * b / a)
        elif 'width' not in item:
            width = round(height * a / b)
        if abs(width / height - a / b) > 0.005:
            raise ValueError('Dimensions do not match the requested aspect ratio.')
    if not (320 <= width <= 4096 and 320 <= height <= 4096 and 0.25 <= width / height <= 4) or width * height > MAX_PIXELS:
        raise ValueError('Use 320–4096 pixels per side, at most 8 million pixels, and aspect ratios between 1:4 and 4:1.')
    lines = item.get('lines', [])
    if not isinstance(lines, list) or len(lines) > 8 or any(not isinstance(x, str) or not x.strip() or len(x) > 300 or '\n' in x or '\r' in x or x.startswith('QR ') for x in lines):
        raise ValueError('Provide up to 8 nonempty plain text lines, up to 300 characters each, without QR directives.')
    svg = safe_svg(item['svg']) if 'svg' in item and item['svg'] != '' else ''
    if not lines and not svg:
        raise ValueError('Provide text lines or a static SVG drawing.')
    if layout == 'standalone' and (not svg or lines):
        raise ValueError('Standalone layout requires SVG and no text lines; compose the drawing freely inside the SVG.')
    if layout == 'card' and not svg:
        raise ValueError('Card layout requires a static SVG slide.')
    if type(item.get('footer', False)) is not bool:
        raise ValueError('Footer must be true or false.')
    badge, note = item.get('badge', ''), item.get('note', '')
    if badge not in ('', 'BEFORE', 'AFTER'):
        raise ValueError('Badge must be BEFORE or AFTER.')
    if not isinstance(note, str) or len(note) > 300 or '\n' in note or '\r' in note:
        raise ValueError('Use a one-line note of at most 300 characters.')
    background = item.get('background', '#F6F5F1' if template == 'paper' else '#FFFFFF')
    if not isinstance(background, str) or not re.fullmatch('#[0-9a-fA-F]{6}', background):
        raise ValueError('Use a six-digit hex background color.')
    return dict(template=template, layout=layout, width=width, height=height, lines=lines,
                svg=svg, footer=item.get('footer', False), badge=badge, note=note,
                background=background, **({'aspect_ratio': aspect} if aspect else {}))


def normalize_visuals(items):
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_VISUALS:
        raise ValueError('Generate between one and sixteen visuals.')
    slides = [normalize_visual(item) for item in items]
    if sum(s['width'] * s['height'] for s in slides) > MAX_BATCH_PIXELS:
        raise ValueError('A visual set must stay within 40 million total pixels.')
    return slides
