import os,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING']='1'
os.environ['HF_HOME']=str(ROOT/'models')
from faster_whisper import WhisperModel
src=next(Path(r'E:\Videos\Promotion Policy\C0323').glob('*.wav'))
print('Loading transcription model',flush=True)
model=WhisperModel('medium.en',device='cpu',compute_type='int8',cpu_threads=12,download_root=str(ROOT/'models'))
segments,info=model.transcribe(str(src),beam_size=5,word_timestamps=True,vad_filter=True,language='en')
out=[]
with (ROOT/'transcript.txt').open('w',encoding='utf-8') as log:
 for s in segments:
  row={'start':s.start,'end':s.end,'text':s.text,'words':[{'start':w.start,'end':w.end,'word':w.word,'probability':w.probability} for w in s.words]}
  out.append(row)
  line=f'{s.start:.2f} --> {s.end:.2f} {s.text}'
  print(line,flush=True);log.write(line+'\n');log.flush()
  (ROOT/'transcript.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print('TRANSCRIPTION COMPLETE',flush=True)
