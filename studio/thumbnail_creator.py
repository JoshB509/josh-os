"""Bounded deterministic thumbnail layouts. Never edits project bodies or starts jobs."""
import base64, copy, hashlib, io, json, math, uuid, zipfile
from pathlib import Path
from xml.sax.saxutils import escape
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from .store import Conflict, now
from . import thumbnail_layers

FONTS = {
    'georgia-italic': ('Georgia Italic', 'Georgia', 'georgiai.ttf', 'normal'),
    'georgia-bold-italic': ('Georgia Bold Italic', 'Georgia', 'georgiaz.ttf', 'bold'),
    'times-italic': ('Times New Roman Italic', 'Times New Roman', 'timesi.ttf', 'normal'),
    'bodoni-italic': ('Bodoni MT Italic', 'Bodoni MT', 'BOD_I.TTF', 'normal'),
}
SIZE = (1280, 720)

def font_path(key):
    return Path('C:/Windows/Fonts') / FONTS[key][2]

def defaults():
    return dict(schema=1, name='Untitled layout', layers=[
        dict(id='background', kind='background', asset='', x=0, y=-12, width=1280, height=744, visible=True),
        dict(id='subject', kind='subject', asset='', x=0, y=0, width=1280, height=720, visible=True),
        dict(id='headline', kind='text', x=640, y=485, lines=['Sell to their', 'imagination'], font='georgia-italic', size=110, tracking=0, leading=1.15, bar=26, bar_mode='backing', bar_offset=-12, bar_padding=14, bar_slant=12, shadow=0.32, shadow_blur=5, shadow_x=2, shadow_y=5, visible=True),
        dict(id='foreground', kind='foreground', asset='', x=0, y=0, width=1280, height=720, visible=False)])

class ThumbnailCreator:
    def __init__(self, store):
        self.store=store
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS thumbnail_workspaces(project_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL)')

    def workspace(self, pid):
        self.store.get(pid)
        with self.store.connect() as c:
            row=c.execute('SELECT body FROM thumbnail_workspaces WHERE project_id=?',(pid,)).fetchone()
        body=json.loads(row['body']) if row else dict(project_id=pid,revision=0,assets=[],layout=defaults(),versions=[],requests=[])
        return dict(body, capabilities=['connected-backing-v1','movable-layers-v2'], fonts=[dict(id=k,name=v[0]) for k,v in FONTS.items() if font_path(k).is_file()])

    def validate(self, layout, assets):
        if isinstance(layout,dict) and layout.get('schema')==2:return thumbnail_layers.validate(layout,assets,FONTS,font_path)
        if not isinstance(layout,dict) or layout.get('schema')!=1: raise ValueError('Unsupported layout schema')
        if not isinstance(layout.get('name'),str) or not 1<=len(layout['name'])<=100:raise ValueError('Layout name needs 1–100 characters')
        layers=layout.get('layers')
        if not isinstance(layers,list) or len(layers)!=4:raise ValueError('Use exactly background, subject, text and foreground layers')
        if {x.get('kind') for x in layers}!={'background','subject','text','foreground'} or len({x.get('id') for x in layers})!=4:raise ValueError('Invalid layer identities')
        allowed={(a['asset'],a['role']) for a in assets}
        for l in layers:
            if not isinstance(l.get('id'),str) or len(l['id'])>50:raise ValueError('Invalid layer identifier')
            if type(l.get('visible')) is not bool:raise ValueError('Invalid layer visibility')
            limits=dict(x=(-2560,3840),y=(-1440,2160))
            if l['kind']=='text':
                limits.update(size=(12,220),tracking=(-5,30),leading=(0.7,2),bar=(0,40),shadow=(0,0.6))
                if l.get('bar_mode','words') not in ('words','backing'):raise ValueError('Invalid yellow backing style')
                for k,bounds in dict(bar_offset=(-80,80),bar_padding=(0,80),bar_slant=(0,60),shadow_blur=(0,30),shadow_x=(-30,30),shadow_y=(-30,30)).items():
                    if k in l:limits[k]=bounds
                if l.get('font') not in FONTS or not font_path(l['font']).is_file():raise ValueError('Chosen font is unavailable')
                if not isinstance(l.get('lines'),list) or len(l['lines'])!=2 or any(not isinstance(t,str) or len(t)>100 or any(ord(ch)<32 for ch in t) for t in l['lines']):raise ValueError('Use two lines, each at most 100 characters')
            else:
                limits.update(width=(1,3840),height=(1,2160))
                aid=l.get('asset')
                if aid and (aid,l['kind']) not in allowed:raise ValueError('Choose an asset registered for this project and layer')
            for k,(lo,hi) in limits.items():
                v=l.get(k)
                if type(v) not in (int,float) or not math.isfinite(v) or not lo<=v<=hi:raise ValueError('Invalid '+k)
        if len(json.dumps(layout))>12000:raise ValueError('Layout is too large')
        return copy.deepcopy(layout)

    def source_asset(self,pid,aid):
        if not any(a['asset']==aid for a in self.workspace(pid)['assets']):raise ValueError('Asset is not registered for this video')
        return self.store.asset(aid)

    def commit(self,pid,revision,request_id,change):
        uuid.UUID(request_id)
        self.store.get(pid)
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT body FROM thumbnail_workspaces WHERE project_id=?',(pid,)).fetchone()
            w=json.loads(row['body']) if row else dict(project_id=pid,revision=0,assets=[],layout=defaults(),versions=[],requests=[])
            if request_id in w['requests']:return self.workspace(pid)
            if w['revision']!=revision:raise Conflict('This thumbnail workspace changed. Close and reopen the creator before saving.')
            change(w)
            w['revision']+=1;w['requests']=(w['requests']+[request_id])[-30:]
            c.execute('INSERT OR REPLACE INTO thumbnail_workspaces VALUES(?,?,?)',(pid,w['revision'],json.dumps(w)))
        return self.workspace(pid)

    def save(self,pid,data):
        def change(w):
            layout=self.validate(data['layout'],w['assets'])
            if layout==w['layout'] and w['versions']:return
            if len(w['versions'])>=100:raise ValueError('This video has reached 100 saved versions. Export layouts before starting a new workspace.')
            w['layout']=layout
            w['versions'].append(dict(id=str(uuid.uuid4()),created_at=now(),layout=layout))
        return self.commit(pid,data['revision'],data['request_id'],change)

    def add_asset(self,pid,data):
        role=data.get('role')
        if role not in ('background','subject','foreground'):raise ValueError('Invalid asset role')
        raw=base64.b64decode(data['data'],validate=True)
        if len(raw)>20*1024*1024:raise ValueError('Asset exceeds 20 MB')
        with Image.open(io.BytesIO(raw)) as im:
            if im.width*im.height>40000000:raise ValueError('Asset exceeds 40 megapixels')
            if role!='background' and (im.mode not in ('RGBA','LA','P') or im.convert('RGBA').getchannel('A').getextrema() in ((255,255),(0,0))):raise ValueError('Subject and foreground need a transparent cutout, not a flattened reference')
            dimensions=list(im.size)
        before=self.workspace(pid)
        if data['request_id'] in before['requests']:return before
        if before['revision']!=data['revision']:raise Conflict('This thumbnail workspace changed. Close and reopen before importing.')
        if len(before['assets'])>=40:raise ValueError('Maximum 40 reusable assets per video')
        asset=self.store.import_image(data['name'],raw)
        def change(w):
            if len(w['assets'])>=40:raise ValueError('Maximum 40 reusable assets per video')
            if not any(a['asset']==asset['asset'] and a['role']==role for a in w['assets']):
                w['assets'].append(dict(asset=asset['asset'],name=str(data['name'])[:150],role=role,dimensions=dimensions))
        return self.commit(pid,data['revision'],data['request_id'],change)

    def render(self,pid,layout):
        w=self.workspace(pid);layout=self.validate(layout,w['assets'])
        if layout['schema']==2:return thumbnail_layers.render(self.store,layout,FONTS,font_path)
        canvas=Image.new('RGBA',SIZE,(35,35,35,255));svg=[]
        for l in layout['layers']:
            if not l['visible']:continue
            if l['kind']!='text':
                if not l['asset']:continue
                path,mime=self.store.asset(l['asset']);im=Image.open(path).convert('RGBA')
                im=im.resize((round(l['width']),round(l['height'])),Image.Resampling.LANCZOS)
                canvas.alpha_composite(im,(round(l['x']),round(l['y'])))
                href='data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode()
                svg.append(f'<g id="{escape(l["kind"])}"><image x="{l["x"]}" y="{l["y"]}" width="{l["width"]}" height="{l["height"]}" preserveAspectRatio="none" href="{href}"/></g>')
                continue
            font=ImageFont.truetype(str(font_path(l['font'])),round(l['size']))
            if l.get('bar_mode','words')=='backing':
                # New opt-in treatment; legacy layouts retain their exact word bars.
                backing=Image.new('RGBA',SIZE);bd=ImageDraw.Draw(backing)
                letters=Image.new('RGBA',SIZE);td=ImageDraw.Draw(letters)
                bar_parts=[];text_parts=[]
                for i,line in enumerate(l['lines']):
                    if not line.strip():continue
                    advances=[font.getlength(ch)+l['tracking'] for ch in line]
                    width=sum(advances)-l['tracking'];x=l['x']-width/2;y=l['y']+i*l['size']*l['leading']
                    padding=l.get('bar_padding',14);slant=l.get('bar_slant',12)
                    top=y+l.get('bar_offset',-12);left=x-padding;right=x+width+padding;bottom=top+l['bar']
                    slant=min(slant,max(0,(right-left)/2))
                    if l['bar']:
                        points=[(left+slant,top),(right,top),(right-slant,bottom),(left,bottom)]
                        bd.polygon(points,fill='#ffd400')
                        coords=' '.join(f'{px},{py}' for px,py in points)
                        bar_parts.append(f'<polygon points="{coords}" fill="#ffd400"/>')
                    cx=x
                    for ch,advance in zip(line,advances):
                        td.text((cx,y),ch,font=font,anchor='ls',fill='white')
                        text_parts.append(f'<text x="{cx}" y="{y}" font-family="{FONTS[l["font"]][1]}" font-style="italic" font-weight="{FONTS[l["font"]][3]}" font-size="{round(l["size"])}" fill="white">{escape(ch)}</text>');cx+=advance
                blur=l.get('shadow_blur',5);dx=round(l.get('shadow_x',2));dy=round(l.get('shadow_y',5))
                mask=letters.getchannel('A').filter(ImageFilter.GaussianBlur(blur)).point(lambda a:round(a*l['shadow']))
                shadow=Image.new('RGBA',SIZE,(0,0,0,0));shadow.putalpha(mask)
                canvas.alpha_composite(backing);canvas.alpha_composite(shadow,(dx,dy));canvas.alpha_composite(letters)
                svg.append('<g id="text"><g id="line-backing">'+''.join(bar_parts)+'</g><g id="editable-type" filter="url(#text-shadow)">'+''.join(text_parts)+'</g></g>')
                continue
            fg=Image.new('RGBA',SIZE);d=ImageDraw.Draw(fg);parts=[]
            # Individual glyph positions make tracking explicit and repeatable.
            for i,line in enumerate(l['lines']):
                advances=[font.getlength(ch)+l['tracking'] for ch in line]
                width=sum(advances)-(l['tracking'] if line else 0);x=l['x']-width/2;y=l['y']+i*l['size']*l['leading']
                word_start=None;word_width=0;cx=x
                for j,ch in enumerate(line+' '):
                    if ch.isspace():
                        if word_start is not None and l['bar']:
                            end=word_start+word_width-l['tracking'];by=y+l['size']*.12
                            d.rectangle((word_start,by,end,by+l['bar']),fill='#ffd400')
                            parts.append(f'<rect x="{word_start}" y="{by}" width="{end-word_start}" height="{l["bar"]}" fill="#ffd400"/>')
                        word_start=None;word_width=0
                    else:
                        if word_start is None:word_start=cx
                        word_width+=advances[j]
                    if j<len(line):cx+=advances[j]
                cx=x
                for ch,advance in zip(line,advances):
                    d.text((cx,y),ch,font=font,anchor='ls',fill='white')
                    parts.append(f'<text x="{cx}" y="{y}" font-family="{FONTS[l["font"]][1]}" font-style="italic" font-weight="{FONTS[l["font"]][3]}" font-size="{round(l["size"])}" fill="white">{escape(ch)}</text>');cx+=advance
            shadow=Image.new('RGBA',SIZE,(0,0,0,0));alpha=fg.getchannel('A').filter(ImageFilter.GaussianBlur(7)).point(lambda a:round(a*l['shadow']));shadow.putalpha(alpha)
            shifted=Image.new('RGBA',SIZE);shifted.alpha_composite(shadow,(2,4));canvas.alpha_composite(shifted);canvas.alpha_composite(fg)
            svg.append(f'<g id="text" filter="url(#shadow)">'+''.join(parts)+'</g>')
        buf=io.BytesIO();canvas.convert('RGB').save(buf,'PNG')
        opacity=next(l['shadow'] for l in layout['layers'] if l['kind']=='text')
        source=f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><defs><filter id="shadow" x="-30%" y="-50%" width="160%" height="200%"><feDropShadow dx="2" dy="4" stdDeviation="7" flood-opacity="{opacity}"/></filter></defs><rect width="1280" height="720" fill="#232323"/>'+''.join(svg)+'</svg>'
        text_layer=next(l for l in layout['layers'] if l['kind']=='text')
        if text_layer.get('bar_mode')=='backing':
            f=f'<filter id="text-shadow" filterUnits="userSpaceOnUse" x="0" y="0" width="1280" height="720" color-interpolation-filters="sRGB"><feDropShadow dx="{round(text_layer.get("shadow_x",2))}" dy="{round(text_layer.get("shadow_y",5))}" stdDeviation="{text_layer.get("shadow_blur",5)}" flood-color="black" flood-opacity="{opacity}"/></filter>'
            source=source.replace('</defs>',f+'</defs>')
        return buf.getvalue(),source

    def geometry(self,pid,layout):
        layout=self.validate(layout,self.workspace(pid)['assets'])
        return thumbnail_layers.geometry(self.store,layout,font_path) if layout['schema']==2 else []

    def export(self,pid,layouts):
        if not isinstance(layouts,list) or not 1<=len(layouts)<=5:raise ValueError('Export one to five variants at a time')
        w=self.workspace(pid)
        for l in layouts:self.validate(l,w['assets'])
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
            for i,l in enumerate(layouts,1):
                png,svg=self.render(pid,l);stem=f'variant-{i:02}'
                z.writestr(stem+'.png',png);z.writestr(stem+'.svg',svg)
                z.writestr(stem+'.json',json.dumps(dict(project_id=pid,layout=l,assets=w['assets']),indent=2))
            used={l['asset'] for v in layouts for l in v['layers'] if l['kind']!='text' and l['asset']}
            for aid in used:
                path,_=self.store.asset(aid);z.write(path,'assets/'+path.name)
            z.writestr('README.txt','PNG is the canonical local render. JSON retains editable lines and layer settings. SVG embeds image bytes and editable glyphs; install the named font to match text. No PSD support. Import cutouts separately; no generation occurs. Canvas: 1280 x 720. Asset files are unmodified originals.')
        return buf.getvalue()
