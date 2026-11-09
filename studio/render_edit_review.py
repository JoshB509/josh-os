"""Take a picture of each animation after every press, with the stand-in of Josh where the camera goes.

Run as a script: python render_edit_review.py <job.json>
job.json holds {"slides", "out", "standin", "scenes": [{"n", "start", "end", "type"}]}.
Prints {"images": {"<n>": ["anim01_b1.jpg", ...]}} on its last line.

Transitions are switched off and running animations jumped to their end, so each picture
shows the settled state after that press. The deck's own counter and progress bar are hidden.
"""
import base64
import json
import pathlib
import sys

# The camera opening in the 1920 by 1080 design (from the deck's own layout).
PIP = dict(left=1322, top=202, width=518, height=674)
SETTLE = """
*{transition:none!important;transition-delay:0s!important;animation-delay:0s!important}
#hud,#prog{display:none!important}
#joshos-cam{position:absolute;z-index:9999;pointer-events:none;background-repeat:no-repeat}
#joshos-cam.pip{border:6px solid #F5C21B;border-radius:28px;box-shadow:0 10px 32px rgba(0,0,0,.28)}
"""


def camera_js(kind, standin_uri):
    # The stand-in is 1914 by 1073 with Josh's face about 47% across, so each opening is cropped round him.
    if kind == 'pip':
        p = PIP
        size = f"auto {p['height']}px"
        width = p['height'] * 1914 / 1073
        x = -(width * 0.475 - p['width'] / 2)
        box = f"box-sizing:border-box;left:{p['left'] - 6}px;top:{p['top'] - 6}px;width:{p['width'] + 12}px;height:{p['height'] + 12}px"
        cls = 'pip'
    elif kind == 'half':
        width = 1080 * 1914 / 1073
        x = -(width * 0.475 - 480)
        size, box, cls = 'auto 1080px', 'left:960px;top:0;width:960px;height:1080px', 'half'
    else:
        return ''
    return f"""(()=>{{let c=document.getElementById('joshos-cam');if(!c){{c=document.createElement('div');c.id='joshos-cam';(document.getElementById('stage')||document.body).appendChild(c)}}
      c.className='{cls}';c.setAttribute('style','{box};background-image:url({standin_uri});background-size:{size};background-position:{x:.0f}px 0');c.style.display='block'}})()"""


def main(job):
    from playwright.sync_api import sync_playwright
    out = pathlib.Path(job['out'])
    out.mkdir(parents=True, exist_ok=True)
    standin = pathlib.Path(job['standin'])
    uri = 'data:image/webp;base64,' + base64.b64encode(standin.read_bytes()).decode()
    images = {}
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception:
            browser = pw.chromium.launch(channel='chrome')
        page = browser.new_page(viewport={'width': 1920, 'height': 1080})
        page.goto(pathlib.Path(job['slides']).as_uri())
        page.add_style_tag(content=SETTLE)
        page.evaluate('document.fonts.ready')
        for s in job['scenes']:
            names = []
            for k, b in enumerate(range(s['start'], s['end'] + 1), 1):
                page.evaluate(f'typeof go==="function"?go({b}):(b={b},render())')
                page.evaluate("document.getAnimations().forEach(a=>{try{a.finish()}catch(e){}})")
                cam = camera_js(s['type'], uri)
                page.evaluate(cam if cam else "(()=>{const c=document.getElementById('joshos-cam');if(c)c.style.display='none'})()")
                page.wait_for_timeout(60)
                name = f"anim{s['n']:02d}_b{k}.jpg"
                page.screenshot(path=str(out / name), type='jpeg', quality=82)
                names.append(name)
            images[str(s['n'])] = names
        browser.close()
    return images


if __name__ == '__main__':
    job = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
    print(json.dumps({'images': main(job)}))
