---
name: "lesson-slides"
description: "Build beat-by-beat HTML lesson slides for a Heard at the Top or CareerOS course script (space bar advances, diagrams build up, house design), save them to the E drive and add the CHANGE-cued script to the lesson's Notion page."
---

# Lesson slides

Use this whenever Josh asks for slides, visuals or "the HTML" for a course lesson. The output is one self-contained HTML file he opens in a browser while reading the script on camera. Every press of the space bar is one **beat**: a new scene, a new element, a highlight, a label. The slides carry diagrams that build, never a bullet deck.

The finished job has three parts, all of them required:
1. The HTML file, saved to the E drive.
2. Screenshots of every beat checked for overlaps and floating elements.
3. The Notion lesson page updated with the file path and a second copy of the script with red bold `CHANGE n` cues.

## 1. Get the script and the design reference

- Find the lesson page in Notion (search the lesson title; the pages live in Curriculum & Content / Heard at the Top · Build Tracker). Use the **latest script version on the Notion page**, not the copy in `E:\CareerOS\04_Programme\Course_Content\Heard_at_the_Top\Scripts`, because Josh edits in Notion after the file is written. Quote his words exactly in the cued script, typos included.
- Design reference is the two bonus PDFs/HTML in `E:\CareerOS\04_Programme\Course_Content\Heard_at_the_Top\Bonuses\` (Final-Check and Emergency-Meeting-Pack). Extract the eight `@font-face{...}` blocks (Outfit 500 to 800, Manrope 400 to 700, base64) from either HTML with a regex and paste them where `/*FONTS*/` sits in the template, so the slides work offline with the same type.
- A previous lesson's slide file in `...\Heard_at_the_Top\Slides\` is the fastest starting point: copy its head, engine and components.

## 2. Plan the beats before writing any HTML

Work through the script paragraph by paragraph and write a beat map: beat number, the words it fires on, what appears. Rules that came out of lesson 1.1:

- Beat 0 is the dark title slide. The first CHANGE fires on the first line of the hook.
- One beat per new visual idea, roughly one every 8 to 10 seconds of speech. A ten-minute lesson is around 65 to 75 beats.
- The cue goes **before** the words, so the change is on screen as he says them. A list of three things is three beats. A sentence that names a mechanism then its consequence is two beats (the thing, then the label or highlight).
- Every signpost between sections gets a dark divider slide (big faint section number, title, one line of pain or outcome in his words).
- Hook gets a simple scene (You, the thing you sent, Them, what came back). The three-bullet promise gets a dark list slide, one row per beat.
- Section content is diagrams: figures, cards, arrows, a box, before/after pairs, loops, ladders, chains. Text on a slide is a label, a short quote from the script, or one caption line. Never a paragraph.
- Each diagram should build inside one scene across several beats rather than cutting to a new slide for every sentence. Elements can also disappear or dim when the script moves on.
- The close gets a next-lesson dark slide with the next lesson's title and its promise line.

On-slide copy follows Josh's writing rules: no em dashes, no colons inside sentences, no "it isn't X, it's Y", no fragment lists, quotes in his words. Labels read like one side of a conversation ("Just the fact", "Still no decision"), never cryptic.

## 3. Build the HTML

Stage is 1920x1080, scaled to fit the window, background switches between white and ink with the scene. Use this engine.

**CSS tokens and rules that matter** (from the bonuses):
```
--white:#FFFFFF --canvas:#FAFAF8 --grey:#F3F2EF --grey-2:#ECEAE5 --ink:#15171C --text:#2B2D33 --muted:#6E717A --faint:#A3A6AD --gold:#F5C21B --gold-deep:#A87B05 --gold-tint:#FDF1C7 --gold-line:#EED27A --line:#E6E3DD --red:#C4453A --red-tint:#FBE4E1 --green:#2E8B57 --green-tint:#E3F3EA
Headings, big statements, chips: Outfit. Body, tags: Manrope.
.eyebrow  Manrope 700, 22px, letter-spacing .2em, uppercase, gold-deep (gold on dark)
.big      Outfit 800, 84px+, em = gold-deep (gold on dark)
h2        Outfit 700, 48px
.cap      Outfit 600, 40px, centred caption line, em = gold-deep
.tag      Manrope 700, 20px, .16em, uppercase, gold-deep (.mute grey, .red red)
.card     white, 2px --line border, radius 22, padding 26/34, 32px. .weak grey bg, .strong gold-tint, .gold solid gold, .ink dark
.bub      ink speech bubble, Outfit 600 34px, tail bottom-left (.r bottom-right). Use for what senior people say.
.pill     rounded label 26px; .red .gold .green .ink variants
.chip     56px gold circle with a number (.ink variant)
.stack    stacked-paper graphic: .sh sheets (78px, margin-top -58) then .top card. `.stack .top:first-child{margin-top:0}`
.person   SVG figure (circle head + shoulders) with .lbl and .sm; .you gold-deep, .them ink. Give it an explicit width (260 to 300px) so it is centred predictably.
.bbox     the black box: ink, radius 30, dot pattern + gold glow via ::before/::after, .ey eyebrow, .q big gold ?, .inside list of white items. Children are position:absolute (.ey top, .q centred through a .qwrap, .inside top:78px) so hidden ones never push visible ones around.
.arr      7px ink bar with a chevron head via ::after; .back (chevron on the left, faint), .down/.up (vertical), .dash, .gold, .red, .mute
Dark scenes: .dots (radial-gradient dot grid masked to the top), .glow, .glow2, .foot strip with two uppercase labels
Divider scenes: .div .pnum (520px faint gold number top right), .dv h1 110px
```

**Beat engine (the part that must not change):**
```html
<section class="scene [dark] [sN]" data-from="9" data-to="19">  <!-- visible for beats 9..19 -->
  <div class="abs card" data-b="10" style="--i:1">...</div>       <!-- appears at beat 10, staggered -->
  <div class="abs bub" data-b="11" data-off="12">...</div>         <!-- gone at 12 -->
  <div class="abs" data-b="1" data-dim="4">...</div>               <!-- dims to 18% at 4 -->
</section>
```
```css
.abs{position:absolute!important}   /* !important, or a later .bbox{position:relative} wins and the box lands 90px off */
.scene{position:absolute;inset:0;padding:90px;opacity:0;visibility:hidden;transition:opacity .35s ease, visibility 0s linear .35s}
.scene.on{opacity:1;visibility:visible;transition:opacity .35s ease}
[data-b]{transition:opacity .5s ease,transform .5s ease;transition-delay:calc(var(--i,0)*.14s)}
[data-b]:not(.on){opacity:0!important;transform:translateY(22px)!important}
.gone{opacity:0!important;transform:scale(.97)!important;pointer-events:none}
.dim{opacity:.18!important}
```
```js
const TOTAL=70; let b=0; const scenes=[...document.querySelectorAll('.scene')];
function render(){
  scenes.forEach(s=>{const f=+s.dataset.from,t=+s.dataset.to,on=b>=f&&b<=t;
    s.className=s.className.replace(/\bat-\d+\b/g,'').replace(/\s+/g,' ').trim(); s.classList.toggle('on',on);
    if(on) for(let n=f;n<=b;n++) s.classList.add('at-'+n);           // scene-level state, e.g. .s4.at-12 .facts .card{...}
    s.querySelectorAll('[data-b]').forEach(el=>el.classList.toggle('on',on&&b>=+el.dataset.b));
    s.querySelectorAll('[data-off]').forEach(el=>el.classList.toggle('gone',on&&b>=+el.dataset.off));
    s.querySelectorAll('[data-dim]').forEach(el=>el.classList.toggle('dim',on&&b>=+el.dataset.dim));});
  const cur=scenes.find(s=>s.classList.contains('on'));
  document.body.classList.toggle('dark',!!(cur&&cur.classList.contains('dark')));
  document.title=`LESSON · beat ${b} / ${TOTAL}`; /* + HUD counter and progress bar, hidden until H */
}
function go(n){b=Math.max(0,Math.min(TOTAL,n));render();}
// fit(): scale #stage (1920x1080) by min(innerWidth/1920, innerHeight/1080) and centre it
// keys: Space/Right/PageDown/Enter next · Left/PageUp/Backspace back · Home/End · digits+Enter jump · H toggles HUD · F fullscreen · click advances
// #n in the URL hash opens on beat n
```

Scene-level animations key off `at-N` classes on the section (`.s4.at-12 .facts .card{transform:translateX(560px) scale(.35);opacity:0}` swallows the fact cards into the box). Use a scene class like `s4` and a `<style>` block inside that section for these.

**Layout rules (each one fixed a real bug):**
- Never write two `style=""` attributes on one tag (a stagger `--i` in a second style attribute is silently dropped). Put `--i:n` inside the one style attribute.
- Captions live on a fixed baseline, `top:880px`, centred, and only one is visible at a time (use `data-off` on the earlier one). Anything else on the slide must end above ~830px.
- Everything on a slide sits on a shared horizontal axis or a shared column. Figures, arrows and boxes share one centre line (an arrow at `top:512px` means the box is 270..770 and the figure is centred on 520).
- Elements that appear later must not be laid out by flow next to hidden siblings; give them their own absolute position, or the hidden ones will push the visible ones around.
- Two columns whose pieces should line up (two bubbles, two stacks, two pills) get the same fixed heights: bubble `min-height:120px`, stack area `height:330px;display:flex;align-items:flex-end`.
- Text in the black box: at most four items at 24px in a 470x500 box.
- Diagram SVGs use a viewBox and `width:100%` inside their card, with everything drawn inside the box so nothing clips at the card edge. Put a start-dot label above the dot, not under it, where a wavy path might cross it.
- Big, not zoomed: card text 30 to 46px, statements 84 to 150px, figures 180px tall. Fill the 1920x1080 frame; a scene sitting in the top half looks unfinished.
- Avoid abstract metaphors (dots, clouds). Draw the mechanism: a wandering dashed arrow that never reaches the target for "drift", a straight arrow bent off course by a gold bar for "steered", cards swallowed into the box, a stack with the gold answer on top.

## 4. Check every beat

Render with Playwright in the container (fonts embedded, so the screenshots are true), one screenshot per beat, tiled into contact sheets, and look at every sheet:
```python
from playwright.sync_api import sync_playwright; from PIL import Image
with sync_playwright() as pw:
    br=pw.chromium.launch(); pg=br.new_page(viewport={'width':1920,'height':1080})
    errs=[]; pg.on('pageerror',lambda e:errs.append(str(e))); pg.goto('file://'+path)
    for b in range(TOTAL+1): pg.evaluate(f'go({b})'); pg.wait_for_timeout(950); pg.screenshot(path=f'shots/b{b:02d}.png')
# tile 9 per sheet at 640x360 and Read each sheet
```
Look for: overlaps, text cut at a card edge, elements floating with no neighbour to align to, uneven gaps between columns, captions colliding with the diagram, the same element at different heights in two columns. Fix, re-shoot the changed beats, then save.

Also confirm `pg.evaluate('b')` reaches TOTAL after TOTAL space presses and the page has no JS errors.

## 5. Save

Path: `E:\CareerOS\04_Programme\Course_Content\Heard_at_the_Top\Slides\YYYY-MM_part-P_lesson-P.N_slug_slides_vNN.html` (copy into `/mnt/user-data/outputs/` then `device_commit_files`). Bump the version for a rebuild rather than overwriting v01 once he has recorded with it.

## 6. Notion

On the lesson page, insert below the current script (use `update_content` with `old_str` = the heading of the next block down, e.g. `# Script v06`, and prepend):
```
# Script vNN · slide cues
<callout icon="🎬" color="yellow_bg">
	**Slides for this lesson:** `E:\...\slides_vNN.html`
	Open it in a browser and press F for full screen. It opens on the title slide. Each red **CHANGE** below is one press of the space bar, N in total, pressed as you reach the words that follow it. Left arrow goes back a beat, H shows a beat counter, typing a number then Enter jumps to that beat. The tab title always shows the beat.
</callout>
## Hook
<span color="red">**CHANGE 1**</span> You gave them every number they could need, <span color="red">**CHANGE 2**</span> and they still sent you away for more.
...
---
```
Cues are inline, immediately before the words they fire on, numbered so a lost place can be recovered from the counter. Keep his headings (Hook, Signpost, Section one, Close). Escape `[` `]` as `\[` `\]`. Add a short line to the page's Notes property ("Slides vNN built DD Mon (N beats), path and cued script on the page").

## Finish

Reply with the path, the beat count, how the file runs (space, left, F, H, tab title), and one or two things he may want to confirm (a beat that could split, a figure in square brackets). Send one screenshot of a mid-build beat so he can see the style without opening the file.