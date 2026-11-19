"""Tariq screen-viewport PiP. Silent alpha overlay; presenter is unchanged below it."""
from pathlib import Path
import subprocess
from PIL import Image, ImageDraw

SHOT_TYPE = 'screen-viewport-pip'
CANVAS = (3840, 2160)
SCREEN = (220, 404, 2260, 1348)
PRESENTER = (2644, 404, 1036, 1348)

def filter_graph(crop, speed):
    x,y,w,h=map(int,crop)
    if min(x,y)<0 or min(w,h)<=0 or speed<=0:raise ValueError('Invalid measured viewport or speed')
    # The crop is explicit and visually approved; never fit a pre-padded composite.
    return (f'[1:v]crop={w}:{h}:{x}:{y},setpts=(PTS-STARTPTS)/{speed},'
            'fps=30,scale=2260:1348:force_original_aspect_ratio=increase:flags=lanczos,'
            'crop=2260:1348:0:0,setsar=1,format=rgba[screen];'
            '[0:v][screen]overlay=220:404:format=auto:shortest=1,format=yuva444p10le[out]')

def render(source, output, *, crop, start, speed, frames, ffmpeg):
    source,output=Path(source),Path(output)
    if not source.is_file():raise ValueError('Source missing')
    if output.exists():raise FileExistsError(output)
    if start<0 or frames<2:raise ValueError('Invalid source start or frame count')
    output.parent.mkdir(parents=True,exist_ok=True)
    base=output.with_suffix('.base.png')
    if base.exists():raise FileExistsError(base)
    im=Image.new('RGBA',CANVAS,(250,248,239,255));d=ImageDraw.Draw(im)
    d.rectangle([2632,392,3692,1764],fill=(245,194,27,255))
    d.rectangle([2644,404,3679,1751],fill=(0,0,0,0));im.save(base)
    cmd=[str(ffmpeg),'-v','error','-nostdin','-n','-loop','1','-framerate','30','-i',str(base),
         '-ss',str(start),'-i',str(source),'-filter_complex',filter_graph(crop,speed),
         '-map','[out]','-frames:v',str(frames),'-an','-c:v','prores_ks','-profile:v','4444',
         '-alpha_bits','16','-qscale:v','9','-threads','2',str(output)]
    subprocess.run(cmd,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return {'type':SHOT_TYPE,'path':str(output),'source':str(source),'crop':list(crop),
            'source_start':start,'speed':speed,'frames':frames,'screen':SCREEN,'presenter':PRESENTER}
