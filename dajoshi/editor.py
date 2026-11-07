"""Conservative automatic decisions, with acoustic and transcript evidence."""
import re
import numpy as np
from .timing import merge, subtract

def tokens(text): return re.findall(r"[a-z0-9']+",text.lower())

def plan(words, pcm, sample_rate, offset, start, end, overrides=None,script=None,fps=30):
    overrides=overrides or {}; flags=[]; decisions=[]
    ws=[dict(w,start=float(w['start'])-offset,end=float(w['end'])-offset) for w in words if float(w['end'])-offset>start and float(w['start'])-offset<end]
    ws.sort(key=lambda w:w['start'])
    if not ws: raise ValueError('No transcript words in the selected range')
    if any(w['end'] < w['start'] for w in ws): raise ValueError('Invalid word timing')
    hop=max(1,sample_rate//100)
    usable=len(pcm)//hop*hop
    energy=np.sqrt(np.mean(pcm[:usable].reshape(-1,hop)**2,axis=1)+1e-14)
    threshold=max(.0015,min(.015,float(np.percentile(energy,20))*2.5))
    def acoustic(a,b):
        lo=max(0,int((a+offset)*100)); hi=min(len(energy),int((b+offset)*100))
        return energy[lo:hi],lo/100-offset
    def boundary(t, before):
        # Only search OUTSIDE transcript speech: never trim through ASR word bounds.
        a,b=(t-.24,t-.08) if before else (t+.12,t+.28)
        e,base=acoustic(a,b)
        if not len(e): return a if before else b
        q=np.flatnonzero(e<threshold)
        if len(q): return base+int(q[-1] if before else q[0])*.01
        flags.append(dict(kind='boundary_energy',source_seconds=t,reason='No quiet boundary outside word; extra speech handle retained'))
        return a if before else b
    chunks=[]; group=[ws[0]]
    for w in ws[1:]:
        if w['start']-group[-1]['end']>.65:
            chunks.append(group); group=[]
        group.append(w)
    chunks.append(group)
    keeps=[]
    for group in chunks:
        a=max(start,boundary(group[0]['start'],True)); b=min(end,boundary(group[-1]['end'],False))
        keeps.append([a,b])
        low=[w for w in group if w.get('probability',1)<.65]
        if low: flags.append(dict(kind='low_confidence_speech',source_seconds=a,reason='Retained uncertain words: '+' '.join(w['word'].strip() for w in low)))
    for left,right in zip(chunks,chunks[1:]):
        a,b=left[-1]['end']+.28,right[0]['start']-.24
        e,base=acoustic(a,b)
        if len(e) and float(np.mean(e>threshold))>.15:
            # ASR can omit false starts. Never equate untranscribed sound with silence.
            keeps.append([max(start,a),min(end,b)])
            flags.append(dict(kind='untranscribed_sound',source_seconds=base,reason='Sound in transcript gap retained; possible breath, laugh or omitted short take'))
        else:
            decisions.append(dict(kind='silence',source_start=a,source_end=b,reason='Transcript gap plus low acoustic energy'))
    # Drop only a strong restart: >=3 matching words, earlier short phrase is an exact
    # prefix, later phrase completes it, and a quiet take separation exists.
    removes=[]
    for i,left in enumerate(chunks[:-1]):
        lt=tokens(' '.join(w['word'] for w in left))
        for right in chunks[i+1:i+3]:
            rt=tokens(' '.join(w['word'] for w in right)); gap=right[0]['start']-left[-1]['end']
            same=len(lt)>=3 and len(lt)<=12 and len(rt)>len(lt) and rt[:len(lt)]==lt
            if same and 0.65<gap<8 and left[-1]['end']-left[0]['start']<6:
                e,_=acoustic(left[-1]['end']+.28,right[0]['start']-.24)
                if len(e) and np.mean(e<threshold)>.8 and min(w.get('probability',1) for w in left+right)>.8:
                    a=max(start,left[0]['start']-.3); b=min(end,left[-1]['end']+.3)
                    removes.append([a,b]); decisions.append(dict(kind='short_restart',source_start=a,source_end=b,reason='High-confidence repeated prefix followed by longer take and quiet gap')); break
            if lt and rt and (lt[:2]==rt[:2] or lt==rt) and gap<10:
                flags.append(dict(kind='possible_retake',source_seconds=left[0]['start'],reason='Repeated wording retained: semantic choice is uncertain'))
    # Explicit transcript labels before substantive speech can be removed, never guessed from energy alone.
    for group in chunks:
        text=' '.join(w['word'] for w in group).strip().lower()
        if text in ['[laughter]','(laughter)','[laughs]','[breath]']:
            removes.append([group[0]['start']-.25,group[-1]['end']+.28])
            decisions.append(dict(kind='labelled_non_speech',source_start=group[0]['start'],source_end=group[-1]['end'],reason=text))
    for group in chunks:
        if len(group)<=2:
            flags.append(dict(kind='short_utterance',source_seconds=group[0]['start'],reason='Short speech retained; may be a false start or pre-take sound'))
    alignment=None
    if script:
        from .script_align import find_restarts
        restart_decisions,restart_flags,alignment=find_restarts(words,script,pcm,sample_rate,offset,fps)
        decisions+=restart_decisions;flags+=restart_flags
        removes.extend([[d['source_start'],d['source_end']] for d in restart_decisions])
    keeps=subtract(merge(keeps),removes)
    keeps=merge(keeps+overrides.get('keep',[]))
    keeps=subtract(keeps,overrides.get('remove',[]))
    keeps=[[max(start,a),min(end,b)] for a,b in keeps if b>start and a<end]
    metrics=dict(noise_threshold=threshold,word_count=len(ws),speech_padding_seconds=[.08,.28])
    if alignment is not None:metrics['script_alignment']=alignment
    return keeps,decisions,flags,metrics
