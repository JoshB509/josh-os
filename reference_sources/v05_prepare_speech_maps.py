from pathlib import Path
import json,csv,subprocess,hashlib,re,difflib,numpy as np,wave
P=Path(__file__).parent;D=Path(r'E:\CareerOS\10_Sales\VSL\VIDEO\08_V05_New_Edit');O=D/'09_Speech_Preparation';O.mkdir(exist_ok=True);ff=r'E:\Videos\Reusable-Video-Kit\tools\ffmpeg.exe';mic=Path(r'E:\Videos\Input\00103_Wireless_PRO-esv2-50p-bg-10p-music-10p.wav')
full=json.loads((P/'v05_audio_edit/FULL_TRANSCRIPT.json').read_text(encoding='utf-8'));words=[w for s in full['segments'] for w in s['words']];sound=np.load(P/'v05_audio_edit/mic103_16k.npy');overlap=[json.loads(x) for x in (P/'v05_audio_edit/OVERLAP_16s_STEP12s.jsonl').read_text(encoding='utf-8').splitlines()]
with wave.open(str(mic),'rb') as w:
 assert w.getnchannels()==1 and w.getsampwidth()==2 and w.getframerate()==48000
 pcm=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').copy()
# Only clearly established take selections are conformed. Unresolved short-onset or content choices remain audibly present and flagged.
cuts=[(74.956667,81.556667,'First They were explaining attempt'),(363.8,372.30,'Two abandoned Third a tool starts'),(438.60,454.78,'Repeated In a way starts'),(514.05,530.03,'First45-slide-deck attempt'),(621.57,626.17,'First falls-in-love sentence'),(726.60,729.20,'First Next we have the structures'),(776.25,781.70,'Incorrect two-line/Monday pair; keep correction'),(823.90,825.85,'Spoken heading What decision makers want'),(827.98,836.35,'First what makes this fast sentence'),(965.55,975.18,'Two abandoned AI-tool list starts'),(1050.90,1053.65,'Spoken Number9 heading'),(1054.50,1061.80,'First work-it-out question'),(1108.20,1111.35,'First incomplete what it cost sentence'),(1114.43,1120.78,'First since then attempt'),(1220.45,1223.57,'First founding price/templates attempt')]
flags=[(249.0,276.0,'CONNECTIVE_DECISION','Script/shotlist literally say for; later ASR attempts hear Or / oh from. Original passage retained.'),(275.8,288.1,'REVEAL_RESTART_BOUNDARY','Multiple starts omitted by long ASR; final Well onset needs audition.'),(380.7,384,'TINY_WHETHER_REDO','Exact and whether boundary needs audition; retained.'),(550,558,'SIX_MONTHS_REDO_BOUNDARY','Second complete take onset needs audition.'),(588,599,'MAKING_SOMETHING_RESTARTS','Overlap ASR hears three starts; full and targeted ASR collapse them.'),(632.5,640.5,'DIFFERENT_MOMENTS_REDO','Final onset about638.6s; exact boundary needs audition.'),(692.2,693.2,'YOU_ARE_RESTART','Tiny restart before We are not trying.'),(893.8,902,'ONCE_YOU_KNOW_IT','ASR passes disagree/omit short speech.'),(935,941.2,'THING_I_LOVE_RESTART','Extra tiny restart; complete final And near940.8s.'),(1002.9,1016.2,'KEEPS_PAYING_OFF_RESTART','Final And onset uncertain; repeated isolated And.'),(1227.4,1232,'A_CTA_REDO','Final complete CTA onset differs across ASR1231.52/1231.90.' )]
def pieces(start,end):
 result=[];p=start
 for a,b,why in cuts:
  if a>=end or b<=start:continue
  if a>p:result.append((p,a))
  p=max(p,b)
 if p<end:result.append((p,end))
 return result
versions={'A':pieces(15.08,1179.80)+pieces(1193.28,1240.75),'B':pieces(15.08,1179.80)+pieces(1247.68,1272.30)}
manifest={'status':'SPEECH AUDIT DRAFT: known redo cuts applied; unresolved passages deliberately retained and marked. Not a final film or final speech edit. No camera key/grade propagated.','camera_minus_mic_seconds':43.11,'human_listening':False,'source_sha256':hashlib.sha256(mic.read_bytes()).hexdigest(),'cuts_applied':[dict(mic_out=a,mic_resume=b,reason=c) for a,b,c in cuts],'unresolved_ranges':[dict(mic_start=a,mic_end=b,label=l,reason=n) for a,b,l,n in flags],'versions':{}}
for ver,ranges in versions.items():
 rows=[];pos=0;chunks=[]
 for j,(a,b) in enumerate(ranges):
  sa=round(a*48000);sb=round(b*48000);n=sb-sa;rows.append({'clip':j+1,'timeline_in_seconds':pos/48000,'timeline_out_seconds':(pos+n)/48000,'mic_in_seconds':sa/48000,'mic_out_seconds':sb/48000,'mic_in_sample':sa,'mic_out_sample':sb,'camera_in_seconds':sa/48000+43.11,'camera_out_seconds':sb/48000+43.11,'purpose':'Shared main' if a<1180 else 'Ending '+ver});pos+=n
  chunk=pcm[sa:sb].astype(np.float32);fade=240;chunk[:fade]*=np.linspace(0,1,fade);chunk[-fade:]*=np.linspace(1,0,fade);chunks.append(chunk.astype('<i2'))
 wav=O/f'V05_{ver}_SPEECH_AUDIT_UNRESOLVED_PASSAGES_RETAINED.wav';assert not wav.exists()
 with wave.open(str(wav),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(48000);w.writeframes(np.concatenate(chunks).tobytes())
 with (O/f'V05_{ver}_SOURCE_EDITS_DRAFT.csv').open('w',newline='',encoding='utf-8-sig') as f:writer=csv.DictWriter(f,fieldnames=rows[0].keys());writer.writeheader();writer.writerows(rows)
 def map_time(t):
  for row in rows:
   if row['mic_in_seconds']<=t<row['mic_out_seconds']:return row['timeline_in_seconds']+t-row['mic_in_seconds']
  return None
 marks=[{'label':l,'timeline_start':map_time(a),'timeline_end':map_time(b),'mic_range':[a,b],'reason':reason} for a,b,l,reason in flags if map_time(a) is not None]
 manifest['versions'][ver]={'duration_seconds':pos/48000,'wav':str(wav),'source_edits':rows,'review_markers':marks,'applied_gain_db':0,'fade_handles_ms':5}
 print('SPEECH DRAFT',ver,pos/48000,'seconds',flush=True)
(O/'V05_SPEECH_AUDIT_EDIT_MAP_v01.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
# A short source review with preceding contrast and all three attempts, not an edited wording suggestion.
review=O/'CONNECTIVE_REVIEW_MIC_230-278.wav';a=pcm[230*48000:278*48000];peak=float(np.max(np.abs(a.astype(float)))/32768);gain=min(10**(5.3/20),10**(-1.5/20)/max(1e-10,peak));a=np.clip(a.astype(float)*gain,-32768,32767).astype('<i2')
with wave.open(str(review),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(48000);w.writeframes(a.tobytes())
question='''Which wording should connect the negative example to the positive result here? Both script v06 and shot list v05 literally say "get all the praise for walking out of every meeting with a clear decision". The recording has an unfinished first "for walking out..." attempt, then an "Or walking out..." attempt, then a complete continuation transcribed "oh, from walking out...". Should I preserve the written "for" wording by joining the existing recorded "for" to the complete later continuation, or did you intend a different connective? Please identify the intended wording; I will not invent or synthesize it.'''
notes={'review_clip':str(review),'source_range_seconds':[230,278],'gain_db':float(20*np.log10(gain)),'source_recording_original_unchanged':True,'correction':'The previous checkpoint incorrectly stated that script v06 says to. Direct source verification confirms both authoritative files say for; the animation prose describes grey from cards then gold to cards.','script_exact':'...someone else can say two lines about very little work and get all the praise for walking out of every meeting with a clear decision, getting pulled into the meetings...','competing_ASR_readings':[{'mic':[249.56,251.92],'clip_seconds':[19.56,21.92],'words':'for walking out of every meeting [stops]','basis':'Full and short-window transcripts agree on for.'},{'mic':[254.5,259.14],'clip_seconds':[24.5,29.14],'words':'Or walking out of every meeting with a clear decision, getting pulled [stops]','basis':'Full and overlap transcripts.'},{'mic':[260.62,275.44],'clip_seconds':[30.62,45.44],'words':"oh from walking out of every meeting with a clear decision, getting pulled into the meetings where things get decided because they value your opinion more than your information, and hearing your work called valuable in meetings that you're not even in",'basis':'Full transcript; short pass hears from with low confidence. Actual audio listening required.'}],'question_for_Josh':question,'no_choice_applied':True}
(O/'CONNECTIVE_QUESTION_AND_EVIDENCE.json').write_text(json.dumps(notes,indent=2),encoding='utf-8');(O/'CONNECTIVE_QUESTION.txt').write_text(question+'\n\nPlay CONNECTIVE_REVIEW_MIC_230-278.wav. Attempts start about19.6s,24.5s and30.6s into the clip.\n\nCORRECTION: Earlier notes saying the script expected to were wrong. The supplied script and shot list say for. No source was changed.\n',encoding='utf-8')
print('CONNECTIVE CLIP AND QUESTION READY',flush=True)
