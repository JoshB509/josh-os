"""Version 2: bounded movable layers with deterministic wrapping and group shadows."""
import base64,copy,io,json,math,re
from xml.sax.saxutils import escape
from PIL import Image,ImageDraw,ImageFont,ImageFilter

SIZE=(1280,720)
LIGHT='a3696b5996d1ee1b460045c148e8c6ee83f7a5c6c1f729475e60705a3fa6818b'
MAX_TEXT=8

def text_layer(identity,text,y=35):
    return dict(id=identity,kind='text',text=text,x=100,y=y,width=1080,size=116,
                font='georgia-bold-italic',tracking=-1.5,leading=1.12,visible=True)

def validate(layout,assets,fonts,font_path):
    if not isinstance(layout.get('name'),str) or not 1<=len(layout['name'])<=100:raise ValueError('Layout name needs 1-100 characters')
    layers=layout.get('layers')
    if not isinstance(layers,list) or not 2<=len(layers)<=11 or any(not isinstance(l,dict) for l in layers):raise ValueError('Use a background, person and up to eight text layers')
    kinds=[l.get('kind') for l in layers]
    if kinds.count('background')!=1 or kinds.count('subject')!=1 or kinds.count('foreground')>1 or kinds.count('text')>MAX_TEXT or any(k not in ['background','subject','text','foreground'] for k in kinds):raise ValueError('Invalid layer types')
    ids=[l.get('id') for l in layers]
    if any(not isinstance(i,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,50}',i) for i in ids) or len(set(ids))!=len(ids):raise ValueError('Invalid layer identifiers')
    bg=layers[0]
    if bg['kind']!='background' or bg.get('asset')!=LIGHT or [bg.get(k) for k in ['x','y','width','height','visible']]!=[0,0,1280,720,True]:raise ValueError('Keep the light office background fixed')
    allowed={(a['asset'],a['role']) for a in assets}
    for l in layers:
        if type(l.get('visible')) is not bool:raise ValueError('Invalid visibility')
        limits={'x':(-2560,3840),'y':(-1440,2160)}
        if l['kind']=='text':
            if l.get('font') not in fonts or not font_path(l['font']).is_file():raise ValueError('Unavailable font')
            t=l.get('text')
            if not isinstance(t,str) or len(t)>500 or any(ord(c)<32 and c!='\n' for c in t):raise ValueError('Text needs at most 500 characters; use Enter for a new line')
            limits.update(width=(80,2400),size=(24,220),tracking=(-5,15),leading=(.9,1.6))
        else:
            limits.update(width=(1,3840),height=(1,2160))
            aid=l.get('asset')
            if aid and (aid,l['kind']) not in allowed:raise ValueError('Choose an image belonging to this project and layer')
        for k,(lo,hi) in limits.items():
            v=l.get(k)
            if type(v) not in (int,float) or not math.isfinite(v) or not lo<=v<=hi:raise ValueError('Invalid '+k)
        if l['kind']=='text' and len(text_geometry(l,font_path)['lines'])>8:raise ValueError('Text wraps beyond eight lines. Widen the text box or shorten the text.')
    if len(json.dumps(layout))>16000:raise ValueError('Layout is too large')
    return copy.deepcopy(layout)

def text_geometry(l,font_path):
    font=ImageFont.truetype(str(font_path(l['font'])),round(l['size']));tracking=l['tracking']
    def measure(t):return font.getlength(t)+max(0,len(t)-1)*tracking
    lines=[]
    for paragraph in l['text'].split('\n'):
        if not paragraph.strip():lines.append('');continue
        line=''
        for word in paragraph.split():
            candidate=(line+' '+word).strip()
            if measure(candidate)<=l['width']:line=candidate;continue
            if line:lines.append(line);line=''
            for ch in word:
                if line and measure(line+ch)>l['width']:lines.append(line);line=''
                line+=ch
        lines.append(line)
    cap_top=font.getbbox('Ag',anchor='ls')[1]
    size=round(l['size']);baseline=l['y']-cap_top
    rows=[]
    for i,line in enumerate(lines):
        width=measure(line);x=l['x']+(l['width']-width)/2;y=baseline+i*size*l['leading']
        rows.append(dict(text=line,x=x,y=y,width=width))
    return dict(font=font,lines=rows,x=l['x'],y=l['y'],width=l['width'],height=max(size,len(lines)*size*l['leading']))

def geometry(store,layout,font_path):
    boxes=[]
    for l in layout['layers'][1:]:
        if not l['visible']:continue
        if l['kind']=='text':
            g=text_geometry(l,font_path);boxes.append(dict(id=l['id'],kind=l['kind'],**{k:g[k] for k in ['x','y','width','height']},line_count=len(g['lines'])))
        elif l.get('asset'):
            p,_=store.asset(l['asset'])
            with Image.open(p) as im:
                box=im.convert('RGBA').getchannel('A').point(lambda a:255 if a>=16 else 0).getbbox() or (0,0,im.width,im.height)
                sx=l['width']/im.width;sy=l['height']/im.height
                boxes.append(dict(id=l['id'],kind=l['kind'],x=l['x']+box[0]*sx,y=l['y']+box[1]*sy,width=(box[2]-box[0])*sx,height=(box[3]-box[1])*sy))
    return boxes

def render(store,layout,fonts,font_path):
    canvas=Image.new('RGBA',SIZE,(35,35,35,255));parts=[];defs=[]
    for l in layout['layers']:
        if not l['visible']:continue
        identity=l['id']
        if l['kind']!='text':
            if not l['asset']:continue
            p,mime=store.asset(l['asset']);im=Image.open(p).convert('RGBA').resize((round(l['width']),round(l['height'])),Image.Resampling.LANCZOS)
            canvas.alpha_composite(im,(round(l['x']),round(l['y'])))
            href='data:'+mime+';base64,'+base64.b64encode(p.read_bytes()).decode()
            parts.append(f'<g id="{identity}"><image x="{round(l["x"])}" y="{round(l["y"])}" width="{round(l["width"])}" height="{round(l["height"])}" preserveAspectRatio="none" href="{href}"/></g>');continue
        g=text_geometry(l,font_path);font=g['font'];size=round(l['size']);fg=Image.new('RGBA',SIZE);glyphs=Image.new('RGBA',SIZE);draw=ImageDraw.Draw(fg);td=ImageDraw.Draw(glyphs);backing=[];letters=[]
        for row in g['lines']:
            text=row['text'];x=row['x'];y=row['y']
            if not text:continue
            # Scale the backing with the type: it overlaps the lower letter bodies;
            # descenders extend beyond it. One unbroken parallelogram per line.
            padding=size*.16;slant=size*.15;top=y-size*.18;bottom=top+size*.22
            left=x-padding;right=x+row['width']+padding
            points=[(left+slant,top),(right,top),(right-slant,bottom),(left,bottom)]
            draw.polygon(points,fill='#ffd400');backing.append('<polygon points="'+' '.join(f'{a},{b}' for a,b in points)+'" fill="#ffd400"/>')
            for i,ch in enumerate(text):
                cx=x+font.getlength(text[:i])+i*l['tracking']
                td.text((cx,y),ch,font=font,anchor='ls',fill='white')
                letters.append(f'<text x="{cx}" y="{y}" font-family="{fonts[l["font"]][1]}" font-style="italic" font-weight="{fonts[l["font"]][3]}" font-size="{size}" fill="white">{escape(ch)}</text>')
        # Shadow the completed yellow+white group once, underneath both elements.
        fg.alpha_composite(glyphs);blur=size*.09;opacity=.14
        alpha=fg.getchannel('A').filter(ImageFilter.GaussianBlur(blur)).point(lambda a:round(a*opacity))
        shadow=Image.new('RGBA',SIZE,(0,0,0,0));shadow.putalpha(alpha);canvas.alpha_composite(shadow,(0,1));canvas.alpha_composite(fg)
        fid='shadow-'+identity
        defs.append(f'<filter id="{fid}" filterUnits="userSpaceOnUse" x="-2560" y="-1440" width="6400" height="3600" color-interpolation-filters="sRGB"><feDropShadow dx="0" dy="1" stdDeviation="{blur}" flood-color="black" flood-opacity="{opacity}"/></filter>')
        parts.append(f'<g id="{identity}" filter="url(#{fid})"><g class="backing">'+''.join(backing)+'</g><g class="type">'+''.join(letters)+'</g></g>')
    b=io.BytesIO();canvas.convert('RGB').save(b,'PNG')
    svg='<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><defs>'+''.join(defs)+'</defs><rect width="1280" height="720" fill="#232323"/>'+''.join(parts)+'</svg>'
    return b.getvalue(),svg
