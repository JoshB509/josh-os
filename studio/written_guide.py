"""Standalone written guides in the house style of the lesson guides.

A guide is its own document on one topic, not a lesson breakdown. It is written as a body of HTML with the lesson guide
kit's components (Heard at the Top Lesson_Guides\\_source: fonts.css, base.css, extra.css and the macros in make.py), and
built here into an HTML file and a PDF in the Guides folder, named YYYY-MM-DD_vNN_<slug>_guide. Earlier versions are never
overwritten.

    python -m studio.written_guide <body.html> <out folder> [kit folder]

The body's first line is <!-- title: The title | slug: the-slug -->. It prints a JSON line with the files, the page count
and a picture of the guide in print layout, for checking.
"""
import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path

KIT = Path(r'E:\CareerOS\04_Programme\Course_Content\Heard_at_the_Top\Lesson_Guides\_source')
RUNHEAD = 'CAREEROS · GUIDE'


def _kit(kit):
    spec = importlib.util.spec_from_file_location('guide_kit_make', Path(kit) / 'make.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def next_version(out, day, slug):
    n = 1
    while (Path(out) / f'{day}_v{n:02d}_{slug}_guide.pdf').exists() or (Path(out) / f'{day}_v{n:02d}_{slug}_guide.html').exists():
        n += 1
    return f'v{n:02d}'


def build(src, out, kit=KIT, runhead=RUNHEAD):
    kit = Path(kit)
    k = _kit(kit)
    text = Path(src).read_text(encoding='utf-8')
    m = re.match(r'\s*<!--\s*title:\s*(.*?)\s*\|\s*slug:\s*([a-z0-9-]+)\s*-->', text)
    if not m:
        raise ValueError('The first line must be <!-- title: The title | slug: the-slug -->')
    title, slug = m.group(1), m.group(2)
    body = text[m.end():]
    body = re.sub(r'\{\{BLD:([^}|]*)(?:\|([^}]*))?\}\}', lambda x: k.building(x.group(1), x.group(2) or ''), body)
    body = re.sub(r'\{\{STRIP:([^}|]*)(?:\|([^}]*))?\}\}', lambda x: k.strip(x.group(1), x.group(2) or ''), body)
    for key, v in k.MAC.items():
        body = body.replace('{{%s}}' % key, v)
    css = k.BASE.replace('{{RUNHEAD}}', runhead)
    html = (f'<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><title>{title}</title>'
            f'<style>{k.FONTS}{css}{k.EXTRA}</style></head><body>{body}</body></html>')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    day = date.today().isoformat()
    name = f'{day}_{next_version(out, day, slug)}_{slug}_guide'
    hp, pp = out / f'{name}.html', out / f'{name}.pdf'
    hp.write_text(html, encoding='utf-8')
    check = out / '_check'
    check.mkdir(exist_ok=True)
    shot = check / f'{name}_print.png'
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            br = p.chromium.launch()
        except Exception:  # noqa: BLE001 - on this PC Playwright drives the installed Chrome
            br = p.chromium.launch(channel='chrome')
        pg = br.new_page()
        pg.goto(hp.as_uri(), wait_until='networkidle')
        pg.wait_for_timeout(400)
        pg.pdf(path=str(pp), prefer_css_page_size=True, print_background=True)
        pg.emulate_media(media='print')
        pg.set_viewport_size(dict(width=794, height=1123))
        pg.screenshot(path=str(shot), full_page=True)
        br.close()
    pages = len(re.findall(rb'/Type\s*/Page[^s]', pp.read_bytes()))
    return dict(title=title, slug=slug, html=str(hp), pdf=str(pp), pages=pages, check_picture=str(shot))


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    print(json.dumps(build(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else KIT)))
