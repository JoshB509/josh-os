"""Conservative saved-map normalization to an explicitly established nominal clock."""
from copy import deepcopy
from fractions import Fraction
from .timing import rate, frame


def representable(fps):
    fps=rate(fps)
    return fps.denominator==1 or (fps.denominator==1001 and fps.numerator%1000==0)


def probe_clock(average,base,guessed,duration):
    """Use agreeing nominal header rates only for negligible average-rate drift.

    Does not scan packets or claim support for arbitrary VFR. Unsupported clocks
    remain explicit and are rejected by worker export preflight before rendering.
    """
    observed=rate(average or guessed or base)
    selected=observed;reason='Reported average clock'
    if not representable(observed) and base and guessed:
        nominal=rate(base)
        drift=abs(observed-nominal)*Fraction(str(duration))
        if nominal==rate(guessed) and representable(nominal) and drift<=Fraction(1,20):
            selected=nominal;reason='Agreeing nominal metadata; total drift at most 0.05 frame'
    return str(selected),dict(reported_average_fps=str(observed),base_fps=str(base) if base else None,
        guessed_fps=str(guessed) if guessed else None,selected_fps=str(selected),basis=reason,
        maximum_change_frames=float(abs(observed-selected)*Fraction(str(duration))),
        packet_audit_performed=False,native_interpretation_verified=False)


def normalize_saved(mapping, nominal):
    old, new = rate(mapping['fps']), rate(nominal)
    if new.denominator != 1 and not (new.denominator == 1001 and new.numerator % 1000 == 0):
        raise ValueError('Nominal clock is not representable in XMEML')
    result = deepcopy(mapping)
    spans = result['spans']
    if not spans:
        raise ValueError('Saved edit has no retained ranges')
    extent = max(s['source_out_frame'] for s in spans)
    drift = abs(Fraction(extent) / old * new - extent)
    if drift > Fraction(1, 20):
        raise ValueError('Clock change exceeds 0.05 frame; explicit retiming is required')
    cursor = 0
    previous_source_end = 0
    offset = Fraction(str(mapping['audio_minus_video_seconds']))
    for s in spans:
        a,b,x,y = (s[k] for k in ('source_in_frame','source_out_frame','out_in_frame','out_out_frame'))
        if not all(isinstance(v,int) for v in (a,b,x,y)) or not (previous_source_end <= a < b and x == cursor and y-x == b-a):
            raise ValueError('Saved edit has incomplete, overlapping or inconsistent ranges')
        for n in (a,b,x,y):
            if frame(Fraction(n)/old,new) != n:
                raise ValueError('Clock normalization changes a saved frame boundary')
        for edge, n in [('in',a),('out',b)]:
            expected = float(Fraction(n)/old+offset)
            if abs(s['audio_'+edge+'_seconds']-expected) > 0.000001:
                raise ValueError('Saved audio timing disagrees with source map')
            s['audio_'+edge+'_seconds'] = float(Fraction(n)/new+offset)
        cursor=y;previous_source_end=b
    if cursor != mapping['duration_frames']:
        raise ValueError('Saved edit duration does not match retained ranges')
    for key in ('source_video','source_audio'):
        info=result[key]
        if info.get('fps'):
            if rate(info['fps']) != old:
                raise ValueError('Source clock differs from saved map')
            info['reported_average_fps']=info['fps'];info['fps']=str(new)
        if extent > frame(info['duration'],new,'floor'):
            raise ValueError('Retained ranges exceed saved source bounds')
    if spans[-1]['audio_out_seconds'] > result['source_audio']['duration']:
        raise ValueError('Retained speech exceeds audio bounds')
    result['fps']=str(new)
    result['clock_normalization']={
        'reported_average_fps':str(old), 'nominal_fps':str(new),
        'maximum_change_frames':float(drift),
        'maximum_change_seconds':float(drift/new),
        'saved_frame_boundaries_unchanged':True,
        'basis':'Explicit nominal clock; metadata discrepancy below 0.05 frame over used source',
        'native_source_interpretation_verified':False,
    }
    return result
