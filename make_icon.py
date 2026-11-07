"""Original geometric D/playhead icon, generated locally from vector-like shapes."""
from PIL import Image,ImageDraw
from pathlib import Path
out=Path(__file__).parent
im=Image.new('RGBA',(256,256),(0,0,0,0));d=ImageDraw.Draw(im)
d.rounded_rectangle((8,8,248,248),radius=53,fill='#111722')
d.rounded_rectangle((48,44,205,212),radius=48,fill='#46ddbc')
d.rectangle((48,44,106,212),fill='#46ddbc')
d.rounded_rectangle((84,78,170,178),radius=25,fill='#111722')
d.polygon([(109,95),(109,161),(155,128)],fill='#f6c86c')
d.rectangle((33,108,52,120),fill='#f6c86c');d.rectangle((33,137,52,149),fill='#f6c86c')
im.save(out/'DaJoshiResolve.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
im.save(out/'DaJoshiResolve.png')
