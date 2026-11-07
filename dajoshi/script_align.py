"""Local word-level sequence alignment. No inference services or network access."""
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
import numpy as np


def normalized(text):
    text=unicodedata.normalize('NFKC',text).lower().replace('\u2019',"'").replace('\u2018',"'")
    return [x.replace("'",'') for x in re.findall(r"[a-z0-9]+(?:'[a-z]+)?",text)]


def script_tokens(script):
    lines=script.splitlines()
    # Markdown script documents can contain version notes and title alternatives.
    first=next((i for i,l in enumerate(lines) if re.match(r'^##\s+Hook\b',l,re.I)),None)
    if first is not None:lines=lines[first:]
    body='\n'.join(l for l in lines if not re.match(r'^\s*#',l))
    body=re.sub(r'\[[^\]]*\]',' ',body)
    return normalized(body)


def transcript_tokens(words):
    values=[];indices=[]
    for i,w in enumerate(words):
        ts=normalized(w['word']);values.extend(ts);indices.extend([i]*len(ts))
    return values,indices


def align(transcript,script):
    """Needleman-Wunsch global alignment, with deterministic latest-match ties.

    Exact match +4, substitution -3, insertion/deletion -2. Returns an exact
    transcript-token -> script-token map, leaving additions/substitutions None.
    A monotonic map alone never authorizes removal of unmatched speech.
    """
    n,m=len(transcript),len(script)
    if not n or not m:raise ValueError('Alignment requires transcript and spoken script')
    if n*m>40000000:raise ValueError('Script alignment is limited to 40 million token pairs')
    previous=[-2*j for j in range(m+1)];trace=[bytearray([2]*(m+1))]
    for i,token in enumerate(transcript,1):
        current=[-2*i]+[0]*m;directions=bytearray(m+1);directions[0]=1
        for j,ref in enumerate(script,1):
            diagonal=previous[j-1]+(4 if token==ref else -3)
            up=previous[j]-2;left=current[j-1]-2
            if diagonal>=up and diagonal>=left:current[j]=diagonal;directions[j]=0
            elif up>=left:current[j]=up;directions[j]=1
            else:current[j]=left;directions[j]=2
        previous=current;trace.append(directions)
    mapping=[None]*n;i=n;j=m
    while i or j:
        direction=trace[i][j]
        if i and j and direction==0:
            if transcript[i-1]==script[j-1]:mapping[i-1]=j-1
            i-=1;j-=1
        elif i and (not j or direction==1):i-=1
        else:j-=1
    return mapping,{'algorithm':'Needleman-Wunsch global token alignment','score':previous[-1],
        'transcript_tokens':n,'script_tokens':m,'matched_tokens':sum(x is not None for x in mapping),
        'script_coverage':sum(x is not None for x in mapping)/m}


def find_restarts(words,script,pcm,sample_rate,offset=0,fps=30):
    """Alignment-backed conservative restart proposals with quiet cut boundaries.

    Global alignment is real sequence alignment, not a semantic model. Local
    repeat selection and confidence thresholds are deliberately heuristic.
    """
    ts,wi=transcript_tokens(words);ss=script_tokens(script);mapping,report=align(ts,ss)
    report.update(method='Global script alignment + local repeated-passage evidence + acoustic cut boundaries',candidates=[])
    if report['script_coverage']<.65:
        return [],[dict(kind='script_alignment_uncertain',source_seconds=0,reason='Script coverage below 65%; no script-based deletion')],report
    positions=defaultdict(list)
    for n in (2,3):
        for i in range(len(ts)-n+1):positions[tuple(ts[i:i+n])].append(i)
    raw={};prefix={'and','so','now','well','but','then','for','example','all'}
    first_token={}
    for k,index in enumerate(wi):first_token.setdefault(index,k)
    def sentence_start(k):
        index=wi[k]
        for p in range(index,max(-1,index-4),-1):
            if words[p].get('segment_start'):return first_token[p]
        return k
    for indices in positions.values():
        for x,i in enumerate(indices):
            for j in indices[x+1:]:
                if words[wi[j]]['start']-words[wi[i]]['start']>35:break
                if j-i<3 or j-i>120:continue
                a,b=sentence_start(i),sentence_start(j)
                for _ in range(3):
                    if a>0 and ts[a-1] in prefix:a-=1
                    else:break
                for _ in range(3):
                    if b>i and ts[b-1] in prefix:b-=1
                    else:break
                if b-a<3:continue
                if not (words[wi[a]]['word'].strip()[:1].isupper() or ts[a] in prefix):continue
                if not (words[wi[b]]['word'].strip()[:1].isupper() or ts[b] in prefix):continue
                old=ts[a:b];later=ts[b:min(len(ts),b+len(old)+16)]
                blocks=SequenceMatcher(None,old,later,autojunk=False).get_matching_blocks()
                matched=sum(z.size for z in blocks);coverage=matched/len(old)
                if coverage<.65 or matched<3:continue
                nonempty=[z for z in blocks if z.size]
                if not nonempty or nonempty[0].a>2 or nonempty[0].b>2:continue
                # Both occurrences being well anchored to distinct scripted
                # passages is evidence of intentional repetition, not a retake.
                old_aligned=sum(v is not None for v in mapping[a:b])/len(old)
                continuation=mapping[b:min(len(ts),b+len(old)+24)]
                if old_aligned>.85 or sum(v is not None for v in continuation)<6:continue
                if old_aligned>.15 and all(mapping[k] is not None for k in range(a,min(a+3,b))) and all(mapping[k] is not None for k in range(b,min(b+3,len(ts)))):continue
                confidence=float(np.mean([words[k].get('probability',1) for k in set(wi[a:b])]))
                if confidence<.72:continue
                raw[(a,b)]=(coverage,old_aligned,confidence)
    # Prefer the complete abandoned phrase over overlapping suffix candidates.
    chosen=[]
    for (a,b),scores in sorted(raw.items(),key=lambda v:(v[0][0],-v[0][1])):
        if any(x<=a and b<=y for x,y,_ in chosen):continue
        chosen.append((a,b,scores))
    groups=[]
    for a,b,scores in chosen:
        if groups and a<groups[-1][1]:continue
        if groups and 0<=a-groups[-1][1]<=3 and all(t in prefix|{'all'} for t in ts[groups[-1][1]:a]):
            groups[-1][1]=b;groups[-1][2].append(scores)
        else:groups.append([a,b,[scores]])
    hop=max(1,sample_rate//100);usable=len(pcm)//hop*hop
    energy=np.sqrt(np.mean(pcm[:usable].reshape(-1,hop)**2,axis=1)+1e-14)
    threshold=max(.0015,min(.03,float(np.percentile(energy,10))*1.8))
    quiet_bins=max(9,int(np.ceil((2/float(fps)+.02)*100)))
    def quiet_boundary(target,previous_word,next_word):
        # ASR word timestamps often abut. Allow a small local correction only
        # inside a sustained acoustic valley, bounded away from word midpoints.
        lo=max(0,target-.18,previous_word['start']+(previous_word['end']-previous_word['start'])*.55 if previous_word else 0)
        hi=min(target+.35,next_word['end']-.05,next_word['start']+(next_word['end']-next_word['start'])*.55) if next_word else target+.1
        begin=max(0,int(lo*100));end=min(len(energy),int(hi*100))
        options=[]
        for k in range(begin,max(begin,end-quiet_bins+1)):
            if np.max(energy[k:k+quiet_bins])<threshold:
                center=(k+quiet_bins/2)/100
                options.append((abs(center-target),center))
        return min(options)[1] if options else None
    removals=[];flags=[]
    for a,b,scores in groups:
        ia,ib=wi[a],wi[b]
        if ia==ib:continue
        oldtext=' '.join(w['word'].strip() for w in words[ia:ib])
        newtext=' '.join(w['word'].strip() for w in words[ib:min(len(words),ib+max(12,min(30,ib-ia+8)))])
        start=quiet_boundary(words[ia]['start'],words[ia-1] if ia else None,words[ia])
        end=quiet_boundary(words[ib]['start']-.08,None,words[ib])
        row=dict(kind='script_restart',abandoned_start=words[ia]['start']-offset,
                 restart_start=words[ib]['start']-offset,before=oldtext,after=newtext,
                 repeat_coverage=min(s[0] for s in scores),prior_script_alignment=max(s[1] for s in scores),
                 confidence=min(s[2] for s in scores),acoustic_threshold=threshold)
        if start is None or end is None or end<=start or (ts[a]=='what' and not words[ia].get('segment_start')):
            row.update(removed=False,reason='Repeated passage found, but a safe quiet cut boundary was not established')
            flags.append(dict(kind='script_restart_boundary_uncertain',source_seconds=row['abandoned_start'],reason=row['reason']+': '+oldtext))
        else:
            row.update(removed=True,source_start=start-offset,source_end=end-offset,
                       reason='Earlier repeated passage is weakly aligned; later continuation matches script; both cuts lie in quiet valleys')
            removals.append(row.copy())
        report['candidates'].append(row)
    report.update(removed_count=len(removals),flagged_count=len(flags),acoustic_threshold=threshold,quiet_window_seconds=quiet_bins/100)
    return removals,flags,report
