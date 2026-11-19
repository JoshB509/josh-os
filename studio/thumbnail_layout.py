"""Composition defaults are suggestions; bounds below protect the renderer's resources.

The output canvas remains 1920x1080. Explicit coordinates may place/crop layers
outside it. Asset paths and output destinations are checked by thumbnail_build.
"""
import math

WIDTH, HEIGHT = 1920, 1080
DEFAULTS = {
    'middle': dict(face_cx=960, head_top=22, body_bottom=1100, desk_top=1008,
                   shade_start=400, baseline=975, bar_left=130, bar_right=1790),
    'close': dict(face_cx=483, face_cy=350, face_w=432, shade_start=325, size=142,
                  baseline=470, bar_left=737, bar_right=1857),
}
# Finite positions, positive extents and bounded raster sizes, not house geometry.
BOUNDS = dict(face_cx=(-3840, 5760), face_cy=(-2160, 3240),
              head_top=(-2160, 3240), body_bottom=(-2160, 4320),
              desk_top=(0, 1080), shade_start=(0, 1080),
              baseline=(-2160, 3240), bar_left=(-3840, 5760),
              bar_right=(-3840, 5760), text_cx=(-3840, 5760),
              face_w=(1, 2160), size=(8, 720), line_gap=(0, 4))


def number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError(f'{name} must be a finite number.')
    if not low <= value <= high:
        raise ValueError(f'{name} must be between {low} and {high}.')
    return value


def setting(spec, name, fmt, default=None):
    return number(spec.get(name, DEFAULTS[fmt].get(name, default)), name, *BOUNDS[name])


def text_box(spec, fmt):
    if 'text_box' in spec:
        return spec['text_box']
    left, right = setting(spec, 'bar_left', fmt), setting(spec, 'bar_right', fmt)
    return dict(left=left, right=right, baseline=setting(spec, 'baseline', fmt),
                cx=setting(spec, 'text_cx', fmt, (left + right) / 2),
                line_gap=setting(spec, 'line_gap', fmt, .92))


def geometry(fmt, spec):
    """Validate normalized specs again in the isolated renderer process."""
    if fmt not in DEFAULTS or not isinstance(spec, dict):
        raise ValueError('Unsupported thumbnail format or settings.')
    if spec.get('scene') not in ('seated', 'standing', 'close'):
        raise ValueError('Unsupported thumbnail scene.')
    p = spec.get('pose')
    if not isinstance(p, dict):
        raise ValueError('The pose must be a set of settings.')
    keys = ('face_cx', 'head_top', 'body_bottom', 'desk_top') if fmt == 'middle' else ('face_cx', 'face_cy', 'face_w')
    for key in keys:
        setting(p, key, fmt)
    if fmt == 'middle':
        number(p['body_bottom'] - p['head_top'], 'pose height', 1, 3240)
    setting(spec, 'shade_start', fmt)
    for key in ('text_behind', 'shade'):
        if key in spec and not isinstance(spec[key], bool):
            raise ValueError(f'{key} must be true or false.')
    if 'style' in spec:
        box = text_box(spec, fmt)
        for key, name in (('left', 'bar_left'), ('right', 'bar_right'), ('cx', 'text_cx'), ('baseline', 'baseline')):
            number(box[key], name, *BOUNDS[name])
        number(box['right'] - box['left'], 'text box width', 1, WIDTH * 3)
        number(box.get('line_gap', .92), 'line_gap', *BOUNDS['line_gap'])
    elif fmt == 'middle':
        line = spec['line']
        for key in ('baseline', 'bar_left', 'bar_right'):
            setting(line, key, fmt)
        number(line.get('cx', 960), 'text_cx', *BOUNDS['text_cx'])
        number(line['bar_right'] - line['bar_left'], 'bar width', 55, WIDTH * 3)
    else:
        for line in spec['lines']:
            setting(line, 'baseline', fmt)
            setting(line, 'size', fmt)
            number(line['cx'], 'text_cx', *BOUNDS['text_cx'])
