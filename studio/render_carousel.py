"""Render one carousel's slides in the Paper style, using Josh's own renderer.

Run as a script: python render_carousel.py <job.json>
job.json holds {"slides": [{"lines": [line, ...], "svg": "<svg ...>", "image": "<path>"}, ...], "out": "<folder>"}.
A slide can carry a drawing (inline SVG) or a picture, shown under its words. Plain lists of lines still work.

The look lives in E:\\CareerOS\\07_Tools\\carousel-render\\render.py. This loads its slide
template without running its own batch, so both always produce the same design.
"""
import asyncio
import base64
import json
import os
import pathlib
import sys

RENDERER = pathlib.Path(os.environ.get('CAROUSEL_RENDERER', r'E:\CareerOS\07_Tools\carousel-render\render.py'))


def load_template():
    source = RENDERER.read_text(encoding='utf-8')
    source = source.replace("asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))", '')
    ns = {'__file__': str(RENDERER), '__name__': 'carousel_render'}
    exec(compile(source, str(RENDERER), 'exec'), ns)
    return ns


# Josh asked for posts that don't jump around between slides (7 Nov 2026): one background on
# every slide, the closing slide set like the others, every slide's text starting on the same
# top line, and one text size across the post. The cover keeps its bigger headline.
STEADY_CSS = """
.s.paper2,.s.ink{background:#F6F5F1;color:#15171C}
.ink .grain{mix-blend-mode:multiply;opacity:.35}
.ink .dots i{background:#D9D4C9}.ink .dots i.on{background:#15171C}
.ink .foot{color:#6D6860}
.ink p{color:#4D4A44}
.ink .num{color:#7A5A00}
.ink p .em{color:#15171C;background:linear-gradient(transparent 62%,#F9DB73 62% 92%,transparent 92%)}
.s{justify-content:flex-start;padding-top:230px}
.close h1{font-family:Outfit;font-weight:600;font-size:var(--h,64px);line-height:1.16;letter-spacing:-.015em;text-transform:none;color:#15171C}
.close p{font-family:Manrope;font-weight:500;font-size:var(--b,32px);line-height:1.55;letter-spacing:0;color:#4D4A44}
.close p .em{color:#15171C;font-weight:700}
"""
# Reads a slide's text sizes after the template has fitted it, and the sizes it started from.
MEASURE = """() => {
  const h = document.querySelector('.c h1') || document.querySelector('.list'), p = document.querySelector('.c p');
  const size = el => el ? parseFloat(getComputedStyle(el).fontSize) : 0;
  const fitted = [size(h), size(p)];
  if (h) h.style.removeProperty('--h'); if (p) p.style.removeProperty('--b');
  return {h: fitted[0], p: fitted[1], h0: size(h), p0: size(p), cover: !!document.querySelector('.s.cover')};
}"""


VISUAL_CSS = """
.vis{position:relative;display:flex;justify-content:center;align-items:center;width:100%;max-width:840px}
.vis img{display:block;max-width:840px;max-height:var(--vh,520px);border-radius:20px;object-fit:contain;
  box-shadow:0 1px 2px rgba(21,23,28,.06),0 18px 44px -18px rgba(21,23,28,.35)}
.vis svg{display:block;max-width:840px;height:var(--vh,520px);width:auto}
"""
# The picture or drawing gives way first, so the words keep their size. It shrinks until it clears the footer.
VISUAL_FIT = ("<script>document.fonts.ready.then(()=>{const v=document.querySelector('.vis');if(!v)return;let h=520;"
              "for(let i=0;i<30&&v.getBoundingClientRect().bottom>940&&h>200;i++){h*=.94;v.style.setProperty('--vh',h+'px')}})</script>")


def visual(slide):
    """The drawing or picture for one slide, as HTML."""
    if slide.get('svg'):
        return f'<div class="vis">{slide["svg"]}</div>'
    if slide.get('image'):
        p = pathlib.Path(slide['image'])
        mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}.get(p.suffix.lower(), 'image/png')
        return f'<div class="vis"><img src="data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}" alt=""></div>'
    return ''


def with_visual(html, slide):
    part = visual(slide)
    if not part:
        return html
    html = html.replace('</style>', VISUAL_CSS + '</style>', 1).replace('<script>', VISUAL_FIT + '<script>', 1)
    at = html.index('\n<div class="foot">')
    # Inside the text column when there is one, so it sits under the words. After a numbered list otherwise.
    cut = at - len('</div>') if html[:at].endswith('</div>') else at
    return html[:cut] + part + html[cut:]


def steady(html, sizes=None):
    html = html.replace('</style>', STEADY_CSS + '</style>', 1)
    if sizes:
        # Set the agreed sizes before the template's own fitting runs, so it has nothing left to shrink.
        set_sizes = ("<script>(()=>{const h=document.querySelector('.c h1')||document.querySelector('.list'),"
                     "p=document.querySelector('.c p');"
                     f"if(h)h.style.setProperty('--h','{sizes[0]:.2f}px');"
                     f"if(p&&{sizes[1]:.2f}>0)p.style.setProperty('--b','{sizes[1]:.2f}px');}})()</script>")
        html = html.replace('<script>', set_sizes + '<script>', 1)
    return html


async def render(slides, out):
    ns = load_template()
    slide_html = ns['slide_html']
    slides = [s if isinstance(s, dict) else {'lines': s} for s in slides]
    from playwright.async_api import async_playwright
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob('slide_*.png'):
        old.unlink()
    slide_html.close_idx = len(slides) - 2 if any(l.startswith('QR ') for l in slides[-1]['lines']) else len(slides) - 1
    tmp = out / '_render.html'
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except Exception:
            browser = await pw.chromium.launch(channel='chrome')
        page = await browser.new_page(viewport={'width': 1080, 'height': 1080})

        async def load(i, sizes=None):
            tmp.write_text(steady(with_visual(slide_html(slides[i]['lines'], i, len(slides)), slides[i]), sizes), encoding='utf-8')
            await page.goto(tmp.as_uri())
            await page.wait_for_function('document.body.dataset.done==1')
            await page.evaluate('document.fonts.ready')

        # First pass: let every slide fit itself, and see how far each one had to shrink.
        measured = []
        for i in range(len(slides)):
            await load(i)
            measured.append(await page.evaluate(MEASURE))
        ratio = lambda m, k: (m[k] / m[k + '0']) if m[k + '0'] else 1
        inner = [m for m in measured if not m['cover']] or measured
        scale_h = min(ratio(m, 'h') for m in inner)
        scale_p = min(ratio(m, 'p') for m in inner if m['p0']) if any(m['p0'] for m in inner) else 1
        # Second pass: every slide uses the same scale, so text is the same size across the post.
        for i, m in enumerate(measured):
            sizes = None if m['cover'] else (m['h0'] * scale_h, m['p0'] * scale_p)
            await load(i, sizes)
            await page.screenshot(path=str(out / f'slide_{i + 1:02d}.png'))
        await browser.close()
    tmp.unlink(missing_ok=True)
    return len(slides)


if __name__ == '__main__':
    job = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
    print(json.dumps({'slides': asyncio.run(render(job['slides'], job['out']))}))
