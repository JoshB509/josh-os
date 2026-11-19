"""Resolve explicitly named approved-storyboard B-roll into silent layers.

No timeline edits here. Unknown clips, ambiguous anchors and animation overlap fail
closed. Native words are on the approved clean-cut clock.
"""
import re
from difflib import SequenceMatcher
from pathlib import Path
from . import edit_animations as ea


def plan(md, words, frame_ticks, shots, rows):
    requests=re.findall(r'`([^`\n]+\.mp4)`(?:\s*\(full screen\))?\s+from "([^"]+)"',md)
    if 'Insert B-roll' in md and not requests:
        raise ValueError('B-roll is requested but has no readable named clips and phrase anchors.')
    if not requests:return []
    fps=ea.fps_of(frame_ticks);fpsf=float(fps)
    tokens=[ea.norm(w['text']) for w in words]
    plans=[];cursor=0
    for filename,phrase in requests:
        candidates=[r for r in rows if Path(r['path']).name==filename]
        if len(candidates)>1:
            candidates=[r for r in candidates if str(Path(r['path']).parent) in md]
        if len(candidates)!=1:raise ValueError('B-roll name is missing or ambiguous in the approved library: '+filename)
        row=candidates[0];want=ea.tokens(phrase);target=' '.join(want);matches=[]
        if len(want)<3:raise ValueError('B-roll anchor needs at least three words: '+phrase)
        for i in range(cursor,len(words)-2):
            for size in range(max(3,len(want)-2),len(want)+3):
                score=SequenceMatcher(None,' '.join(tokens[i:i+size]),target).ratio()
                if score>=.68:matches.append((score,i,size))
        if not matches:raise ValueError('B-roll anchor was not found on the approved cut: '+phrase)
        matches.sort(reverse=True);score,i,size=matches[0]
        other=next((s for s,j,n in matches if abs(j-i)>max(size,n)),0)
        if score-other<.06:raise ValueError('B-roll anchor is ambiguous: '+phrase)
        selected=words[i:i+size]
        if any(w.get('partial') for w in selected):raise ValueError('B-roll anchor contains a clipped word: '+phrase)
        start=round(selected[0]['start']*fpsf)
        plans.append(dict(track=4,kind='broll',path=row['path'],sha256=row['sha256'],start_f=start,in_f=0,
                          out_f=int(float(row['duration_seconds'])*fpsf),name='B-roll '+filename,
                          anchor=phrase,matched=' '.join(w['text'] for w in selected),match_score=score,
                          source_word_ids=[w['id'] for w in selected]))
        cursor=i+size
    end=round(max(w['end'] for w in words)*fpsf)
    back=re.search(r'Back to talking head on "([^"]+)"',md)
    if back:
        want=ea.tokens(back.group(1))
        hits=[i for i in range(cursor,len(tokens)-len(want)+1) if tokens[i:i+len(want)]==want]
        if len(hits)!=1:raise ValueError('B-roll return-to-camera anchor is missing or ambiguous: '+back.group(1))
        end=min(end,round(words[hits[0]]['start']*fpsf))
    for k,p in enumerate(plans):
        a=p['start_f'];limit=min(end,plans[k+1]['start_f'] if k+1<len(plans) else end)
        for shot in shots:
            if shot['start_f']<=a<shot['end_f']:
                # Preserve every approved animation frame: B-roll may start in the following gap.
                a=shot['end_f']
            if shot['start_f']>=a:limit=min(limit,shot['start_f'])
        p['start_f']=a;p['out_f']=min(p['out_f'],limit-a)
        if p['out_f']<2:raise ValueError('No animation-free interval remains for '+p['name'])
    return plans


def verify_files(layers):
    from .transcript_worker import file_digest
    for layer in layers:
        if file_digest(Path(layer['path']))!=layer['sha256']:
            raise ValueError('B-roll file changed after storyboard planning: '+layer['name'])


def scale_ops(info, layers, probe):
    """Only Scale on exact named B-roll clips in the new assembly; no source/cut edits."""
    width,height=info['frame'];frame=int(info['frame_ticks']);ops=[];expected=[]
    for layer in layers:
        if layer.get('kind')!='broll':continue
        norm=lambda p:str(p).replace('\\','/').casefold()
        hits=[x for x in info['items'] if x['kind']=='v' and x['track']==layer['track'] and norm(x.get('path'))==norm(layer['path'])
              and abs(int(x['start'])-layer['start_f']*frame)<=frame//2]
        if len(hits)!=1:raise ValueError('B-roll clip identity is missing or ambiguous before sizing: '+layer['name'])
        item=hits[0];header=probe(layer['path']);w,h=header['width'],header['height']
        if not w or not h or abs(width/height-w/h)>.001:raise ValueError('B-roll needs an explicit aspect-ratio treatment: '+layer['name'])
        if header.get('channels'):raise ValueError('The approved B-roll library should contain silent video: '+layer['name'])
        motion=next((e for e in item['effects'] if 'motion' in str(e['match']).lower()),None)
        if not motion or not any(p['name'].lower()=='scale' for p in motion['params']):raise ValueError('B-roll Motion Scale is unavailable')
        scale=100*width/w
        ops.append(dict(op='set',seq=info['name'],kind='v',track=item['track'],index=item['index'],effect=motion['match'],param='Scale',value=scale))
        expected.append(dict(path=layer['path'],start=item['start'],track=item['track'],index=item['index'],scale=scale,
                             position=next((p['value'] for p in motion['params'] if p['name'].lower()=='position'),None)))
    return ops,expected


def apply_fullframe(flow, sequence, layers):
    from dajoshi.media import probe
    verify_files([x for x in layers if x.get('kind')=='broll'])
    before=flow.read_sequence(sequence)
    ops,expected=scale_ops(before,layers,probe)
    if ops:
        response=flow.bridge_call('tq_ops',dict(ops=ops))
        if not response.get('ok'):raise ValueError('Premiere refused B-roll sizing: '+str(response.get('steps',[])[-1:]))
    after=flow.read_sequence(sequence)
    for row in expected:
        item=next((x for x in after['items'] if x['kind']=='v' and x['track']==row['track'] and x['index']==row['index']),None)
        if not item or item['start']!=row['start'] or item['path']!=row['path']:raise ValueError('B-roll identity changed while sizing')
        motion=next(e for e in item['effects'] if 'motion' in str(e['match']).lower())
        params={p['name'].lower():p['value'] for p in motion['params']}
        if abs(float(params['scale'])-row['scale'])>.001 or params.get('position')!=row['position']:raise ValueError('B-roll framing readback differs from the full-frame plan')
    return dict(verified=True,frame=after['frame'],clips=expected)
