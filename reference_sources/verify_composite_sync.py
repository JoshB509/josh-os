from pathlib import Path
import subprocess,json
import numpy as np, cv2
ff=r'E:\Videos\Reusable-Video-Kit\tools\ffmpeg.exe';o=Path('analysis')
def frames(path,start,duration):
 raw=subprocess.check_output([ff,'-v','error','-threads','2','-ss',str(start),'-i',str(path),'-t',str(duration),'-vf','scale=480:270','-threads','1','-pix_fmt','gray','-f','rawvideo','-'])
 return np.frombuffer(raw,np.uint8).reshape(-1,270,480)
out=[]
for t in [24,310,700]:
 # Match time-dependent mouth features in approved composite to untouched original.
 g=frames(r'E:\Videos\Promotion Policy\C0323\C0323_green.mp4',t,1.2)
 a=frames(r'E:\Videos\Promotion Policy\C0323.MP4',t,2.4)
 # SIFT spatial registration from same pose to account for reframing.
 sift=cv2.SIFT_create();kp,des=sift.detectAndCompute(g[0],None);kp2,des2=sift.detectAndCompute(a[16],None)
 matches=cv2.BFMatcher().knnMatch(des,des2,k=2);good=[m for m,n in matches if m.distance<.65*n.distance and 150<kp[m.queryIdx].pt[0]<330]
 H,inl=cv2.estimateAffinePartial2D(np.float32([kp2[m.trainIdx].pt for m in good]),np.float32([kp[m.queryIdx].pt for m in good]),method=cv2.RANSAC)
 mapped=np.array([cv2.warpAffine(f,H,(480,270)) for f in a]);scores=[]
 for lag in range(0,30):
  # Tight mouth/jaw crop excludes green/background; compare changing mouth shape.
  v=g[:,80:135,195:265].astype(float);v2=mapped[lag:lag+len(g),80:135,195:265].astype(float)
  scores.append((float(np.mean((v-v2)**2)),lag))
 best=sorted(scores)[:5];row={'composite_start':t,'original_minus_composite_seconds':best[0][1]/25,'best_mse_lags':best,'feature_inliers':int(inl.sum()),'transform':H.tolist()};out.append(row);print(row,flush=True)
(o/'composite_origin_verification.json').write_text(json.dumps(out,indent=2))
