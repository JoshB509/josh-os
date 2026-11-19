# Kit image draft preparation: operational boundary

`studio/kit_hosted_drafts.py` is a host-only, offline preparation layer and durable
single-attempt gate. It is **not exposed to Laia or wired into the server**. It
does not upload files, fetch URLs, call Kit, obtain credentials, or control a browser.
The default transport is unavailable and fails before reserving a write.

The supported reusable pieces are:

- Exact original PNG bytes must match a trusted host's read of the existing
  Kit-hosted resource. URL receipts bind email id/revision/content, original image
  version/source hash/PNG hash, alt text, placement, dimensions and evidence id.
- Only the observed `https://embed.filekitcdn.com/e/...` resource family is accepted;
  no arbitrary hosts, redirects, credential URLs, fragments or query strings.
- All 1–16 images are composed at their exact body boundaries, with escaped alt
  text, preserved links/Liquid and no captions. Missing receipts fail; images are
  never silently omitted or converted to a text-only draft.
- Complete duplicate discovery selects an existing matching or linked broadcast.
  A protected/ambiguous target fails. An existing draft never falls back to create.
- The exact packet binds `public:false`, `send_at:null`, source revision, target,
  baseline, image URLs and explicit audience intent. Unspecified audience stays
  unspecified; it never defaults to segment 607863 or another saved preference.
- Trusted authenticated request identity authorizes one packet digest. A durable
  exclusive reservation prevents repeated writes. Uncertain writes never retry.
  Successful repeats only read the existing draft and verify its current state.
- Fresh local and remote checks precede the write. The transport must provide an
  exact-baseline update guard and prove an unset audience where requested. Saved
  subject, preview, complete image HTML, audience and private unsent state are
  checked afterwards. The gate never mutates the local email.

## What is still unavailable

CareerOS `KitChromeBrowser.readiness()` still reports `available:false` and has
no image-upload/insertion implementation. The separately authenticated cloud
browser used for the one-off delivered draft is not a CareerOS/Laia capability.

No production transport implements this new gate. Existing `KitDrafts` remains
unchanged: it guards approved text draft writes through the Kit connector, blocks
local images, requires a chosen audience, and limits updates to broadcasts it owns.
The manually delivered broadcast is linked locally, not falsely marked as a draft
created by that guarded writer. Its original images cannot be replaced by that API
path merely because local source files exist.

To activate reuse, the host needs supported receipt intake for existing Kit URLs,
a verified draft transport with raw readback evidence, explicit authorization
wiring, and confirmed unset-audience behavior. New images additionally require a
supported upload operation. Exact-baseline updates remain blocked for adapters
that cannot enforce that contract; API availability alone is not enough.

No installation, security change, broad browser permission, custom CDP channel,
public hosting change, restart or live Kit mutation is included in this change.

## Documented service capabilities

- [Kit create broadcast](https://developers.kit.com/api-reference/broadcasts/create-a-broadcast)
  supports HTML and specifies `send_at:null` for a draft and `public:true` for web
  publication. This gate fixes `public:false` and `send_at:null`.
- [Kit connector tools](https://help.kit.com/en/articles/15072804-ai-connector-tools)
  documents HTML/content-tree draft creation. Its current list and the
  [official endpoint index](https://developers.kit.com/llms.txt) list no image-upload
  operation. No private upload endpoint is assumed here.

Tests use isolated fake transports and local PNG fixtures only. They do not prove
that a live upload or production adapter exists.
