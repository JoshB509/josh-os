"""One rational timing authority shared by export, preview and animation cues."""
from fractions import Fraction
import math

def rate(value):
    r = Fraction(str(value))
    if r <= 0 or r > 1000:
        raise ValueError('Invalid frame rate')
    return r

def frame(t, fps, rounding='nearest'):
    n = Fraction(str(t)) * rate(fps)
    if rounding == 'floor': return math.floor(n)
    if rounding == 'ceil': return math.ceil(n)
    return math.floor(n + Fraction(1, 2))

def merge(ranges):
    result = []
    for a, b in sorted(ranges):
        if b <= a: continue
        if result and a <= result[-1][1]: result[-1][1] = max(result[-1][1], b)
        else: result.append([a, b])
    return result

def subtract(keeps, removes):
    result = []
    for a, b in keeps:
        p = a
        for x, y in merge(removes):
            if y <= p or x >= b: continue
            if x > p: result.append([p, min(x, b)])
            p = max(p, y)
        if p < b: result.append([p, b])
    return result

def build_map(keeps, fps, offset, source_duration, audio_duration):
    fps = rate(fps); offset = Fraction(str(offset)); cursor = 0; spans = []
    for a, b in merge(keeps):
        # Outward rounding protects the selected speech at both boundaries.
        start = frame(max(a, 0, float(-offset)), fps, 'floor')
        end = frame(min(b, source_duration, audio_duration-float(offset)), fps, 'ceil')
        start = max(start, math.ceil(-offset*fps), 0)
        end = min(end, frame(source_duration, fps, 'floor'), math.floor((Fraction(str(audio_duration))-offset)*fps))
        if spans and start <= spans[-1]['source_out_frame']:
            prev = spans.pop(); cursor = prev['out_in_frame']; start = prev['source_in_frame']
        if end <= start: continue
        spans.append(dict(source_in_frame=start, source_out_frame=end,
                          out_in_frame=cursor, out_out_frame=cursor+end-start,
                          audio_in_seconds=float(Fraction(start,1)/fps+offset),
                          audio_out_seconds=float(Fraction(end,1)/fps+offset)))
        cursor += end-start
    if not spans: raise ValueError('No retained media: inspect transcript, source range and overrides')
    return dict(version=1, fps=str(fps), audio_minus_video_seconds=float(offset),
                duration_frames=cursor, spans=spans)

def map_source(mapping, seconds):
    f = frame(seconds, mapping['fps'])
    for s in mapping['spans']:
        if s['source_in_frame'] <= f < s['source_out_frame']:
            return s['out_in_frame'] + f-s['source_in_frame']
    return None

def map_interval(mapping, a, b):
    x, y = frame(a, mapping['fps']), frame(b, mapping['fps'])
    result = []
    for s in mapping['spans']:
        lo, hi = max(x,s['source_in_frame']), min(y,s['source_out_frame'])
        if hi > lo: result.append((s['out_in_frame']+lo-s['source_in_frame'],s['out_in_frame']+hi-s['source_in_frame']))
    return result

def split_spans(mapping, boundaries):
    result=[]
    for s in mapping['spans']:
        edges=sorted({s['out_in_frame'],s['out_out_frame']} | {x for x in boundaries if s['out_in_frame']<x<s['out_out_frame']})
        for a,b in zip(edges,edges[1:]):
            d=a-s['out_in_frame']; e=b-s['out_in_frame']; r=float(rate(mapping['fps']))
            result.append(dict(source_in_frame=s['source_in_frame']+d,source_out_frame=s['source_in_frame']+e,out_in_frame=a,out_out_frame=b,audio_in_seconds=s['audio_in_seconds']+d/r,audio_out_seconds=s['audio_in_seconds']+e/r))
    return result
