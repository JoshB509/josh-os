# Tokens

The canonical values live in `studio/web/tokens.css`, which loads after `brand.css`. New components must use these tokens instead of raw values.

## Type

Fonts: `--display` is Outfit for headings and names, and `--font` is Manrope for everything else.

| Token | Size / line height | Use |
|---|---|---|
| `--fs-caption` | 12px / 1.4 | Metadata, timestamps, counts |
| `--fs-small` | 13px / 1.45 | Secondary lines, chips, labels |
| `--fs-body` | 15px / 1.55 | Body text and form fields |
| `--fs-lead` | 16px / 1.55 | A team member's message |
| `--fs-sub` | 18px / 1.3 | Card titles, people's names |
| `--fs-h2` | 24px / 1.25 | Section headings |
| `--fs-h1` | 32px / 1.15 | Page titles |
| `--fs-display` | 36px / 1.1 | The Today greeting |

Weights: 400 for body, 500 for list titles, 600 for headings and names, 700 for page titles only. Letter spacing is -0.015em on h1 and display sizes and 0 elsewhere. Reading text keeps a `--measure` of 62ch.

## Spacing (4px base)

`--sp-1` 4 · `--sp-2` 8 · `--sp-3` 12 · `--sp-4` 16 · `--sp-5` 24 · `--sp-6` 32 · `--sp-7` 48 · `--sp-8` 64px.

Use `--sp-2` between related items in a group, `--sp-4` for padding inside a card, `--sp-5` between cards, and `--sp-6` between page sections.

## Shape and depth

`--r-sm` 6px for chips and inputs, `--r-md` 10px for list rows and buttons, `--r-lg` 16px for cards, `--r-full` for avatars and pills. Use `--card-shadow` for resting cards and `--lift` for hover.

## Colour

| Token | Light | Dark | Use |
|---|---|---|---|
| `--text` | #22242A | #E6E6E4 | Body text |
| `--ink` | #15171C | #ECECEA | Headings, primary buttons |
| `--muted` | #5F626A (6.1:1) | #A3A6AD | Secondary text |
| `--faint` | #6C6F77 (5.0:1) | #8A8D94 (5.3:1) | Metadata, placeholders |
| `--gold` | #F5C21B | #F5C21B | "Needs you" marker and focus halo, never text |
| `--agent` | per person | per person | Photo ring and desk edge only |

Focus shows as a 2px ink outline with a gold halo.

## Layout

The page content is capped at 1180px with side padding of 40px on desktop and 16px on phones. Breakpoints are 760px (phone tab bar and stacked layout) and 1100px (two-column Today). Tap targets on phones are at least 44px tall.
