from pathlib import Path
import subprocess,json,numpy as np
ff=r'E:\Videos\Reusable-Video-Kit\tools\ffmpeg.exe'
cam=r'E:\Videos\Input\2026-10-12 16-26-47.mp4'
mic=r'E:\Videos\Input\00103_Wireless_PRO-esv2-50p-bg-10p-music-10p.wav'
def decode(p,a,d):
 return np.frombuffer(subprocess.run([ff,'-v','error','-ss',str(a),'-i',p,'-t',str(d),'-vn','-ac','1','-ar','16000','-f','f32le','-'],capture_output=True,check=True).stdout,dtype='<f4')
rows=[]
for t in [60,90,600,1150]:
 m=decode(mic,t,20);c=decode(cam,t+42.9,20.5)
 # Different microphones have different phase/room responses. 1ms energy envelopes reduce this influence.
 def env(x):return np.sqrt(np.mean(x[:len(x)//16*16].reshape(-1,16)**2,axis=1))
 m=env(m);c=env(c);m=m-m.mean();n=len(m);N=1<<(len(c)+n-2).bit_length()
 cc=np.fft.irfft(np.fft.rfft(c,N)*np.fft.rfft(m[::-1],N),N)[n-1:len(c)]
 s=np.r_[0,np.cumsum(c,dtype=float)];ss=np.r_[0,np.cumsum(c*c,dtype=float)]
 cc/=np.sqrt(np.maximum(1e-20,ss[n:]-ss[:-n]-(s[n:]-s[:-n])**2/n)*np.sum(m*m))
 k=int(cc.argmax());rows.append(dict(mic_start=t,camera_minus_mic_seconds=round(42.9+k/1000,3),correlation=float(cc[k])))
out={'method':'Normalized correlation of 20-second energy envelopes at 1ms resolution; microphones differ. This establishes audio alignment, not camera sensor/audio latency or native lip-sync playback.','checks':rows,'selected_offset_seconds':float(np.median([r['camera_minus_mic_seconds'] for r in rows])),'human_listening':False,'native_lip_sync_verified':False}
Path('v05_audio_edit/FINE_SYNC.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
