# Kit email visual status — 16 November 2026

**The authenticated owner-review UI and the supported read-only browser adapter are
implemented. Image upload/insertion remains disabled.** No operational insertion or
upload success is claimed. Paragraph focus and the plus menu's Image entry are now
live-observed. The running app was not restarted.

## Callable, observed read path

`kit_chrome_worker.py` runs one persistent, dedicated Claude-in-Chrome session with
immutable exact-call, single-use PreToolUse guards and raw PostToolUse receipts. It grants no
general Chrome capability to Laia. File upload, paste, typing, scheduling, publishing,
sending and other content-edit actions are absent. Tab operations require IDs returned
by this session's own supported tab-context tool. The bounded inspection sequence can
focus a freshly observed non-link paragraph, open its exact observed plus icon, and
open the documented Image entry solely to inspect Media Gallery. It cannot choose an
asset, file, form or other content-block entry. Capabilities expire and are single-use;
validation and transport run under one lock. Real click receipts must confirm the exact
ref/coordinate. A completed call without valid evidence permits only owned-tab cleanup.

`kit_chrome_browser.py` now reads the exact private unsent broadcast through the
supported Kit `get_broadcast(id, include=["content_tree"])`, then checks its editor with
supported scoped `read_page` and `get_page_text`. It does not extract editor DOM, cookies,
query-string collections, private runtime state or model-generated HTML. It rechecks
the API baseline and closes its positively owned tab before returning.

The separate `inspect_image_menu(broadcast_id, after_line=...)` method uses the
observed paragraph/plus recipe and stops at the Image entry. `kit_image_recipe.py`
binds unique non-link text to exact adjacent paragraph hashes; blank, link-first,
before-first, ambiguous and changed targets fail closed. The plus capability requires
the observed SVG glyph and visible button geometry from one fixed metadata script.
No arbitrary JavaScript, typing or model-supplied selectors are accepted. This method
is not a general Laia tool and is not an insertion authorization.

`kit_editor_observation.py` validates the observed linear paragraph/text/link schema.
It checks the complete rendered wording inside the observed editor/footnote boundaries
and every body href in order. Unknown blocks, conversion warnings, denied/truncated
observations, extra wording and unfamiliar boundaries fail closed. Kit's ordinary HTML
tables/style wrappers are handled through the clean structured body rather than guessed
or stripped. Empty paragraph/caret filler and builder-added safe link attributes have
narrow canonical treatment. Exact wording, paragraph boundaries, link destinations,
marks and insertion anchors remain bound. Template and existing-footer hashes are
included in baseline/approval checks.

An end-to-end run of the actual reader on draft **26402089** succeeded: complete wording
and links matched, status was `draft`, `public` was false and `send_at` null. The API
content/subject/preview/audience/private-unscheduled baseline remained unchanged. Its
owned tab **1340484148** was closed successfully. This was a read, not an approval or
write. Raw evidence and hash-only validation reports are in the isolated task workspace
`kit-supported-observation`, not public storage.

## Owner review and integration

`kit_visual_review.py` requires the existing authenticated owner browser session,
rejects agent API-token callers and enforces same-origin writes. Single-use nonces bind
the session, action, packet ID and digest. The host constructs OwnerReview identity.
The page shows the exact existing draft, subject, preview, audience, PNG preview/version/
hash/alt and adjacent paragraph text. Preparation is read-only; approval and execution
are separate actions. Upload buttons remain disabled because insertion readiness is
false. The existing ownership, revision, local-image, recovery and API-stripping guards
remain intact.

The small `server.py` wiring and `web/email-view.js` review link are integrated after
exact preimage/conflict checks. No unrelated CSS, homepage, YouTube or agent changes
were overwritten. The source is ready for the next separately authorized application
restart; the old running process does not gain new routes without reloading.

## Inspection cleanup and earlier blocked path

The supported tab-context/close docs say that only the current session's group can be
controlled. There is no supported recovery/adoption tool for ended sessions. Earlier
tab **1340484142** (draft 26402091) therefore could not be recovered or closed safely.
No ownership was forced, no unrelated tab was touched, and **Take over editing** was
never used. Other earlier ended-session inspection tabs were 1340484141 (dashboard),
1340484143 (New Tab), 1340484144 (lock screen) and 1340484145 (dashboard).

New inspection tabs **1340484146**, **1340484148**, **1340484150** and **1340484152** were positively
identified and successfully closed while their sessions were alive. The code now does
this routinely. Tab 1340484146 was used for permitted text/link/API observation and one
formatting-menu inspection; tab 1340484150 was screenshot-only, and tab 1340484152 added a non-mutating hover over an existing blank paragraph boundary. Neither screenshot nor hover exposed an Image/Media Gallery control. API content and relevant
metadata were unchanged before/after both runs.

The next inspection tabs **1340484154**, **1340484156** and **1340484158** were also
closed successfully. Their API baselines were unchanged after paragraph focus,
visible-button metadata inspection and opening the plus menu respectively.

A later attempt stopped before the metadata read because the worker's own prompt
contradicted its fixed-script allowlist. The transcript confirms no tool invocation
for that read; no Image entry or upload was reached. The old fail-closed behavior
ended that session before cleanup, leaving its positively identified tab
**1340484160**, draft **26402089** ("The founding price goes on Saturday"), open.
The prompt inconsistency is corrected, and completed failed calls now allow only
closing their positively owned tabs before process shutdown. One retry encountered
Kit's editing lock; it did not take over, and its own tab **1340484161** was closed.
Supported tools cannot recover/adopt tab 1340484160 from its ended session. That tab
needs ordinary owner closure before continuing on this draft; no unrelated tab should
be closed. The earlier ended-session cleanup limitation still applies.

The earlier broad DOM read returned `[BLOCKED: Cookie/query string data]`. That path is
disabled in the transport. It was not retried through encoding tricks or private APIs.
Independent supported accessibility/plain-text/API observations were subsequently
permitted and sufficient for the bounded text-body baseline. A Windows hook UTF-8
decoding bug was also fixed and regression-tested so normal permitted tool responses
retain exact Unicode; this is unrelated to bypassing the blocked DOM response.

## Remaining insertion blocker

The actual supported UI now establishes: click an existing unique paragraph without
editing -> visible plus button -> content menu -> Media / Image / "Upload or add from
a 3rd party". The plus menu was opened in tab 1340484158; no block was inserted and the
full API baseline stayed unchanged. Its menu subtree and non-personal button metadata
are captured in `tests/fixtures/kit_chrome/image_menu.json`.

The next non-mutating observation is the documented Image -> Media Gallery transition,
currently blocked by the draft lock above. That transition has not been live-verified;
the worker supports only its exact observed Image menu ref, with mocked envelope tests.
An existing `type=file` control in the broad accessibility tree is not sufficient proof:
it may be hidden/unrelated and must not be used as an upload target.

After the gallery can be read, identify its actual local-file input and inspect alt
metadata controls without saving changes. The first future write needing later exact
owner authorization is supplying the approved immutable PNG to that observed input
(which uploads to Kit), followed by selecting that exact uploaded resource into the
approved draft boundary and saving its approved alt text. Placement, auto-save,
transformed CDN/resource identity, clean image content-tree schema, and persistence
after reload cannot be claimed until that controlled authorized test. None has occurred.
Do not invent selectors, hidden upload endpoints or public hosting. `upload_image`
accepts screenshot IDs, not the immutable source PNG, and is not a substitute.

After that recipe is validated against supported tool fixtures, a later owner action
must approve the exact owned unsent draft, image version/hash, alt, placement and saved
baseline before any live upload. Verify persistence after reload plus read-only API
identity and unchanged wording/links/preview/audience/template/private-unscheduled
state. Track trusted UI/upload source evidence if Kit transforms bytes. Uncertain runs
must inspect for an existing image and never blindly duplicate it.

## Windows permit-file failure

The original shared `policy.tmp.replace(policy.json)` fails reproducibly in the
sandbox's default temp directory: 30/30 exact-transport tests failed with WinError 5,
while the same 30 passed in workspace temp. A held old policy reader can also prevent
Windows replacement. This is not treated as a harmless transient failure.

Each call now exclusively creates immutable policy, ready, claimed, receipt-complete
and retired files. The actual hook `tool_use_id`, policy hash, exact tool and exact
arguments bind receipts, so a late response cannot satisfy a later identical call.
No overwrite, delete, alternate permission mode or guard relaxation is used. Active
permit ambiguity, changed policy, replay, incomplete receipt and unclaimed response
fail closed. Stress tests cover both temp roots and concurrent claims/clicks.

Applied-repository validation additionally found that Windows argv quoting left the
no-space repository hook path unquoted, and Git Bash removed its backslashes before
Python could start. The hook command now uses shell-quoted forward-slash paths. A
local integration executes the actual gate under Git Bash with both a no-space script
path and an audit directory containing spaces and a literal dollar sign. Run it with
`KIT_TEST_GIT_BASH=1` outside the restricted DLL sandbox; that sandbox cannot start the
Git Bash executable (0xC0000142). The ordinary unit suite does not require a native
shell. No hook, approval, or permission protection is bypassed when invocation fails.

## Tests

187 focused Python tests (including the explicit local Git Bash integration) cover actual server dispatch behind authentication,
paired/session nonce boundaries, supported response fixtures, UTF-8 hook receipts,
canonical structured-body validation, template/footer changes, insertion approval and
recovery, email links, Laia email, Kit draft/recovery and carousel regressions. The
new tests also exercise immutable Windows permits, late identical responses, completed
failure cleanup, exact observed menu permissions, unique anchors and concurrent clicks. The
previous isolated headless owner-review UI test also passed with all traffic mocked.
Fixtures containing email text are anonymized; raw real observations remain only in
the task workspace. No real draft, upload, paste, scheduling, sending, credential or
security-setting changes were made.

Official workflow references:
- [Kit Media Gallery](https://help.kit.com/en/articles/3743270-media-gallery)
- [Kit image/content menu and alt controls](https://help.kit.com/en/articles/4951536-the-email-editor-adding-media)
- [Claude Code hook tool-use identity](https://code.claude.com/docs/en/hooks)
- [Kit clipboard image support announcement](https://kit.com/resources/blog/email-template-marketplace-announcement)
