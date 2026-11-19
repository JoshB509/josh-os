# YouTube publishing and native A/B workflow

Josh wants Sam to handle the release after the final edit is done: use the YouTube Data API to upload the final video, add the approved title and description, set the thumbnail and schedule the release, then use Chrome for YouTube Studio's native A/B test.

## Current capability, never assume success

The existing Analytics connection stays read-only. The separate publishing API adapter is implemented, but Google publishing consent and a live end-to-end test are still pending until explicitly completed. Use the prepare_release action to prepare a packet and direct Josh to the Publishing review page. Only Josh may approve its exact packet through the explicit review control. Never click that approval control or produce approval yourself. Report returned job states accurately; preparation is not upload success. Do not silently substitute browser uploading for the requested API workflow. The adapter supports new scheduled uploads, not changes to existing videos.

## Prepare when Josh says the edit is done

CareerOS's video page is the one version of the release (19 Nov 2026). Publish uses exactly what it shows: the chosen title, the description and pinned comment in Packaging, the thumbnails in Main, B and C, and the approved final export. You are given that as the video's "publishing" block. Never write your own title, description or "package" for publishing and never keep a separate copy. If something is wrong, change it in CareerOS (draft the description again, ask Josh to choose the title or thumbnail) so the page shows it, then publish what the page shows. prepare_release takes only the video, unlisted or schedule, and Josh's three disclosure answers, and builds the packet from the page. Do not infer made-for-kids, paid-promotion or altered-content answers; ask Josh, or point him to the Publish panel on the video.

An unlisted upload needs no date or time. Never ask Josh for a time when he publishes unlisted. Only scheduling needs the release date, clock time and named timezone saved on the video; then show Josh the local time, timezone and UTC equivalent, including the daylight-saving offset, and flag ambiguous or nonexistent daylight-saving times before approval. Do not borrow the Analytics reporting timezone.

Prepare three approved thumbnail concepts for the first native Test & Compare round, including a short hypothesis and labels. If testing titles as well, show the exact title variants and get approval for that scope. If only thumbnails were requested, preserve the approved title.

## Review gate

Show one concise final release packet: channel, export, title, complete description, primary thumbnail, the test variants, audience/disclosures, and release date/time/timezone. Ask Josh to approve uploading and scheduling that exact packet and starting the specified native test. Record his explicit approval with the packet revision. Saying the edit is done is a preparation trigger, not approval of missing release details. A material change invalidates approval. Never create approval on Josh's behalf.

## API stage once the adapter is connected

Use a separate publishing credential; never broaden or replace the existing Analytics token/client. Start with the narrowest supported scopes. A new scheduled upload can use videos.insert with private status and a future publishAt value. Subsequent metadata/status edits need a scope accepted by videos.update and separate implementation; do not assume upload-only access covers them.

Use resumable upload. Bind the operation to the approved packet and full file hash, save the resumable session securely, and save the returned YouTube video ID immediately. On timeout or crash, recover/check the existing session and remote video before retrying. Never blindly start another upload. Fail closed on an uncertain result.

Set the approved primary thumbnail through the supported API. Read back the exact video ID and verify channel ownership, metadata, private status and scheduled UTC timestamp. Check upload and processing status. A successful HTTP request alone does not prove the schedule or processing is ready. If a thumbnail or later step fails, keep the existing ID and recover that step instead of uploading again.

Google removed the old private-only restriction for unverified API projects on 2 November 2026 (https://developers.google.com/youtube/data/revision_history?hl=en). Do not require an audit merely to publish. Verify API enablement, correct consent/channel and quota. OAuth configuration and any actual account restriction still need checking; never bypass an actual denial. Always set privacy explicitly to private with the approved future publishAt; Google's default can be public.

## Chrome native A/B stage

Only after the API stage has a verified video ID, open that exact video in the intended channel's YouTube Studio in Chrome. Inspect whether the video and channel support the requested native test. Private/scheduled status may prevent starting a test; if Studio does not allow it, record pending availability and explain when it can be revisited. Do not change privacy or bring forward publication merely to enable a test.

Use only the approved variants. If Studio shows different rules, missing eligibility or an unavailable control, stop and report the actual blocker. Do not simulate native A/B by manually rotating thumbnails. Save/start only within Josh's per-video approval. Verify the native test status after saving and keep a screenshot or precise visible status. Uploaded variants alone do not mean a test is running.

## Finish and results

Record the YouTube video ID and URL, API-confirmed schedule and timezone, processing state, test state (running, configured, pending eligibility, or blocked), hypotheses and variants in the video's existing results/workflow records. Tell Josh separately what is confirmed and what remains. Continue the existing scorecard and results-review workflow after release; don't fabricate metrics or test winners. Later test rounds need approval for their specific variants before starting.
