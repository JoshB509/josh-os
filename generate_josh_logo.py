"""Render the native SVG with installed Chrome; build all Windows ICO frames.

Usage: installed Python with Pillow and Playwright, generate_josh_logo.py.
No downloads or production services. JoshOS.svg is the editable source.
"""
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent
with sync_playwright() as p:
    browser=p.chromium.launch(channel='chrome',headless=True,args=['--disable-background-networking','--disable-sync'])
    page=browser.new_page(viewport={'width':1024,'height':1024},device_scale_factor=1)
    page.set_content('<style>html,body{margin:0;background:transparent}svg{display:block;width:1024px;height:1024px}</style>'+ (ROOT/'JoshOS.svg').read_text())
    page.screenshot(path=str(ROOT/'JoshOS-master.png'),omit_background=True)
    browser.close()
with Image.open(ROOT/'JoshOS-master.png') as master:
    im=master.convert('RGBA').resize((256,256),Image.Resampling.LANCZOS)
    im.save(ROOT/'JoshOS.png')
    # Existing /icon.png route reads this compatibility filename. No server reload.
    im.save(ROOT/'DaJoshiResolve.png')
    im.save(ROOT/'JoshOS.ico',sizes=[(s,s) for s in (16,20,24,32,40,48,64,128,256)])
print('Rendered SVG, 256px PNG, and ICO frames: 16,20,24,32,40,48,64,128,256')
