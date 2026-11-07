"""Render one carousel's slides in the Paper style, using Josh's own renderer.

Run as a script: python render_carousel.py <job.json>
job.json holds {"slides": [[line, line], ...], "out": "<folder>"}.

The look lives in E:\\CareerOS\\07_Tools\\carousel-render\\render.py. This loads its slide
template without running its own batch, so both always produce the same design.
"""
import asyncio
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


async def render(slides, out):
    ns = load_template()
    slide_html = ns['slide_html']
    from playwright.async_api import async_playwright
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob('slide_*.png'):
        old.unlink()
    slide_html.close_idx = len(slides) - 2 if any(l.startswith('QR ') for l in slides[-1]) else len(slides) - 1
    tmp = out / '_render.html'
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except Exception:
            browser = await pw.chromium.launch(channel='chrome')
        page = await browser.new_page(viewport={'width': 1080, 'height': 1080})
        for i, lines in enumerate(slides):
            tmp.write_text(slide_html(lines, i, len(slides)), encoding='utf-8')
            await page.goto(tmp.as_uri())
            await page.wait_for_function('document.body.dataset.done==1')
            await page.evaluate('document.fonts.ready')
            await page.screenshot(path=str(out / f'slide_{i + 1:02d}.png'))
        await browser.close()
    tmp.unlink(missing_ok=True)
    return len(slides)


if __name__ == '__main__':
    job = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
    print(json.dumps({'slides': asyncio.run(render(job['slides'], job['out']))}))
