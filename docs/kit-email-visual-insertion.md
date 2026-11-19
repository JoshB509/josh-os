# Kit email visual insertion contract

Status: preparation, exact owner approval, orchestration and recovery are implemented and mock-tested. **No live Kit browser adapter is installed.** The default readiness response is unavailable; it cannot inspect, upload, paste, insert, schedule or send. No owner review UI or agent/browser permission expansion has been activated.

Kit supports clipboard image pasting and Image > Media Gallery uploads. These are the only planned write methods. There is no new public hosting service or invented upload API:

- https://kit.com/resources/blog/email-template-marketplace-announcement (clipboard support)
- https://help.kit.com/en/articles/3743270-media-gallery (local PNG upload and image metadata)

## Bound scope

`KitVisualInsertions.prepare(email_id, revision, broadcast_id, method=...)` reads through a trusted injected adapter. It requires an existing broadcast recorded as created by this email's guarded Kit workflow. The local email must be active and unprotected. The exact UI destination, private draft status, `public: false`, `send_at: null`, unpublished state, subject, preview and audience must be observed. A read-only `get_broadcast` result is cross-checked whenever usable; if unavailable, its reason is recorded and UI evidence remains mandatory. API verification that was available at review cannot silently disappear later.

V1 is deliberately limited to a linear paragraph document matching the saved linked email body. Each saved visual must have a unique PNG and a unique adjacent paragraph boundary; repeated ambiguous boundaries, layouts, preexisting remote images, unsupported HTML and remote/local copy differences stop preparation. Blank body lines count. The packet binds:

- Saved email id, revision and content hash.
- Existing owned broadcast id and exact destination.
- Supported UI method, immutable visual version/source/PNG hash, byte size and alt text.
- Insertion position and adjacent paragraph hashes.
- Exact normalized read-back baseline, including existing wording, links and formatting.

Packet content is data for escaped review, never executable model HTML. No caller supplies file paths, upload URLs, selectors or arbitrary browser actions. PNG bytes are resolved from committed email visual history and rehashed.

`OwnerReview` must be constructed by a trusted authenticated owner-review handler, never by deserializing agent output. Its owner identity, action, originating event, packet id and full packet digest must match. Generation, a preview request, a model assertion, an old approval or approval of another packet is insufficient. Approval and execution remain separate. No activation HTTP handler is installed in this change; a readiness-only patch is staged for later integration.

## Adapter obligations and evidence

`kit_visual_browser.py` defines a narrow `inspect` / `insert_image` contract and an explicitly unavailable default. The existing broad Chrome tool list and ChatGPT computer-use session are **not** a service adapter. Supplying an adapter is privileged application code; the Python Protocol is not an arbitrary-browser security sandbox.

A future production implementation must use supported visible Kit UI and bind to the exact account and broadcast. It must expose only image upload/paste, image insertion and alt-text controls, never generic navigation, scripting, scheduling or sending to Laia. It must stop on unexpected UI, authentication, account or destination changes. It must not retry mutations internally.

The coordinator rereads the draft after reload and checks local revision/assets before each image. The adapter must invoke `before_mutation` with a fresh UI observation immediately before every allowed mutation phase: `upload` then `insert` for Media Gallery, or `paste` for clipboard. Each phase is authorized once. Denied or expired callbacks stop the operation. The adapter records an `UploadReceipt` as soon as upload identity is known, before insertion for Media Gallery and immediately after clipboard paste. Approval covers that exact PNG upload and draft edit only.

Receipt evidence binds the actual upload's immutable PNG hash, Kit resource identity, observed source URL and a trusted adapter audit artifact. Reloaded UI must tie the inserted image source to that resource identity; alt text, placement and unchanged existing document are checked. CDN transformations are allowed through this observed identity, **not** by claiming remote JPG/PNG bytes equal the original. If Kit exposes no reliable resource identity/correlation, the adapter cannot claim verified completion. No network fetch or URL inference substitutes for evidence.

## State and recovery

- `not_started`: prepared or owner-approved, no insertion reserved. An unavailable adapter leaves it here.
- `insertion_uncertain`: reserved atomically before any possible UI mutation; includes interrupted/partial autosave or failed verification.
- `verified_complete`: complete trusted upload/phase evidence plus exact persistence after reload; read-only Kit evidence is checked when usable.

Events are stored separately in `kit_visual_events`; model success prose is never completion evidence. A second execution cannot retry an uncertain or completed packet. `recover` only inspects the exact draft. It never uploads, pastes, resets approval or blindly duplicates an image. Full matching persistence may be confirmed after interruption. Missing, partial, duplicated, altered or unidentified images remain uncertain and need manual reconciliation plus a separately reviewed future plan. Even when the baseline is unchanged, recovery does not automatically repeat an upload. There is no unlock endpoint.

Recovery can confirm the original packet after later local edits; `local_changed` explicitly distinguishes that historical completion from the current email revision.

## API preservation guard

`kit_drafts.py` checks the insertion ledger during proposal validation, approval checks and again inside its write reservation transaction. An uncertain or completed browser insertion blocks ordinary API content replacement by email or broadcast id, even after local visual references are removed. This avoids stripping inserted images. The browser coordinator likewise refuses to reserve while a Kit API write is running or uncertain. Existing local-only `body_html` and HTML export guards remain unchanged. A future image-preserving API update workflow needs its own reviewed design; this implementation does not permit it.

## Remaining live verification, requiring later authorization

1. Implement and audit a constrained supported browser adapter; verify current Kit UI observations/controls with a separately authorized test draft. Do not derive broad Laia Chrome permissions from this contract.
2. Wire an authenticated owner review page with escaped packet details, PNG previews, exact placement, baseline, method and explicit upload/draft-edit scope. Bind approval to an authenticated review nonce/event, not model-supplied identity. Stage/rebase app integration against concurrent changes before applying it.
3. Review and explicitly approve a packet for one exact owned unsent draft and PNG set. Only then test the supported upload/paste action, alt text and insertion position.
4. Verify after reload and read-only Kit retrieval: exact destination, source correlation, one copy per approved image, unchanged subject/body/preview/audience and private unscheduled state. Exercise an interrupted autosave without blindly retrying.
5. Activate code only at a separately authorized normal app restart. No restart, real email mutation, upload, browser editor interaction, credential change, schedule or send was performed during implementation.
