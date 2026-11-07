"""Revision-safe clean-cut operations. Decisions are explicit, never guessed by a model."""
import copy
import hashlib
import json
import math
from fractions import Fraction
from .timing import rate


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_map(mapping):
    fps = rate(mapping['fps']); offset = Fraction(str(mapping['audio_minus_video_seconds']))
    cursor = 0; previous = -1
    vd = Fraction(str(mapping['source_video']['duration']))
    ad = Fraction(str(mapping['source_audio']['duration']))
    if not mapping.get('sync', {}).get('verified'):
        raise ValueError('Verified microphone sync evidence is required')
    if abs(float(mapping['sync'].get('timeline_offset_seconds', offset)) - float(offset)) > 1e-8:
        raise ValueError('Sync report and timeline offset disagree')
    if not mapping['spans']: raise ValueError('The clean cut cannot be empty')
    for s in mapping['spans']:
        a,b,x,y = [s[k] for k in ('source_in_frame','source_out_frame','out_in_frame','out_out_frame')]
        if any(type(v) is not int for v in (a,b,x,y)):
            raise ValueError('Timing spans must use integer frames')
        if not 0 <= a < b <= math.floor(vd*fps) or a < previous or x != cursor or y-x != b-a:
            raise ValueError('Invalid source ranges or discontinuous output map')
        ai,ao = Fraction(a,1)/fps+offset,Fraction(b,1)/fps+offset
        if ai < 0 or ao > ad: raise ValueError('Audio range outside source')
        if abs(float(ai)-s['audio_in_seconds']) > 1e-7 or abs(float(ao)-s['audio_out_seconds']) > 1e-7:
            raise ValueError('Audio timing disagrees with kept timeline')
        previous=b; cursor=y
    if type(mapping['duration_frames']) is not int or cursor != mapping['duration_frames']:
        raise ValueError('Duration disagrees with kept timeline')
    return mapping


def reject_ranges(mapping, decisions):
    """Subtract explicit source-frame ranges without build_map's outward re-rounding.

    For seconds, round the rejected interval OUTWARD, so no frame touching it
    returns through an outward-rounded retained range. The snapped decision is
    included for review; subframe ambiguity is never silently hidden.
    """
    validate_map(mapping); result=copy.deepcopy(mapping); fps=rate(mapping['fps'])
    offset=Fraction(str(mapping['audio_minus_video_seconds'])); rejected=[]
    for d in decisions:
        if not isinstance(d.get('reason'),str) or not d['reason'].strip():
            raise ValueError('Each semantic removal needs a reason')
        if d.get('coordinate_space') != 'source_video_seconds':
            raise ValueError('Removal coordinates must be source_video_seconds')
        a,b=Fraction(str(d['start'])),Fraction(str(d['end']))
        if not 0 <= a < b <= Fraction(str(mapping['source_video']['duration'])):
            raise ValueError('Rejected range outside source')
        rejected.append(dict(d,source_in_frame=math.floor(a*fps),source_out_frame=math.ceil(b*fps)))
    spans=[]; cursor=0
    for s in mapping['spans']:
        remaining=[(s['source_in_frame'],s['source_out_frame'])]
        for d in rejected:
            new=[]; x,y=d['source_in_frame'],d['source_out_frame']
            for a,b in remaining:
                if y<=a or x>=b:new.append((a,b))
                else:
                    if a<x:new.append((a,x))
                    if y<b:new.append((y,b))
            remaining=new
        for a,b in remaining:
            spans.append(dict(source_in_frame=a,source_out_frame=b,out_in_frame=cursor,
                              out_out_frame=cursor+b-a,audio_in_seconds=float(Fraction(a,1)/fps+offset),
                              audio_out_seconds=float(Fraction(b,1)/fps+offset)))
            cursor+=b-a
    result.update(spans=spans,duration_frames=cursor)
    validate_map(result)
    for d in rejected:
        assert all(s['source_out_frame']<=d['source_in_frame'] or s['source_in_frame']>=d['source_out_frame'] for s in spans)
    return result,rejected


def remap_words(mapping, words):
    """Word input is selected-audio seconds. Partial words block approval."""
    validate_map(mapping); fps=rate(mapping['fps']);offset=Fraction(str(mapping['audio_minus_video_seconds']))
    kept=[]; issues=[]; previous=Fraction(-1)
    for index,w in enumerate(words):
        a,b=Fraction(str(w['start']))-offset,Fraction(str(w['end']))-offset
        if b<=a or a<previous or not str(w.get('word','')).strip():
            raise ValueError('Invalid or unordered source word timestamps')
        previous=a
        hits=[]
        for s in mapping['spans']:
            x,y=Fraction(s['source_in_frame'],1)/fps,Fraction(s['source_out_frame'],1)/fps
            if a<y and b>x:hits.append((s,x,y))
        if not hits:continue
        if len(hits)!=1 or a<hits[0][1] or b>hits[0][2]:
            issues.append(dict(kind='partial_word',word=w['word'],source_word_index=index,
                               reason='Word crosses a cut boundary; re-align/retranscribe before approval'))
            continue
        s,x,y=hits[0];start=Fraction(s['out_in_frame'],1)/fps+a-x;end=start+b-a
        kept.append(dict(w,source_start=float(a),source_end=float(b),start=float(start),end=float(end),source_word_index=index))
    if not kept:issues.append(dict(kind='no_words',reason='No validated words remain'))
    return kept,issues
