# Sam YouTube publishing

## Implemented

A separate YouTube Data API uploader, encrypted publishing OAuth, durable approval-gated jobs, a review page, Sam preparation action, resumable upload recovery, thumbnail upload and remote verification. Analytics code/client/token remain unchanged. No credentials, consent, real upload or restart occurred during development.

Sam prepares release details. Josh opens Publishing in JoshOS, reviews the full packet and explicitly approves upload/scheduling. Sam's schema cannot approve. Browser approval requires an authenticated session, same-origin browser headers and a one-use nonce bound to the packet digest. This is a local trusted-app interaction gate, not an identity-proof system against someone with local OS/code access. No model may click the approval control on Josh's behalf.

## One-time publishing setup

1. In the intended Google Cloud project, verify YouTube Data API v3 is enabled. Create a separate Desktop OAuth client rather than modifying the working Analytics client.
2. With Josh's permission to establish publishing access, run:

    python -B -m studio.youtube_publish_auth --connect "C:\path\to\desktop-client.json"

3. Josh chooses the intended Google/Brand Account and approves upload + read-only YouTube access. The exact requested scopes are youtube.upload and youtube.readonly. The callback uses loopback, PKCE and state. The OAuth client/token are encrypted with this Windows user's DPAPI in Data/youtube-publishing, separate from Data/youtube/oauth.dpapi. Never paste credentials into chat.
4. Coordinate a JoshOS restart to load the new routes. No running process is automatically restarted by these files.
5. Use one explicitly approved release to verify live behavior. Unit tests are not proof of Google/channel permission or successful publication.

Google removed the unverified-project private-only restriction on 2 November 2026. An audit is not automatically needed just to publish. API enablement, OAuth grant, channel identity, quota and any actual account restriction still require checking. Source: https://developers.google.com/youtube/data/revision_history?hl=en

## Release behavior

- The packet includes exact channel, full export SHA-256, title, description, primary thumbnail and three distinct PNG/JPEG variants, category/language, audience and disclosure decisions, future local datetime, IANA timezone and explicit UTC offset. DST gaps/wrong offsets are rejected.
- MP4/MOV/M4V exports are supported by this first adapter. Asset existence/content and image format are checked; actual encoding/processing remains YouTube's check.
- Review must be explicit. The same digest returns the same job. An already-approved same-file/channel job prevents a changed packet from silently creating another upload.
- The insert explicitly sets private status and the approved future publishAt. It also requests subscriber notifications. Editing/rescheduling existing videos is intentionally not implemented; that would require broader permissions.
- Jobs live in a SQLite ledger. Resumable session URLs are DPAPI-encrypted. Per-job OS locks prevent concurrent runs and release on process exit. Responses/errors never expose tokens or session URLs.
- Retry recovers the saved session/server offset or existing video ID. Uncertain initialization without a durable session blocks further insertion pending manual reconciliation. It never blindly re-uploads.
- The final chunk is withheld if streamed file hash differs or release time is less than five minutes away. Reconcile the existing incomplete upload before revising a late schedule. This protection does not guarantee Google processing finishes before the chosen release time; schedule with sufficient lead time.
- Thumbnail failure retains the uploaded video ID. Retry recovers that step. Remote checks verify channel, metadata, disclosures, privacy, schedule and processing. Processing is a distinct pending state. Keep the page open or use its status/retry control to recheck; no scheduled background watcher is installed.
- The release is already scheduled once the video upload completes. If a later thumbnail/verification step fails, inspect the saved video in Studio promptly; the service does not cancel its schedule automatically or claim failure means nothing was scheduled.

## Native A/B

Sam uses Chrome on the saved Studio URL and only the approved variants. Native A/B requires desktop Studio and advanced features. Private videos, Shorts, scheduled lives, made-for-kids/mature content and Premieres are not eligible (Premieres become eligible after ending). Since API-scheduled uploads are private until release, the test may need to start after publication. Never publish early merely to start a test. No fake thumbnail rotation and no claimed running test without visible confirmation.

Source: https://support.google.com/youtube/answer/16391400?hl=en

## Verification

    python -B -m unittest discover -s tests -p "test_youtube*.py"

Live Google consent, upload/scheduling and native A/B remain untested until a specific authorized video is available. The module provides no delete action or broad credential-management access.

## Source references

- https://developers.google.com/youtube/data/reference/rest/v3/videos/insert
- https://developers.google.com/youtube/data/reference/rest/v3/videos
- https://developers.google.com/youtube/data/reference/rest/v3/thumbnails/set
- https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
