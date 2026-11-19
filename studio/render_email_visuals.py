"""Offline email visuals. Templates are starting points; standalone SVG is a free canvas."""
from html import escape
from pathlib import Path

try:
    from .visual_spec import normalize_visuals
except ImportError:  # render_carousel.py is also launched as a standalone script
    from visual_spec import normalize_visuals


def visual_html(slide):
    w, h = slide['width'], slide['height']
    pad = round(min(w, h) * .04)
    title = escape(slide['lines'][0]) if slide['lines'] else ''
    lines = ''.join('<p>' + escape(line) + '</p>' for line in slide['lines'][1:])
    words = f'<div class="words"><h1>{title}</h1>{lines}</div>' if title else ''
    badge = f'<span class="badge {slide["badge"].lower()}">{slide["badge"]}</span>' if slide['badge'] else ''
    note = f'<div class="note">{escape(slide["note"])}</div>' if slide['note'] else ''
    footer = '<div class="footer">@joshbrindley9537</div>' if slide['footer'] else ''
    drawing = f'<div class="drawing">{slide["svg"]}</div>' if slide['svg'] else ''
    # All interpolated CSS values are normalized numbers, enums, or a validated color.
    return f'''<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'none'; img-src 'none'; font-src 'none'; connect-src 'none'">
<style>
*{{box-sizing:border-box}}html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden}}
body{{font-family:Arial,sans-serif;color:#15171C;background:{slide['background']}}}
.canvas{{display:flex;flex-direction:column;width:100%;height:100%;padding:{pad}px;gap:{pad//2}px}}
.badge{{align-self:flex-end;flex:none;padding:8px 20px;border-radius:999px;font-size:{max(16,round(w*.013))}px;font-weight:700;letter-spacing:.06em;background:#15171C;color:white}}
.badge.after{{background:#F5C21B;color:#15171C}}
.words{{flex:none}}h1{{font-size:{round(w*.042)}px;line-height:1.16;margin:0 0 16px;overflow-wrap:anywhere}}
p{{font-size:{round(w*.025)}px;line-height:1.45;margin:10px 0;overflow-wrap:anywhere}}
.drawing{{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;overflow:hidden}}
.drawing>svg{{width:100%;height:100%;display:block}}
.note{{flex:none;font-size:{max(18,round(w*.019))}px;line-height:1.3;white-space:nowrap;color:#4D4A44}}
.footer{{flex:none;font-size:18px;color:#6D6860}}
.standalone{{padding:{pad if (badge or note or footer) else 0}px}}
</style></head><body><main class="canvas {slide['layout']}">{badge}{words}{drawing}{note}{footer}</main></body></html>'''


# Fail rather than crop or reduce a note to unreadable type. The previous saved version survives.
FIT = '''() => {
 const canvas=document.querySelector('.canvas'), words=document.querySelector('.words'), note=document.querySelector('.note');
 if(note){let size=parseFloat(getComputedStyle(note).fontSize);const floor=Math.max(16,innerWidth*.013);
   while(note.scrollWidth>note.clientWidth && size>floor){size-=.5;note.style.fontSize=size+'px'}
   if(note.scrollWidth>note.clientWidth)throw Error('The note is too long for this canvas; shorten it or increase the width.');}
 if(words){let scale=1;const nodes=[...words.querySelectorAll('h1,p')], sizes=nodes.map(n=>parseFloat(getComputedStyle(n).fontSize));
   const available=canvas.clientHeight-parseFloat(getComputedStyle(canvas).paddingTop)*2;
   const max=available*(document.querySelector('.drawing')?.55:.85);
   while(words.getBoundingClientRect().height>max && scale>.55){scale-=.025;nodes.forEach((n,i)=>n.style.fontSize=(sizes[i]*scale)+'px')}
   if(words.getBoundingClientRect().height>max)throw Error('Text does not fit legibly; shorten it or increase the canvas.');}
 for(const el of document.querySelectorAll('.badge,.words,.drawing,.note,.footer')){
   const r=el.getBoundingClientRect();if(r.bottom>innerHeight+1||r.right>innerWidth+1||r.width<=0||r.height<=0)throw Error('Visual content exceeds the canvas.');}
 return true;
}'''


async def render_email_visuals(slides, out):
    slides = normalize_visuals(slides)
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    # Never clear existing output. Jobs must get a fresh immutable version directory.
    if any(out.glob('slide_*.png')) or (out / 'manifest.json').exists():
        raise ValueError('Email visual output must be a fresh directory.')
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except Exception:
            browser = await pw.chromium.launch(channel='chrome', headless=True)
        try:
            context = await browser.new_context(service_workers='block', device_scale_factor=1)
            await context.route('**/*', lambda route: route.abort())
            page = await context.new_page()
            for i, slide in enumerate(slides, 1):
                await page.set_viewport_size({'width': slide['width'], 'height': slide['height']})
                await page.set_content(visual_html(slide), wait_until='load')
                await page.evaluate('document.fonts.ready')
                await page.evaluate(FIT)
                await page.screenshot(path=str(out / f'slide_{i:02d}.png'), animations='disabled')
        finally:
            await browser.close()
    return len(slides)
