from pathlib import Path
import json,subprocess,wave
import numpy as np
from scipy.signal import correlate
from PIL import Image,ImageDraw,ImageFont
W=Path(r'C:\Users\Anigma PC\Documents\Codex\2026-10-13\ok-x20\work');OUT=Path('analysis');OUT.mkdir(exist_ok=True)
ff=r'E:\Videos\Reusable-Video-Kit\tools\ffmpeg.exe'
mic=Path(r'E:\Videos\Promotion Policy\C0323\00168_Wireless_PRO-esv2-50p-bg-10p-music-10p.wav')
def decode(path,s,d):
 return np.frombuffer(subprocess.check_output([ff,'-v','error','-threads','2','-ss',str(s),'-i',str(path),'-t',str(d),'-vn','-ac','1','-ar','16000','-f','f32le','-']),np.float32)
p=json.loads((W/'edit_plan_v02.json').read_text());words=[w for s in json.loads((W/'transcript.json').read_text()) for w in s['words']]
x=decode(mic,0,760);np.save(OUT/'mic16.npy',x)
rows=[]
for t in [24,310,700]:
 a=decode(r'E:\Videos\Promotion Policy\C0323.MP4',t,8);b=x[int((t-1)*16000):int((t+12)*16000)]
 # Compare same speech from camera scratch audio against the separate mic.
 c=correlate(b,a,method='fft',mode='valid');i=int(np.argmax(abs(c)));offset=(i/16000)-1
 corr=float(c[i]/np.sqrt(np.sum(a*a)*np.sum(b[i:i+len(a)]**2)))
 rows.append({'camera_start':t,'mic_minus_camera_seconds':offset,'normalized_correlation':corr,'scratch_rms':float(np.sqrt(np.mean(a*a)))})
(OUT/'sync_verification.json').write_text(json.dumps(rows,indent=2));print('SYNC',rows,flush=True)
font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',16)
for page in range(5):
 im=Image.new('RGB',(1500,900),'white');d=ImageDraw.Draw(im)
 for j,e in enumerate(p['edits'][page*6:page*6+6]):
  s=e['audio_in']-.4;end=min(e['audio_out'],e['audio_in']+1.8);y=j*150
  d.text((12,y+5),f"Take {page*6+j+1} | V02 {e['timeline_start']:.2f} | mic {s:.2f}-{end:.2f}",fill='black',font=font)
  for k in range(1400):
   lo=int((s+(end-s)*k/1400)*16000);hi=max(lo+1,int((s+(end-s)*(k+1)/1400)*16000));h=min(34,float(abs(x[lo:hi]).max())*110)
   d.line((50+k,y+65-h,50+k,y+65+h),fill='navy')
  xx=50+(.4/(end-s))*1400;d.line((xx,y+25,xx,y+105),fill='red',width=2)
  ws=[w for w in words if w['end']>s and w['start']<end]
  d.text((12,y+110),' '.join(f"{w['word'].strip()}[{w['start']:.2f}]" for w in ws),fill='black',font=font)
 im.save(OUT/f'onsets_{page+1}.png')
with (OUT/'words_context.txt').open('w') as f:
 for i,e in enumerate(p['edits']):
  f.write(f"\nTAKE {i+1} V02 {e['timeline_start']} MIC {e['audio_in']}-{e['audio_out']}\n")
  ws=[w for w in words if w['end']>e['audio_in']-.7 and w['start']<e['audio_out']+.7]
  for j,w in enumerate(ws):
   if w['start']<e['audio_in']+1.5 or w['end']>e['audio_out']-1.5:f.write(f"{w['start']:.2f}-{w['end']:.2f} {w['word']}\n")
  for a,b in zip(ws,ws[1:]):
   if b['start']-a['end']>.40:f.write(f"GAP {a['end']:.2f}-{b['start']:.2f} {a['word']} / {b['word']}\n")
print('Analysis files saved',flush=True)
