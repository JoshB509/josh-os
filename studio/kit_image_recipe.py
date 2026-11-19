"""Observed, bounded UI recipe steps. Upload/insertion are not enabled by this module."""
import json
import math
import re

from .kit_editor_observation import tree_body
from .kit_visual_insertions import Document,digest

# This exact plus glyph was observed in supported visible-button metadata after
# paragraph focus. It identifies a menu opener, never an upload or insertion.
PLUS_PATH='M11 13H6a1 1 0 0 1-.71-.29A1 1 0 0 1 5 12q0-.42.29-.71A1 1 0 0 1 6 11h5V6q0-.42.29-.71A1 1 0 0 1 12 5a.97.97 0 0 1 1 1v5h5q.42 0 .71.29.3.29.29.71c-.01.42-.1.52-.29.71A1 1 0 0 1 18 13h-5v5q0 .42-.29.71A1 1 0 0 1 12 19a1 1 0 0 1-.71-.29A1 1 0 0 1 11 18z'


def boundary_target(receipt):
    """Recognize only the observed visible plus button from raw tool metadata."""
    try:controls=json.loads(receipt['response'][0]['text'])
    except (KeyError,IndexError,TypeError,ValueError):raise ValueError('No supported boundary-control metadata.') from None
    if not isinstance(controls,list):raise ValueError('Unexpected control metadata.')
    matches=[c for c in controls if isinstance(c,dict) and c.get('tag')=='BUTTON' and c.get('type')=='submit'
             and c.get('paths')==[PLUS_PATH] and c.get('lines')==[] and c.get('disabled') is False
             and c.get('title') is None and c.get('haspopup') is None]
    if len(matches)!=1:raise ValueError('The observed paragraph plus control is absent or ambiguous.')
    rect=matches[0].get('bounds',{})
    if (set(rect)!={'x','y','width','height'} or any(type(v) not in (int,float) or not math.isfinite(v) for v in rect.values())
            or rect['width']!=20 or rect['height']!=20 or not 0<=rect['x']<=200 or not 0<=rect['y']<=10000):
        raise ValueError('The paragraph plus control has unfamiliar geometry.')
    return [round(rect['x']+10),round(rect['y']+10)]


def image_menu_target(page):
    """Only the observed Image menu entry, not Gallery or any image asset."""
    if not isinstance(page,str) or '[BLOCKED:' in page or '[TRUNCATED' in page:
        raise ValueError('A complete supported menu observation is required.')
    named=list(re.finditer(r'^(\s*)button "Image Upload or add from a 3rd party" \[(ref_\d+)\]$',page,re.M))
    if named:
        # The verified Kit menu has nested duplicate labels. Only a unique
        # directly nested inner control is eligible; unrelated duplicates fail.
        if len(named)==1:return named[0].group(2)
        if (len(named)==2 and len(named[1].group(1))>len(named[0].group(1))
                and not page[named[0].end():named[1].start()].strip()):return named[1].group(2)
        raise ValueError('The visible inner Image control is ambiguous.')
    pattern=(r'^ heading "Media" \[ref_\d+\]\n button \[ref_\d+\]\n'
             r'  button \[(ref_\d+)\]\n   generic "Image" \[ref_\d+\]\n'
             r'   generic "Upload or add from a 3rd party" \[ref_\d+\]$')
    matches=re.findall(pattern,page,re.M)
    if len(matches)!=1:raise ValueError('The exact observed Image menu entry is absent or ambiguous.')
    return matches[0]


def paragraph_target(api,scoped,after_line):
    """Bind an existing non-link text node to one exact paragraph boundary.

    Clicking this node only selects the existing paragraph. Typing, empty blocks,
    URL links, before-first placement and ambiguous/blank targets are unsupported.
    Caller must independently validate the complete UI/API body before using it.
    """
    if (not isinstance(scoped,str) or not re.match(r'^textbox "Email content editor" \[ref_\d+\]\n',scoped)
            or '[BLOCKED:' in scoped or '[TRUNCATED' in scoped):
        raise ValueError('A complete scoped editor observation is required.')
    body=tree_body(api);tree=api['content_tree']
    if type(after_line) is not int or not 1<=after_line<=len(tree):raise ValueError('This recipe requires placement after an existing paragraph.')
    children=tree[after_line-1].get('children')
    if not children or not isinstance(children[0].get('text'),str) or not children[0]['text'].strip():
        raise ValueError('Blank or link-first paragraph placement needs a separately observed recipe.')
    expected=children[0]['text'].strip()
    candidates=[]
    for raw,ref in re.findall(r'^ generic ("(?:\\.|[^"\\])*") \[(ref_\d+)\]$',scoped,re.M):
        label=json.loads(raw)
        if label==expected or (len(label)>=80 and expected.startswith(label)):candidates.append((label,ref))
    if len(candidates)!=1:raise ValueError('The visible paragraph target is absent or ambiguous.')
    label,ref=candidates[0]
    first_texts=[p['children'][0].get('text','').strip() for p in tree if p.get('children')]
    if sum(t==label or (len(label)>=80 and t.startswith(label)) for t in first_texts)!=1:
        raise ValueError('The paragraph prefix is not unique in the exact body.')
    nodes=Document(body['html']).nodes
    return dict(ref=ref,label=label,after_line=after_line,left_hash=digest(nodes[after_line-1]),
                right_hash=digest(nodes[after_line]) if after_line<len(nodes) else None)
