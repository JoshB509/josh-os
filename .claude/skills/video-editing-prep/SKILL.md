---
name: "video-editing-prep"
description: "Turn a finished video script (VSL, YouTube or sales video) into the two editing assets: an HTML animation deck of numbered segments and a two-column script and shot list."
---

# Video editing prep

Josh films the script as talking head (A-roll). This skill takes the finished script and produces the two things he needs to edit it:

1. **The animation deck.** One self-contained HTML file. He opens it in a browser, presses space through it and screen-records it as one pass, then cuts each animation into the edit where the shot list says.
2. **The shot list.** The full script in a two-column table. Column A is the script segment. Column B is the shot, either "Talking head" or "Insert Animation N" with a short description and the words each press lands on.

The reference build is `E:\CareerOS\10_Sales\VSL\2026-10_executive-communication-program_vsl_slides_v01.html` with its shot list beside it. Copy its head, CSS, beat engine and components rather than starting from nothing.

## 1. Get the script

Use the latest version Josh points to, usually on the E drive or in Notion. If two versions exist, use the newest and say which one in your reply. Quote his words exactly in the shot list.

## 2. Decide what needs an animation

This is the first and most important step. Most of the video stays as talking head, and an animation only goes in where it helps the viewer see something.

- **Open on his face.** The first lines of the hook are always talking head, so the viewer sees him before anything appears. The first animation comes after that.
- **Good candidates.** The promises at the end of the hook, proof (numbers, reviews), a before and after, a loop or contradiction, a list he counts through, a mechanism hinted at (four cards with question marks), the objections he names, what's in the offer, bonuses and the CTA.
- **Leave as talking head.** Stories, jokes, emotional beats, transitions, the doubt and the turn in a story, and anything where his delivery carries it.
- Aim for something on screen every 20 to 40 seconds of speech, never two in a row that say the same thing. Single cards (below) fill the gaps between the bigger animations, so a ten minute video has many more visual moments than a handful of animations.

### Josh's patterns, every video

- **"In this video" is always half and half.** Where the hook says what's coming ("in this video I'll show you", "here's what you'll learn", "by the end of this"), it's a half and half animation, with each promise building on its own press on the left and Josh on the right.
- **"Watch this next" is always half and half, left blank.** On the closing invitation to watch another video, it's a half and half end screen. The left half stays completely blank (no text, thumbnail, arrow or placeholder), because Josh adds the YouTube end screen there. Hold it to the end of the video.
- **Single cards for emphasis.** When he lands a point worth remembering, put one short phrase on a full screen card on the words he says it with. It's one card, one idea, three to eight words, with at most a gold highlight or a strike-through. Use these often.
- **Develop an animation in parts.** Don't hold a PiP or full screen animation on screen through a long explanation. Build its first presses, cut back to talking head while he explains ("Back to full-frame talking head from …"), then come back to the same animation where it left off ("Back to Animation N") and build its next presses. It's one scene in the slides with its presses numbered straight through, and the shot list marks every time it leaves and comes back. A running device, like a tracker of the N tips or a board that fills up, works the same way across the whole video.
- **Make it visual.** Prefer a picture of the idea (two people in a meeting, a chessboard, a chart, a before and after) to a sentence on a card. Text-only scenes are for single cards and lists.

## 3. The animation types

There are no title slides, section dividers, dark cards or big headers. Only these types are used.

**Full screen.** Plain white frame with simple text, simple icons and small amounts of animation (things appearing, staggered, a strike-through, a highlight). Used for stats, review cards, lists and the CTA.

**Picture in picture.** A fixed block on the right is left free for his camera, and the animation sits to its left. The block must be in exactly the same place on every PiP animation. It is one element outside the scenes, shown whenever the current scene is PiP:

```css
#pip{position:absolute;left:1322px;top:202px;width:518px;height:674px;background:#D6D2C9;display:none;z-index:50}
body.pip #pip{display:flex}
```

That position matches the box in his PowerPoint layout on a 1920 by 1080 frame. PiP content lives between x 110 and 1240, centred vertically on the box (roughly y 220 to 880).

**Half and half.** Josh is in the right half of the frame and the animation fills the left half on a white panel. The scene has the class `scene half`, and one element outside the scenes marks Josh's half:

```css
#half{position:absolute;left:960px;top:0;width:960px;height:1080px;background:#D6D2C9;display:none;z-index:50}
body.half #half{display:flex}
```

Content lives between x 110 and 850, centred vertically. In the shot list, write "Slide your camera to the right half from …" on the words it starts on, then "Insert Animation N (half and half, you on the right half)". The watch-next end screen is a half and half scene with no content in it at all, marked "(half and half, end screen, left half blank)".

## 4. Segments and beats

- Each animation is preceded by a **number card**. It's a plain grey (`--grey`) frame with the big number, the type ("Full screen", "Picture in picture" or "Half and half") and a short name. This lets him find each animation on the recording and cut it in. Beat 0 is the card for Animation 1.
- After the card, each press is one beat that adds the next element. Plan the beats against the words they land on, with roughly one press per idea.
- After the last beat of an animation, the next press shows the next number card.
- Controls: space, right arrow or click to go forward, left to go back, H shows the beat counter, F for full screen, a number then Enter jumps to that beat, and the tab title shows the beat.

## 5. Design

Use the house white-and-gold style from the bonuses (Final Check, Emergency Meeting Pack). The fonts are Outfit and Manrope, embedded as base64 `@font-face` blocks taken from any Heard at the Top slide file in `04_Programme\Course_Content\Heard_at_the_Top\Slides`. Use these tokens: `--ink #15171C`, `--gold #F5C21B`, `--gold-deep #A87B05`, `--gold-tint #FDF1C7`, `--grey #F3F2EF`, `--line #E6E3DD`, `--red #C4453A`, `--green #2E8B57`.

The components that worked are cards (plain, weak grey, strong gold-tint, solid gold), pills, gold number chips, ink speech bubbles for what senior people say, paper stacks, arrows, review cards with five gold stars, quote and name, stat blocks (big number with a label), small icon tiles for bonuses, a gold button with a down arrow for the CTA, and representative mini screens (for example a grid of little lesson thumbnails for a lesson count).

Layout rules that came out of feedback:
- Don't cramp it. Give each element its own row with real space between, and keep text at 30 to 46px on cards and 44 to 60px for statements. If a scene feels tight, cut an element rather than shrinking everything.
- Nothing wraps unless it's meant to. Widen the card instead.
- Content is centred on the frame (full screen), on the space left of the camera block (PiP) or on the left half (half and half), never sitting in the top half.
- Elements that appear later get their own absolute position, so hidden ones never push visible ones around.
- Show the mechanism by hinting at it, never revealing it. Question-mark cards for "the 4 things" pass, and the actual four things written out fail. The same goes for reviews. If a real review gives the method away, show only the part with the result and say so in your reply.
- Reviews are real, taken word for word from the sales page capture or the Reviews docs in Google Drive (search the reviewer's name). Never invent a quote.
- On-screen text follows Josh's writing rules (no em dashes, no colons splitting sentences, no fragment lists). It has no square brackets either, because anything to confirm is raised in the reply, not left on screen.

## 6. Check every beat

Render with Playwright in the container, one screenshot per beat with about 1 to 2 seconds of wait for transitions, tiled nine to a contact sheet, and look at every sheet. Check for overlaps, wrapping, cramped rows, content sitting too high, and anything touching the camera block. Fix, re-shoot the changed beats, and confirm that pressing space TOTAL times reaches the last beat with no JS errors.

## 7. The shot list

A markdown table, `| Script | Shot |`, with a bold row for each script section heading.

- Column A is the script segment, word for word, with paragraphs joined by `<br><br>`.
- Column B is either `Talking head` or this format:
  `**Insert Animation N** (full screen)` or `(picture in picture, you in the right-hand box)`, then one line describing what builds, then one line per press, `Press 1 on "…"`, quoting the words it lands on.
- Where a paragraph starts as talking head and cuts to the animation partway through, say so ("Talking head, then cut to the animation from '…'").
- Animation numbers run 1 to N in script order and match the number cards in the deck exactly.
- The top of the file gives the deck's path, how to record it, and the camera block position.

## 8. Save and send

Save both files to the E drive next to the script's project (VSLs go in `E:\CareerOS\10_Sales\VSL\`), named `YYYY-MM_<project>_<video>_slides_vNN.html` and `YYYY-MM_<project>_<video>_shot-list_vNN.md`. Bump the version once he has recorded with one. Send both with SendUserFile plus one screenshot of a PiP beat. Then confirm they're saved on the E drive.

## Finish

Reply in a few lines with how many animations there are (full screen, PiP, half and half and single cards), the total presses, how many come back after a cut to talking head, where the files are, and anything for him to confirm (a figure, a trimmed review, a beat that could split). When he asks for changes, update the deck and the shot list together so the animation numbers always match, re-check the changed beats, and save both again.