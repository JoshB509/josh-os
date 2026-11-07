---
name: careeros-playbook
description: "Build CareerOS downloadable PDFs in house style: playbooks, cheat sheets and worksheets for the Promoted in 6 programme, lead magnets and client handouts. Use whenever Josh asks for a playbook, cheat sheet, worksheet, one-pager or downloadable PDF."
---

# CareerOS downloadables

Build print-ready PDFs matching the Promoted in 6 house style. Reference set lives in `E:\CareerOS\04_Programme\Promoted_in_6\Downloadables\Sprint-One\`. Read one before starting if the format is unfamiliar.

## Before writing anything

Ask, unless the answer is obvious from the request:

1. **Format.** Playbook (15 to 25 pages, teaches a mechanism), cheat sheet (1 to 2 pages, dense reference for use while working), or worksheet (fill-in, produces an artefact for a call).
2. **Who it is for.** A named client, a sprint cohort, or a public lead magnet. This changes the sign-off and whether a sprint task appears.
3. **The one behaviour it changes.** Every document earns its place by changing one thing the reader does. Name it before drafting.

Then read `08_Strategy/audience.md` and `09_Email/voice-and-sales-language.md` for language, and check the relevant call transcript in `10_Sales/Calls/` if it is for a named client.

## Build process

Write a single self-contained HTML file, then render to PDF with headless Chromium.

```bash
pip install playwright --break-system-packages 2>/dev/null
python3 - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.goto('file:///path/to/doc.html', wait_until='networkidle')
    pg.pdf(path='/mnt/user-data/outputs/doc.pdf', format='A4',
           print_background=True, margin={'top':'0','bottom':'0','left':'0','right':'0'})
    b.close()
PY
```

Chromium is preinstalled at `/opt/pw-browsers/chromium`. Never run `playwright install`.

Always open the rendered PDF with Read afterwards and check every page. Page breaks are the most common defect.

## Design tokens

Use these exact values. Never substitute.

```css
:root{
  --paper:#FBF8F0;      /* page background */
  --cream:#F7F1E4;      /* tinted example boxes */
  --sand:#EDE6D3;       /* closing callout fill */
  --ink:#171A21;        /* dark blocks, covers, part dividers */
  --text:#2A2622;       /* body text on light */
  --muted:#8B8073;      /* captions, weak examples, page numbers */
  --brass:#A8813A;      /* accent on light backgrounds */
  --brass-lt:#E8C44D;   /* accent on dark backgrounds */
  --clay:#B0563B;       /* terminal step in a figure, the decision */
  --line:rgba(38,34,29,0.12);
  --line-soft:rgba(38,34,29,0.07);
}
```

Fonts: **Fraunces** for headings and pull quotes, **Inter** for everything else. Load from Google Fonts, with `serif` and `system-ui` fallbacks.

Page: A4, 22mm side margins, 18mm top and bottom. Body 10.5pt at 1.65 line height.

## Component library

**Eyebrow.** Inter, 9.5pt, uppercase, `letter-spacing:.16em`, weight 600. Brass on light, brass-lt on dark. Sits above every title.

**Cover.** Full-bleed ink page. Eyebrow (`SPRINT ONE · WEEKS 1 & 2`), Fraunces title at 46pt in paper colour, a 3px brass rule 70px wide, then a one-sentence promise in muted text at 11pt. Footer rule with `PROMOTED IN 6` left and `CAREEROS` right.

**Part divider.** Full-bleed ink page. `PART ONE` eyebrow, Fraunces title at 34pt, brass rule, two-line description. Content starts at roughly 40% down the page.

**Part header band.** The inset alternative to a full divider: ink block with 34mm padding, same contents. Use when the part is short.

**Running header.** Right-aligned, uppercase, letterspaced, muted, 8pt, document title. Every page except covers and dividers.

**Page number.** Centred, bottom, muted, 8pt.

**Section head.** Fraunces 17pt, weight 500. Optional 3px brass rule, 60px wide, directly beneath.

**Weak and strong pair.** The signature component. Eyebrow `WEAK` in muted, then a cream box with a 3px muted left border and muted text. Then eyebrow `STRONG` in brass, then a cream box with a 3px brass left border and full-strength text. The weak example must be a cliché people genuinely write. The strong example must be the same work, reframed, never a different task.

**Pull quote.** 1.5px brass rule above, Fraunces 15pt across the full measure, hairline rule below. One per part at most, holding the sentence the part exists to deliver.

**Dark callout.** Ink block, brass-lt eyebrow, paper text. Used for `WHAT YOU WILL PRODUCE THIS SPRINT` and `YOUR SPRINT`. Closes a playbook.

**Sand callout.** Sand fill, no border, bold lead-in sentence then supporting text. Closes a cheat sheet.

**Figure.** A flow of boxes joined by brass arrows. First box ink, middle boxes cream, terminal box clay. Each box has a bold label and a small muted sub-label. Beneath the figure: a 7.5pt muted note explaining the mechanism in one sentence, then a centred caption `Figure 1 · Short description` in muted 8.5pt. Number figures sequentially through the document.

**Timeline figure.** A horizontal rule with brass dots, labels above and time markers below, the final dot in clay and larger. Used to show compounding over months.

**Checklist.** 12px square outline boxes with 1.5px `--line` border, 10pt text, generous row spacing.

## Cheat sheet layout

Two columns. Dark header band across the top with eyebrow, Fraunces title, and a one-line instruction on how to use it. Then a `TOOL ONE` through `TOOL FOUR` grid, each with an eyebrow, Fraunces title, a one-line definition, and a weak and strong pair. Then a row of four small ink cards breaking down a framework. Then bonus moves as cream boxes with brass left borders. Close with a sand callout. Footer: `Title · Cheat Sheet · CareerOS`.

A cheat sheet is used while working, so it must be dense and scannable. No paragraphs over two lines.

## Voice

Josh's global writing rules apply and are non-negotiable. On top of those:

**Second person throughout.** The reader is "you". Josh appears as "I" only when telling a story that happened to him.

**Explain the mechanism before giving the line.** Why it works comes first, then what to say. A line without a mechanism gets used once and abandoned.

**Name real people and real artefacts.** Sarah, Priya, the churn analysis, Thursday's session, the steering committee. Never "a stakeholder" or "your manager" where a name would work.

**Numbers are specific.** Four tools, three traits, fifty handovers a month, roughly twelve real replies. Never "several" or "a number of".

**Link each section to the next.** End on the bridge: "which is the third tool", "and that leads into the second tool".

**British spelling.** Realising, recognise, favour, behaviour.

**Expectations stay honest.** Say when most attempts will do nothing. The reference cheat sheet says fifty handovers produces about twelve replies and one or two that grow into something. That candour is what makes the rest credible.

**Never claim client results.** There are none yet. Proof is Josh's own history, the YouTube audience, and reader-reported change.

## Structure of a playbook

1. Cover
2. **Before you start.** What the document covers, what it will produce, how to read it. Ends on a dark callout naming the sprint output.
3. **Part one, the problem.** Why the reader's current behaviour produces nothing. Mechanical, not motivational. At least one figure.
4. **Part two, the tools.** One section per tool. Each: what it is, why it works, weak and strong pair, bridge to the next.
5. **Part three, in the formats they use.** The tools running inside a real email, a meeting, a message.
6. **A worked story.** Something that started small and compounded. Timeline figure.
7. **Closing dark callout.** What to produce and bring to the call.

## Naming and filing

`YYYY-MM_topic-slug_playbook_v01.pdf` or `_cheat-sheet_v01.pdf` or `_worksheet_v01.pdf`.

Programme material goes in `04_Programme/Promoted_in_6/Downloadables/<Sprint>/`. Lead magnets go in `05_Lead_Magnets/<Magnet>/Asset/`. Something built for one client goes in `01_Clients/<Name>/03_Deliverables/`.

Keep the HTML source beside the PDF so it can be edited rather than rebuilt.

## Quality checklist

Before delivering, read the rendered PDF and confirm:

- No heading orphaned at the foot of a page, and no weak and strong pair split across pages
- Figures sit on one page with their caption
- Every figure has a note and a sequential caption
- Every weak example is a real cliché, and every strong example is the same work reframed
- No em dashes anywhere
- Running header and page number on every content page
- Exact token colours, with no invented shades
- The document changes one named behaviour, and a reader could act on it this afternoon